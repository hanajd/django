# F.1 预评价报告表生成包（格式 / 内容分离）

把「基础表格格式」与「各表头内容」拆开，便于分别控制整表版式与单个栏目。

## 目录

```
f1_eval_report/
├── base/
│   ├── format.json              # 正文整表：页边距、字号、表题、分页
│   ├── attachment_format.json   # ★ 附件页：表框、标题位置、页面模式
│   └── columns.py
├── sections/
│   ├── catalog.py               # 主表格模板选择（gbzt / 250075YP）
│   ├── gbzt.py                  # GBZ 标准主表格栏目
│   └── yp250075.py              # 250075YP 主表格栏目
├── templates/                   # 栏目 Markdown 模板
│   ├── body/01～13_*.md
│   └── back/attachments.md      # ★ 附件使用说明（版式细则）
├── attachments.py               # ★ 附件图册页生成
├── embed/landscape.py           # 正文内 A3 附图（放射防护分区）
├── assemble.py / generate.py / cli.py
└── examples/
    └── attachments_example.json
```

## 怎么控制

| 想改什么 | 改哪里 |
|----------|--------|
| 正文页边距、字号、表题 | `base/format.json` |
| 正文栏目结构 / 字段绑定 | `sections/gbzt.py` 或 `sections/yp250075.py`（`--template` 选择） |
| 栏目文案模板 | `templates/body/*.md` |
| **附件清单文案 / 附图版式说明** | `templates/back/attachments.md` |
| **附件页边距、A3/旋转等模式尺寸** | `base/attachment_format.json` |

---

## 附件使用说明（摘要）

完整说明见 [`templates/back/attachments.md`](templates/back/attachments.md)。

### A. 表 F.1「附件」栏（只写名称）

在单元格内逐行填写，例如：

```
附件一 建设项目放射性职业病危害评价委托单
附件二 《事业单位法人证书》《医疗机构执业许可证》
…
附件九 关于《…预评价报告表》的评审意见修改说明
```

对应字段：`fields.attachments_list`。

### B. 后面的附图页（图片）

- **不需要**再画评价表「附件」表头  
- **主标题**（`附件一` / `附件二`）：表框**外**上方，左对齐  
- **次级标题**（`附件 2-1`）：表框**内**、图片上方，左对齐  
- **图片**：表框内，通常一页一张  

### C. 常用格式（页面 / 图片）

| 需求 | 数据字段 |
|------|----------|
| 默认 A4 竖版 | `"page_mode": "a4_portrait"` |
| 图片旋转 90° | `"image_rotate_deg": 90` |
| A4 放大为 A3 | `"page_mode": "a3_portrait"` |
| 页面旋转 90°（横版） | `"page_mode": "a4_landscape"` 或 `"a3_landscape"` |

### D. 生成附件 PDF

```powershell
cd c:\Users\13785\Desktop\pandoctest

python f1_eval_report/cli.py generate-attachments `
  --data f1_eval_report/examples/attachments_example.json `
  --output attachments_demo.pdf
```

---

## 生成正文（含附件清单栏）

```powershell
python f1_eval_report/cli.py generate `
  --data radiation_detection/examples/gbzt_f1_250375.json `
  --adapt-250375 `
  --output gbzt_f1_250375.pdf
```

（若数据中尚无 `attachments_list`，该栏为空，可按上面格式补入。）

## 设计要点（正文）

1. **工作场所布局**：正文 + 表4/表5  
2. **放射防护分区**：正文 + A3 附图（侧栏表头保留 A4 列宽；不重复表题）  
3. **页脚页码**：按当前页实际宽高，边距与 A4 一致  
