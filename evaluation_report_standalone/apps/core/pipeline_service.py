"""Minimal path helpers for evaluation_report (no OCR pipeline)."""

from __future__ import annotations

from pathlib import Path

from django.conf import settings


def library_absolute_path(relative_path: str) -> Path:
    rel = Path(relative_path)
    if rel.is_absolute():
        raise ValueError("relative_path must be relative")
    base = Path(settings.FILE_LIBRARY_ROOT).resolve()
    full = (base / rel).resolve()
    full.relative_to(base)
    return full


def ensure_file_library_dirs() -> None:
    for key in (
        "FILE_LIBRARY_ROOT",
        "FILE_LIBRARY_EVALUATION_FORM_DIR",
        "FILE_LIBRARY_ATTACHMENT_DIR",
    ):
        path = getattr(settings, key, None)
        if path:
            Path(path).mkdir(parents=True, exist_ok=True)
