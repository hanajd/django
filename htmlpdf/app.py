import io
import os
import json
import re
import base64
import fitz
from flask import Flask, render_template, request, jsonify, send_file

# ========== 基础配置 ==========
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_ROOT = os.path.join(BASE_DIR, "pdf_templates")
os.makedirs(TEMPLATE_ROOT, exist_ok=True)

app = Flask(__name__,
            template_folder=os.path.join(BASE_DIR, "templates"),
            static_folder=os.path.join(BASE_DIR, "static"))

FONTS_DIR = os.path.join(BASE_DIR, "fonts")
SIMSUN_FONT = os.path.join(FONTS_DIR, "SIMSUN.TTC")
TIMES_FONT_CANDIDATES = [
    os.path.join(FONTS_DIR, "times.ttf"),
    os.path.join(FONTS_DIR, "Times New Roman.ttf"),
    r"C:\Windows\Fonts\times.ttf",
    r"C:\Windows\Fonts\timesbd.ttf"
]
CHECK_FONT_CANDIDATES = [
    os.path.join(FONTS_DIR, "SEGUISYM.TTF"),
    os.path.join(FONTS_DIR, "seguisym.ttf"),
    os.path.join(FONTS_DIR, "SegoeUISymbol.ttf"),
    r"C:\Windows\Fonts\seguisym.ttf",
    r"C:\Windows\Fonts\segoeui.ttf",
    SIMSUN_FONT
]
DEFAULT_FONT_PT = 10.5  # 五号
MIN_TEXT_FIELD_WIDTH = DEFAULT_FONT_PT + 2
MIN_TEXT_FIELD_HEIGHT = (DEFAULT_FONT_PT * 1.2) + 2


def _coerce_rect(raw_rect):
    if raw_rect is None:
        return None
    if isinstance(raw_rect, fitz.Rect):
        return raw_rect
    if hasattr(raw_rect, "bbox"):
        box = getattr(raw_rect, "bbox")
        if box and len(box) >= 4:
            return fitz.Rect(float(box[0]), float(box[1]), float(box[2]), float(box[3]))
    if isinstance(raw_rect, (list, tuple)) and len(raw_rect) >= 4:
        return fitz.Rect(float(raw_rect[0]), float(raw_rect[1]), float(raw_rect[2]), float(raw_rect[3]))
    return None


def _extract_table_cells(page: fitz.Page) -> list[fitz.Rect]:
    cells: list[fitz.Rect] = []
    if hasattr(page, "find_tables"):
        try:
            table_finder = page.find_tables()
        except Exception:
            table_finder = None
        tables = getattr(table_finder, "tables", None) or []
        for table in tables:
            rows = getattr(table, "rows", None)
            if rows:
                for row in rows:
                    for cell in (getattr(row, "cells", None) or []):
                        rect = _coerce_rect(cell)
                        if rect is not None and not rect.is_empty:
                            cells.append(rect)
                continue
            for cell in (getattr(table, "cells", None) or []):
                rect = _coerce_rect(cell)
                if rect is not None and not rect.is_empty:
                    cells.append(rect)

    # 补充矢量矩形，兼容部分 find_tables 漏检场景
    try:
        drawings = page.get_drawings()
    except Exception:
        drawings = []
    for d in drawings:
        if d.get("type") not in ("s", "fs", "sf"):
            continue
        rect = _coerce_rect(d.get("rect"))
        if rect is None or rect.is_empty:
            continue
        w, h = float(rect.width), float(rect.height)
        if w < 18 or h < 10:
            continue
        if w > page.rect.width * 0.95 and h > page.rect.height * 0.95:
            continue
        cells.append(rect)
    return cells


def _pick_cell_at_point(page: fitz.Page, page_x: float, page_y: float):
    point = fitz.Point(float(page_x), float(page_y))
    containing = [r for r in _extract_table_cells(page) if r.contains(point)]
    if not containing:
        return None
    # 同一点命中多个框时优先最小框，更接近具体单元格
    containing.sort(key=lambda r: max(0.0, r.width * r.height))
    return containing[0]


def pick_times_font():
    for path in TIMES_FONT_CANDIDATES:
        if os.path.exists(path):
            return path
    return None


def pick_check_font():
    for path in CHECK_FONT_CANDIDATES:
        if os.path.exists(path):
            return path
    return None


def contains_cjk(text: str) -> bool:
    return re.search(r"[\u3400-\u9FFF]", text or "") is not None


def wrap_text_to_width(text: str, max_width: float, font: fitz.Font, font_size: float) -> str:
    lines = []
    for para in (text or "").splitlines() or [""]:
        current = ""
        for ch in para:
            trial = current + ch
            if font.text_length(trial, fontsize=font_size) <= max_width:
                current = trial
            else:
                if current:
                    lines.append(current)
                    current = ch
                else:
                    lines.append(ch)
                    current = ""
        lines.append(current)
    return "\n".join(lines)


def fit_text_for_box(text: str, rect: fitz.Rect, font: fitz.Font) -> tuple[str, float]:
    max_fs = min(10.5, rect.height * 0.85)
    min_fs = 5.0
    fs = max(max_fs, min_fs)
    while fs >= min_fs:
        wrapped = wrap_text_to_width(text, max(1.0, rect.width - 2), font, fs)
        line_count = max(1, wrapped.count("\n") + 1)
        line_height = fs * 1.2
        if line_count * line_height <= max(1.0, rect.height - 1):
            return wrapped, fs
        fs -= 0.5
    return wrap_text_to_width(text, max(1.0, rect.width - 2), font, min_fs), min_fs


def ensure_min_text_rect(rect: fitz.Rect) -> fitz.Rect:
    width = rect.width
    height = rect.height
    if width >= MIN_TEXT_FIELD_WIDTH and height >= MIN_TEXT_FIELD_HEIGHT:
        return rect
    cx = (rect.x0 + rect.x1) / 2.0
    cy = (rect.y0 + rect.y1) / 2.0
    half_w = max(width, MIN_TEXT_FIELD_WIDTH) / 2.0
    half_h = max(height, MIN_TEXT_FIELD_HEIGHT) / 2.0
    return fitz.Rect(cx - half_w, cy - half_h, cx + half_w, cy + half_h)


def safe_inset_rect(rect: fitz.Rect, pad: float) -> fitz.Rect:
    if rect.width <= pad * 2 + 0.5 or rect.height <= pad * 2 + 0.5:
        return rect
    return fitz.Rect(rect.x0 + pad, rect.y0 + pad, rect.x1 - pad, rect.y1 - pad)


# ========== 前端页面 ==========
@app.route("/")
def index():
    return render_template("index.html")

# ========== 1. 上传PDF（临时保存） ==========
@app.post("/api/upload-pdf")
def upload_pdf():
    if "pdf" not in request.files:
        return jsonify({"error": "请选择PDF"}), 400
    file = request.files["pdf"]
    if not (file.filename or "").lower().endswith(".pdf"):
        return jsonify({"error": "仅支持PDF"}), 400

    temp_dir = os.path.join(TEMPLATE_ROOT, "_temp")
    os.makedirs(temp_dir, exist_ok=True)
    pdf_path = os.path.join(temp_dir, "original.pdf")
    file.save(pdf_path)
    rel_path = pdf_path.replace(BASE_DIR, "").replace("\\", "/")
    return jsonify({"path": rel_path})

# ========== 2. 导出模板/内容JSON ==========
@app.post("/api/export-json")
def export_json():
    data = request.get_json() or {}
    fields = data.get("fields", [])
    content = data.get("content", {})
    export_type = (data.get("type") or "merged").strip().lower()
    file_stem = (data.get("name") or "template").strip() or "template"

    if export_type == "fields":
        payload = fields
        filename = f"{file_stem}.fields.json"
    elif export_type == "content":
        payload = content
        filename = f"{file_stem}.content.json"
    else:
        # 合并格式：把内容内嵌到每个字段条目中
        merged_fields = []
        for field in fields:
            item = dict(field)
            fid = item.get("id")
            if "content" not in item and fid in content:
                item["content"] = content.get(fid, "")
            merged_fields.append(item)
        payload = {"fields": merged_fields}
        filename = f"{file_stem}.json"

    out = io.BytesIO()
    out.write(json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"))
    out.seek(0)
    return send_file(out, mimetype="application/json", as_attachment=True, download_name=filename)


# ========== 3. 导入模板/内容JSON ==========
@app.post("/api/import-json")
def import_json():
    if "jsonFile" not in request.files:
        return jsonify({"error": "请选择JSON文件"}), 400

    file = request.files["jsonFile"]
    try:
        raw = file.read().decode("utf-8")
        data = json.loads(raw)
    except Exception:
        return jsonify({"error": "JSON文件格式无效"}), 400

    if isinstance(data, list):
        # 兼容旧版 fields.json
        return jsonify({"fields": data, "content": {}})
    if isinstance(data, dict):
        # 兼容旧版 content.json
        if "fields" not in data and "content" not in data:
            return jsonify({"fields": [], "content": data})
        fields = data.get("fields", [])
        content_map = data.get("content", {})
        # 兼容新版：content内嵌在fields[]里
        if not content_map and isinstance(fields, list):
            for item in fields:
                if isinstance(item, dict) and "id" in item and "content" in item:
                    content_map[item["id"]] = item.get("content", "")
        return jsonify({
            "fields": fields,
            "content": content_map
        })
    return jsonify({"error": "JSON结构不支持"}), 400

# ========== 4. 导出填写完成的PDF ==========
@app.post("/api/save-pdf")
def save_pdf():
    data = request.get_json()
    fields = data.get("fields", [])
    pdf_path = os.path.join(TEMPLATE_ROOT, "_temp", "original.pdf")
    if not pdf_path or not os.path.exists(pdf_path):
        return jsonify({"error": "请先上传或加载PDF"}), 400

    doc = fitz.open(pdf_path)
    times_font_path = pick_times_font()
    check_font_path = pick_check_font()
    if not os.path.exists(SIMSUN_FONT):
        doc.close()
        return jsonify({"error": f"未找到宋体字体文件：{SIMSUN_FONT}"}), 500
    if not check_font_path:
        doc.close()
        return jsonify({"error": "未找到可用的对勾符号字体文件"}), 500

    for f in fields:
        page = doc[int(f["page"]) - 1]
        field_type = (f.get("fieldType") or "text").lower()
        raw_rect = fitz.Rect(f["x0"], f["y0"], f["x1"], f["y1"])
        if field_type == "text":
            raw_rect = ensure_min_text_rect(raw_rect)
        r = safe_inset_rect(raw_rect, 0.6)

        if field_type == "check":
            checked = bool(f.get("checked")) or str(f.get("value", "")).strip() in ("1", "true", "True", "yes", "on", "✓")
            if checked:
                page.insert_font(fontname="F_CHECK", fontfile=check_font_path)
                remain = page.insert_textbox(
                    r, "✓", fontname="F_CHECK", fontsize=10.5,
                    color=(0, 0, 0), align=fitz.TEXT_ALIGN_CENTER, overlay=True
                )
                # 某些PDF在textbox布局下可能仍不落字，回退为中心点直写
                if remain < 0:
                    cx = (r.x0 + r.x1) / 2.0
                    cy = (r.y0 + r.y1) / 2.0
                    pt = fitz.Point(cx - 3.2, cy + 3.2)
                    page.insert_text(
                        pt, "✓", fontname="F_CHECK", fontsize=10.5,
                        color=(0, 0, 0), overlay=True
                    )
            continue

        if field_type == "image":
            image_data = (f.get("imageData") or "").strip()
            if not image_data:
                continue
            try:
                if image_data.startswith("data:"):
                    image_data = image_data.split(",", 1)[1]
                image_bytes = base64.b64decode(image_data)
                page.insert_image(r, stream=image_bytes, keep_proportion=True, overlay=True)
            except Exception:
                continue
            continue

        text = str(f.get("value", "")).strip()
        if not text:
            continue

        align = fitz.TEXT_ALIGN_CENTER
        # 中文字段优先宋体，纯英文数字优先 Times New Roman（若可用）
        use_simsun = contains_cjk(text) or not times_font_path
        font_name = "F_SIMSUN" if use_simsun else "F_TIMES"
        font_file = SIMSUN_FONT if use_simsun else times_font_path
        page.insert_font(fontname=font_name, fontfile=font_file)
        font_obj = fitz.Font(fontfile=font_file)
        wrapped_text, fs = fit_text_for_box(text, r, font_obj)
        if "\n" in wrapped_text:
            align = fitz.TEXT_ALIGN_LEFT

        line_count = max(1, wrapped_text.count("\n") + 1)
        text_h = line_count * fs * 1.2
        if text_h < r.height:
            offset_y = (r.height - text_h) / 2.0
            text_rect = fitz.Rect(r.x0, r.y0 + offset_y, r.x1, r.y1)
        else:
            text_rect = r

        remain = page.insert_textbox(
            text_rect, wrapped_text, fontname=font_name, fontsize=fs,
            color=(0, 0, 0), align=align, overlay=True
        )
        # 极小框在 textbox 可能直接落字失败，回退为中心点直写
        if remain < 0:
            flat_text = wrapped_text.replace("\n", " ").strip()
            if flat_text:
                shown = flat_text[: max(1, min(len(flat_text), 32))]
                text_w = font_obj.text_length(shown, fontsize=fs)
                cx = (text_rect.x0 + text_rect.x1) / 2.0
                cy = (text_rect.y0 + text_rect.y1) / 2.0
                pt = fitz.Point(cx - (text_w / 2.0), cy + (fs * 0.35))
                page.insert_text(
                    pt, shown, fontname=font_name, fontsize=fs,
                    color=(0, 0, 0), overlay=True
                )

    out = io.BytesIO()
    doc.save(out, clean=True, garbage=3)
    doc.close()
    out.seek(0)
    return send_file(out, mimetype="application/pdf", as_attachment=True, download_name="填写完成.pdf")


@app.post("/api/table-cell-at-point")
def table_cell_at_point():
    data = request.get_json() or {}
    page_no = int(data.get("page") or 0)
    x = float(data.get("x") or 0.0)
    y = float(data.get("y") or 0.0)
    pdf_path = os.path.join(TEMPLATE_ROOT, "_temp", "original.pdf")
    if not os.path.exists(pdf_path):
        return jsonify({"error": "请先上传或加载PDF"}), 400
    if page_no <= 0:
        return jsonify({"error": "页码无效"}), 400

    doc = fitz.open(pdf_path)
    try:
        if page_no > len(doc):
            return jsonify({"error": "页码超出范围"}), 400
        page = doc[page_no - 1]
        rect = _pick_cell_at_point(page, x, y)
        if rect is None:
            return jsonify({"found": False, "cell": None})
        return jsonify(
            {
                "found": True,
                "cell": {
                    "x": float(rect.x0),
                    "y": float(rect.y0),
                    "w": float(rect.width),
                    "h": float(rect.height),
                },
            }
        )
    finally:
        doc.close()

# 允许访问模板目录下的PDF
@app.route("/pdf_templates/<path:filename>")
def serve_template_pdf(filename):
    return send_file(os.path.join(TEMPLATE_ROOT, filename))

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)