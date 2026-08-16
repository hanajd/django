# -*- coding: utf-8 -*-
"""项目内 PyMuPDF 统一字库目录。

所有 HTML 转 PDF、报告填表、合并、防护表、F1 评价等共用 ``<项目根>/fonts/``。
中文默认：
  - 宋体 → 思源宋体 ``SourceHanSerifSC-VF.ttf``（可变字体，按字重实例化为 Regular/Bold）
  - 黑体 → 思源黑体 ``SourceHanSansSC-VF.ttf``（同上）

历史路径 ``htmlpdf/fonts``、``radiation_detection_report/fonts`` 应为指向本目录的软链。
"""
from __future__ import annotations

import hashlib
import threading
from pathlib import Path
from typing import Iterable, Literal, Optional

Family = Literal["serif", "sans"]

# 用户提供的思源可变字体（简体）
SOURCE_HAN_SERIF_VF = "SourceHanSerifSC-VF.ttf"
SOURCE_HAN_SANS_VF = "SourceHanSansSC-VF.ttf"

WEIGHT_REGULAR = 400
WEIGHT_BOLD = 700

_VF_BY_FAMILY: dict[str, str] = {
    "serif": SOURCE_HAN_SERIF_VF,
    "sans": SOURCE_HAN_SANS_VF,
}

_LOCK = threading.Lock()
_MEMO: dict[tuple[str, int], Path] = {}


def project_root() -> Path:
    # utils/pymupdf_fonts.py → 项目根
    return Path(__file__).resolve().parent.parent


def project_fonts_dir() -> Path:
    """统一字库目录：``<BASE_DIR>/fonts``。"""
    return project_root() / "fonts"


def ensure_project_fonts_dir() -> Path:
    root = project_fonts_dir()
    root.mkdir(parents=True, exist_ok=True)
    return root


def fonts_cache_dir() -> Path:
    d = project_fonts_dir() / "cache"
    d.mkdir(parents=True, exist_ok=True)
    return d


def resolve_font_file(*candidates: str, extra_dirs: Iterable[Path | str] | None = None) -> Optional[Path]:
    """按候选文件名在统一字库（及可选附加目录）中查找第一个存在的文件。"""
    roots: list[Path] = [project_fonts_dir()]
    for d in extra_dirs or ():
        p = Path(d)
        if p not in roots:
            roots.append(p)
    for root in roots:
        for name in candidates:
            n = (name or "").strip()
            if not n:
                continue
            path = root / n
            if path.is_file():
                return path
    return None


def _vf_source_path(family: Family) -> Optional[Path]:
    name = _VF_BY_FAMILY.get(family) or ""
    return resolve_font_file(name)


def _instance_cache_path(family: Family, weight: int, vf: Path) -> Path:
    # 文件名含源文件哈希，源更新后自动重建；v2：修正实例化后仍残留 ExtraLight 的 name 表
    digest = hashlib.sha1(
        f"v2:{vf.name}:{vf.stat().st_size}:{int(vf.stat().st_mtime)}".encode()
    ).hexdigest()[:10]
    label = "Bold" if int(weight) >= 600 else "Regular"
    fam = "Serif" if family == "serif" else "Sans"
    return fonts_cache_dir() / f"SourceHan{fam}SC-{label}-w{int(weight)}-{digest}.ttf"


def _rewrite_instance_name_table(font, *, family: Family, weight: int) -> None:
    """实例化后把 PostScript/完整名从 VF-ExtraLight 改为 Regular/Bold，便于 PDF 嵌入与选中。"""
    try:
        from fontTools.ttLib import TTFont
    except Exception:
        return
    del TTFont  # type only
    is_bold = int(weight) >= 600
    style = "Bold" if is_bold else "Regular"
    fam_en = "Source Han Serif SC" if family == "serif" else "Source Han Sans SC"
    fam_zh = "思源宋体" if family == "serif" else "思源黑体"
    ps = f"SourceHan{'Serif' if family == 'serif' else 'Sans'}SC-{style}"
    full_en = f"{fam_en} {style}"
    full_zh = f"{fam_zh} {style}"
    try:
        name = font["name"]
    except Exception:
        return
    updates = {
        1: (fam_en, fam_zh),
        2: (style, style),
        4: (full_en, full_zh),
        6: (ps, None),
        16: (fam_en, fam_zh),
        17: (style, style),
    }
    for rec in list(name.names):
        if rec.nameID not in updates:
            continue
        en, zh = updates[rec.nameID]
        try:
            if rec.platformID == 3 and rec.langID == 0x409 and en:
                rec.string = en
            elif rec.platformID == 3 and rec.langID == 0x804 and zh:
                rec.string = zh
            elif rec.platformID == 1 and en and rec.nameID != 6:
                rec.string = en.encode("latin-1", errors="replace")
            elif rec.nameID == 6 and en:
                # PostScript 名须为 ASCII
                rec.string = en if isinstance(rec.string, str) else en.encode("latin-1")
        except Exception:
            continue
    # nameID 3 Unique
    try:
        for rec in name.names:
            if rec.nameID == 3 and rec.platformID == 3 and rec.langID == 0x409:
                rec.string = f"1.000;ADBO;{ps}"
    except Exception:
        pass


def ensure_cjk_font_instance(family: Family = "serif", *, weight: int = WEIGHT_REGULAR) -> Optional[Path]:
    """
    将思源可变字体实例化为指定字重的静态 TTF（缓存到 fonts/cache/）。
    PyMuPDF 对 VF 默认轴（常为 ExtraLight）支持不佳，必须实例化后再嵌入。
    """
    w = int(weight)
    key = (family, w)
    cached = _MEMO.get(key)
    if cached is not None and cached.is_file():
        return cached

    with _LOCK:
        cached = _MEMO.get(key)
        if cached is not None and cached.is_file():
            return cached
        vf = _vf_source_path(family)
        if vf is None:
            return None
        out = _instance_cache_path(family, w, vf)
        if out.is_file():
            _MEMO[key] = out
            return out
        try:
            from fontTools.ttLib import TTFont
            from fontTools.varLib.instancer import instantiateVariableFont
        except Exception:
            # 无 fontTools 时退回原始 VF（显示会偏细）
            _MEMO[key] = vf
            return vf

        font = TTFont(str(vf), recalcBBoxes=False, recalcTimestamp=False)
        # 仅保留 wght；其他轴若存在则冻结为默认
        coords: dict[str, float] = {"wght": float(w)}
        try:
            fvar = font["fvar"]
            for axis in fvar.axes:
                tag = axis.axisTag
                if tag == "wght":
                    continue
                coords[tag] = float(axis.defaultValue)
        except Exception:
            pass
        inst = instantiateVariableFont(font, coords, inplace=False)
        _rewrite_instance_name_table(inst, family=family, weight=w)
        tmp = out.with_suffix(".tmp.ttf")
        inst.save(str(tmp))
        tmp.replace(out)
        _MEMO[key] = out
        return out


def song_font_path(*, bold: bool = False) -> Optional[Path]:
    """宋体：优先思源宋体 Regular/Bold，其次旧 SIMSUN。"""
    inst = ensure_cjk_font_instance("serif", weight=WEIGHT_BOLD if bold else WEIGHT_REGULAR)
    if inst is not None:
        return inst
    return resolve_font_file("SIMSUN.TTC", "simsun.ttc", "SIMSUN.TTF", "simsun.ttf")


def hei_font_path(*, bold: bool = False) -> Optional[Path]:
    """黑体：优先思源黑体 Regular/Bold，其次 SIMHEI / 文泉驿。"""
    # 标题场景常把「黑体」当强调：默认给 Regular，bold=True 用 700
    inst = ensure_cjk_font_instance("sans", weight=WEIGHT_BOLD if bold else WEIGHT_REGULAR)
    if inst is not None:
        return inst
    return resolve_font_file(
        "SIMHEI.TTF",
        "simhei.ttf",
        "wqy-zenhei.ttc",
        "WQY-ZENHEI.TTC",
    )


def warmup_cjk_font_cache() -> list[Path]:
    """预生成 Regular/Bold 缓存（部署或调试页可调用）。"""
    out: list[Path] = []
    for fam in ("serif", "sans"):
        for w in (WEIGHT_REGULAR, WEIGHT_BOLD):
            p = ensure_cjk_font_instance(fam, weight=w)  # type: ignore[arg-type]
            if p is not None:
                out.append(p)
    return out
