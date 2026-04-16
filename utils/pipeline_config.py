"""
管线目录与模型相关全局配置（可由 set_pipeline_directories / Django 注入覆盖）。
默认路径与 tablet_backend.settings 中 FILE_LIBRARY_* 布局一致（未调用 set_pipeline_directories 时）。
"""
import os
from pathlib import Path

_BASE = Path(__file__).resolve().parent.parent
_FILE_LIBRARY = _BASE / "media" / "file_library"
_TEMP = _FILE_LIBRARY / "temp"

UPLOAD_FOLDER = str(_FILE_LIBRARY / "uploads")
MINERU_MD_FOLDER = str(_TEMP / "mineru_md")
OUTPUT_JSON_FOLDER = str(_FILE_LIBRARY / "json")
STATIC_FOLDER = str(_TEMP / "static")
TEMP_PDF_FOLDER = str(_TEMP / "temp_pdf")
BATCH_ARCHIVE = str(_TEMP / "batches")
PREVIEW_IMG = str(_TEMP / "preview_images")

MODEL_NAME = os.environ.get("EQUIPMENT_MODEL_NAME", "qwen3:14b-q4_K_M")
MAX_RETRIES = 3
SINGLE_DEVICE_STRUCT = {
    "设备名称": "",
    "设备型号": "",
    "设备编号": "",
    "生产厂家": "",
    "额定参数": {},
}
ALLOWED_EXTENSIONS = {"pdf", "jpg", "jpeg", "png"}


def set_pipeline_directories(
    upload_folder=None,
    mineru_md_folder=None,
    output_json_folder=None,
    static_folder=None,
    temp_pdf_folder=None,
    batch_archive=None,
    preview_img=None,
):
    global UPLOAD_FOLDER, MINERU_MD_FOLDER, OUTPUT_JSON_FOLDER, STATIC_FOLDER, TEMP_PDF_FOLDER, BATCH_ARCHIVE, PREVIEW_IMG
    if upload_folder is not None:
        UPLOAD_FOLDER = str(upload_folder)
    if mineru_md_folder is not None:
        MINERU_MD_FOLDER = str(mineru_md_folder)
    if output_json_folder is not None:
        OUTPUT_JSON_FOLDER = str(output_json_folder)
    if static_folder is not None:
        STATIC_FOLDER = str(static_folder)
    if temp_pdf_folder is not None:
        TEMP_PDF_FOLDER = str(temp_pdf_folder)
    if batch_archive is not None:
        BATCH_ARCHIVE = str(batch_archive)
    if preview_img is not None:
        PREVIEW_IMG = str(preview_img)
    for folder in (
        UPLOAD_FOLDER,
        MINERU_MD_FOLDER,
        OUTPUT_JSON_FOLDER,
        STATIC_FOLDER,
        TEMP_PDF_FOLDER,
        BATCH_ARCHIVE,
        PREVIEW_IMG,
    ):
        os.makedirs(folder, exist_ok=True)
