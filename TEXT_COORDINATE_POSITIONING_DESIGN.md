# 文本坐标定位设计文档（PDF / Word）

> 目标：说明“如何根据坐标定位文本”，并给出一套可扩展到 **PDF / Word 全文定位** 的通用方案。  
> 约束：本文只提供设计与示例代码，不涉及任何现有业务代码改动。

---

## 1. 问题定义

当我们说“根据坐标定位文本”时，通常包含 3 个层次：

1. **坐标提取**：从文档中拿到每段/每词/每字的空间位置（`x,y,w,h,page`）。
2. **坐标索引**：把这些文本与坐标组织成可查询的数据结构。
3. **坐标命中**：给定一个矩形区域（或某个锚点），快速找到区域内文本，并按阅读顺序还原。

---

## 2. 你当前场景里的核心思路（简化）

以 PDF 为例，典型流程是：

1. 渲染页面（用于展示）。
2. 从文本层读取 `items`（每个文本片段有 transform / width / height）。
3. 把 transform 转成统一坐标（真实坐标系，不依赖缩放后的 UI）。
4. 用户框选一个矩形区域（UI 坐标）。
5. 先把 UI 坐标逆变换回真实坐标，再做“矩形相交筛选”。
6. 将命中文本按阅读顺序排序（上到下、左到右）后拼接。

这就是“坐标定位文本”的最小可用模型。

---

## 3. 通用方案设计（支持 Word + PDF）

## 3.1 架构总览

- **Extractor 层**（提取）
  - `PdfExtractor`：抽取 PDF 文本及 bbox。
  - `WordExtractor`：抽取 Word 文本，并重建近似/精准 bbox。
- **Normalizer 层**（标准化）
  - 统一输出 `Token` 结构：`doc_id/page/x/y/w/h/text/block_id/line_id`。
- **Indexer 层**（索引）
  - 按页构建 `R-Tree` 或网格索引（Spatial Index）。
  - 同时保留阅读顺序索引。
- **Locator 层**（定位）
  - `query_rect(page, x0, y0, x1, y1)`：返回区域内 token。
  - `query_anchor(text, strategy)`：按语义锚点反查附近字段。

---

## 3.2 统一数据模型

建议统一一份中间格式（JSON / DB 均可）：

```json
{
  "doc_id": "xxx",
  "source_type": "pdf",
  "pages": [
    {
      "page": 1,
      "width": 595.28,
      "height": 841.89,
      "tokens": [
        {
          "id": "t1",
          "text": "检测仪器",
          "x": 123.4,
          "y": 256.7,
          "w": 48.0,
          "h": 12.0,
          "cx": 147.4,
          "cy": 262.7,
          "line_id": "l8",
          "block_id": "b2",
          "order": 102
        }
      ]
    }
  ]
}
```

### 字段约定

- 坐标统一采用 **左上角原点**（或左下角也可，但必须全链路一致）。
- `order` 为全局阅读顺序序号，用于稳定拼接。
- `line_id/block_id` 用于语义聚合（行、段落、表格单元）。

---

## 4. PDF 文本定位实现方案

## 4.1 提取

推荐库：

- 前端：`pdf.js`（适合浏览器可视化编辑器）
- 后端：`PyMuPDF` / `pdfplumber`（批处理、服务端索引）

### Python 示例（PyMuPDF）

```python
import fitz  # PyMuPDF

def extract_pdf_tokens(path: str):
    doc = fitz.open(path)
    pages = []
    for pno, page in enumerate(doc, start=1):
        words = page.get_text("words")  # x0,y0,x1,y1,"word",block_no,line_no,word_no
        tokens = []
        for idx, w in enumerate(words):
            x0, y0, x1, y1, text, block_no, line_no, word_no = w
            tokens.append({
                "id": f"p{pno}_t{idx}",
                "text": text,
                "x": x0,
                "y": y0,
                "w": x1 - x0,
                "h": y1 - y0,
                "cx": (x0 + x1) / 2,
                "cy": (y0 + y1) / 2,
                "block_id": f"b{block_no}",
                "line_id": f"b{block_no}_l{line_no}",
                "order": idx
            })
        pages.append({"page": pno, "tokens": tokens, "width": page.rect.width, "height": page.rect.height})
    return pages
```

## 4.2 区域命中

核心是矩形相交：

```python
def intersects(a, b):
    # a,b: (x0,y0,x1,y1)
    return not (a[2] < b[0] or a[0] > b[2] or a[3] < b[1] or a[1] > b[3])
```

### 命中并按阅读顺序拼接

```python
def query_text_in_rect(tokens, rect):
    x0, y0, x1, y1 = rect
    hits = []
    for t in tokens:
        tb = (t["x"], t["y"], t["x"] + t["w"], t["y"] + t["h"])
        if intersects(tb, rect):
            hits.append(t)
    # 先行后列（可加行容差）
    hits.sort(key=lambda t: (round(t["cy"], 1), t["cx"]))
    return "".join(t["text"] for t in hits)
```

---

## 5. Word 文本定位实现方案

Word 比 PDF 难点在于：`docx` 是流式排版，不天然提供“最终渲染坐标”。

你可以选两种路线：

## 路线 A（推荐，精度高）：转 PDF 后统一走 PDF 引擎

1. `docx -> pdf`（LibreOffice / Aspose / Office 服务）。
2. 对输出 PDF 做第 4 节的提取与定位。

优点：

- 坐标准确，表格/分页效果与最终查看一致。

缺点：

- 需要转换服务，处理链路更长。

## 路线 B：直接解析 docx，重建近似坐标

1. 读取段落、run、表格。
2. 根据页面设置（纸张、边距、字号、行距）估算布局。
3. 生成近似 bbox。

优点：

- 无需转换。

缺点：

- 坐标是“排版模拟值”，复杂版式误差较大。

---

## 6. 如果你要“定位整个文档所有文本”，应如何设计

## 6.1 离线预处理（推荐）

适合大量文档检索：

1. 文档入库时提取全部 token + bbox。
2. 存储到 `document_tokens`（DB）或 `doc.json`（对象存储）。
3. 每页构建空间索引（R-Tree）。
4. 查询时 O(logN) 命中，避免每次全量扫描。

### 表结构建议（简版）

- `documents(id, source_type, file_path, page_count, created_at)`
- `document_pages(id, document_id, page_no, width, height)`
- `document_tokens(id, page_id, text, x, y, w, h, cx, cy, line_id, block_id, ord)`

## 6.2 在线即时解析（文档少时可用）

- 用户上传后即时提取，不做持久索引。
- 适合小规模编辑器，不适合高并发检索。

---

## 7. 坐标系与缩放的关键注意点

1. **存储坐标必须是原始坐标**（与渲染缩放无关）。
2. UI 框选坐标必须做 `show -> real` 逆变换。
3. 页面旋转（90/180/270）要在提取阶段统一校正。
4. 纵向文本、混合字体、OCR 文本要单独处理方向与置信度。

---

## 8. OCR 场景（扫描件）扩展

如果 PDF/Word 里没有可提取文本（扫描件），需要 OCR：

- 引擎：PaddleOCR / Tesseract / 云 OCR
- 输出：每个文本行/词的 polygon 或 bbox + `confidence`

建议在 token 里加：

- `source = "ocr" | "native"`
- `confidence`

查询时可设置阈值：低置信文本不参与自动命名，仅用于候选提示。

---

## 9. 锚点定位策略（你的“前缀定位”可泛化）

可设计 3 类锚点规则：

1. **行锚点**：同一行（`|cy - anchor.cy| <= tol`）命中。
2. **方向锚点**：锚点右侧最近 / 左侧最近。
3. **序号锚点**：从锚点文本提取数字，映射第 N 个字段。

这三类组合后，基本可以覆盖表单模板命名/绑定的大部分场景。

---

## 10. 推荐实施路径（从快到稳）

### Phase 1（1-2 天）

- 先做 PDF 全文 token 提取 + 矩形命中查询。
- 输出命中高亮与拼接文本。

### Phase 2（2-4 天）

- 加入空间索引（R-Tree）和行容差策略。
- 支持右侧最近 / 左侧最近 / 数字序号命中。

### Phase 3（3-7 天）

- 接入 Word -> PDF 转换链路。
- 做多格式统一中间层与缓存。

### Phase 4（持续）

- OCR 混合文档支持。
- 锚点策略可配置化（按模板类型调参）。

---

## 11. 验收标准（建议）

1. 给定矩形区域，命中文本召回率 > 98%（可选可提取 PDF）。
2. 右/左方向命中目标字段准确率 > 95%（模板场景）。
3. 同文档重复查询响应 < 50ms（有索引时，单页 1~3 万 token）。
4. Word 转 PDF 后与原模板视觉位置偏差 < 1 行高。

---

## 12. 一句话总结

“按坐标定位文本”的本质是：  
**把文档中的文本转换成可检索的空间对象（Token + BBox），再用统一坐标系进行矩形命中和规则推断。**  
对于 Word，工程上最稳的路线是先转 PDF，再复用同一套定位引擎。

