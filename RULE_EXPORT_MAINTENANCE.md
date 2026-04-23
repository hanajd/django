# 规则导出维护文档

本文档用于维护“前端模板规则导出”链路，目标是让你快速定位以下问题：

- 为什么某个字段被分到了错误步骤/分组
- 为什么导出的 `id`、`title`、`type` 不符合预期
- 如何按命名规范稳定控制导出结构

---

## 1. 规则导出入口

### 1.1 Web 端 HTMLPDF 导出前端 JSON

- 入口接口：`/files/htmlpdf/api/export-frontend-json/`
- 主要代码：`apps/core/views.py` 的 `htmlpdf_api_export_frontend_json`
- 规则引擎函数：`utils/frontend_schema_rule_engine.py` 的 `build_frontend_schema_by_rules()`

### 1.2 任务接口自动导出前端模板

- 接口：`GET /api/v2/inspections/projects/{projectId}/tasks/{taskNo}/export-frontend-json`
- 主要代码：`apps/api/inspection_views.py` 的 `InspectionTaskFrontendJsonExportAPIView`
- 当前行为：优先规则导出；异常时回退到 `extract_frontend_template`

---

## 2. 规则文件与职责

核心文件：`utils/frontend_schema_rule_engine.py`

主要职责：

- 从 `pdf.fields` 推断前端 `steps/sections/fields`
- 根据字段 label / id / rawId 推断类型与单位
- 用下划线命名规则做结构分层
- 做导出后处理（日期合并、签名修正、排序、默认值注入）

---

## 3. 关键规则总览

## 3.1 字段类型推断

函数：`_infer_type(raw_type, label)`

- `image` 或签名关键词 -> `signature`
- `check` -> `radio` 或 `boolean`
- 含日期关键词 -> `date`
- 含数值关键词 -> `number`
- 含备注/结论关键词 -> `textarea`
- 其余 -> `text`

## 3.2 分组规则（两层）

1) **下划线规则优先**：`_classify_by_underscore(raw_key)`

- 形如 `prefix_suffix` 的字段会按前缀分组
- `prefix` 决定 section（而不是最后一段）
- 为避免中文前缀塌缩，使用 `_underscore_stable_slug()` 生成稳定 id

2) **语义规则回退**：`_classify_group(label, field_type)`

- 无下划线时，根据关键词归到基础信息/仪器/质控/判定/签名等步骤

## 3.3 字段 id / 标签

- `id`：优先英文语义（`_to_english_id` + 小驼峰）
- 下划线字段：最后一段通过 `_UNDERSCORE_SUFFIX_MAP` 映射（如 `kv -> kv`, `报出值 -> reportValue`）
- `label`：通过 `_UNDERSCORE_LABEL_MAP` 或原中文后缀生成

## 3.4 导出后处理

函数调用顺序（`build_frontend_schema_by_rules` 末尾）：

1. `_ensure_basic_defaults`
2. `_post_optimize_js117`（仅 JS-117 特例）
3. `_force_signature_semantics`
4. `_merge_split_date_fields`
5. `_sort_fields_by_coordinate_order`
6. `_inject_underscore_section_rules`
7. `_force_signature_step_last`
8. `_strip_coordinate_keys`

---

## 4. 命名规范（强烈建议）

为了稳定导出结构，建议在 `pdf.fields[].id`（或 placeholder 映射后 id）遵循：

### 4.1 下划线层级

- 推荐：`分组前缀_字段后缀`
- 示例：
  - `高对比分辨力_kv`
  - `高对比分辨力_报出值`
  - `低对比度分辨力_result`

同一前缀会稳定进入同一 section。

### 4.2 后缀语义

尽量使用 `_UNDERSCORE_SUFFIX_MAP` 已支持后缀：

- `kv`, `ma`, `ww`, `wl`, `lp`, `rv`, `roi`
- `报出值`, `判定`, `结果`, `测量值`, `真实长度`, `测量长度`

未收录后缀会走自动英文化，可能导致命名不一致。

### 4.3 签名字段

使用明确语义 id：

- `author`, `reviewer`, `approver`

并确保 `fieldType=image` 或 label 含签名关键词。

---

## 5. 常见问题与排查

## 5.1 字段进错分组

排查顺序：

1. 看字段是否含 `_`（有下划线先走 `_classify_by_underscore`）
2. 看前缀是否一致（同前缀会并组）
3. 看 rawKey 来源（label 优先，其次 rawId）
4. 无下划线时检查 `_classify_group` 关键词命中

## 5.2 中文前缀 section 冲突

现已通过 `_underscore_stable_slug` 规避。若仍冲突，检查是否前缀实际完全相同。

## 5.3 类型不对（checkbox 被当 boolean/radio）

检查：

- 原字段 `fieldType`
- label 是否包含“验收/状态/模式”（会偏向 `radio`）

## 5.4 日期字段被拆散/合并异常

检查 `_merge_split_date_fields` 识别规则：

- `xxx_year/month/day`
- `xxxYear/Month/Day`
- 中文 `...年/月/日`

---

## 6. 维护改动清单（建议流程）

每次改规则时建议按以下步骤：

1. 在 `frontend_schema_rule_engine.py` 修改目标函数
2. 用同一模板重复导出前端 JSON 做对比
3. 重点检查：
   - `steps` 顺序
   - `sections` 数量与 id
   - 关键字段 `id/type/label`
4. 若是兼容性改动，保留回退策略（如 API 里已有 fallback）

---

## 7. 与 HTMLPDF 字段约定关系

规则导出依赖 `pdf.fields` 字段结构，至少包含：

- `id`
- `fieldType`
- `page/x/y/w/h`
- `title`（推荐）

如果历史模板仍使用“语义在 placeholder，id=fx”的旧结构，先执行迁移，再做规则导出，避免分组和映射不稳定。

