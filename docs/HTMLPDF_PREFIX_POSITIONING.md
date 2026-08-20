# HTMLPDF「定位前缀 / 向右填 / 向左填」实现说明

本文说明 `@django` 项目中 HTMLPDF 编辑器“定位前缀”相关能力的具体实现，包括前端交互、定位算法、命名合成、以及与后端导出的衔接。

补充：DSA 多层级表格结构化导出规范见 `HTMLPDF_MATRIX_TABLE_SCHEMA.md`。

## 1. 功能入口与总体流程

入口页面：

- `GET /files/htmlpdf/editor/{template_pdf_id}/`
- 页面模板：`htmlpdf/templates/index.html`

工具栏三个命名按钮：

- `定位前缀`（`prefix`）
- `向右填`（`fillRight`）
- `向左填`（`fillLeft`）

总体流程：

1. 用户切换到某种命名模式（`setDrawMode`）。
2. 在 PDF 页面上框选一个“关键词区域”（橡皮筋框）。
3. 前端从 PDF 文本层提取该区域文字，生成一个 `mark`。
4. 根据 `mark` 类型计算受影响输入框（全行 / 目标框）。
5. 统一执行 `applyAllNamingMarks()`，把命名规则写回每个字段 `placeholder`。
6. 保存模板或导出前端 JSON 时，这些命名进入持久化结果。

## 2. 关键数据结构

核心变量在 `htmlpdf/templates/index.html`：

- `fields`: 当前画布上的输入框/勾选框/图片框集合
- `marks`: 命名定位标记集合
- `KEYWORD_ROW_TOLERANCE_PX = 10`: 行命中容差（Y 方向）

单个 `mark` 结构：

- `id`: 标记 ID（如 `mk1`）
- `type`: `prefix` / `fillRight` / `fillLeft`
- `page`: 页码
- `x,y,w,h`: 标记框在 PDF 原坐标系下的位置
- `text`: 从标记框识别出的关键词文本
- `targetFieldId`: 命中的目标字段 ID（右填/左填使用）
- `targetFieldNo`: 通过“数字定位”解析出的序号（可选）

## 3. 文本定位是怎么做的

### 3.1 PDF 文本层预处理

在渲染每页时（`renderPdfAndFields`）：

1. 用 `pdf.js` 渲染 canvas。
2. 通过 `page.getTextContent()` 读取文本项。
3. 每个文本项转换为统一坐标：
   - `x, y, w, h`
   - `cx, cy`（中心点）
4. 存入 `pageMeta[p].textItems`。

### 3.2 框选区域识别关键词

在 `stopDraw` 中调用 `getKeywordTextInRect(pageNo, realX, realY, realW, realH)`：

1. 选出与框选矩形有重叠的文本项（矩形相交判断）。
2. 按阅读顺序排序（先 `cy` 再 `cx`）。
3. 拼接 `str`，去空白后得到 `mark.text`。

若没识别到文字，提示“未识别到文字，请扩大定位框后重试”。

## 4. 三种模式的命中规则

## 4.1 `prefix`（定位前缀）

语义：给同一行范围内所有字段添加前缀片段。

命中逻辑：

- 同页；
- 字段中心点 `Y` 落在标记框上下容差内（±10px，显示坐标）。

结果：

- 该行全部字段会拿到该前缀片段（可叠加多个前缀标记）。

## 4.2 `fillRight`（向右填）

语义：把标记文本填到“右侧目标字段”。

目标字段优先级：

1. 已存在 `targetFieldId`（历史稳定绑定）；
2. `targetFieldNo`（文本内数字序号，如“3”对应第 3 个字段）；
3. 自动几何匹配（`pickTargetFieldByDirection`）：
   - 同页；
   - 字段类型仅 `text/check`；
   - 同行（Y 容差）；
   - 字段在标记中心右侧；
   - 按横向 gap 最小优先。

## 4.3 `fillLeft`（向左填）

逻辑与 `fillRight` 对称：

- 目标取同页、同行、位于标记中心左侧；
- 同样支持 `targetFieldId` / `targetFieldNo` / 自动匹配三层兜底。

## 5. “数字定位”机制

函数：`extractLocatorNumber(text)` + `getFieldByOrdinal(ordinal)`。

规则：

- 从标记文本中提取首个数字（支持全角数字转半角）。
- 按“阅读顺序字段列表”取第 N 个字段：
  - 先页码；
  - 再 `y`；
  - 再 `x`；
  - 再 `id` 兜底。

这让“仪器3”“检测点2”等带序号文本可直接绑定到指定字段，而不是纯几何推断。

## 6. 最终命名如何合成

核心函数：`applyAllNamingMarks()`。

步骤：

1. 将 `marks` 分桶：
   - `prefixByField`
   - `rightByField`
   - `leftByField`
2. 每个桶内按 `x` 升序排序。
3. 单字段命名片段顺序固定：
   - `prefixParts + rightParts + leftParts`
4. 每段执行 `normalizeNamePiece`：
   - 去首尾 `_`
   - 仅保留首段，避免历史 `_原名` 污染
5. 用 `_` 拼接后写回 `field.placeholder`。

没有命中任何规则的字段保持原名不变。

当 `marks` 清空时，会回退为 `originalPlaceholder`。

## 7. 为什么能“稳定不漂移”

为避免页面缩放、字段增删后命名漂移，代码做了两层稳定性处理：

- 标记中保存 `targetFieldId`，后续优先按 ID 绑定；
- 历史标记若无 `targetFieldId`，首次应用自动补齐后写回内存。

因此只要字段 ID 不变，右填/左填的命中基本稳定。

## 8. 与后端导出的衔接

前端保存时会把字段列表提交给：

- `POST /files/htmlpdf/api/export-json/`
- `POST /files/htmlpdf/api/export-frontend-json/`

后端在 `apps/core/views.py` 做两件关键事：

1. `_sanitize_pdf_field_texts`：对 `id/title/placeholder` 统一清洗（符号、括号等规则）。
2. `_normalize_pdf_fields_for_unified_template`：
   - 保留 `pdfFieldId = f序号` 的稳定映射；
   - `placeholder/title` 进入统一模板结构，供规则引擎继续分层处理。

所以“定位前缀”产出的名称，最终会体现在模板 JSON 与前端 JSON 中。

## 9. 相关函数索引（便于二次开发）

前端（`htmlpdf/templates/index.html`）：

- 模式切换：`setDrawMode`
- 框选流程：`startDraw` / `onDraw` / `stopDraw`
- 文本提取：`getKeywordTextInRect`
- 行命中判断：`isFieldCenterInMarkYRange`
- 方向选目标：`pickTargetFieldByDirection`
- 数字定位：`extractLocatorNumber` / `getFieldByOrdinal`
- 统一命名：`applyAllNamingMarks`

后端（`apps/core/views.py`）：

- 模板导出：`htmlpdf_api_export_json`
- 前端 JSON 导出：`htmlpdf_api_export_frontend_json`
- 文本清洗：`_sanitize_pdf_field_texts`
- 字段映射标准化：`_normalize_pdf_fields_for_unified_template`

## 10. 使用建议

- 框选前缀时尽量只框“纯关键词”，避免带多余文本。
- 右填/左填建议优先利用序号文本（如“3”），绑定更稳。
- 若字段位置变化较大，建议删除旧标记后重框，避免历史目标残留。

