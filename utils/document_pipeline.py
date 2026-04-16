"""
文件 → PDF → 合并 PDF → MinerU → 清洗 Markdown → Ollama → 设备 JSON 结构。
"""
import logging
import os
from typing import Any, Dict, List, Tuple

from PIL import Image

from utils import pipeline_config
from utils.md_clean import clean_md_content
from utils.mineru_ops import (
    convert_pdf_to_md,
    find_mineru_extracted_images,
    find_mineru_layout_pdf,
    read_md_file,
)
from utils.ollama_extract import _normalize_device_item, extract_devices_with_ollama
from utils.pdf_merge import image_to_pdf, merge_pdfs
from utils.preview import pdf_to_preview_image

logger = logging.getLogger(__name__)


def save_original_file(batch_id: str, filename: str, content: bytes) -> str:
    batch_dir = os.path.join(pipeline_config.BATCH_ARCHIVE, batch_id, "original")
    os.makedirs(batch_dir, exist_ok=True)
    path = os.path.join(batch_dir, filename)
    with open(path, "wb") as f:
        f.write(content)
    return path


def process_all_files(
    file_payloads: List[Tuple[str, bytes]], batch_id: str
) -> Tuple[List[Dict[str, Any]], Dict[str, Any], dict]:
    pipeline = {
        "input_files": len(file_payloads),
        "pdf_parts_built": 0,
        "merge_ok": False,
        "mineru_md_found": False,
        "text_chars": 0,
        "error": "",
    }
    image_data = {
        "original_images": [],
        "layout_images": [],
        "mineru_extracted_images": [],
    }

    temp_pdfs = []

    for i, (filename, raw) in enumerate(file_payloads):
        if not raw:
            continue
        if "." not in filename:
            continue
        ext = filename.rsplit(".", 1)[-1].lower()
        if ext not in pipeline_config.ALLOWED_EXTENSIONS:
            continue

        original_path = save_original_file(batch_id, filename, raw)

        stem = os.path.join(pipeline_config.TEMP_PDF_FOLDER, f"{batch_id}_{i}")
        src = f"{stem}.{ext}"
        out_pdf = f"{stem}.pdf"

        try:
            if ext in ("jpg", "jpeg", "png"):
                with open(src, "wb") as f:
                    f.write(raw)
                if image_to_pdf(src, out_pdf):
                    temp_pdfs.append(out_pdf)
                    img_url = f"/static/preview/{os.path.basename(original_path)}.png"
                    with Image.open(src) as img:
                        img.thumbnail((800, 1000))
                        img.save(
                            os.path.join(
                                pipeline_config.PREVIEW_IMG,
                                f"{os.path.basename(original_path)}.png",
                            ),
                            "PNG",
                        )
                    image_data["original_images"].append(
                        {"name": filename, "url": img_url, "path": original_path}
                    )
                os.remove(src)
            elif ext == "pdf":
                with open(out_pdf, "wb") as f:
                    f.write(raw)
                temp_pdfs.append(out_pdf)
                preview_urls = pdf_to_preview_image(
                    out_pdf, pipeline_config.PREVIEW_IMG, f"orig_{batch_id}_{i}"
                )
                for url in preview_urls:
                    image_data["original_images"].append(
                        {"name": filename, "url": url, "path": original_path}
                    )
        except Exception as e:
            logger.error("处理原始文件失败: %s", e)

    pipeline["pdf_parts_built"] = len(temp_pdfs)

    batch_dir = os.path.join(pipeline_config.BATCH_ARCHIVE, batch_id)
    merged_pdf = os.path.join(batch_dir, "merged.pdf")
    mineru_dir = os.path.join(batch_dir, "mineru_output")
    os.makedirs(batch_dir, exist_ok=True)
    os.makedirs(mineru_dir, exist_ok=True)

    try:
        if not temp_pdfs:
            pipeline["error"] = "无有效PDF生成"
            return [_normalize_device_item({})], pipeline, image_data

        merge_ok, merge_msg = merge_pdfs(temp_pdfs, merged_pdf)
        if not merge_ok:
            pipeline["error"] = f"合并失败: {merge_msg}"
            return [_normalize_device_item({})], pipeline, image_data
        pipeline["merge_ok"] = True

        md_path = convert_pdf_to_md(merged_pdf, mineru_dir)
        pipeline["mineru_md_found"] = bool(md_path)

        layout_pdf = find_mineru_layout_pdf(mineru_dir)
        if layout_pdf:
            layout_urls = pdf_to_preview_image(
                layout_pdf, pipeline_config.PREVIEW_IMG, f"layout_{batch_id}"
            )
            image_data["layout_images"] = [
                {"name": f"染色识别图_{i}", "url": u} for i, u in enumerate(layout_urls)
            ]

        mineru_extract_imgs = find_mineru_extracted_images(mineru_dir)
        for img_idx, img_src_path in enumerate(mineru_extract_imgs):
            try:
                preview_img_name = f"mineru_extract_{batch_id}_{img_idx}.png"
                preview_img_path = os.path.join(pipeline_config.PREVIEW_IMG, preview_img_name)

                with Image.open(img_src_path) as img:
                    img.thumbnail((800, 1000))
                    img.save(preview_img_path, "PNG")

                img_url = f"/static/preview/{preview_img_name}"
                image_data["mineru_extracted_images"].append(
                    {
                        "name": f"MinerU提取图_{img_idx}",
                        "url": img_url,
                        "path": img_src_path,
                    }
                )
                logger.info("生成提取图片预览: %s", preview_img_path)
            except Exception as e:
                logger.error("处理提取图片失败 %s: %s", img_src_path, e)

        if not md_path:
            pipeline["error"] = "MinerU未生成MD"
            return [_normalize_device_item({})], pipeline, image_data

        # md = clean_md_content(read_md_file(md_path))  # 清洗md内容
        md = read_md_file(md_path)  # 读取md文件
        pipeline["text_chars"] = len(md)
        if not md:
            pipeline["error"] = "文本为空"
            return [_normalize_device_item({})], pipeline, image_data

        devices = extract_devices_with_ollama(md, len(file_payloads))
        return devices, pipeline, image_data

    finally:
        pass
