"""检测提交 JSON：将内嵌 base64 图片提取为待落盘条目，并精简 JSON。"""
from __future__ import annotations

import base64
import copy
import hashlib
import json
import os
import re
from typing import Any, Iterator

from utils.frontend_schema_rule_engine import CANONICAL_SIGNATURE_PDF_FIELD_ROLES

_DATA_URL_RE = re.compile(
    r"^data:image/(?P<fmt>jpeg|jpg|png|webp);base64,(?P<data>.+)$",
    re.IGNORECASE | re.DOTALL,
)

# 现场记录仅保留三个签名角色（与 JS009 V3 模板一致）
CANONICAL_SIGNATURE_ROLES = ("inspector", "checker", "accompanyingPerson")

# 现行模板：仅 f625 / f632 / f633 三个签名 pdfFieldId
_PDF_FIELD_TO_SIGNATURE_ROLE: dict[str, str] = {
    pid: role for pid, (role, _label) in CANONICAL_SIGNATURE_PDF_FIELD_ROLES.items()
}

_SIGNATURE_PDF_FIELD_IDS = frozenset(_PDF_FIELD_TO_SIGNATURE_ROLE.keys())

_ROLE_TO_PDF_FIELD: dict[str, str] = {
    role: pid for pid, (role, _label) in CANONICAL_SIGNATURE_PDF_FIELD_ROLES.items()
}

# 旧版提交只读归并，禁止写回 dynamicData
_LEGACY_PDF_FIELD_TO_SIGNATURE_ROLE: dict[str, str] = {
    "f36": "inspector",
    "f35": "checker",
    "f34": "accompanyingPerson",
    "f76": "inspector",
    "f78": "checker",
    "f77": "accompanyingPerson",
}

_LEGACY_SIGNATURE_PDF_FIELD_IDS = frozenset(_LEGACY_PDF_FIELD_TO_SIGNATURE_ROLE.keys())

_ROLE_ALIAS_TO_CANONICAL: dict[str, str] = {
    "inspector": "inspector",
    "maininspector": "inspector",
    "author": "inspector",
    "检测员": "inspector",
    "参与主要检测人员": "inspector",
    "checker": "checker",
    "reviewer": "checker",
    "校核": "checker",
    "校核员": "checker",
    "校核员及校核日期": "checker",
    "accompanyingperson": "accompanyingPerson",
    "approver": "accompanyingPerson",
    "受检单位陪同人": "accompanyingPerson",
    "f625": "inspector",
    "f632": "checker",
    "f633": "accompanyingPerson",
    "f36": "inspector",
    "f35": "checker",
    "f34": "accompanyingPerson",
    "f76": "inspector",
    "f78": "checker",
    "f77": "accompanyingPerson",
}

_SIGNATURE_KEYS = frozenset(_ROLE_ALIAS_TO_CANONICAL.keys()) | frozenset(
    CANONICAL_SIGNATURE_ROLES
)

# dynamicData 中签名字段 pdfFieldId（现行 + 旧版只读）
_DYNAMIC_SIGNATURE_FIELD_IDS = _SIGNATURE_PDF_FIELD_IDS | _LEGACY_SIGNATURE_PDF_FIELD_IDS

_MIN_INLINE_BINARY_LEN = 120


def decode_image_data_url(data_url: str) -> tuple[bytes, str]:
    """解析 data:image/...;base64,... 为 (bytes, 扩展名)。"""
    if not isinstance(data_url, str) or not data_url.strip():
        raise ValueError("empty dataUrl")
    match = _DATA_URL_RE.match(data_url.strip())
    if not match:
        raise ValueError("unsupported dataUrl format")
    fmt = match.group("fmt").lower()
    ext = "jpg" if fmt in ("jpeg", "jpg") else fmt
    raw = base64.b64decode(match.group("data"), validate=True)
    if not raw:
        raise ValueError("empty image bytes")
    return raw, ext


def _ext_from_image_magic(raw: bytes) -> str:
    if raw.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if raw.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if raw.startswith(b"RIFF") and len(raw) > 12 and raw[8:12] == b"WEBP":
        return "webp"
    return "png"


def decode_image_bytes_any(value: str) -> tuple[bytes, str]:
    """data URL 或纯 base64（根据魔数判断 png/jpg/webp）。"""
    if not isinstance(value, str):
        raise ValueError("not a string")
    text = value.strip()
    if not text or len(text) < 40:
        raise ValueError("too short")
    if text.startswith("data:image"):
        return decode_image_data_url(text)
    compact = re.sub(r"\s+", "", text)
    raw = base64.b64decode(compact, validate=True)
    if not raw:
        raise ValueError("empty")
    return raw, _ext_from_image_magic(raw)


def _looks_like_inline_image(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    s = value.strip()
    if len(s) < _MIN_INLINE_BINARY_LEN:
        return False
    if s.startswith("data:image"):
        return True
    if s.startswith("iVBORw") or s.startswith("/9j/") or s.startswith("UklGR"):
        return True
    if len(s) > 400 and re.fullmatch(r"[A-Za-z0-9+/=\s]+", s[: min(len(s), 2000)] or ""):
        return True
    return False


def _is_stored_media_path(value: object) -> bool:
    if not isinstance(value, str):
        return False
    s = value.strip()
    return s.startswith(("/media", "media/", "file_library", "inspection_submits"))


def _media_relative_path(value: str) -> str:
    s = value.strip().lstrip("/")
    if s.startswith("media/"):
        s = s[6:]
    return s


def _sha256_of_stored_media(value: str) -> str | None:
    """已落盘 /media/... 或 file_library/... 路径对应文件的 sha256；读不到则 None。"""
    if not _is_stored_media_path(value):
        return None
    try:
        from django.conf import settings

        rel = _media_relative_path(value)
        full = os.path.join(settings.MEDIA_ROOT, rel)
        if not os.path.isfile(full):
            return None
        with open(full, "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()
    except OSError:
        return None


def _reuse_mirror_stored_url(
    payload: dict,
    path: tuple,
    *,
    raw_digest: str,
) -> str | None:
    """
    testResult 等镜像栏位若与 dynamicData 中同 key 的已落盘文件内容一致，直接复用 URL，避免重复落盘。
    """
    if len(path) != 2 or path[0] != "testResult":
        return None
    dd = payload.get("dynamicData")
    if not isinstance(dd, dict):
        return None
    key = path[1]
    stored = dd.get(key)
    if not isinstance(stored, str) or not _is_stored_media_path(stored):
        return None
    if _sha256_of_stored_media(stored) == raw_digest:
        return stored.strip()
    return None


def _safe_asset_filename(key: str, ext: str, idx: int) -> str:
    safe = re.sub(r"[^\w\-.]+", "_", (key or "asset").strip())[:48] or "asset"
    return f"{safe}_{idx:02d}.{ext}"


def _canonical_signature_role(key: str) -> str | None:
    k = str(key or "").strip()
    if not k:
        return None
    if k in CANONICAL_SIGNATURE_ROLES:
        return k
    return _ROLE_ALIAS_TO_CANONICAL.get(k) or _ROLE_ALIAS_TO_CANONICAL.get(k.lower())


def _signature_value_priority(value: str) -> int:
    """已落盘路径优先于内联 base64。"""
    if _is_stored_media_path(value):
        return 0
    if _looks_like_inline_image(value):
        return 1
    return 9


def consolidate_submit_signatures(payload: dict) -> dict:
    """
    将 signatures / dynamicData 中分散的签名合并为三个角色，并去掉 f665 等误占槽位的图片。
    """
    if not isinstance(payload, dict):
        return payload
    out = copy.deepcopy(payload)
    sig_in = out.get("signatures") if isinstance(out.get("signatures"), dict) else {}
    dd = out.get("dynamicData") if isinstance(out.get("dynamicData"), dict) else {}

    buckets: dict[str, list[str]] = {r: [] for r in CANONICAL_SIGNATURE_ROLES}

    def _offer(raw_key: str, value: object) -> None:
        role = _canonical_signature_role(raw_key)
        if not role or not isinstance(value, str):
            return
        s = value.strip()
        if not s:
            return
        if _is_stored_media_path(s) or _looks_like_inline_image(s):
            buckets[role].append(s)

    for k, v in sig_in.items():
        _offer(str(k), v)
    for pid in _DYNAMIC_SIGNATURE_FIELD_IDS:
        _offer(pid, dd.get(pid))

    # 误写入 f665/f676/f677 的签名图：仅当对应角色仍空时归位到检测员/校核/陪同
    _LEGACY_MISPLACED = (
        ("f665", "inspector"),
        ("f676", "checker"),
        ("f677", "accompanyingPerson"),
    )
    for pid, role in _LEGACY_MISPLACED:
        val = dd.get(pid)
        if not isinstance(val, str) or not val.strip():
            continue
        if not (_is_stored_media_path(val) or _looks_like_inline_image(val)):
            continue
        if not buckets[role]:
            buckets[role].append(val.strip())

    merged: dict[str, str | None] = {}
    for role in CANONICAL_SIGNATURE_ROLES:
        candidates = sorted(
            dict.fromkeys(buckets[role]),
            key=_signature_value_priority,
        )
        merged[role] = candidates[0] if candidates else None
    out["signatures"] = merged

    if isinstance(out.get("dynamicData"), dict):
        dd_out = out["dynamicData"]
        for pid in _SIGNATURE_PDF_FIELD_IDS:
            role = _PDF_FIELD_TO_SIGNATURE_ROLE.get(pid)
            if role and merged.get(role):
                dd_out[pid] = merged[role]
        for pid, _role in _LEGACY_MISPLACED:
            if _looks_like_inline_image(dd_out.get(pid)) or _is_stored_media_path(
                dd_out.get(pid)
            ):
                dd_out.pop(pid, None)
        for pid in _LEGACY_SIGNATURE_PDF_FIELD_IDS:
            val = dd_out.get(pid)
            if isinstance(val, str) and val.strip() and (
                _looks_like_inline_image(val) or _is_stored_media_path(val)
            ):
                dd_out.pop(pid, None)
    return out


def extract_canonical_signature_assets(payload: dict) -> tuple[dict, list[dict]]:
    """每个角色至多落盘一张 PNG；同内容哈希复用同一文件。"""
    out = consolidate_submit_signatures(payload)
    pending: list[dict] = []
    sig = out.get("signatures")
    if not isinstance(sig, dict):
        return out, pending

    hash_to_pending: dict[str, dict] = {}

    for role in CANONICAL_SIGNATURE_ROLES:
        val = sig.get(role)
        if not isinstance(val, str) or not val.strip():
            sig[role] = None
            continue
        if _is_stored_media_path(val):
            pid = _ROLE_TO_PDF_FIELD.get(role)
            if pid and isinstance(out.get("dynamicData"), dict):
                out["dynamicData"][pid] = val.strip()
            continue
        if not _looks_like_inline_image(val):
            sig[role] = None
            continue
        try:
            raw, ext = decode_image_bytes_any(val)
        except (ValueError, base64.binascii.Error):
            sig[role] = None
            continue
        digest = hashlib.sha256(raw).hexdigest()
        if digest in hash_to_pending:
            item = hash_to_pending[digest]
            item.setdefault("targets", []).append(("signatures", role))
            pid = _ROLE_TO_PDF_FIELD.get(role)
            if pid:
                item["targets"].append(("dynamicData", pid))
            sig[role] = ""
            continue
        filename = f"{role}.{ext}"
        targets: list[tuple] = [("signatures", role)]
        pid = _ROLE_TO_PDF_FIELD.get(role)
        if pid:
            targets.append(("dynamicData", pid))
        item = {
            "filename": filename,
            "raw_bytes": raw,
            "subdir": "signatures",
            "targets": targets,
            "target": targets[0],
        }
        hash_to_pending[digest] = item
        pending.append(item)
        sig[role] = ""

    return out, pending


def extract_section_photo_assets(payload: dict) -> tuple[dict, list[dict]]:
    """
    深拷贝 payload，移除 sectionPhotos 中的 dataUrl / 内联 url，返回待落盘照片列表。
    """
    out = copy.deepcopy(payload)
    photos = out.get("sectionPhotos")
    if not isinstance(photos, list) or not photos:
        return out, []

    pending: list[dict] = []
    new_photos: list = []

    for idx, photo in enumerate(photos):
        if not isinstance(photo, dict):
            new_photos.append(photo)
            continue
        item = dict(photo)
        inline = item.pop("dataUrl", None) or item.pop("data_url", None)
        if not inline:
            inline = item.get("url") if _looks_like_inline_image(item.get("url")) else None
            if inline:
                item["url"] = ""
        if not inline:
            new_photos.append(item)
            continue
        if _is_stored_media_path(inline):
            item["url"] = inline
            new_photos.append(item)
            continue
        try:
            if str(inline).startswith("data:image"):
                raw, ext = decode_image_data_url(inline)
            else:
                raw, ext = decode_image_bytes_any(str(inline))
        except (ValueError, base64.binascii.Error):
            item["dataUrl"] = inline
            new_photos.append(item)
            continue
        filename = _safe_asset_filename(
            str(item.get("sectionId") or ""),
            ext,
            idx,
        )
        pending.append(
            {
                "filename": filename,
                "raw_bytes": raw,
                "subdir": "photos",
                "target": ("sectionPhotos", idx, "url"),
                "targets": [("sectionPhotos", idx, "url")],
                "photo_meta": item,
            }
        )
        item["url"] = ""
        new_photos.append(item)

    out["sectionPhotos"] = new_photos
    return out, pending


def _extract_dict_image_fields(
    container: dict,
    *,
    subdir: str,
    path_prefix: tuple,
    pending: list[dict],
    skip_keys: frozenset[str] | None = None,
) -> None:
    skip = skip_keys or frozenset()
    idx = 0
    for key, val in list(container.items()):
        if key in skip or not _looks_like_inline_image(val):
            continue
        try:
            raw, ext = decode_image_bytes_any(val)
        except (ValueError, base64.binascii.Error):
            continue
        filename = _safe_asset_filename(str(key), ext, idx)
        idx += 1
        pending.append(
            {
                "filename": filename,
                "raw_bytes": raw,
                "subdir": subdir,
                "target": path_prefix + (key,),
            }
        )
        container[key] = ""


def extract_inline_binary_assets(payload: dict) -> tuple[dict, list[dict]]:
    """兼容旧调用名：等价于 ``extract_all_payload_binary_assets``。"""
    return extract_all_payload_binary_assets(payload)


def _strip_dict_inline_images(container: dict | None, *, extra_keys: frozenset[str] | None = None) -> dict:
    if not isinstance(container, dict):
        return {}
    extra = extra_keys or frozenset()
    cleaned = dict(container)
    for key, value in list(cleaned.items()):
        if not isinstance(value, str):
            continue
        if key in extra or key in _SIGNATURE_KEYS or len(value) > _MIN_INLINE_BINARY_LEN:
            if _looks_like_inline_image(value) or value.startswith("data:image"):
                cleaned[key] = ""
    return cleaned


def _parse_floor_plan_diagram_json(raw: str) -> dict | None:
    s = (raw or "").strip()
    if not s.startswith("{"):
        return None
    try:
        obj = json.loads(s)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    if isinstance(obj, dict) and str(obj.get("kind") or "") == "floorPlanDiagram":
        return obj
    return None


def _normalize_floor_plan_container(container: dict) -> str | None:
    """
    归一化单个 dict 内的 f686/f688/f689；返回待落盘的平面图 base64（若尚未落盘）。
    """
    if not isinstance(container, dict):
        return None
    pending_image: str | None = None
    for slot in list(container.keys()):
        val = container.get(slot)
        if not isinstance(val, str) or "floorPlanDiagram" not in val:
            continue
        diagram = _parse_floor_plan_diagram_json(val)
        if not diagram:
            continue
        data = diagram.get("data") if isinstance(diagram.get("data"), dict) else {}
        if slot in ("f686", "f689"):
            if data.get("length") is not None:
                container["f686"] = data.get("length")
            if data.get("width") is not None:
                container["f688"] = data.get("width")
            img = diagram.get("imageBase64")
            if isinstance(img, str) and img.strip():
                pending_image = img.strip()
            if slot == "f689" and not _is_stored_media_path(val):
                slim = {k: v for k, v in diagram.items() if k != "imageBase64"}
                container["f689"] = json.dumps(slim, ensure_ascii=False)
    if pending_image and not _is_stored_media_path(container.get("f689")):
        existing = container.get("f689")
        if not isinstance(existing, str) or not existing.strip():
            container["f689"] = pending_image
        elif _parse_floor_plan_diagram_json(existing):
            container["f689"] = pending_image
    return pending_image


def normalize_floor_plan_dynamic_data(payload: dict) -> dict:
    """
    JS009：平面图在 f689，长宽在 f686/f688；客户端常写在 f686 或 testResult 镜像栏位。
    同步处理 dynamicData 与 testResult，避免遗漏内嵌 imageBase64。
    """
    if not isinstance(payload, dict):
        return payload
    out = copy.deepcopy(payload)
    shared_inline: str | None = None
    for key in ("dynamicData", "testResult"):
        block = out.get(key)
        if not isinstance(block, dict):
            continue
        img = _normalize_floor_plan_container(block)
        if img and not shared_inline:
            shared_inline = img
    if shared_inline:
        for key in ("dynamicData", "testResult"):
            block = out.get(key)
            if not isinstance(block, dict):
                continue
            cur = block.get("f689")
            if _is_stored_media_path(cur):
                continue
            if not isinstance(cur, str) or not cur.strip():
                block["f689"] = shared_inline
            elif _parse_floor_plan_diagram_json(cur):
                block["f689"] = shared_inline
    return out


def _iter_payload_value_paths(obj: Any, prefix: tuple = ()) -> Iterator[tuple[tuple, Any]]:
    if isinstance(obj, dict):
        for key, val in obj.items():
            yield from _iter_payload_value_paths(val, prefix + (key,))
    elif isinstance(obj, list):
        for idx, val in enumerate(obj):
            yield from _iter_payload_value_paths(val, prefix + (idx,))
    else:
        yield prefix, obj


def _extract_image_bytes_from_string(val: str) -> tuple[bytes, str] | None:
    if not isinstance(val, str):
        return None
    s = val.strip()
    if not s or _is_stored_media_path(s):
        return None
    if _looks_like_inline_image(s):
        try:
            return decode_image_bytes_any(s)
        except (ValueError, base64.binascii.Error):
            return None
    if "floorPlanDiagram" in s and "imageBase64" in s:
        diagram = _parse_floor_plan_diagram_json(s)
        if not isinstance(diagram, dict):
            return None
        b64 = diagram.get("imageBase64")
        if isinstance(b64, str) and b64.strip():
            try:
                return decode_image_bytes_any(b64.strip())
            except (ValueError, base64.binascii.Error):
                return None
    return None


def _subdir_for_payload_path(path: tuple) -> str:
    if path and path[0] == "signatures":
        return "signatures"
    if path and path[0] == "sectionPhotos":
        return "photos"
    return "assets"


def _filename_for_payload_path(path: tuple, ext: str, idx: int) -> str:
    if path and path[0] == "signatures" and len(path) >= 2:
        role = str(path[1])
        if role in CANONICAL_SIGNATURE_ROLES:
            return f"{role}.{ext}"
    leaf = str(path[-1]) if path else "asset"
    return _safe_asset_filename(leaf, ext, idx)


def _enqueue_binary_asset(
    *,
    pending: list[dict],
    hash_index: dict[str, dict],
    raw: bytes,
    ext: str,
    subdir: str,
    target: tuple,
    filename: str,
) -> None:
    digest = hashlib.sha256(raw).hexdigest()
    if digest in hash_index:
        item = hash_index[digest]
        targets = item.setdefault("targets", [])
        if target not in targets:
            targets.append(target)
        return
    item = {
        "filename": filename,
        "raw_bytes": raw,
        "subdir": subdir,
        "targets": [target],
        "target": target,
    }
    hash_index[digest] = item
    pending.append(item)


def _clear_value_at_path(payload: dict, path: tuple) -> None:
    if not path:
        return
    obj: Any = payload
    for part in path[:-1]:
        if isinstance(obj, dict):
            obj = obj.get(part)
        elif isinstance(obj, list) and isinstance(part, int) and 0 <= part < len(obj):
            obj = obj[part]
        else:
            return
    last = path[-1]
    if isinstance(obj, dict):
        obj[last] = ""
    elif isinstance(obj, list) and isinstance(last, int) and 0 <= last < len(obj):
        if isinstance(obj[last], dict):
            obj[last]["url"] = ""


def _paths_claimed_by_pending(pending: list[dict]) -> set[tuple]:
    claimed: set[tuple] = set()
    for item in pending:
        for t in item.get("targets") or [item.get("target")]:
            if t:
                claimed.add(tuple(t))
    return claimed


def extract_all_payload_binary_assets(payload: dict) -> tuple[dict, list[dict]]:
    """
    全量提取 payload 中的内联图片：签名三角色 + sectionPhotos + 其余路径；
    相同字节只落盘一次，所有引用路径写回同一 URL（不重不漏）。
    """
    out = consolidate_submit_signatures(copy.deepcopy(payload))
    out, pending = extract_canonical_signature_assets(out)
    hash_index: dict[str, dict] = {
        hashlib.sha256(item["raw_bytes"]).hexdigest(): item
        for item in pending
        if item.get("raw_bytes")
    }
    claimed = _paths_claimed_by_pending(pending)

    out, photo_pending = extract_section_photo_assets(out)
    for item in photo_pending:
        raw = item.get("raw_bytes")
        if not raw:
            continue
        digest = hashlib.sha256(raw).hexdigest()
        if digest in hash_index:
            existing = hash_index[digest]
            t = item.get("target")
            if t and t not in existing.setdefault("targets", []):
                existing["targets"].append(t)
            continue
        hash_index[digest] = item
        pending.append(item)
    claimed |= _paths_claimed_by_pending(photo_pending)

    asset_idx = 0
    for path, val in _iter_payload_value_paths(out):
        if not path or path in claimed:
            continue
        if path[0] == "signatures":
            continue
        if not isinstance(val, str):
            continue
        if path[0] == "sectionPhotos" and len(path) >= 2:
            if _is_stored_media_path(val):
                continue
        decoded = _extract_image_bytes_from_string(val)
        if not decoded:
            continue
        raw, ext = decoded
        digest = hashlib.sha256(raw).hexdigest()
        reused = _reuse_mirror_stored_url(out, path, raw_digest=digest)
        if reused:
            obj: Any = out
            for part in path[:-1]:
                if isinstance(obj, dict):
                    obj = obj[part]
                elif isinstance(obj, list) and isinstance(part, int):
                    obj = obj[part] if 0 <= part < len(obj) else None
                else:
                    obj = None
                    break
            if isinstance(obj, dict):
                obj[path[-1]] = reused
            claimed.add(path)
            continue
        subdir = _subdir_for_payload_path(path)
        filename = _filename_for_payload_path(path, ext, asset_idx)
        asset_idx += 1
        _enqueue_binary_asset(
            pending=pending,
            hash_index=hash_index,
            raw=raw,
            ext=ext,
            subdir=subdir,
            target=path,
            filename=filename,
        )
        _clear_value_at_path(out, path)
        claimed.add(path)

    return out, pending


def _strip_inline_value(val: Any) -> Any:
    if isinstance(val, str):
        s = val.strip()
        if _is_stored_media_path(s):
            return val
        if _looks_like_inline_image(s) or s.startswith("data:image"):
            return ""
        if "floorPlanDiagram" in s and "imageBase64" in s:
            diagram = _parse_floor_plan_diagram_json(s)
            if diagram:
                slim = {k: v for k, v in diagram.items() if k != "imageBase64"}
                return json.dumps(slim, ensure_ascii=False)
        if len(s) > _MIN_INLINE_BINARY_LEN and _looks_like_inline_image(s):
            return ""
        return val
    if isinstance(val, dict):
        return {k: _strip_inline_value(v) for k, v in val.items()}
    if isinstance(val, list):
        return [_strip_inline_value(v) for v in val]
    return val


def strip_all_inline_binary(payload: dict) -> dict:
    """递归移除 JSON 中残留的内联 base64 / data URL（落盘后调用）。"""
    if not isinstance(payload, dict):
        return payload
    return _strip_inline_value(copy.deepcopy(payload))


def find_residual_inline_binary(payload: dict) -> list[str]:
    """返回仍含内联二进制的 JSON 路径（调试用，应为空）。"""
    paths: list[str] = []
    for path, val in _iter_payload_value_paths(payload):
        if not isinstance(val, str):
            continue
        s = val.strip()
        if not s or _is_stored_media_path(s):
            continue
        if _extract_image_bytes_from_string(s):
            paths.append(".".join(str(p) for p in path))
    return paths


def strip_inline_signatures(payload: dict) -> dict:
    """兼容旧调用名：递归剥离全 JSON 内联二进制。"""
    return strip_all_inline_binary(payload)


def prepare_submit_payload_for_storage(
    payload: dict,
    *,
    strip_signatures: bool = True,
) -> tuple[dict, list[dict], list[dict]]:
    """
    提交落库用：提取 sectionPhotos、dynamicData、signatures 中的二进制，
    返回 (精简后的 payload, sectionPhotos 待落盘, 其它二进制待落盘)。
 落盘后须调用 apply_extracted_asset_urls 写回相对 URL。
    """
    storage_payload = normalize_floor_plan_dynamic_data(payload)
    storage_payload, all_pending = extract_all_payload_binary_assets(storage_payload)
    if strip_signatures:
        storage_payload = strip_all_inline_binary(storage_payload)
    residual = find_residual_inline_binary(storage_payload)
    if residual:
        import logging

        logging.getLogger(__name__).warning(
            "submit payload still has inline binary after extraction: %s",
            ", ".join(residual[:20]),
        )
    pending_photos = [item for item in all_pending if item.get("subdir") == "photos"]
    pending_binaries = [item for item in all_pending if item.get("subdir") != "photos"]
    return storage_payload, pending_photos, pending_binaries


def apply_extracted_asset_urls(
    payload: dict,
    pending_items: list[dict],
    created_rows: list[dict],
) -> None:
    """将落盘后的 library_media_url 写回 payload 中 target 路径。"""
    from apps.core.library_file_service import library_media_url

    created_idx = 0
    for item in pending_items:
        if not item.get("raw_bytes"):
            continue
        if created_idx >= len(created_rows):
            break
        row = created_rows[created_idx]
        created_idx += 1
        rel = row.get("relative_path") or ""
        if not rel:
            continue
        url = library_media_url(rel)
        targets = item.get("targets")
        if not isinstance(targets, list) or not targets:
            t = item.get("target")
            targets = [t] if t else []
        for target in targets:
            if not target:
                continue
            obj: Any = payload
            for i, part in enumerate(target):
                if i == len(target) - 1:
                    if isinstance(obj, dict):
                        obj[part] = url
                    elif (
                        isinstance(obj, list)
                        and isinstance(part, int)
                        and 0 <= part < len(obj)
                    ):
                        if isinstance(obj[part], dict):
                            obj[part]["url"] = url
                    break
                if isinstance(obj, dict):
                    obj = obj.get(part)
                elif isinstance(obj, list) and isinstance(part, int):
                    obj = obj[part] if 0 <= part < len(obj) else None
                else:
                    break
