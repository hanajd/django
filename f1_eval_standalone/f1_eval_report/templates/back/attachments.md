# 附件格式说明

## 1. 在评价表（表 F.1）正文中的写法

「附件」栏目单元格内，逐行填写附件一至附件九的**名称清单**（不是插图）：

```
附件一 建设项目放射性职业病危害评价委托单
附件二 《事业单位法人证书》《医疗机构执业许可证》
附件三 本项目基本信息
附件四 本项目相关图纸
附件五 本项目放射工作人员配备计划
附件六 应急预案
附件七 其他放射防护管理制度
附件八 关于《{项目全称}放射性职业病危害预评价报告表》的评审意见
附件九 关于《{项目全称}放射性职业病危害预评价报告表》的评审意见修改说明
```

- 字段键：`fields.attachments_list`（多行文本）
- 可用 `attachments.format_attachment_list_text(...)` 由清单数组自动拼成

附件八、九中的 `{项目全称}` 按项目替换。

---

## 2. 后面附图页版式（图片附件）

附图页**不再**画评价表「附件」表头，只保留：

```
附件二                          ← 主标题：表格框外上方，左对齐
┌────────────────────────────┐
│ 附件 2-1                    │ ← 次级标题：表格框内、图片上方，左对齐
│                            │
│         （图片）             │ ← 图片在表格线内，通常一页一张
│                            │
└────────────────────────────┘
         （页脚页码）
```

| 元素 | 位置 | 对齐 | 示例 |
|------|------|------|------|
| 主标题 | 表框**外**、上方 | 左对齐 | `附件一` `附件二` |
| 次级标题 | 表框**内**、图片上方 | 左对齐 | `附件 2-1` `附件 4-1` |
| 图片 | 表框内（次级标题下方） | 等比居中适应 | jpeg/png |
| 页码 | 页脚，边距同 A4 | 奇右偶左 | |

同一附件多张图时：第一页写主标题；续页可省略主标题，只写 `附件 2-2` 等次级标题。

---

## 3. 常用页面 / 图片格式（`page_mode` + `image_rotate_deg`）

配置文件：`base/attachment_format.json`

| 模式键 | 含义 | 页面尺寸（pt） |
|--------|------|----------------|
| `a4_portrait`（默认） | A4 竖版 | 595.3 × 841.9 |
| `a4_landscape` / `page_rotate_90` | **页面旋转 90°**（A4 横版） | 841.9 × 595.3 |
| `a3_portrait` / `a3_enlarge` | **A4 放大为 A3** 竖版 | 841.9 × 1190.55 |
| `a3_landscape` / `a3_rotate` | A3 横版（页面旋转） | 1190.55 × 841.9 |

图片旋转（不改纸张方向）：

| `image_rotate_deg` | 含义 |
|--------------------|------|
| `0` | 不旋转 |
| `90` / `180` / `270` | **图片旋转**对应角度后再放入表框 |

可选组合示例：

- 竖图过大但纸张仍用 A4：`page_mode=a4_portrait` + `image_rotate_deg=90`
- 图纸很大：`page_mode=a3_portrait`
- 宽幅图纸：`page_mode=a3_landscape`

---

## 4. 数据 JSON 结构

见 `examples/attachments_example.json`：

```json
{
  "attachments_list_items": [
    { "code": "附件一", "title": "建设项目放射性职业病危害评价委托单" }
  ],
  "attachment_pages": [
    {
      "primary_title": "附件二",
      "secondary_title": "附件 2-1",
      "image": "path/to/image.jpeg",
      "page_mode": "a4_portrait",
      "image_rotate_deg": 0
    }
  ]
}
```

---

## 5. 生成命令

```powershell
# 仅生成附件图册 PDF
python f1_eval_report/cli.py generate-attachments `
  --data f1_eval_report/examples/attachments_example.json `
  --output attachments_demo.pdf
```

版式参数统一改：`base/attachment_format.json`（边距、字号、各 page_mode 尺寸）。
