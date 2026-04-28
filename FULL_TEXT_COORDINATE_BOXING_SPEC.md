# 全量文本坐标框选方案（Word/PDF）  

> 目标：基于“前缀提取”中用到的坐标命中思想，扩展为**对文档全部连续文字自动定位**，输出：  
> 1) `JSON`（文本 + 坐标）  
> 2) 新的可视化 `PDF`（已绘制坐标框）

---

## 1. 你要的最终结果（定义）

给定一个 Word 或 PDF，系统自动完成：

1. 识别文档中的所有文字对象；
2. 将“连续文字”聚合成文本块（不是单字碎片）；
3. 为每个文本块计算坐标框（`x,y,w,h,page`）；
4. 保存为结构化 JSON；
5. 生成一个新 PDF，在原文档对应位置绘制坐标框与编号。

---

## 2. 复用“前缀提取”能力的核心思路

“前缀提取”本质上是：

- 有文本层坐标；
- 用几何规则做命中；
- 按阅读顺序重组文本。

扩展到“全量框选”时，只是把“手动框选一个区域”改为“自动遍历全页并聚类成连续文本块”。

---

## 3. 总体处理流程（端到端）

## Step A：文档标准化输入

- 若输入是 `PDF`：直接提取文本层。
- 若输入是 `Word(docx)`：先转换为 PDF，再走同一管线（推荐）。

> 推荐 Word -> PDF 的原因：最终坐标与视觉排版一致，表格/分页更稳定。

## Step B：提取基础 token（词/字）

每页提取最小文本单元（建议 word 级）：

- `text`
- `x0,y0,x1,y1`
- `page`
- `line_hint`（可选）
- `block_hint`（可选）

## Step C：连续文字聚合（关键）

将 token 自动聚为“连续文字块”，规则建议：

1. 同页；
2. 同行（`|cy1-cy2| <= 行容差`）优先；
3. 横向间距小于阈值时合并；
4. 发生明显换行时，按段落策略决定是否继续合并；
5. 表格单元格建议按网格分隔，避免跨列误并。

## Step D：生成文本块 bbox

对每个聚合块计算外接矩形：

- `x = min(x0)`
- `y = min(y0)`
- `w = max(x1)-min(x0)`
- `h = max(y1)-min(y0)`

并保留：

- `text`（按阅读顺序拼接）
- `tokens`（成员 token 列表）
- `order`（页内顺序）

## Step E：输出 JSON

将所有页面文本块写入统一 JSON（见第 5 节）。

## Step F：生成带框 PDF

在原 PDF 上覆盖绘制：

- 矩形框；
- 编号；
- 可选：半透明背景。

导出 `*_boxed.pdf`。

---

## 4. “连续文字”聚合策略（建议实现）

这是成败关键。推荐两阶段：

## 4.1 行内聚合（horizontal merge）

按 `page + cy` 分桶（带容差），同桶按 `x` 排序，逐个合并：

- gap = 下一个 token 的 `x0 - 当前 token 的 x1`
- 若 `gap <= gap_threshold`（例如 3~8 px，随字号动态）则合并

## 4.2 跨行聚合（paragraph merge，可选）

若满足以下条件可合并为段落块：

- 两行左边界接近；
- 行间距接近常规行距；
- 无明显分栏/表格边界；
- 末尾非强断句符（可选策略）。

> 如果你更偏向“每行都框出来”，可以关闭跨行聚合。

---

## 5. JSON 输出规范（建议）

```json
{
  "schema": "text_boxing/v1",
  "source": {
    "file_name": "example.pdf",
    "file_type": "pdf",
    "page_count": 2
  },
  "pages": [
    {
      "page": 1,
      "width": 595.28,
      "height": 841.89,
      "blocks": [
        {
          "id": "p1_b1",
          "order": 1,
          "text": "这是一个连续文本块",
          "bbox": { "x": 120.5, "y": 230.2, "w": 180.3, "h": 16.4 },
          "token_count": 6,
          "tokens": [
            { "text": "这是", "x0": 120.5, "y0": 230.2, "x1": 144.2, "y1": 246.6 },
            { "text": "一个", "x0": 146.0, "y0": 230.2, "x1": 168.6, "y1": 246.6 }
          ]
        }
      ]
    }
  ]
}
```

### 说明

- `bbox` 建议统一采用 PDF 原始坐标系；
- `tokens` 可选保留，便于审计与回溯；
- `order` 用于稳定重现阅读顺序。

---

## 6. 生成“带坐标框新 PDF”的方案

推荐库：`PyMuPDF`（fitz）

处理方式：

1. 打开原 PDF；
2. 遍历 JSON 中每个 `block`；
3. 在对应页画矩形框；
4. 在框左上角绘制 `block id` 或序号；
5. 保存为新文件（如 `xxx_boxed.pdf`）。

颜色规范（新增）：

- 普通连续文本块：红色框（`RGB: 1,0,0`）
- **表格单元格文本块：绿色框（`RGB: 0,0.6,0`）**

实现建议：

1. 在分块阶段给 block 增加类型标识：`block_type`
2. 典型取值：
   - `text`：普通文本
   - `table_cell`：表格单元格
3. 绘框时按 `block_type` 选择颜色。

示例（概念代码）：

```python
import fitz

def draw_boxes(input_pdf, output_pdf, pages_json):
    doc = fitz.open(input_pdf)
    for p in pages_json:
        page_no = p["page"] - 1
        page = doc[page_no]
        for b in p["blocks"]:
            x = b["bbox"]["x"]
            y = b["bbox"]["y"]
            w = b["bbox"]["w"]
            h = b["bbox"]["h"]
            rect = fitz.Rect(x, y, x + w, y + h)
            block_type = b.get("block_type", "text")
            color = (0, 0.6, 0) if block_type == "table_cell" else (1, 0, 0)
            page.draw_rect(rect, color=color, width=0.8)
            page.insert_text((x, max(0, y - 2)), b["id"], fontsize=6, color=(1, 0, 0))
    doc.save(output_pdf)
```

> 如需标签颜色也一致，可将 `insert_text` 的 `color` 同步改为 `color`。

---

## 7. Word 输入的推荐链路

## 7.1 推荐链路（高精度）

`docx -> pdf -> 提取token -> 聚合block -> json + boxed.pdf`

优点：

- 排版和坐标一致；
- 代码复用 PDF 主链路。

## 7.2 直接解析 docx（不推荐做第一版）

直接从 OOXML 估算坐标误差较大，尤其在：

- 自动换行；
- 表格跨单元；
- 字体替换；
- 页边距/分页符。

---

## 8. 参数建议（可配置）

- `line_tolerance_px`: 同行判定容差（默认 2~4）
- `row_tolerance_px`: 行命中容差（默认 8~12）
- `gap_threshold_px`: 行内合并阈值（默认 3~8）
- `merge_paragraph`: 是否跨行合并（默认 false）
- `draw_label`: 是否绘制编号（默认 true）
- `draw_fill_alpha`: 是否画半透明底色（默认 0.08）

---

## 9. 质量校验（建议）

至少做三类验收：

1. **完整性**：文本总字符数覆盖率（块拼接后 vs 原提取）>= 98%
2. **定位准确**：抽样 100 个块，框内文本准确率 >= 95%
3. **稳定性**：同一文档重复运行，块数量与顺序一致（允许极少浮动）

---

## 10. 最小可交付版本（MVP）

第一版建议只做：

1. 仅 PDF 输入；
2. word 先手工转 pdf；
3. 行内聚合，不做跨行；
4. 输出 `json + boxed.pdf`。

这样最快拿到稳定效果，后续再扩展到自动 Word 转换和段落级聚合。

---

## 11. 一句话总结

你要的功能可以直接落地为一条统一流水线：  
**“文档转标准 PDF -> 提取 token 坐标 -> 连续文字聚合成 block -> 导出 JSON -> 回写绘框 PDF”**。  
这与现有前缀定位是同一几何命中思想，只是从“单区域命中”升级成“全页自动分块命中”。

