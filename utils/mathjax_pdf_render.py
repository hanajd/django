"""Render MathJax / TeX equations to PNG for PDF stamping."""
from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

_BASE = Path(__file__).resolve().parents[1]
_MATHJAX_SCRIPT = _BASE / "scripts" / "mathjax_tex2svg.mjs"

_TEX_WRAP_RE = re.compile(r"^\$\$(.*)\$\$$", re.DOTALL)
_TEX_INLINE_RE = re.compile(r"^\$(.*)\$$", re.DOTALL)


def extract_tex_from_submit(value: Any) -> str:
    """Strip $$ / $ wrappers and normalize unicode from tablet MathJax payloads."""
    if value is None or isinstance(value, bool):
        return ""
    s = str(value).strip()
    if not s:
        return ""
    m = _TEX_WRAP_RE.match(s)
    if m:
        s = m.group(1).strip()
    else:
        m = _TEX_INLINE_RE.match(s)
        if m and s.count("$") == 2:
            s = m.group(1).strip()
    s = (
        s.replace("\u00a0", " ")
        .replace("\u202f", " ")
        .replace("\u2007", " ")
        .replace("\u00ad", "-")
        .replace("\u2212", "-")
        .replace("\u2013", "-")
        .replace("\u2014", "-")
    )
    s = re.sub(r"[ \t]+", " ", s).strip()
    return s


def looks_like_mathjax_equation(value: Any, field: Optional[Mapping[str, Any]] = None) -> bool:
    """True when content should be MathJax-stamped instead of plain text."""
    if isinstance(field, Mapping):
        disp = str(field.get("displayFormat") or "").strip().lower()
        if not disp:
            src = field.get("source")
            if isinstance(src, Mapping):
                disp = str(src.get("displayFormat") or "").strip().lower()
        if disp == "latex":
            return bool(extract_tex_from_submit(value) or str(value or "").strip())
    s = str(value or "").strip()
    if not s:
        return False
    if s.startswith("$$") and s.endswith("$$") and len(s) >= 4:
        return True
    if "\\ln" in s or "\\rm" in s or "\\mathrm" in s or "^{" in s:
        return True
    return False


def _rgb_tuple_to_hex(color: Sequence[float] | None) -> str:
    if not color or len(color) < 3:
        return "#e53935"
    try:
        r, g, b = [max(0, min(255, int(round(float(c) * 255)))) for c in color[:3]]
        return f"#{r:02x}{g:02x}{b:02x}"
    except (TypeError, ValueError):
        return "#e53935"


@lru_cache(maxsize=1)
def _resolve_node() -> str:
    return shutil.which("node") or ""


def _mathjax_svg(tex: str, *, color_hex: str = "#e53935", display: bool = True) -> bytes:
    node = _resolve_node()
    if not node or not _MATHJAX_SCRIPT.is_file():
        raise RuntimeError("mathjax script or node unavailable")
    payload = json.dumps(
        {"tex": tex, "display": bool(display), "color": color_hex},
        ensure_ascii=False,
    )
    proc = subprocess.run(
        [node, str(_MATHJAX_SCRIPT)],
        input=payload.encode("utf-8"),
        capture_output=True,
        timeout=30,
        check=False,
    )
    if proc.returncode != 0 or not proc.stdout.strip():
        err = (proc.stderr or b"").decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"mathjax render failed: {err or proc.returncode}")
    return proc.stdout


def _svg_to_png_bytes(svg_bytes: bytes, *, dpi: float = 220.0) -> bytes:
    import fitz

    doc = fitz.open("svg", svg_bytes)
    try:
        page = doc[0]
        # scale for crisp stamp
        zoom = max(1.0, float(dpi) / 72.0)
        mat = fitz.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=mat, alpha=True)
        return pix.tobytes("png")
    finally:
        doc.close()


def _matplotlib_png(
    tex: str,
    *,
    color: Sequence[float] | None = None,
    max_width_pt: float = 200.0,
    max_height_pt: float = 40.0,
) -> bytes:
    """Fallback when Node/MathJax unavailable: mathtext PNG."""
    import io

    import matplotlib

    matplotlib.use("Agg")
    from matplotlib import pyplot as plt

    rgb = color if color and len(color) >= 3 else (0.9, 0.22, 0.21)
    # mathtext prefers $...$
    expr = tex.strip()
    if not (expr.startswith("$") and expr.endswith("$")):
        expr = f"${expr}$"
    w_in = max(0.8, min(6.0, float(max_width_pt) / 72.0))
    h_in = max(0.35, min(2.0, float(max_height_pt) / 72.0))
    fig = plt.figure(figsize=(w_in, h_in), dpi=220)
    fig.patch.set_alpha(0.0)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_axis_off()
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.text(
        0.5,
        0.5,
        expr,
        color=rgb,
        fontsize=14,
        ha="center",
        va="center",
        usetex=False,
    )
    buf = io.BytesIO()
    fig.savefig(buf, format="png", transparent=True, bbox_inches="tight", pad_inches=0.02)
    plt.close(fig)
    return buf.getvalue()


def render_equation_png(
    value: Any,
    *,
    field: Optional[Mapping[str, Any]] = None,
    color: Sequence[float] | None = None,
    max_width_pt: float = 200.0,
    max_height_pt: float = 48.0,
) -> Optional[bytes]:
    """
    Render submit equation (optionally ``$$...$$``) to PNG bytes.
    Prefer MathJax (Node); fall back to matplotlib mathtext.
    """
    tex = extract_tex_from_submit(value)
    if not tex:
        return None
    color_hex = _rgb_tuple_to_hex(color)
    try:
        svg = _mathjax_svg(tex, color_hex=color_hex, display=True)
        return _svg_to_png_bytes(svg)
    except Exception as exc:
        logger.warning("MathJax render failed, fallback to matplotlib: %s", exc)
        try:
            return _matplotlib_png(
                tex,
                color=color,
                max_width_pt=max_width_pt,
                max_height_pt=max_height_pt,
            )
        except Exception as exc2:
            logger.warning("matplotlib equation render failed: %s", exc2)
            return None


def stamp_equation_on_page(
    page,
    rect,
    value: Any,
    *,
    field: Optional[Mapping[str, Any]] = None,
    color: Sequence[float] | None = None,
) -> bool:
    """Insert rendered equation image into ``rect``. Returns True on success."""
    import fitz

    if rect is None:
        return False
    r = fitz.Rect(rect)
    if r.width <= 1 or r.height <= 1:
        return False
    png = render_equation_png(
        value,
        field=field,
        color=color,
        max_width_pt=float(r.width),
        max_height_pt=float(r.height),
    )
    if not png:
        return False
    try:
        page.insert_image(r, stream=png, keep_proportion=True, overlay=True)
        return True
    except Exception as exc:
        logger.warning("equation insert_image failed: %s", exc)
        return False
