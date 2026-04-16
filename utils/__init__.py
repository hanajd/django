"""
Tablet 后台文档与模型处理工具集。

后续新增的后台文件处理逻辑请放在本包内（例如新模块或子包）。
"""
from utils.pipeline_config import (
    ALLOWED_EXTENSIONS,
    MAX_RETRIES,
    MODEL_NAME,
    OUTPUT_JSON_FOLDER,
    PREVIEW_IMG,
    SINGLE_DEVICE_STRUCT,
    STATIC_FOLDER,
    set_pipeline_directories,
)
from utils.document_pipeline import process_all_files
from utils.ollama_extract import _normalize_device_item

__all__ = [
    "ALLOWED_EXTENSIONS",
    "MAX_RETRIES",
    "MODEL_NAME",
    "OUTPUT_JSON_FOLDER",
    "PREVIEW_IMG",
    "SINGLE_DEVICE_STRUCT",
    "STATIC_FOLDER",
    "set_pipeline_directories",
    "process_all_files",
    "_normalize_device_item",
]
