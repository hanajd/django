"""检测提交 JSON：将内嵌 base64 图片提取为待落盘条目，并精简 JSON。"""
from __future__ import annotations

import base64
import copy
import re

_DATA_URL_RE = re.compile(
    r"^data:image/(?P<fmt>jpeg|jpg|png|webp);base64,(?P<data>.+)$",
    re.IGNORECASE | re.DOTALL,
)

_SIGNATURE_KEYS = frozenset(
    {
        "inspector",
        "checker",
        "accompanyingPerson",
        "author",
        "reviewer",
        "approver",
        "mainInspector",
        "检测员",
        "校核",
        "校核员及校核日期",
        "受检单位陪同人",
        "f76",
        "f77",
        "f78",
    }
)


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


def _safe_photo_filename(section_id: str, captured_at: str, idx: int, ext: str) -> str:
    sid = re.sub(r"[^\w\-.]+", "_", (section_id or "section").strip())[:48] or "section"
    cap = re.sub(r"[^0-9T]", "", captured_at or "")[:20] or "unknown"
    return f"{sid}_{cap}_{idx:02d}.{ext}"


def extract_section_photo_assets(payload: dict) -> tuple[dict, list[dict]]:
    """
    深拷贝 payload，移除 sectionPhotos[].dataUrl，返回待落盘照片列表。
    每项含 filename、raw_bytes、photo_meta（原条目字段，不含 dataUrl）。
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
        data_url = item.pop("dataUrl", None) or item.pop("data_url", None)
        if not data_url:
            new_photos.append(item)
            continue
        try:
            raw, ext = decode_image_data_url(data_url)
        except (ValueError, base64.binascii.Error):
            item["dataUrl"] = data_url
            new_photos.append(item)
            continue
        filename = _safe_photo_filename(
            str(item.get("sectionId") or ""),
            str(item.get("capturedAt") or ""),
            idx,
            ext,
        )
        pending.append(
            {
                "filename": filename,
                "raw_bytes": raw,
                "photo_meta": item,
            }
        )
        item["url"] = ""
        new_photos.append(item)

    out["sectionPhotos"] = new_photos
    return out, pending


def strip_inline_signatures(payload: dict) -> dict:
    """签名单独落盘为 PNG，JSON 内去掉大段 base64。"""
    out = copy.deepcopy(payload)
    signatures = out.get("signatures")
    if not isinstance(signatures, dict):
        return out
    cleaned = dict(signatures)
    for key, value in list(cleaned.items()):
        if key not in _SIGNATURE_KEYS:
            continue
        if isinstance(value, str) and len(value) > 120:
            cleaned[key] = ""
    out["signatures"] = cleaned
    return out


def prepare_submit_payload_for_storage(
    payload: dict,
    *,
    strip_signatures: bool = True,
) -> tuple[dict, list[dict]]:
    """提交落库用：提取 sectionPhotos 二进制，可选去掉 signatures 内联 base64。"""
    storage_payload, pending_photos = extract_section_photo_assets(payload)
    if strip_signatures:
        storage_payload = strip_inline_signatures(storage_payload)
    return storage_payload, pending_photos
