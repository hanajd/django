"""LaTeX 模板目录浏览与编辑。"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from django.conf import settings

from .constants import REPORT_TEMPLATES
from .natural_sort import natural_sort_key
from .models import EvaluationLatexTemplate, EvaluationReport


class TemplatePreviewError(ValueError):
    pass


LATEX_BUILD_SUFFIXES = {
    ".aux",
    ".log",
    ".toc",
    ".out",
    ".fls",
    ".fdb_latexmk",
    ".synctex.gz",
    ".bbl",
    ".blg",
    ".xdv",
}
TEX_SUFFIX = ".tex"
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg"}
PDF_SUFFIX = ".pdf"
PREVIEWABLE_ASSET_SUFFIXES = IMAGE_SUFFIXES | {PDF_SUFFIX}


def asset_preview_type(suffix: str) -> str:
    return "pdf" if suffix.lower() == PDF_SUFFIX else "image"


def template_root_for_report(report: EvaluationReport) -> Path:
    from .latex_template_service import get_template

    tpl = get_template(report.latex_template_key)
    root = tpl.storage_root
    if not root.is_dir():
        raise TemplatePreviewError(f"模板目录不存在：{root}")
    return root.resolve()


def template_root_for_library(template: EvaluationLatexTemplate) -> Path:
    root = template.storage_root
    if not root.is_dir():
        raise TemplatePreviewError(f"模板目录不存在：{root}")
    return root.resolve()


def latex_root_for_report(report: EvaluationReport) -> Path:
    """优先使用报告工作区 LaTeX，否则回退到源模板。"""
    work = report.work_dir
    if work:
        work_latex = work / "latex"
        if (work_latex / "main.tex").is_file():
            return work_latex.resolve()
    return template_root_for_report(report)


def latex_source_kind(report: EvaluationReport) -> str:
    work = report.work_dir
    if work and (work / "latex" / "main.tex").is_file():
        return "work"
    return "template"


def latex_project_kind(root: Path) -> str:
    """
    完整模板：appendix/ 含 .tex 且 images/ 含图片。
    进行中：二者仅满足其一。
    空白模板：均无实质内容。
    """
    appendix = root / "appendix"
    images = root / "images"
    has_appendix = appendix.is_dir() and any(
        p.is_file() and p.suffix.lower() == TEX_SUFFIX for p in appendix.rglob("*")
    )
    has_images = images.is_dir() and any(
        p.is_file() and p.suffix.lower() in PREVIEWABLE_ASSET_SUFFIXES for p in images.rglob("*")
    )
    if has_appendix and has_images:
        return "complete"
    if has_appendix or has_images:
        return "partial"
    return "blank"


def latex_project_kind_for_report(report: EvaluationReport, root: Path) -> str:
    """源模板目录固定视为空白模板；工作区按实际内容判断。"""
    if latex_source_kind(report) == "template":
        return "blank"
    return latex_project_kind(root)


def build_latex_file_tree(root: Path, report: EvaluationReport | None = None) -> list[dict[str, Any]]:
    """构建 Overleaf 风格的文件树。"""
    source_is_template = report is not None and latex_source_kind(report) == "template"
    nodes: list[dict[str, Any]] = []

    main = root / "main.tex"
    if main.is_file():
        nodes.append(
            {
                "name": "main.tex",
                "kind": "file",
                "relpath": "main.tex",
                "editable": True,
            }
        )

    top_dirs = ["chapters"]
    if not source_is_template:
        for extra in ("appendix", "images"):
            dirpath = root / extra
            if dirpath.is_dir() and _dir_has_listable_files(root, dirpath):
                top_dirs.append(extra)

    for dirname in top_dirs:
        dirpath = root / dirname
        if not dirpath.is_dir() or not _dir_has_listable_files(root, dirpath):
            continue
        folder = _build_dir_node(root, dirpath, report)
        if folder:
            nodes.append(folder)

    return nodes


def latex_project_kind_label(kind: str) -> str:
    return {
        "blank": "空白模板",
        "partial": "进行中",
        "complete": "完整模板",
    }.get(kind, "空白模板")


def _copy_blank_template(src: Path, dest: Path) -> None:
    """复制空白模板：main.tex + chapters/ + files/ + fonts/（个性化长文占位）。"""
    dest.mkdir(parents=True, exist_ok=True)
    main_src = src / "main.tex"
    if main_src.is_file():
        shutil.copy2(main_src, dest / "main.tex")
    for sub in ("chapters", "files", "fonts"):
        sub_src = src / sub
        if not sub_src.is_dir():
            continue
        sub_dest = dest / sub
        if sub_dest.exists():
            shutil.rmtree(sub_dest)
        shutil.copytree(sub_src, sub_dest)


def ensure_editable_latex(report: EvaluationReport) -> Path:
    """将空白源模板复制到工作区（若尚未存在），并同步个性化关键词到 main.tex。"""
    from .keywords_service import ensure_report_personalized_latex

    work = report.ensure_work_dir()
    work_latex = work / "latex"
    created = False
    if not (work_latex / "main.tex").is_file():
        src = template_root_for_report(report)
        _copy_blank_template(src, work_latex)
        created = True
    ensure_work_latex_images(report, work_latex)
    ensure_work_latex_fonts(report, work_latex)
    root = work_latex.resolve()
    # Always keep PROJECT_VARS in sync with DB (import from tex when DB empty).
    try:
        ensure_report_personalized_latex(report, root)
    except Exception:
        if created:
            pass
    return root


def ensure_work_latex_images(report: EvaluationReport, work_latex: Path | None = None) -> Path:
    """Ensure ``latex/images`` exists; copy from template when work copy is empty."""
    root = work_latex or latex_root_for_report(report)
    dest = root / "images"
    has_files = dest.is_dir() and any(p.is_file() for p in dest.rglob("*"))
    if has_files:
        return dest

    try:
        src = template_root_for_report(report) / "images"
    except TemplatePreviewError:
        src = None
    if src is not None and src.is_dir() and any(p.is_file() for p in src.rglob("*")):
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            # empty or placeholder dir
            try:
                dest.rmdir()
            except OSError:
                shutil.rmtree(dest)
        shutil.copytree(src, dest)
    else:
        dest.mkdir(parents=True, exist_ok=True)
    return dest


def ensure_work_latex_fonts(report: EvaluationReport, work_latex: Path | None = None) -> Path:
    """Ensure ``latex/fonts`` has Source Han (etc.); copy from template when missing."""
    root = work_latex or latex_root_for_report(report)
    dest = root / "fonts"
    needed = ("SourceHanSerifSC-VF.ttf",)
    has_needed = dest.is_dir() and all((dest / name).is_file() for name in needed)
    if has_needed:
        return dest

    try:
        src = template_root_for_report(report) / "fonts"
    except TemplatePreviewError:
        src = None
    if src is not None and src.is_dir() and any(p.is_file() for p in src.iterdir()):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.mkdir(exist_ok=True)
        for item in src.iterdir():
            if item.is_file():
                target = dest / item.name
                if not target.is_file():
                    shutil.copy2(item, target)
    else:
        dest.mkdir(parents=True, exist_ok=True)
    return dest


def resolve_work_media_file(report: EvaluationReport, relpath: str) -> Path | None:
    """Resolve images/... under latex root, work dir, or source template."""
    rel = (relpath or "").replace("\\", "/").lstrip("/")
    if not rel or ".." in rel.split("/"):
        return None

    roots: list[Path] = []
    work = report.work_dir
    if work:
        roots.append((work / "latex").resolve())
        roots.append(work.resolve())
    try:
        roots.append(latex_root_for_report(report))
    except TemplatePreviewError:
        pass
    try:
        roots.append(template_root_for_report(report))
    except TemplatePreviewError:
        pass

    seen: set[str] = set()
    for root in roots:
        key = str(root)
        if key in seen:
            continue
        seen.add(key)
        if not root.is_dir():
            continue
        target = (root / rel).resolve()
        try:
            target.relative_to(root)
        except ValueError:
            continue
        if target.is_file():
            return target
    return None


def _safe_tex_path(root: Path, relpath: str) -> Path:
    rel = (relpath or "main.tex").replace("\\", "/").lstrip("/")
    if ".." in rel.split("/"):
        raise TemplatePreviewError("非法路径")
    target = (root / rel).resolve()
    root_resolved = root.resolve()
    if not str(target).startswith(str(root_resolved)):
        raise TemplatePreviewError("非法路径")
    if target.suffix.lower() != ".tex":
        raise TemplatePreviewError("仅支持 .tex 文件")
    if not target.is_file():
        raise TemplatePreviewError("文件不存在")
    return target


def _is_build_artifact(path: Path) -> bool:
    return path.suffix.lower() in LATEX_BUILD_SUFFIXES


def _file_allowed_in_tree(root: Path, path: Path) -> bool:
    if not path.is_file() or _is_build_artifact(path):
        return False
    rel = path.relative_to(root)
    parts = rel.parts
    suffix = path.suffix.lower()

    if len(parts) == 1 and parts[0] == "main.tex":
        return suffix == TEX_SUFFIX
    if not parts:
        return False
    top = parts[0]
    if top == "chapters":
        return suffix == TEX_SUFFIX
    if top == "appendix":
        return suffix == TEX_SUFFIX or suffix in PREVIEWABLE_ASSET_SUFFIXES
    if top == "images":
        return suffix in PREVIEWABLE_ASSET_SUFFIXES
    return False


def _dir_has_listable_files(root: Path, dirpath: Path) -> bool:
    for path in dirpath.rglob("*"):
        if _file_allowed_in_tree(root, path):
            return True
    return False


def _tree_entry_sort_key(dirpath: Path, entry: Path) -> tuple:
    """appendix 目录：每个 attachment_XX.tex 后紧跟其图片。"""
    if dirpath.name == "appendix" and entry.is_file():
        if entry.suffix.lower() == TEX_SUFFIX:
            return (natural_sort_key(entry.stem + "."), 0, ())
        stem = entry.stem
        parts = stem.split("_")
        if len(parts) >= 3 and parts[0] == "attachment" and parts[1].isdigit():
            slot = f"{parts[0]}_{parts[1]}"
            return (natural_sort_key(slot + "."), 1, natural_sort_key(entry.name))
    return (natural_sort_key(entry.name), 0 if entry.is_dir() else 1, ())


def _build_dir_node(
    root: Path,
    dirpath: Path,
    report: EvaluationReport | None = None,
) -> dict[str, Any] | None:
    from .appendix_service import library_original_name_for_latex_asset

    children: list[dict[str, Any]] = []
    for entry in sorted(
        dirpath.iterdir(),
        key=lambda p: _tree_entry_sort_key(dirpath, p),
    ):
        if entry.is_dir():
            sub = _build_dir_node(root, entry, report)
            if sub:
                children.append(sub)
        else:
            if not _file_allowed_in_tree(root, entry):
                continue
            rel = entry.relative_to(root).as_posix()
            suffix = entry.suffix.lower()
            if suffix == TEX_SUFFIX:
                children.append(
                    {
                        "name": entry.name,
                        "kind": "file",
                        "relpath": rel,
                        "editable": True,
                    }
                )
            else:
                library_name = ""
                if report and dirpath.name == "appendix":
                    library_name = library_original_name_for_latex_asset(report, entry.name) or ""
                children.append(
                    {
                        "name": entry.name,
                        "kind": "asset",
                        "relpath": rel,
                        "media_relpath": f"latex/{rel}",
                        "preview_type": asset_preview_type(suffix),
                        "library_name": library_name,
                        "editable": False,
                    }
                )
    if not children:
        return None
    return {
        "name": dirpath.name,
        "kind": "folder",
        "relpath": dirpath.relative_to(root).as_posix(),
        "children": children,
    }


def list_template_tex_files(report: EvaluationReport) -> list[dict[str, str]]:
    """扁平 .tex 列表（兼容旧逻辑）。"""
    root = latex_root_for_report(report)
    items: list[dict[str, str]] = []

    def walk(nodes: list[dict[str, Any]]) -> None:
        for node in nodes:
            if node["kind"] == "file":
                rel = node["relpath"]
                items.append(
                    {
                        "relpath": rel,
                        "name": node["name"],
                        "dirname": str(Path(rel).parent.as_posix()) if "/" in rel else "",
                    }
                )
            elif node["kind"] == "folder":
                walk(node.get("children") or [])

    walk(build_latex_file_tree(root, report))
    items.sort(
        key=lambda x: (
            0 if x["relpath"] == "main.tex" else 1,
            natural_sort_key(x["relpath"]),
        )
    )
    return items


def resolve_asset_preview(report: EvaluationReport, relpath: str) -> dict[str, str]:
    """校验并返回工作区内可预览资源（图片/PDF）。"""
    if latex_source_kind(report) != "work":
        raise TemplatePreviewError("源模板中无法预览附件，请先生成或编辑项目副本")
    root = latex_root_for_report(report)
    rel = (relpath or "").replace("\\", "/").lstrip("/")
    if ".." in rel.split("/"):
        raise TemplatePreviewError("非法路径")
    target = (root / rel).resolve()
    if not str(target).startswith(str(root.resolve())):
        raise TemplatePreviewError("非法路径")
    if not target.is_file():
        raise TemplatePreviewError("文件不存在")
    suffix = target.suffix.lower()
    if suffix not in PREVIEWABLE_ASSET_SUFFIXES:
        raise TemplatePreviewError("该文件类型不支持预览")
    return {
        "relpath": rel,
        "preview_type": asset_preview_type(suffix),
        "media_relpath": f"latex/{rel}",
    }


def is_previewable_asset_relpath(relpath: str) -> bool:
    return Path(relpath).suffix.lower() in PREVIEWABLE_ASSET_SUFFIXES


def resolve_template_preview_selection(
    report: EvaluationReport,
    selected: str,
    *,
    view: str = "",
) -> dict[str, Any]:
    """解析模板页当前选中项（TeX 或图片/PDF）。"""
    selected = (selected or "main.tex").strip()
    want_asset = view == "asset" or is_previewable_asset_relpath(selected)

    if want_asset:
        try:
            asset = resolve_asset_preview(report, selected)
            _, tex_content = read_template_tex(report, "main.tex")
            return {
                "initial_view": "asset",
                "selected_file": asset["relpath"],
                "preview_asset": asset,
                "tex_content": tex_content,
                "tex_relpath": "main.tex",
            }
        except TemplatePreviewError:
            pass

    relpath, content = read_template_tex(report, selected)
    return {
        "initial_view": "tex",
        "selected_file": relpath,
        "preview_asset": None,
        "tex_content": content,
        "tex_relpath": relpath,
    }


def read_template_tex(report: EvaluationReport, relpath: str | None = None) -> tuple[str, str]:
    root = latex_root_for_report(report)
    rel = relpath or "main.tex"
    target = _safe_tex_path(root, rel)
    return rel, target.read_text(encoding="utf-8")


def write_template_tex(report: EvaluationReport, relpath: str, content: str) -> str:
    from .keywords_service import normalize_tabular_blank_lines

    root = ensure_editable_latex(report)
    target = _safe_tex_path(root, relpath)
    target.write_text(normalize_tabular_blank_lines(content), encoding="utf-8")
    return target.relative_to(root).as_posix()


def read_library_template_tex(template: EvaluationLatexTemplate, relpath: str | None = None) -> tuple[str, str]:
    root = template_root_for_library(template)
    rel = relpath or "main.tex"
    target = _safe_tex_path(root, rel)
    return rel, target.read_text(encoding="utf-8")


def write_library_template_tex(
    template: EvaluationLatexTemplate,
    relpath: str,
    content: str,
    *,
    allow_system: bool = False,
) -> str:
    """写入模板库文件。内置模板默认只读；allow_system=True 时允许「保存到模板」。"""
    from .keywords_service import normalize_tabular_blank_lines
    from .template_push import write_library_tex

    paths = write_library_tex(
        template,
        relpath,
        normalize_tabular_blank_lines(content),
        allow_system=allow_system,
    )
    return paths[0] if paths else relpath


def resolve_library_asset_preview(template: EvaluationLatexTemplate, relpath: str) -> dict[str, str]:
    root = template_root_for_library(template)
    rel = (relpath or "").replace("\\", "/").lstrip("/")
    if ".." in rel.split("/"):
        raise TemplatePreviewError("非法路径")
    target = (root / rel).resolve()
    if not str(target).startswith(str(root.resolve())):
        raise TemplatePreviewError("非法路径")
    if not target.is_file():
        raise TemplatePreviewError("文件不存在")
    suffix = target.suffix.lower()
    if suffix not in PREVIEWABLE_ASSET_SUFFIXES:
        raise TemplatePreviewError("该文件类型不支持预览")
    return {
        "relpath": rel,
        "preview_type": asset_preview_type(suffix),
        "media_relpath": rel,
    }


def resolve_library_template_preview_selection(
    template: EvaluationLatexTemplate,
    selected: str,
    *,
    view: str = "",
) -> dict[str, Any]:
    selected = (selected or "main.tex").strip()
    want_asset = view == "asset" or is_previewable_asset_relpath(selected)
    if want_asset:
        try:
            asset = resolve_library_asset_preview(template, selected)
            _, tex_content = read_library_template_tex(template, "main.tex")
            return {
                "initial_view": "asset",
                "selected_file": asset["relpath"],
                "preview_asset": asset,
                "tex_content": tex_content,
                "tex_relpath": "main.tex",
            }
        except TemplatePreviewError:
            pass
    relpath, content = read_library_template_tex(template, selected)
    return {
        "initial_view": "tex",
        "selected_file": relpath,
        "preview_asset": None,
        "tex_content": content,
        "tex_relpath": relpath,
    }


def build_latex_file_tree_for_template(template: EvaluationLatexTemplate) -> list[dict[str, Any]]:
    root = template_root_for_library(template)
    nodes: list[dict[str, Any]] = []
    main = root / "main.tex"
    if main.is_file():
        nodes.append(
            {
                "name": "main.tex",
                "kind": "file",
                "relpath": "main.tex",
            }
        )
    for sub in ("chapters", "appendix", "images"):
        subdir = root / sub
        if subdir.is_dir():
            node = _build_dir_node(root, subdir, report=None)
            if node:
                nodes.append(node)
    return nodes
