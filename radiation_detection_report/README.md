# 工作场所放射防护检测结果表格

从模板 PDF 提取版式坐标，根据 JSON 数据生成「1.1 工作场所放射防护检测结果」表格 PDF。可整体复制本文件夹到其他项目使用。

## 两套模板：现场记录 vs 检测报告

二者都是「工作场所放射防护检测结果」表格，**版式逻辑相近**（检测点简单/复杂行、本底行、注释行、跨页续表），但用于不同业务阶段：

| 对比项 | **现场记录表**（tabletest） | **检测报告表**（fhtest） |
|--------|---------------------------|--------------------------|
| 文档类型 | JXFS/JS-009 原始记录 | 正式检测报告正文 |
| 章节标题 | 五、工作场所放射防护检测结果 | 1.1工作场所放射防护检测结果 |
| 页面方向 | 横版 A4（841×595） | 竖版 A4（595×842） |
| 表前说明 | (1)仪器信息 + (2)检测条件（表外） | 检测条件（表内首行） |
| 点位编号列 | 序号 | 检测点编号 |
| 结果列 | 3次读数M + 均值M̄ + 报出值D | 检测结果 + 标准要求 + 结果评价 |
| 本底行 | 本底水平（10格①–⑩） | 本底值（单行） |
| 数据阶段 | **现场原始测量**（填读数） | **报告报出**（填判定结果） |

**可复用的核心逻辑**：检测点位置（简单行 / 复杂合并行+子行）、左对齐位置列、跨页重复表头、本底与注释自适应行高、宋体+Times 混排。

| 模板 | 来源 | schema | 版式/模板文件 | PDF 生成器 |
|------|------|--------|---------------|------------|
| **现场记录版** | tabletest.pdf 第五章 p5–7 | `workplace_radiation_js009/v1` | `layout/tabletest_chapter5_full.json` | 待实现 |
| **报告版** | fhtest.pdf 1.1 节 | `radiation_detection_table_layout/v1` | `layout/default_layout.json` | `generator.py` ✅ |

典型工作流：**现场记录表填原始读数 → 计算/判定 → 报告表填报出值与合格评价**。

## 目录结构

```
radiation_detection_report/
├── README.md                 # 本说明（移植指南）
├── requirements.txt          # 依赖：PyMuPDF
├── __init__.py               # Python 包入口
├── generator.py              # 核心：PDF 表格生成（报告版）
├── extract_layout.py         # 提取 fhtest 报告版版式
├── extract_tabletest_layout.py     # 提取 tabletest 现场记录首页版式
├── extract_tabletest_chapter5.py   # 提取 tabletest 第五章全部表格模板
├── sample_data_generator.py      # 随机生成测试数据（报告版，可选）
├── cli.py                    # 统一命令行
├── layout/
│   ├── default_layout.json           # 报告版版式（fhtest）
│   ├── tabletest_layout.json         # 现场记录首页版式
│   └── tabletest_chapter5_full.json  # 现场记录第五章完整模板（p5–7）
├── examples/
│   └── sample_data.json      # 示例数据
└── fonts/                    # 字体（移植时须一并复制）
    ├── SIMSUN.TTC            # 宋体
    ├── TIMES.TTF             # Times New Roman
    └── SEGUISYM.TTF          # 符号（≤、μ 等）
```

## 依赖

```bash
pip install -r requirements.txt
```

需要 **PyMuPDF**（`import fitz`）。Windows 下使用本目录 `fonts/` 中的字体；若无字体文件则回退系统字体。

## 快速开始

在**本目录的上一级**执行（或把本目录加入 `PYTHONPATH`）：

```bash
# 1. 生成 PDF（使用默认版式 + 示例数据）
python -m radiation_detection_report.generator \
  --data radiation_detection_report/examples/sample_data.json \
  --output out.pdf

# 2. 统一 CLI
python radiation_detection_report/cli.py generate \
  --data radiation_detection_report/examples/sample_data.json \
  --output out.pdf

# 3. 从自己的模板 PDF 重新提取版式（报告版）
python radiation_detection_report/cli.py extract-layout \
  --input your_template.pdf --page 6 \
  --output radiation_detection_report/layout/default_layout.json

# 4. 提取 tabletest 第五章全部表格（第5–7页，至第六章前）
python radiation_detection_report/cli.py extract-chapter5 \
  --input tabletest.pdf \
  --output radiation_detection_report/layout/tabletest_chapter5_full.json \
  --compact

# 5. 仅提取第五章首页版式坐标
python radiation_detection_report/cli.py extract-tabletest-layout \
  --input tabletest.pdf \
  --output radiation_detection_report/layout/tabletest_layout.json
```

### tabletest 原始记录版（第五章）版式摘要

- **页面**：横版，第 5 页起（自动搜索「五、工作场所放射防护检测结果」）
- **章节标题**：y≈60，仅首页
- **前置块**（仅首页）：(1) 仪器信息 y≈74；(2) 检测条件 y≈89
- **表格** y_top≈101.6，x≈31–811
- **列宽（x0→x1）**：

| 列 | x0 | x1 |
|----|-----|-----|
| 序号 | 31.1 | 65.0 |
| 位置-主（复杂） | 65.0 | 147.8 |
| 位置-子 | 147.8 | 232.0 |
| 读数1 | 232.0 | 328.4 |
| 读数2 | 328.4 | 444.9 |
| 读数3 | 444.9 | 552.4 |
| 测量均值M̄ | 552.4 | 668.5 |
| 报出值D | 668.5 | 810.8 |

- **行高**：表头≈20.7pt，简单行≈18.9pt，复杂子行≈19.1pt
- **末页**：本底水平行（10 个读数位 ①–⑩）+ 表下注释说明

## 数据 JSON 格式（`data.json`）

```json
{
  "condition": "检测条件：CT 螺旋扫描，120kV，200mA……",
  "points": [
    {
      "type": "simple",
      "id": "1",
      "location": "工作人员操作位",
      "result": "0.15",
      "standard": "≤2.5",
      "evaluation": "合格"
    },
    {
      "type": "complex",
      "location": "铅玻璃观察窗C外表面30cm",
      "sub_rows": [
        { "id": "3", "location_sub": "中部", "result": "0.15", "standard": "≤2.5", "evaluation": "合格" },
        { "id": "4", "location_sub": "上端", "result": "0.16", "standard": "≤2.5", "evaluation": "合格" }
      ]
    }
  ],
  "background": { "label": "本底值（μSv/h）", "value": "0.14～0.15" },
  "notes": [
    "注：1.上表中检测结果未扣除本底值……",
    "2.检测点下方无建筑物……"
  ]
}
```

### 字段说明

| 字段 | 说明 |
|------|------|
| `condition` | 检测条件，仅**首页**表格上方第一行，高度随文本自适应 |
| `points` | 检测点列表，按顺序绘制 |
| `points[].type` | `simple` 单行点位；`complex` 复杂点位（左合并名 + 右多子行） |
| `points[].id` | 简单点位编号；复杂点位编号写在每个 `sub_rows[].id` |
| `points[].location` | 简单点位：整列「检测点位置」；复杂点位：左列主名，**距离接在名称末尾**（如 `铅玻璃观察窗C外表面30cm`） |
| `location_name` + `location_distance` | 可选，等价于 `location` 拼接 |
| `sub_rows[].location_sub` | 复杂点位右侧子位置：中部、上端等 |
| `background` | 本底值行（可选） |
| `notes` | 注释字符串数组，合并为注释行，**五号字**，高度自适应 |

### 复杂点位编号规则

复杂点位按 **子行数量** 占用编号。例如子行 id 为 3、4、5，则占 3 个编号；整表可连续编号 1～N。

## 版式 JSON 格式（`layout.json`）

由 `extract_layout.py` 从模板 PDF 某一页提取，或手工微调。核心字段：

| 路径 | 含义 |
|------|------|
| `section_title` | 章节标题「1.1工作场所放射防护检测结果」坐标（仅首页绘制一次） |
| `table.x_left` / `table.x_right` | 表格左右边界 |
| `table.columns` | 各列 x0/x1：point_id、location_main、location_sub、result、standard、evaluation |
| `table.row_heights` | 参考行高：condition、header、simple、complex_sub |
| `pagination.content_bottom` | 每页表格最大下边界 y |
| `pagination.footer_margin_below_table` | 满页时表格底到页底空白（模板约 92pt） |
| `pagination.continuation_y_top` | 换页后续表起始 y（无章节标题） |
| `pagination.repeat_header_on_new_page` | 换页是否重复表头 |

**注意**：`location_main` 右边界须为 **198.45** 左右（复杂点位左列宽度），勿与简单行合并列宽（326）混淆；生成器内已做纠正。

## 排版规则（生成逻辑摘要）

| 项目 | 规则 |
|------|------|
| 字体 | 汉字 `fonts/SIMSUN.TTC`；英文数字 `fonts/TIMES.TTF`；符号 `SEGUISYM.TTF` |
| 字号 | 表格正文/表头/检测条件：**小四 12pt**；注释：**五号 10.5pt** |
| 检测点位置列 | **左对齐**（表头、简单行、复杂左列、子行位置） |
| 其他列 | 水平居中 |
| 单元格文字 | 垂直居中；条件行、注释行高度**按文本折行自适应** |
| 换页 | 超过 `content_bottom` 换新页；**不重复**章节标题；**重复**表头五行 |
| 复杂点位 | 左列合并显示 `location`；每子行独立编号与结果列 |

## 移植到其他应用（Python API）

```python
from radiation_detection_report import (
    generate_table_pdf,
    load_layout,
    load_report_data,
    format_complex_location,
    default_layout_path,
)

# 方式 A：文件路径
generate_table_pdf(
    layout_path=default_layout_path(),
    data_path="my_report.json",
    output_pdf="report_table.pdf",
)

# 方式 B：内存对象
from radiation_detection_report import TableLayout, RadiationTableBuilder

layout = load_layout(default_layout_path())
data = {
    "condition": "检测条件：……",
    "points": [{"type": "simple", "id": "1", "location": "工作人员操作位", ...}],
}
RadiationTableBuilder(layout, data).save("out.pdf")

# 复杂点位名称
loc = format_complex_location("铅玻璃观察窗C外表面", "30cm")
# => "铅玻璃观察窗C外表面30cm"
```

### 移植检查清单

1. 复制整个 `radiation_detection_report/` 目录（**含 `fonts/`**）。
2. 安装 `pymupdf`。
3. 准备 `layout.json`（可用自带 `layout/default_layout.json` 或从贵司模板 PDF 提取）。
4. 业务系统输出符合上述结构的 `data` JSON。
5. 调用 `generate_table_pdf` 或嵌入 `RadiationTableBuilder.build()` 返回的 `fitz.Document` 再合并进总报告 PDF。

### 与主 PDF 合并示例

```python
import fitz
from radiation_detection_report import generate_table_pdf, load_layout, load_report_data

# 生成表格页
table_doc = fitz.open()
# ... 或 generate 到临时文件再打开
from radiation_detection_report import RadiationTableBuilder, load_layout

layout = load_layout("layout/default_layout.json")
builder = RadiationTableBuilder(layout, data)
table_doc = builder.build()

# 合并到主文档
main = fitz.open("full_report.pdf")
main.insert_pdf(table_doc, start_at=len(main))
main.save("full_report_merged.pdf")
```

## 模块说明

| 模块 | 职责 |
|------|------|
| `generator.py` | 表格绘制、分页、字体、行高计算 |
| `extract_layout.py` | `page.find_tables()` + 标题词坐标 → layout JSON |
| `sample_data_generator.py` | 批量构造测试用 `points`（可选） |
| `cli.py` | `generate` / `extract-layout` / `sample-data` 子命令 |

## 常见问题

**Q: 表格下方空白很大？**  
满页时与模板一致约 92pt 页脚区；若当页未排满就换页，会额外出现「未用绘图区」空白，属分页策略，非版式错误。

**Q: 如何更换模板样式？**  
对新区模板 PDF 执行 `extract-layout`，检查 `location_main.x1≈198.45`，再替换 `layout/default_layout.json`。

**Q: 中文挤在一起？**  
须使用包内 `fonts/` 且通过 `fitz.Font(fontfile=...)` 测宽；勿仅用 `get_text_length` 配合嵌入字体名。

---

参考模板：`fhtest.pdf` 第 6 页（页眉「第 4 页」）。版式提取命令默认 `--page 6`。
