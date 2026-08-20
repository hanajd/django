# 共用模板资源

所有评价报告 LaTeX 模板共用，勿在各模板目录下重复存放。

- `images/` — 封面 logo 等模板级图片（如 `qc_watermark_logo.png`）
- `infoimages/` — 共用说明/示意类图片

报告工作区编译时，会把 `images/` 中缺失的文件同步到该报告的 `latex/images/`。
各模板自己的 `images/` 仅用于本模板覆盖或上传图。
