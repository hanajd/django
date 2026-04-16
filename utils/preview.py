import logging
import os
from typing import List

from pdf2image import convert_from_path

logger = logging.getLogger(__name__)


def pdf_to_preview_image(pdf_path: str, save_dir: str, prefix: str) -> List[str]:
    if not os.path.exists(pdf_path):
        return []
    try:
        pages = convert_from_path(pdf_path, dpi=150)
        paths = []
        for i, page in enumerate(pages):
            out = os.path.join(save_dir, f"{prefix}_{i}.png")
            page.save(out, "PNG")
            paths.append(f"/static/preview/{os.path.basename(out)}")
        return paths
    except Exception as e:
        logger.error("PDF转图片失败: %s", e)
        return []
