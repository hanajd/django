import logging
import os
import shutil
import subprocess
from typing import List, Tuple

try:
    from pypdf import PdfWriter as _PdfWriter
    from pypdf import PdfReader as _PdfReader
except ImportError:
    _PdfWriter = None
    _PdfReader = None
from PyPDF2 import PdfMerger
from PIL import Image

logger = logging.getLogger(__name__)


def image_to_pdf(image_path: str, output_pdf_path: str) -> bool:
    try:
        with Image.open(image_path) as img:
            img = img.convert("RGB")
            img.save(output_pdf_path, "PDF", resolution=100.0, save_all=True)
        return os.path.exists(output_pdf_path) and os.path.getsize(output_pdf_path) > 0
    except Exception as e:
        logger.error("图片转PDF失败: %s", e)
        return False


def _merge_pdfs_ghostscript(pdf_paths: List[str], output_path: str) -> Tuple[bool, str]:
    gs_bin = shutil.which("gs")
    if not gs_bin:
        return False, ""
    out_abs = os.path.abspath(output_path)
    ins = [os.path.abspath(p) for p in pdf_paths if os.path.isfile(p)]
    if not ins:
        return False, ""
    try:
        cmd = [
            gs_bin,
            "-q",
            "-dNOPAUSE",
            "-dBATCH",
            "-sDEVICE=pdfwrite",
            "-dPDFSETTINGS=/default",
            f"-sOutputFile={out_abs}",
        ] + ins
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
        )
        if proc.returncode == 0 and os.path.isfile(out_abs) and os.path.getsize(out_abs) > 0:
            return True, ""
        return False, f"Ghostscript错误: {proc.stderr[:800]}"
    except Exception as e:
        return False, str(e)


def merge_pdfs(pdf_paths: List[str], output_path: str) -> Tuple[bool, str]:
    valid = [p for p in pdf_paths if os.path.isfile(p) and os.path.getsize(p) > 0]
    if not valid:
        return False, "无有效PDF"
    out_dir = os.path.dirname(os.path.abspath(output_path))
    os.makedirs(out_dir, exist_ok=True)

    if _PdfWriter and _PdfReader:
        try:
            w = _PdfWriter()
            page_count = 0
            for p in valid:
                try:
                    r = _PdfReader(p, strict=False)
                    for page in r.pages:
                        w.add_page(page)
                        page_count += 1
                except Exception:
                    pass
            if page_count > 0:
                with open(output_path, "wb") as f:
                    w.write(f)
                return True, ""
        except Exception:
            pass

    try:
        merger = PdfMerger()
        for p in valid:
            merger.append(p)
        with open(output_path, "wb") as f:
            merger.write(f)
        merger.close()
        return True, ""
    except Exception:
        pass

    ok_gs, msg_gs = _merge_pdfs_ghostscript(valid, output_path)
    if ok_gs:
        return True, "使用Ghostscript合并"
    return False, "合并失败"
