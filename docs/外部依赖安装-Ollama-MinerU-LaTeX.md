# 外部依赖安装手册：Ollama · MinerU · LaTeX

> **用途**：把本 Django 项目（`tablet_backend`）依赖的三类**系统级/重量级**外部能力装齐、验通、排障。  
> **读者**：接手运维、现场交付、新同事装机。  
> **整理日期**：2026-08-21  
> **关联**：[部署流程-从零安装与源码保护评估.md](部署流程-从零安装与源码保护评估.md)、[打包与新设备安装说明.md](打包与新设备安装说明.md)、[01-configuration.md](01-configuration.md)、[05-utils-and-pipelines.md](05-utils-and-pipelines.md)、[评价报告书-LaTeX功能说明.md](评价报告书-LaTeX功能说明.md)

---

## 0. 先搞清：什么必须装、什么可降级

```text
浏览器 / 平板 App
        │
        ▼
   Django 主站（必须）
        │
        ├── media / 字体 / Node MathJax（检测报告 PDF 强烈建议）
        │
        ├── Ollama :11434 ──┐
        │                   ├── 文档 OCR 管线（可选业务）
        └── MinerU CLI ─────┘
        │
        └── TeX（lualatex/xelatex）── 评价报告书编译（可选业务）
```

| 组件 | 不装时仍可用 | 不装时不可用 |
|------|--------------|--------------|
| **Django + requirements 核心包** | — | 整站 |
| **Ollama** | 文件库、工作流、检测报告导出、签名、F.1、多数 Admin | `/files/process/` 文档识别；OCR API；铭牌/设备结构化抽取；调试页 LLM |
| **MinerU** | 同上（主站） | 管线 PDF→Markdown；OCR 批次无 MD 产出 |
| **TeX Live** | 检测报告、F.1、日常业务 | **评价报告书**「确认生成 PDF」；报告状态 → `FAILED` |
| **Node + mathjax-full** | 多数 PDF 填充 | 含 `$$…$$` 的公式叠印可能失败/降级 |
| **NVIDIA GPU** | CPU 可勉强跑（慢）；主站无 GPU 也能跑 | 大模型/OCR 体验差；14B 量化建议有显卡 |

**推荐装机顺序（含全部能力）：**

1. OS 基础包（`poppler`、`libgl`…）  
2. （可选）NVIDIA 驱动  
3. **Ollama** + 拉模型  
4. Python venv + `pip install -r requirements.txt`（含 MinerU / ollama SDK）  
5. 确认 `which mineru`  
6. Node + `npm install`  
7. **TeX Live** + 确认 `which lualatex`  
8. 核对项目根 `fonts/` 与 `htmlpdf/fonts/`  
9. 配置环境变量 → migrate → 验收清单（本文 §6）

---

## 1. 环境变量速查（可直接贴进 systemd / `.env`）

```bash
# ---- OCR 管线：Ollama ----
export OLLAMA_HOST=http://127.0.0.1:11434
export EQUIPMENT_MODEL_NAME=qwen3:14b-q4_K_M
# export OLLAMA_OPTIONS='{"num_gpu":999,"num_ctx":8192}'

# ---- OCR 管线：MinerU ----
export MINERU_BACKEND=pipeline
# export MINERU_TIMEOUT=600
# export MINERU_KEEP_OUTPUT=0
# export MINERU_CUDA_VISIBLE_DEVICES=0

# ---- 评价报告书：LaTeX ----
export LATEX_ENGINE=lualatex
export LATEX_RUN_TIMES=2

# ---- 可选：前端模板 LLM 草稿（默认关）----
# export ENABLE_LLM_FRONTEND_TEMPLATE_DRAFT=1
```

完整表见 [01-configuration.md](01-configuration.md) §6～§8。运行时还可用仓库根 `llm_runtime.json`（超级管理员调试页）覆盖 Ollama 的 host/model，**验收时以实际生效配置为准**。

---

## 2. Ollama（大模型服务）

### 2.1 本项目怎么用它

| 环节 | 代码位置 | 说明 |
|------|----------|------|
| 文档管线末段抽取设备 JSON | `utils/document_pipeline.py` → `utils/ollama_extract.py` | MinerU 产出 MD 后调用 |
| Web | `/files/process/`（`perm_process_pipeline`） | `apps/core/pipeline_service.py` |
| API | `POST .../library/files/ocr/` 等 | `apps/api/` |
| 可选：前端模板草稿 | `utils/extract_frontend_template.py` | 需 `ENABLE_LLM_FRONTEND_TEMPLATE_DRAFT=1` |
| GPU 忙时压低参数 | `utils/gpu_scheduler.py` | `PIPELINE_OLLAMA_*` 等 |

默认模型名：`utils/pipeline_config.py` → **`qwen3:14b-q4_K_M`**（可用 `EQUIPMENT_MODEL_NAME` 覆盖）。  
默认地址：`OLLAMA_HOST=http://127.0.0.1:11434`（**无单独端口变量**，改端口就改 URL）。

### 2.2 在线安装（Ubuntu）

```bash
curl -fsSL https://ollama.com/install.sh | sh
sudo systemctl enable --now ollama
ollama --version
curl -s http://127.0.0.1:11434/api/tags
```

拉取本项目默认模型（体积大，需磁盘与时间）：

```bash
ollama pull qwen3:14b-q4_K_M
ollama list
```

连通自检：

```bash
curl http://127.0.0.1:11434/api/tags
ollama run qwen3:14b-q4_K_M "回复：ok"
```

### 2.3 离线 / 迁移模型

1. 源机：`ollama list` 记下模型名；模型目录常见 **`~/.ollama`**（若以 systemd 用户运行，可能在该用户家目录）。  
2. 整目录拷到新机同路径，或按 Ollama 官方文档导出/导入。  
3. 新机：`systemctl restart ollama` → `ollama list` 应能看到同名模型。  
4. Django 侧 `EQUIPMENT_MODEL_NAME` **必须与 `ollama list` 名称一致**。

打包迁移另见 [打包与新设备安装说明.md](打包与新设备安装说明.md)。

### 2.4 安全

- 默认只监听本机。**不要**把 `11434` 裸暴露到公网。  
- Django 通过 `OLLAMA_HOST` 访问即可；多机部署时用内网 + 防火墙。

### 2.5 失败时业务表现（接手必知）

| 场景 | 行为 |
|------|------|
| Ollama 未起 / 连不上 | `extract_devices_with_ollama` 重试（默认 3 次）后打 error 日志，返回**空字段设备骨架**，一般**不把整站打挂** |
| 模型名不对 | 调用失败，同上或管线结果无有效结构化数据 |
| 前端模板 LLM | 失败返回 `{}` |
| 主站其它功能 | 正常 |

验收时：**不要**只看 Django HTTP 200；要看管线结果里是否有真实设备字段，或看 `ollama` 日志。

### 2.6 常用排障

| 现象 | 处理 |
|------|------|
| `connection refused` | `systemctl status ollama`；检查 `OLLAMA_HOST` |
| 模型不存在 | `ollama list` 对齐 `EQUIPMENT_MODEL_NAME` / `llm_runtime.json` |
| 显存爆 / 极慢 | 减小 `OLLAMA_OPTIONS` 的 `num_ctx`；看 `gpu_scheduler` 是否自动压参；避免与 MinerU 同时占满 GPU |
| SDK 缺失 | venv 内 `pip show ollama outlines`；二者均在 `requirements.txt` |

---

## 3. MinerU（PDF → Markdown）

### 3.1 本项目怎么用它

| 环节 | 说明 |
|------|------|
| 安装形态 | Python 包 **`mineru>=2.7.0`**（`requirements.txt`）；安装后 PATH 中应有 CLI **`mineru`** |
| 调用 | `utils/mineru_ops.convert_pdf_to_md`：`mineru -p <pdf> -o <outdir> [-b <backend>]` |
| 后端默认 | Django `settings.MINERU_BACKEND` 默认 **`pipeline`**（管线启动时写入环境，避免误走 hybrid+vLLM） |
| 超时 | `MINERU_TIMEOUT` 默认 **600** 秒 |
| 批次输出 | `media/file_library/temp/batches/<batch_id>/mineru_output/` |
| 聚合 MD 目录 | `media/file_library/temp/mineru_md/`（配置名 `PIPELINE_MINERU_MD`） |

`torch` **未在 requirements 中显式 pin**，随 MinerU 传递安装；**与 CUDA / 驱动强相关**。源机有 GPU、新机无 GPU（或反之）时，离线 wheel 常会失败 → 在新机按硬件重装或降级「无 OCR」。

### 3.2 系统库（apt，与部署文档一致）

```bash
sudo apt update
sudo apt install -y \
  build-essential curl wget \
  python3 python3-venv python3-dev \
  poppler-utils \
  libgl1 libglib2.0-0 \
  fonts-dejavu-core
```

- `poppler-utils`：PDF→图等。  
- `libgl1` / `libglib2.0-0`：视觉/OCR 栈常见缺库。

### 3.3 随 Django 虚拟环境安装

```bash
cd /path/to/django          # 含 manage.py
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip wheel
pip install -r requirements.txt

which mineru
mineru --help | head
```

目标 Python：**3.12.x**（与 `requirements.txt` 头注释一致；3.10 也可，但交付以已验证版本为准）。

若暂时装不上 MinerU，可先注释/拆分依赖保证 Django 主站起来，再单独排 CUDA/torch（见 §3.6）。

### 3.4 后端选择说明

| 值 | 含义 | 建议 |
|----|------|------|
| `pipeline` | 默认；兼容性最好 | **生产默认，务必先用这个验收** |
| `hybrid-auto-engine` | MinerU 2.x hybrid；可能牵涉更多 GPU/编译依赖 | 仅环境成熟时 `export MINERU_BACKEND=hybrid-auto-engine` 再启动 Django |

注意：`utils/mineru_ops.resolve_mineru_backend` 在**环境变量未设**时，裸调 CLI 可能偏向 GPU→hybrid；但 **经 Django 管线会先 `setdefault` 为 settings 的 `pipeline`**。接手排障时以进程环境里的 `MINERU_BACKEND` 为准。

### 3.5 首次运行与模型缓存

首次成功跑通 `mineru` 时，常会从网络拉取版面/OCR 相关模型（具体清单随 MinerU 版本变化，**以官方文档为准**）。

- **需出网**，或事先在可联网机器跑通一次后拷贝缓存目录到离线机。  
- 常见缓存位置（按实际安装工具变化，装机时用 `du`/`find` 确认）：  
  - `~/.cache/huggingface/`  
  - `~/.cache/modelscope/`  
  - 用户家目录下 MinerU / magic-pdf 相关目录  

离线交付：把源机上述缓存打进迁移包，并在新机保持**同一运行用户**家目录结构，减少重新下载。

冒烟（在 venv 内，任选一页不太大的 PDF）：

```bash
source .venv/bin/activate
mkdir -p /tmp/mineru_smoke_out
mineru -p /path/to/sample.pdf -o /tmp/mineru_smoke_out -b pipeline
# 在输出树中应能找到 .md；Django 侧会取「最大」且文件名不像 middle/layout 的 md
```

### 3.6 失败时业务表现

| 场景 | 行为 |
|------|------|
| `mineru` 不在 PATH | 日志 `mineru未安装...`，返回空路径 → 管线常出现 **`MinerU未生成MD`** |
| 超时 | 子进程被终止；批次失败 |
| CUDA/torch 不匹配 | 子进程报错；改回 `MINERU_BACKEND=pipeline`、对齐驱动与 PyTorch wheel |

### 3.7 排障表

| 现象 | 处理 |
|------|------|
| `which mineru` 为空 | 确认已 `source .venv`；`pip show mineru`；systemd 的 `Environment=PATH=.../.venv/bin:...` |
| ImportError / torch | 按显卡重装匹配的 torch；或无 GPU 用 CPU 轮（极慢） |
| 有输出目录但无 MD | 看 mineru 日志；磁盘满；权限；`MINERU_KEEP_OUTPUT=1` 保留目录人工查 |
| 与 Ollama 抢显存 | `MINERU_CUDA_VISIBLE_DEVICES` / `OLLAMA_PREFERRED_GPU_INDEX` 分卡；或错开任务 |

相关代码：`utils/mineru_ops.py`、`utils/document_pipeline.py`、`utils/gpu_scheduler.py`。

---

## 4. LaTeX / TeX Live（评价报告书）

### 4.1 本项目怎么用它

| 项 | 值 |
|----|-----|
| 业务 | **评价报告书**（`/evaluation-reports/`，`perm_evaluation_report`） |
| 编译入口 | `converter/latex_compiler.py` → `LaTeXCompiler.compile_project` |
| 实际命令 | `<LATEX_ENGINE> -interaction=nonstopmode main.tex`（默认跑 **`LATEX_RUN_TIMES=2`** 遍） |
| 默认引擎 | **`lualatex`**（可用环境变量改为 `xelatex`） |
| 主文件 | 工作区内硬编码 **`main.tex`**（主站 settings 不依赖 `LATEX_MAIN_FILE`） |
| 模板根 | 仓库 `latex/`（如 `yp250420/`）；工作副本在 `media/evaluation_reports/work/report_<id>/latex/` |

缺引擎时抛出（报告 `FAILED`，**无静默降级**）：

```text
LaTeX engine '<engine>' not found in PATH. Install TeX Live and ensure lualatex/xelatex is available.
```

功能流程详见 [评价报告书-LaTeX功能说明.md](评价报告书-LaTeX功能说明.md)。

### 4.2 字体（两套路径，都要齐）

| 用途 | 路径 | 必备文件（仓库内应有） |
|------|------|------------------------|
| 评价报告书工作区 | 项目根 **`fonts/`**（工作区常链到 `latex/fonts/`） | `SIMSUN.TTC`、`SourceHanSansSC-VF.ttf`；封面可选 `SourceHanSerifSC-VF.ttf` |
| 检测报告 PDF 叠印 | **`htmlpdf/fonts/`**（常见与根 `fonts/` 内容同类） | 至少 `SIMSUN.TTC` |

交付/迁移时**两处都要带上**，不要假设系统已装中易字体。西文可能回退系统 Times New Roman（`\IfFontExistsTF`）。

### 4.3 模板用到的宏包（节选）

来自 `latex/yp250420/main.tex` 等：`ctex`、`geometry`、`graphicx`、`fancyhdr`、`tabularx`、`amsmath`/`amssymb`、`tikz`（及 shapes/arrows/positioning）、`longtable`、`makecell`、`multirow`、`float`、`caption`、`etoolbox`、`iftex`、`pifont`、`titletoc`、`lastpage`、`ragged2e`、`array`、`calc`、`setspace`；附件 PDF 时动态需要 **`pdfpages`**。

### 4.4 安装方式 A：TeX Live Full（最省心，磁盘大）

```bash
sudo apt update
sudo apt install -y texlive-full
which lualatex
lualatex --version
```

适合：交付机磁盘充裕、希望少踩宏包缺失。体积可达数 GB～十余 GB。

### 4.5 安装方式 B：精简包集合（Ubuntu，推荐有经验时使用）

```bash
sudo apt update
sudo apt install -y \
  texlive-luatex \
  texlive-xetex \
  texlive-latex-recommended \
  texlive-latex-extra \
  texlive-fonts-recommended \
  texlive-fonts-extra \
  texlive-lang-chinese \
  texlive-pictures \
  texlive-science \
  latexmk
```

然后确认：

```bash
which lualatex xelatex
kpsewhich ctex.sty
kpsewhich tikz.sty
kpsewhich longtable.sty
kpsewhich makecell.sty
kpsewhich pdfpages.sty
```

若 `kpsewhich` 找不到某 sty：用 `apt-file search` / `tlmgr`（若用官方 TeX Live）补包，或改回方式 A。

### 4.6 引擎与环境变量

```bash
export LATEX_ENGINE=lualatex    # 或 xelatex
export LATEX_RUN_TIMES=2
```

systemd 中为运行 Django 的用户保证 `PATH` 能 `which lualatex`（部分精简安装在 `/usr/bin`，一般无问题）。

### 4.7 编译冒烟（不经过 Web）

在已生成工作区或拷贝的模板目录：

```bash
cd /path/to/work/latex   # 含 main.tex 与 fonts
lualatex -interaction=nonstopmode main.tex
# 再跑一遍以稳定目录/引用
lualatex -interaction=nonstopmode main.tex
ls -la main.pdf
```

Web 路径：新建评价报告书 → 走完生成/编辑 → 「确认生成 PDF」。失败时看报告 `error_message` 与工作区 `.log`。

### 4.8 排障表

| 现象 | 处理 |
|------|------|
| engine not found | 安装 TeX；检查服务用户 PATH |
| Missing `\usepackage{...}` / File not found | 补宏包或改 `texlive-full` |
| 中文方框 / 字体警告 | 检查工作区 `fonts/` 是否链到项目根字体；`SIMSUN.TTC`、思源字体是否存在 |
| 已出 PDF 但退出码非 0 | 编译器对 ctex 字体告警可能非零仍留 PDF；以是否生成可用 PDF 与业务页为准 |
| 与检测报告混淆 | 检测 PDF **不走** TeX；只有评价报告书需要 |

---

## 5. GPU 与多卡（OCR 场景）

1. 安装匹配的 NVIDIA 驱动，`nvidia-smi` 正常。  
2. 确认 Ollama / PyTorch（MinerU）能看见 GPU。  
3. 常用环境变量（详见 `utils/gpu_scheduler.py` 注释）：

| 变量 | 作用 |
|------|------|
| `MINERU_CUDA_VISIBLE_DEVICES` | 限制 MinerU 可见卡 |
| `OLLAMA_PREFERRED_GPU_INDEX` | Ollama 偏好卡 |
| `PIPELINE_GPU_AUTO_OLLAMA` / `PIPELINE_GPU_AUTO_MINERU` | 默认开：按显存自动收紧参数 |
| `PIPELINE_OLLAMA_NUM_CTX_*` / `PIPELINE_OLLAMA_NUM_GPU_*_CAP` | 忙闲档位 |

无 GPU：主站仍可运行；OCR/14B 模型会很慢或不可用，可按合同降级关闭管线页（`PARTY_A_DEMO_DISABLE_PIPELINE` 等）。

---

## 6. 装完验收清单（打印勾选）

### 6.1 主站（必须）

- [ ] `python manage.py migrate` 成功  
- [ ] 浏览器登录 Web  
- [ ] 文件库列表、上传  
- [ ] 任选一条检测报告导出 PDF（字体正常、非乱码）

### 6.2 Ollama

- [ ] `systemctl is-active ollama` → active  
- [ ] `curl -s http://127.0.0.1:11434/api/tags` 含目标模型  
- [ ] `EQUIPMENT_MODEL_NAME` 与 `ollama list` 一致  

### 6.3 MinerU

- [ ] 运行 Django 的同一环境 `which mineru` 有路径  
- [ ] 冒烟 PDF 能产出 `.md`  
- [ ] （合同含 OCR）Web `/files/process/` 或 API OCR 跑通一批，无 `MinerU未生成MD`

### 6.4 LaTeX

- [ ] `which lualatex`（或所配 `LATEX_ENGINE`）  
- [ ] `kpsewhich ctex.sty` 成功  
- [ ] 项目根 `fonts/SIMSUN.TTC` 与思源字体存在  
- [ ] 评价报告书编译出 PDF，状态非 `FAILED`

### 6.5 Node / 公式

- [ ] `node_modules/mathjax-full` 存在（若仓库提供 `npm install`）  
- [ ] 含 `$$…$$` 的报告叠印抽查  

---

## 7. 与其它文档的分工

| 文档 | 读它做什么 |
|------|------------|
| **本文** | Ollama / MinerU / TeX **怎么装、怎么验、失败会怎样** |
| [部署流程-从零安装与源码保护评估.md](部署流程-从零安装与源码保护评估.md) | 整机从零：systemd、Nginx、安全、验收总流程 |
| [打包与新设备安装说明.md](打包与新设备安装说明.md) | 旧机 → 新机：db、media、wheel、模型目录拷贝 |
| [05-utils-and-pipelines.md](05-utils-and-pipelines.md) | 管线代码地图与目录 |
| [评价报告书-LaTeX功能说明.md](评价报告书-LaTeX功能说明.md) | 评价报告书**业务**步骤与模型 |
| [01-configuration.md](01-configuration.md) | 全部环境变量与路径常量 |

---

## 8. 修订记录

| 日期 | 说明 |
|------|------|
| 2026-08-21 | 初版：补齐 Ollama / MinerU / LaTeX 安装、验收、失败语义与排障 |
