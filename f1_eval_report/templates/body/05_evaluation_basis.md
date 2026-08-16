# 5 主要评价依据

| 元数据 | 值 |
|--------|-----|
| id | `evaluation_basis` |
| 类型 | 固定模板（常用文本模板拼装） |
| 默认源 | `templates/common/evaluation_basis/` |
| 参照 | 《250075YP 南昌大学第二附属医院（新建院区）》预评价报告表 |

## 说明

- **默认一律使用常用文本模板**，不从评价信息表提取。
- 正式条文按小节存放，便于单独维护：

| 小节 | 文件 |
|------|------|
| 1.法律、法规、规章及规范性文件（标题） | `common/evaluation_basis/01_section_header.md` |
| 1.1法律 | `02_laws.md` |
| 1.2法规 | `03_regulations.md` |
| 1.3规章 | `04_rules.md` |
| 1.4规范性文件 | `05_normative_docs.md` |
| 2.主要标准 | `06_standards.md` |
| 3.基础资料 | `07_basic_materials.md` |
| 4.参考资料 | `08_references.md` |

- 拼装规则：`common/evaluation_basis/assemble.json`
- 兼容缓存：`templates/defaults/evaluation_basis.json`（由各小节拼装生成）
