"""
Android APK 在线更新（OTA）：热更新配置 + APK 落盘。

配置文件 ``app_ota_runtime.json`` 修改后无需重启即可生效。
未配置或 enabled=false 时，版本检查返回 has_update=false，不影响业务。

发版推荐上传编译产物 zip（含 .apk + .json），见 docs/APK_PACKAGE_GUIDE.md。
"""
from __future__ import annotations

import hashlib
import io
import json
import re
import time
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from django.conf import settings

# 允许中文等 Unicode 文件名（编译产物如「放射安全检测报告-v0.1.16-build16.apk」）
_UNSAFE_APK_NAME_RE = re.compile(r'[\x00-\x1f\\/]|^\.|\.\.|[\r\n]')

_cache_mtime: float | None = None
_cache_cfg: "AppOtaRuntimeConfig | None" = None

_MAX_ZIP_UNCOMPRESSED = 512 * 1024 * 1024  # 512 MiB 防护
_MAX_ZIP_MEMBERS = 32


@dataclass(frozen=True)
class AppOtaRuntimeConfig:
    enabled: bool = False
    version: str = ""
    build_number: int = 0
    filename: str = ""
    file_size: int = 0
    sha256: str = ""
    release_notes: str = ""
    force_update: bool = False

    @property
    def is_ready(self) -> bool:
        if not self.enabled:
            return False
        if self.build_number <= 0 or not (self.version or "").strip():
            return False
        if not (self.filename or "").strip():
            return False
        return apk_file_path(self.filename).is_file()


@dataclass(frozen=True)
class OtaPackageIngestResult:
    filename: str
    file_size: int
    sha256: str
    version: str = ""
    build_number: int = 0
    release_notes: str = ""
    force_update: bool = False
    meta_from_json: bool = False
    package_kind: str = "apk"  # apk | zip


def app_ota_config_path() -> Path:
    configured = getattr(settings, "APP_OTA_RUNTIME_CONFIG_FILE", None)
    if configured:
        return Path(configured)
    return Path(settings.BASE_DIR) / "app_ota_runtime.json"


def app_ota_apk_dir() -> Path:
    configured = getattr(settings, "APP_OTA_APK_DIR", None)
    if configured:
        root = Path(configured)
    else:
        root = Path(settings.MEDIA_ROOT) / "apk"
    root.mkdir(parents=True, exist_ok=True)
    return root


def sanitize_apk_filename(name: str) -> str:
    """校验并规范化 APK 文件名（仅 basename，允许中文）。"""
    base = Path(str(name or "").strip()).name
    if not base.lower().endswith(".apk"):
        raise ValueError("文件名须以 .apk 结尾")
    if len(base) < 5 or len(base) > 200:
        raise ValueError("APK 文件名长度不合法")
    if _UNSAFE_APK_NAME_RE.search(base):
        raise ValueError("APK 文件名含非法字符（禁止路径分隔符、控制字符、..）")
    stem = base[:-4].strip()
    if not stem:
        raise ValueError("APK 文件名无效")
    return base


def apk_file_path(filename: str) -> Path:
    safe = sanitize_apk_filename(filename)
    root = app_ota_apk_dir().resolve()
    path = (root / safe).resolve()
    if path.parent != root:
        raise ValueError("非法 APK 路径")
    return path


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _parse_bool(raw: Any, default: bool = False) -> bool:
    if isinstance(raw, bool):
        return raw
    s = str(raw or "").strip().lower()
    if s in ("1", "true", "yes", "on"):
        return True
    if s in ("0", "false", "no", "off"):
        return False
    return default


def _filename_from_download_url(url: str) -> str:
    raw = str(url or "").strip()
    if not raw:
        return ""
    try:
        path = unquote(urlparse(raw).path or "")
    except Exception:
        path = raw
    name = Path(path).name
    if name.lower().endswith(".apk"):
        try:
            return sanitize_apk_filename(name)
        except ValueError:
            return ""
    return ""


def parse_package_meta_json(raw: bytes | str | dict[str, Any]) -> dict[str, Any]:
    """解析发版 zip 内的 JSON 元数据（APK_PACKAGE_GUIDE 字段）。"""
    if isinstance(raw, dict):
        data = raw
    else:
        if isinstance(raw, bytes):
            text = raw.decode("utf-8-sig")
        else:
            text = str(raw)
        data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("发版 JSON 根须为对象")
    out: dict[str, Any] = {}
    out["version"] = str(data.get("version") or "").strip()
    try:
        out["build_number"] = int(data.get("build_number"))
    except (TypeError, ValueError):
        out["build_number"] = 0
    try:
        out["file_size"] = int(data.get("file_size") or 0)
    except (TypeError, ValueError):
        out["file_size"] = 0
    out["release_notes"] = str(data.get("release_notes") or "").strip()
    out["force_update"] = _parse_bool(data.get("force_update"), False)
    out["download_url"] = str(data.get("download_url") or "").strip()
    out["filename_hint"] = _filename_from_download_url(out["download_url"])
    return out


def _from_dict(data: dict[str, Any] | None) -> AppOtaRuntimeConfig:
    if not isinstance(data, dict):
        return AppOtaRuntimeConfig()
    try:
        build = int(data.get("build_number") or 0)
    except (TypeError, ValueError):
        build = 0
    try:
        size = int(data.get("file_size") or 0)
    except (TypeError, ValueError):
        size = 0
    filename = str(data.get("filename") or "").strip()
    try:
        if filename:
            filename = sanitize_apk_filename(filename)
    except ValueError:
        filename = ""
    return AppOtaRuntimeConfig(
        enabled=_parse_bool(data.get("enabled"), False),
        version=str(data.get("version") or "").strip(),
        build_number=max(0, build),
        filename=filename,
        file_size=max(0, size),
        sha256=str(data.get("sha256") or "").strip().lower(),
        release_notes=str(data.get("release_notes") or "").strip(),
        force_update=_parse_bool(data.get("force_update"), False),
    )


def get_app_ota_runtime_config(*, force_reload: bool = False) -> AppOtaRuntimeConfig:
    global _cache_mtime, _cache_cfg
    path = app_ota_config_path()
    try:
        mtime = path.stat().st_mtime if path.is_file() else None
    except OSError:
        mtime = None
    if (
        not force_reload
        and _cache_cfg is not None
        and _cache_mtime == mtime
    ):
        return _cache_cfg
    data: dict[str, Any] | None = None
    if path.is_file():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                data = raw
        except (OSError, json.JSONDecodeError, UnicodeError):
            data = None
    cfg = _from_dict(data)
    if cfg.filename:
        try:
            fp = apk_file_path(cfg.filename)
            if fp.is_file():
                size = cfg.file_size or int(fp.stat().st_size)
                sha = cfg.sha256 or ""
                if size != cfg.file_size or (not sha):
                    if not sha:
                        sha = _sha256_file(fp)
                    cfg = AppOtaRuntimeConfig(
                        enabled=cfg.enabled,
                        version=cfg.version,
                        build_number=cfg.build_number,
                        filename=cfg.filename,
                        file_size=size,
                        sha256=sha,
                        release_notes=cfg.release_notes,
                        force_update=cfg.force_update,
                    )
        except (OSError, ValueError):
            pass
    _cache_mtime = mtime
    _cache_cfg = cfg
    return cfg


def write_app_ota_runtime_config(
    *,
    enabled: bool,
    version: str,
    build_number: int,
    filename: str,
    release_notes: str = "",
    force_update: bool = False,
    file_size: int | None = None,
    sha256: str | None = None,
) -> AppOtaRuntimeConfig:
    global _cache_mtime, _cache_cfg
    safe_name = sanitize_apk_filename(filename)
    fp = apk_file_path(safe_name)
    if not fp.is_file():
        raise FileNotFoundError(f"APK 文件不存在: {safe_name}")
    size = int(file_size) if file_size is not None else int(fp.stat().st_size)
    digest = (sha256 or "").strip().lower() or _sha256_file(fp)
    cfg = AppOtaRuntimeConfig(
        enabled=bool(enabled),
        version=str(version or "").strip(),
        build_number=max(0, int(build_number)),
        filename=safe_name,
        file_size=max(0, size),
        sha256=digest,
        release_notes=str(release_notes or "").strip(),
        force_update=bool(force_update),
    )
    path = app_ota_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = asdict(cfg)
    tmp = path.with_suffix(path.suffix + f".{int(time.time())}.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
    _cache_mtime = path.stat().st_mtime
    _cache_cfg = cfg
    return cfg


def validate_build_number_monotonic(
    *,
    new_build: int,
    previous_build: int,
    package_replaced: bool,
) -> None:
    """
    build_number 须整数比较且单调递增。
    - 换包或提高 build：必须 > 旧值
    - 仅改元数据且 build 不变：允许
    """
    prev = max(0, int(previous_build or 0))
    nb = int(new_build)
    if nb <= 0:
        raise ValueError("build_number 须为正整数")
    if prev <= 0:
        return
    if package_replaced and nb <= prev:
        raise ValueError(f"换包发版时 build_number 须大于当前 {prev}（收到 {nb}）")
    if (not package_replaced) and nb < prev:
        raise ValueError(f"build_number 不能回退（当前 {prev}，收到 {nb}）")
    if (not package_replaced) and nb > prev:
        # 仅改元数据却提高 build：允许（未换包也可登记更高 build，较少见）
        return
    if (not package_replaced) and nb == prev:
        return
    if package_replaced and nb > prev:
        return


def _write_apk_bytes(data: bytes, filename: str) -> tuple[str, int, str]:
    safe = sanitize_apk_filename(filename)
    dest = apk_file_path(safe)
    dest.write_bytes(data)
    return safe, len(data), _sha256_bytes(data)


def save_uploaded_apk(uploaded_file, *, preferred_name: str = "") -> tuple[str, int, str]:
    """
    保存上传的 APK 到 media/apk/。
    返回 (filename, file_size, sha256)。
    """
    raw_name = preferred_name or getattr(uploaded_file, "name", "") or "app.apk"
    if not str(raw_name).lower().endswith(".apk"):
        raw_name = f"{raw_name}.apk"
    safe = sanitize_apk_filename(raw_name)
    dest = apk_file_path(safe)
    h = hashlib.sha256()
    size = 0
    with dest.open("wb") as out:
        for chunk in uploaded_file.chunks():
            out.write(chunk)
            h.update(chunk)
            size += len(chunk)
    return safe, size, h.hexdigest()


def _read_uploaded_all(uploaded_file) -> bytes:
    chunks: list[bytes] = []
    total = 0
    for chunk in uploaded_file.chunks():
        chunks.append(chunk)
        total += len(chunk)
        if total > _MAX_ZIP_UNCOMPRESSED:
            raise ValueError("上传文件过大")
    return b"".join(chunks)


def _ingest_zip_bytes(blob: bytes) -> OtaPackageIngestResult:
    try:
        zf = zipfile.ZipFile(io.BytesIO(blob))
    except zipfile.BadZipFile as exc:
        raise ValueError("不是有效的 zip 包") from exc
    with zf:
        infos = [i for i in zf.infolist() if not i.is_dir()]
        if len(infos) > _MAX_ZIP_MEMBERS:
            raise ValueError("zip 内文件过多")
        total_uncomp = 0
        apk_candidates: list[tuple[str, zipfile.ZipInfo]] = []
        json_candidates: list[tuple[str, zipfile.ZipInfo]] = []
        for info in infos:
            name = Path(info.filename).name
            if not name or name.startswith("."):
                continue
            # zip slip：禁止绝对路径 / 上级目录
            if ".." in Path(info.filename).parts or info.filename.startswith("/") or info.filename.startswith("\\"):
                raise ValueError(f"zip 含非法路径: {info.filename}")
            total_uncomp += max(0, int(info.file_size or 0))
            if total_uncomp > _MAX_ZIP_UNCOMPRESSED:
                raise ValueError("zip 解压后体积过大")
            lower = name.lower()
            if lower.endswith(".apk"):
                apk_candidates.append((name, info))
            elif lower.endswith(".json"):
                json_candidates.append((name, info))
        if not apk_candidates:
            raise ValueError("zip 中未找到 .apk 文件")

        meta: dict[str, Any] = {}
        meta_from_json = False
        json_bytes: bytes | None = None
        # 优先与 apk 同主名的 json
        apk_stems = {Path(n).stem.lower() for n, _ in apk_candidates}
        preferred_json = None
        for n, info in json_candidates:
            if Path(n).stem.lower() in apk_stems:
                preferred_json = (n, info)
                break
        if preferred_json is None and json_candidates:
            preferred_json = json_candidates[0]
        if preferred_json is not None:
            json_bytes = zf.read(preferred_json[1])
            meta = parse_package_meta_json(json_bytes)
            meta_from_json = True

        # 选定 APK：优先 JSON download_url 文件名，其次与 json 同名，否则第一个
        apk_name = ""
        apk_info: zipfile.ZipInfo | None = None
        hint = str(meta.get("filename_hint") or "")
        if hint:
            for n, info in apk_candidates:
                if n == hint or n.lower() == hint.lower():
                    apk_name, apk_info = n, info
                    break
        if apk_info is None and preferred_json is not None:
            jstem = Path(preferred_json[0]).stem.lower()
            for n, info in apk_candidates:
                if Path(n).stem.lower() == jstem:
                    apk_name, apk_info = n, info
                    break
        if apk_info is None:
            apk_name, apk_info = apk_candidates[0]

        apk_data = zf.read(apk_info)
        if not apk_data:
            raise ValueError("APK 文件为空")
        # 若 JSON 指定了文件名且与包内不同，仍以包内实际文件名为准（保证可下载）
        try:
            safe_name = sanitize_apk_filename(apk_name)
        except ValueError as exc:
            raise ValueError(f"zip 内 APK 文件名不合法: {exc}") from exc

        filename, size, digest = _write_apk_bytes(apk_data, safe_name)
        declared = int(meta.get("file_size") or 0)
        if declared > 0 and declared != size:
            # 以实际落盘为准，不阻断发版
            pass
        return OtaPackageIngestResult(
            filename=filename,
            file_size=size,
            sha256=digest,
            version=str(meta.get("version") or ""),
            build_number=int(meta.get("build_number") or 0),
            release_notes=str(meta.get("release_notes") or ""),
            force_update=bool(meta.get("force_update")),
            meta_from_json=meta_from_json,
            package_kind="zip",
        )


def ingest_uploaded_release_package(
    uploaded_file,
    *,
    preferred_apk_name: str = "",
) -> OtaPackageIngestResult:
    """
    摄入发版包：优先 zip（apk+json），亦兼容直接上传 .apk。
    download_url 中的 Host 忽略，仅取文件名；对外接口仍按请求 Host 拼绝对地址。
    """
    upload_name = str(getattr(uploaded_file, "name", "") or "").strip()
    lower = upload_name.lower()
    # 先看扩展名；zip 用魔数兜底
    head = b""
    if hasattr(uploaded_file, "read"):
        # Django UploadedFile：peek 后需 seek(0)
        try:
            pos = uploaded_file.tell()
        except Exception:
            pos = 0
        try:
            head = uploaded_file.read(4) or b""
            uploaded_file.seek(pos)
        except Exception:
            try:
                uploaded_file.seek(0)
            except Exception:
                pass

    is_zip = lower.endswith(".zip") or head.startswith(b"PK\x03\x04") or head.startswith(b"PK\x05\x06")
    is_apk = lower.endswith(".apk")

    if is_zip and not is_apk:
        blob = _read_uploaded_all(uploaded_file)
        return _ingest_zip_bytes(blob)

    if is_apk or (not is_zip and lower.endswith(".apk")):
        preferred = preferred_apk_name or upload_name or "app.apk"
        filename, size, digest = save_uploaded_apk(uploaded_file, preferred_name=preferred)
        return OtaPackageIngestResult(
            filename=filename,
            file_size=size,
            sha256=digest,
            package_kind="apk",
        )

    # 无名扩展：尝试 zip / 当 apk
    blob = _read_uploaded_all(uploaded_file)
    if blob[:2] == b"PK":
        return _ingest_zip_bytes(blob)
    if preferred_apk_name or upload_name:
        name = preferred_apk_name or upload_name
        if not str(name).lower().endswith(".apk"):
            name = f"{name}.apk"
        filename, size, digest = _write_apk_bytes(blob, name)
        return OtaPackageIngestResult(
            filename=filename,
            file_size=size,
            sha256=digest,
            package_kind="apk",
        )
    raise ValueError("请上传发版 zip（含 apk+json）或 .apk 文件")


def parse_client_build(raw: Any) -> int | None:
    if raw is None or raw == "":
        return None
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return None


def build_version_check_payload(
    *,
    request,
    current_build: int | None,
    current_version: str = "",
) -> dict[str, Any]:
    """
    组装版本检查响应。download_url 按当前请求 Host 拼绝对地址（可换服务器）。
    另附 download_path 供客户端用 kBaseUrl 自行拼接。
    """
    del current_version  # 仅展示/日志用，比较以 build 为准
    cfg = get_app_ota_runtime_config()
    empty = {
        "has_update": False,
        "version": cfg.version or "",
        "build_number": int(cfg.build_number or 0),
        "download_url": "",
        "download_path": "",
        "file_size": int(cfg.file_size or 0),
        "sha256": cfg.sha256 or "",
        "release_notes": cfg.release_notes or "",
        "force_update": bool(cfg.force_update),
    }
    if not cfg.is_ready:
        empty["build_number"] = 0
        return empty

    # 路径段保留 Unicode（与 APK_PACKAGE_GUIDE 示例一致）；Django 路由可匹配
    rel_path = f"/api/v2/app/apk/{cfg.filename}"
    try:
        absolute = request.build_absolute_uri(rel_path)
    except Exception:
        absolute = rel_path

    has_update = current_build is None or int(cfg.build_number) > int(current_build)
    return {
        "has_update": bool(has_update),
        "version": cfg.version,
        "build_number": int(cfg.build_number),
        "download_url": absolute,
        "download_path": rel_path,
        "file_size": int(cfg.file_size or 0),
        "sha256": cfg.sha256 or "",
        "release_notes": cfg.release_notes or "",
        "force_update": bool(cfg.force_update) if has_update else False,
    }
