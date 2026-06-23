# 工作场所放射防护检测结果：PDF 提取与表格生成技术文档

> 本文档描述从 **JXFS/JS-009 现场记录 PDF**（如 `js-009.pdf`）提取第五章「工作场所放射防护检测结果」，并分别生成 **现场记录表 PDF**（横版）与 **报告表 PDF**（竖版）的完整实现逻辑。  
> 目标读者：需要从零还原或嵌入 Django 的程序员。  
> 代码参考目录：`radiation_detection/`（包名在代码中写作 `radiation_detection_report`，见 §1.2）。

---

## 1. 总览

### 1.1 业务流程

```
js-009.pdf（已填写的现场记录）
        │
        ▼
extract_js009_chapter5()          ← extract_js009_chapter5.py
        │
        ├─► field_record_data     ← 现场记录 JSON（含三次读数、均值、报出值）
        │         │
        │         ▼
        │   FieldRecordTableBuilder + js009_layout.json
        │         │
        │         ▼
        │   field_record.pdf      （横版 A4，还原原始记录样式）
        │
        └─► report_data           ← 报告 JSON（仅报出值 + 标准 + 评价）
                  │
                  ▼
            RadiationTableBuilder + default_layout.json
                  │
                  ▼
            report_table.pdf      （竖版 A4，检测报告正文 1.1 节样式）
```

### 1.2 涉及文件（最小可运行集）

| 文件 | 职责 |
|------|------|
| `extract_js009_chapter5.py` | 从 PDF 提取第五章表格数据 |
| `report_evaluation.py` | 报出值与标准比较，判定合格/不合格 |
| `generator.py` | **报告表** PDF 绘制（竖版） |
| `field_record_generator.py` | **现场记录表** PDF 绘制（横版） |
| `md_rich_text.py` | `**加粗**` 文本解析（generator 依赖，报告表可选特性） |
| `layout/default_layout.json` | 报告表版式坐标 |
| `layout/js009_layout.json` | js-009 现场记录表版式坐标 |
| `layout/tabletest_layout.json` | 通用横版版式（备用，field_record 默认路径） |
| `examples/js009_*.json` | 提取结果示例 |

**依赖**：`pymupdf >= 1.23.0`（`import fitz`）

**字体**：`generator.py` 优先加载 `fonts/SIMSUN.TTC`、`TIMES.TTF`；若无则回退 Windows 系统字体。Linux 部署需安装中文字体。

**包名说明**：当前 `radiation_detection/` 内 Python 文件的 import 路径为 `radiation_detection_report.*`。移植时统一文件夹名与 import 即可。

---

## 2. PDF 提取：`extract_js009_chapter5.py`

### 2.1 入口函数

```python
def extract_js009_chapter5(pdf_path: str) -> Dict[str, Any]:
    ...
```

返回结构：

```json
{
  "schema": "js009_chapter5_extract/v1",
  "source_pdf": "js-009.pdf",
  "chapter_range": { "start_page": 5, "end_page": 7 },
  "preface": {
    "instrument_info": "137Cs 校准因子k： …",
    "condition": "检测条件：1.09曝光模式（部位：0.9）…"
  },
  "points": [ /* 分组后的检测点，见 §3.1 */ ],
  "background": { "label": "…", "slots": ["…"], "range": "19.435~164.047" },
  "notes": ["1. …", "2. …"],
  "field_record_data": { /* 见 §3.2 */ },
  "report_data": { /* 见 §3.3 */ },
  "summary": { "raw_rows_kept": 21, "point_groups": 11, "simple": 3, "complex": 8 }
}
```

### 2.2 页范围定位

1. **第五章起始页**：全文搜索 `"五、工作场所放射防护"`，取 1-based 页码。
2. **第六章起始页**：全文搜索 `"六、平面布局"`，取 1-based 页码。
3. 处理页区间：`[start_page - 1, end_page - 1)`（0-based 索引，不含第六章首页）。

### 2.3 表格识别（跳过 CT 性能表）

同页可能有多张表。用 PyMuPDF `page.find_tables()` 遍历，取**表头行**同时含 `"检测点位置"` 且含 `"序号"` 或 `"测量读数"` 的那张表：

```python
def _is_header_row(row):
    joined = "".join(row)
    return "检测点位置" in joined and ("序号" in joined or "测量读数" in joined)
```

### 2.4 行解析规则

对表格 `matrix = tab.extract()` 逐行处理：

| 行类型 | 识别条件 | 处理 |
|--------|----------|------|
| 表头 | `_is_header_row` | 首页保留定位；续页跳过 |
| 本底行 | 含 `"本底水平"` | 缓存到 `bg_buf`，等待后续行补全 ①–⑩ |
| 注释行 | 以 `1.检测` / `1．检测` 开头，或含 `响应时间修正系数` | 解析为 `notes` 列表 |
| 数据行 | 其他 | 见下方过滤与解析 |

**占位符过滤**（关键）：

```python
def _row_has_slash_placeholder(row):
    for i, cell in enumerate(row):
        if norm(cell) == "/":
            if i == 0:   # 序号列 "/" 表示复杂点位的续行，允许
                continue
            return True  # 任一其他列为 "/" → 丢弃整行
    return False
```

**列映射**：

- 8 列：`序号 | 位置主 | 位置子 | 读数1 | 读数2 | 读数3 | 均值 | 报出值`
- 7 列：`序号 | 位置主 | 读数1 | 读数2 | 读数3 | 均值 | 报出值`（无位置子列）

**位置清洗**：

- `location_main`：去掉行首的数字/点号噪声（如 `35.3 观察窗` → `观察窗`）
- `location_sub`：纯数字（如 `9`）视为无效子位置，置空
- 跳过 `检测项目/检测条件/检测结果/报出值` 等表头残留

### 2.5 检测条件提取（含悬浮红字）

PDF 中检测条件在表外，格式：

```
（2）、检测条件：                 曝光模式（部位：       ），球管朝            照射，          kV      mA         s（       mAs）。
```

填写值以**红色悬浮字**（color=`16711680` = `#FF0000`）叠在空白处，普通 `get_text()` 拿不到。

**算法**：

1. 正则从页面文本取模板：`（2）、(检测条件.*?)(?=序号)`
2. 定位锚点 y：找含 `（2）、检测条件` 或 `曝光模式（部位` 的 span 中心 y（勿用页眉处另一个「检测条件」列名）
3. 收集红色 span：`anchor_y - 8 <= cy <= anchor_y + 3` 且 `x >= 40`
4. 按 `bbox[0]`（x 坐标）排序
5. 用红色值依次替换模板中**连续 2 个以上空格**的空白区（`re.sub(r' {2,}', fill, template)`）
6. 后处理单位间距：`298kV` → `298 kV` 等
7. 输出保留 `检测条件：` 前缀，去掉 `（2）、`

**js-009 实测红字值**（7 个空白位）：`1.09`（曝光模式）、`0.9`（部位）、`1.18`（球管朝向）、`298`（kV）、`0.87`（mA）、`226`（s）、`0.86`（mAs）

### 2.6 本底行解析

本底可能占 1–2 行，含 ①–⑩ 十个读数槽和范围值：

```python
{
  "label": "本底水平（μSv/h）及范围",
  "slots": ["19.435", "164.047", ...],  # 最多 10 个
  "range": "19.435~164.047"
}
```

### 2.7 检测点分组 `_group_points`

将扁平行列表合并为 **simple** / **complex** 两组：

```
规则：
- 若 location_main 非空：
  - 有 location_sub → 新建 complex 组，首条进 sub_rows
  - 无 location_sub → 新建 simple 组
- 若 location_main 为空但有 location_sub → 追加到当前 complex 的 sub_rows
- 遇到新的 location_main 时，先关闭上一组
```

**complex 示例**（观察窗，多子行）：

```
行1: 序号=3~4, main=观察窗C…30cm处, sub=中部
行2: 序号=/,    main=空,           sub=上端
行3: 序号=/,    main=空,           sub=下端
→ 合并为一个 complex，id=3~4，sub_rows=[中部, 上端, 下端]
```

### 2.8 转换为两套输出数据

**现场记录 `field_record_data`**：几乎原样传递 `points`，附加：

```json
{
  "instrument_info": "…",
  "condition": "…",
  "points": [ /* 含 readings/mean_m/report_d */ ],
  "background": { "label": "…", "slots": [], "range": "…" },
  "notes": []
}
```

**报告 `report_data`**：重新编号并裁剪列：

- simple：取 `report_d`（无则 `mean_m`）→ `result`；`standard` 固定 `≤2.5`；`evaluation` 自动计算
- complex：子行**重新连续编号**（1, 2, 3…），组级 `id` 写为 `首~尾`（如 `3~4`）；每组子行各自有 result/standard/evaluation
- `condition` 写入表内首行（报告表专用）
- `background` 简化为 `{ "label": "本底值（μSv/h）", "value": "范围或前两槽拼接" }`

---

## 3. 数据 JSON 结构

### 3.1 中间结构 `points[]`（提取后分组）

**简单点位**：

```json
{
  "type": "simple",
  "id": "1",
  "location": "管线（□地沟 □✓线孔）洞口 处",
  "readings": ["0.2", "0.35", "0.16"],
  "mean_m": "0.24",
  "report_d": "0.31"
}
```

**复杂点位**：

```json
{
  "type": "complex",
  "id": "3~4",
  "location": "观察窗C 35.3 （ ）外表面30cm 处",
  "sub_rows": [
    {
      "location_sub": "中部",
      "readings": ["0.35", "0.33", "0.36"],
      "mean_m": "0.35",
      "report_d": "0.45"
    },
    {
      "location_sub": "上端",
      "readings": ["0.34", "0.31", "0.30"],
      "mean_m": "0.32",
      "report_d": "0.42"
    }
  ]
}
```

### 3.2 现场记录表输入 `field_record_data`

见 `examples/js009_field_record.json`。与 `points` 结构相同，额外有：

- `instrument_info`：表外仪器说明（生成器当前**不绘制**表外块，仅保留在 JSON；横版 PDF 只画表格本体）
- `condition`：同上
- `background.slots`：10 个本底读数
- `background.range`：本底范围，画在报出值列

### 3.3 报告表输入 `report_data`

见 `examples/js009_report.json`：

```json
{
  "condition": "检测条件：1.09曝光模式（部位：0.9）…",
  "points": [
    {
      "type": "simple",
      "id": "1",
      "location": "管线洞口处",
      "result": "0.31",
      "standard": "≤2.5",
      "evaluation": "合格"
    },
    {
      "type": "complex",
      "id": "3~4",
      "location": "观察窗C…30cm 处",
      "sub_rows": [
        { "id": "3", "location_sub": "中部", "result": "0.45", "standard": "≤2.5", "evaluation": "合格" },
        { "id": "4", "location_sub": "上端", "result": "0.42", "standard": "≤2.5", "evaluation": "不合格" }
      ]
    }
  ],
  "background": { "label": "本底值（μSv/h）", "value": "19.435~164.047" },
  "notes": ["1. 上表中检测结果未扣除本底值…"]
}
```

---

## 4. 合格判定：`report_evaluation.py`

```python
DEFAULT_RADIATION_STANDARD = "≤2.5"

def evaluate_result(result, standard="≤2.5") -> str:
    # 解析 result 中第一个数字
    # ≤2.5：value <= 2.5 → "合格"，否则 "不合格"
    # 无法解析数字 → 返回 ""
```

`apply_report_evaluations(data)` 遍历所有 simple/complex 子行，补全 `standard` 与 `evaluation`。

报告 PDF 中 **不合格** 的评价文字用红色绘制（`generator._evaluation_text_color`）。

---

## 5. 现场记录表 PDF 生成：`field_record_generator.py`

### 5.1 版式

- **页面**：横版 A4（841.9 × 595.3 pt）
- **版式文件**：`layout/js009_layout.json`（从 js-009.pdf 实测）或 `tabletest_layout.json`
- **schema**：`workplace_radiation_js009/v1`

### 5.2 表头列（与原始记录一致）

| 列 | 说明 |
|----|------|
| 序号 | 简单行单号；复杂行组号（如 `3~4`），纵向合并 |
| 检测点位置 | 简单行整列；复杂行分主列+子列 |
| 测量读数 M（μSv/h） | 3 次读数，表头合并为一个宽列 |
| 测量均值 M̄ | |
| 报出值 D | |

### 5.3 绘制顺序 `FieldRecordTableBuilder.build()`

```
新页 → 章节标题（仅首页）→ 表头
→ 逐点绘制（simple / complex）
→ 本底行（10 格 ①–⑩ + 范围）
→ 注释行
```

### 5.4 复杂点位合并规则（关键）

与报告表相同：

```
┌────┬──────────┬────────┬─────┬─────┬─────┬────┬────┐
│3~4 │ 观察窗C  │ 中部   │ r1  │ r2  │ r3  │ M̄  │ D  │
│    │ 30cm处   ├────────┼─────┼─────┼─────┼────┼────┤
│    │ (合并)   │ 上端   │ …   │ …   │ …   │ …  │ …  │
└────┴──────────┴────────┴─────┴─────┴─────┴────┴────┘
```

实现要点：

1. `id_rect`、`main_rect` 高度 = 整组 `y0~y1`
2. 子行只画 `location_sub` + 测量列
3. 子行间横线从 `col_location_sub[0]` 画到 `table_x_right`（不切断左侧合并格）
4. 组底横线从 `table_x_left` 画到 `table_x_right`

### 5.5 行高自适应

每行高度 = 该行所有单元格文本换行后的最大高度（`_autofit_cells_height`）。  
复杂组总高 = `max(主位置列所需高, 各子行高之和)`，子行高用 `normalize_band_heights` 缩放对齐。

### 5.6 本底行

- 左半：标签「本底水平（μSv/h）及范围」
- 中间：`BACKGROUND_SLOT_X` 定义的 10 格 x 坐标，两行五列排 ①–⑩
- 右端：`col_report_d` 列画范围值

### 5.7 调用

```python
from radiation_detection_report.field_record_generator import (
    FieldRecordTableBuilder,
    load_field_record_layout,
    generate_field_record_pdf,
)

generate_field_record_pdf(
    "layout/js009_layout.json",
    "examples/js009_field_record.json",
    "field_record.pdf",
)
```

---

## 6. 报告表 PDF 生成：`generator.py`

### 6.1 版式

- **页面**：竖版 A4（595.3 × 841.9 pt）
- **版式文件**：`layout/default_layout.json`（从 fhtest.pdf 第 6 页提取）
- **schema**：`radiation_detection_table_layout/v1`
- **章节标题**：`1.1工作场所放射防护检测结果`（仅首页）

### 6.2 表头列（与现场记录对齐，去掉读数列）

| 列 | 说明 |
|----|------|
| 序号 | 同现场记录 |
| 检测点位置 | 同现场记录 |
| 报出值 D，μSv/h | 对应现场记录的报出值 |
| 标准要求 | 默认 `≤2.5` |
| 结果评价 | 合格 / 不合格（不合格红色） |

### 6.3 与现场记录版的差异

| 项目 | 现场记录表 | 报告表 |
|------|-----------|--------|
| 页面方向 | 横版 | 竖版 |
| 检测条件 | 表外（JSON 保留，生成器不画） | **表内首行**（通栏单元格） |
| 数据列 | 3 次读数 + 均值 + 报出值 | 报出值 + 标准 + 评价 |
| 本底行 | 10 格 + 范围 | 单行「本底值（μSv/h）」+ 范围 |
| 字号 | 五号 10.5pt | 小四 12pt（注释五号） |

### 6.4 绘制顺序 `RadiationTableBuilder.build()`

```
新页 → 章节标题（仅首页）
→ 检测条件行（仅首页，通栏）
→ 表头
→ 逐点（simple / complex，合并规则同 §5.4）
→ 本底行
→ 注释行
```

### 6.5 分页

- `content_bottom`：表格最大下边界（约 750pt）
- 超出则 `_new_page(continuation=True)`：不重复章节标题，**重复表头**
- 续页起始 y：`continuation_y_top`（约 72pt）

### 6.6 版式 JSON 关键字段

```json
{
  "table": {
    "columns": {
      "point_id":       { "x0": 70.73,  "x1": 120.6 },
      "location_main":  { "x0": 120.6,  "x1": 198.45 },
      "location_sub":   { "x0": 198.45, "x1": 326.0 },
      "result":         { "x0": 326.0,  "x1": 410.8 },
      "standard":       { "x0": 410.8,  "x1": 489.1 },
      "evaluation":     { "x0": 489.1,  "x1": 524.57 }
    },
    "complex_location_main_right": 198.45
  },
  "pagination": {
    "content_bottom": 749.88,
    "continuation_y_top": 72.0,
    "repeat_header_on_new_page": true,
    "include_condition_on_first_page_only": true
  }
}
```

**注意**：`location_main.x1` 必须约 **198.45**（复杂点位左列宽），不能与简单行用的 `location_full` 宽（326）混淆。`TableLayout.from_json` 内有纠正逻辑。

### 6.7 调用

```python
from radiation_detection_report.generator import (
    RadiationTableBuilder,
    load_layout,
    load_report_data,
    generate_table_pdf,
)

# load_report_data 会自动 apply_report_evaluations
generate_table_pdf(
    "layout/default_layout.json",
    "examples/js009_report.json",
    "report_table.pdf",
)
```

---

## 7. 共享绘制基础设施（`generator.py` 内）

以下被 `field_record_generator.py` 复用：

| 函数/类 | 作用 |
|---------|------|
| `FontResources` | 宋体 + Times 混排，按字符选字体 |
| `_autofit_row_height` | 按列宽折行计算行高 |
| `_autofit_cells_height` | 多列取最大行高 |
| `normalize_band_heights` | 复杂点子行高度按比例缩放至组总高 |
| `_draw_cell_border` / `_draw_cell_content` | 画框 + 居中/左对齐文本 |
| `resolve_complex_location` | 复杂点位主位置文本 |
| `_draw_table_hline` | 行底横线 |

---

## 8. 命令行用法

在项目根目录（`radiation_detection` 的上一级）：

```powershell
# 提取
python radiation_detection_report/cli.py extract-js009-chapter5 `
  --input js-009.pdf `
  --output radiation_detection/examples/js009_chapter5_data.json `
  --report-data radiation_detection/examples/js009_report.json `
  --field-record radiation_detection/examples/js009_field_record.json

# 生成现场记录表
python radiation_detection_report/cli.py generate-field-record `
  --data radiation_detection/examples/js009_field_record.json `
  --layout radiation_detection/layout/js009_layout.json `
  --output field_record.pdf

# 生成报告表
python radiation_detection_report/cli.py generate `
  --data radiation_detection/examples/js009_report.json `
  --layout radiation_detection/layout/default_layout.json `
  --output report_table.pdf
```

---

## 9. Django 嵌入示例

```python
import io
import tempfile
from django.http import HttpResponse, JsonResponse
from django.views import View

from radiation_detection_report.extract_js009_chapter5 import extract_js009_chapter5
from radiation_detection_report.generator import RadiationTableBuilder, load_layout
from radiation_detection_report.field_record_generator import (
    FieldRecordTableBuilder,
    load_field_record_layout,
)
from radiation_detection_report.report_evaluation import apply_report_evaluations


def extract_from_upload(pdf_bytes: bytes) -> dict:
    fd, path = tempfile.mkstemp(suffix=".pdf")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(pdf_bytes)
        return extract_js009_chapter5(path)
    finally:
        os.unlink(path)


class WorkplaceRadiationReportView(View):
    """上传 js-009 PDF，返回报告表 PDF。"""
    def post(self, request):
        pdf_file = request.FILES.get("pdf")
        if not pdf_file:
            return JsonResponse({"error": "请上传 pdf"}, status=400)
        data = extract_from_upload(pdf_file.read())
        layout = load_layout("radiation_detection_report/layout/default_layout.json")
        report_data = apply_report_evaluations(data["report_data"])
        doc = RadiationTableBuilder(layout, report_data).build()
        buf = io.BytesIO()
        doc.save(buf)
        doc.close()
        resp = HttpResponse(buf.getvalue(), content_type="application/pdf")
        resp["Content-Disposition"] = 'attachment; filename="report_table.pdf"'
        return resp
```

---

## 10. 还原实现检查清单

按此顺序可实现完整功能：

- [ ] 1. 安装 `pymupdf`，确认 `page.find_tables()` 可用
- [ ] 2. 实现页码定位（五章头、六章头）
- [ ] 3. 实现表格筛选（含「检测点位置」表头）
- [ ] 4. 实现行过滤（`/` 占位符规则）
- [ ] 5. 实现 7/8 列行解析
- [ ] 6. 实现 `_group_points` 简单/复杂分组
- [ ] 7. 实现检测条件红字提取（`get_text("dict")` + color=16711680）
- [ ] 8. 实现本底行、注释行解析
- [ ] 9. 实现 `_to_field_record_data` 与 `_to_report_data`
- [ ] 10. 准备 `default_layout.json` 与 `js009_layout.json`
- [ ] 11. 实现 `TableLayout` / `FieldRecordLayout` 数据类
- [ ] 12. 实现字体加载与 `_autofit_*` 行高计算
- [ ] 13. 实现简单行绘制
- [ ] 14. 实现复杂行合并绘制（序号+主位置纵合并，子行分割）
- [ ] 15. 实现分页 + 续页表头
- [ ] 16. 实现本底行、注释行
- [ ] 17. 实现 `report_evaluation` 合格判定与红色不合格
- [ ] 18. 用 `js-009.pdf` 端到端验证

### 10.1 验证要点

| 检查项 | 预期 |
|--------|------|
| 过滤后行数 | js-009 约 21 行、11 个点位组 |
| 检测条件 | 含红字：`检测条件：1.09曝光模式（部位：0.9）…298 kV 0.87 mA 226 s（0.86 mAs）` |
| 复杂点位 | 观察窗、防护门、各墙体等，序号列 `3~4` 形式纵合并 |
| 不合格 | 报出值 > 2.5 的评价为红色「不合格」 |
| 跨页 | 检测点超过一页时表头重复、章节标题不重复 |

---

## 11. 两套表格对照

```
现场记录（横版）                    报告（竖版）
─────────────────────────────────────────────────
五、工作场所放射防护检测结果         1.1工作场所放射防护检测结果
(1) 仪器信息  ← JSON 保留           （不画）
(2) 检测条件  ← JSON 保留           检测条件 ← 表内首行
┌──┬────┬───┬───┬───┬───┬──┬──┐   ┌──┬────┬────┬────┬────┐
│序│位置│读│读│读│均值│报│      │序│位置│报出│标准│评价│
│号│    │1 │2 │3 │    │出│      │号│    │值  │要求│    │
└──┴────┴───┴───┴───┴───┴──┴──┘   └──┴────┴────┴────┴────┘
本底水平 ①–⑩ + 范围                 本底值（单行）
注释                                注释
```

---

## 12. 参考示例文件

| 文件 | 内容 |
|------|------|
| `examples/js009_chapter5_data.json` | 完整提取结果 |
| `examples/js009_field_record.json` | 现场记录表输入 |
| `examples/js009_report.json` | 报告表输入 |
| `layout/default_layout.json` | 报告版式 |
| `layout/js009_layout.json` | js-009 现场记录版式 |

---

*文档版本：与 `radiation_detection` 代码同步，涵盖 js-009 第五章提取、红字检测条件、复杂点位纵合并、合格判定与双 PDF 生成全流程。*
