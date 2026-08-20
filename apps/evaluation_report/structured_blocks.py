"""Parse / serialize chapter .tex into structured blocks for non-LaTeX editing."""

from __future__ import annotations

import html
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from converter.md2latex import convert_inline, escape_latex_plain, image_to_latex
from .flowchart_codec import (
    FlowModel,
    latex_flowchart_to_model,
    model_to_latex as flowchart_model_to_latex,
    model_to_preview_html as flowchart_model_to_preview_html,
)
from .tabular_codec import (
    ColSpec,
    GridCell,
    TableModel,
    latex_table_to_model,
    model_body_rows_to_latex,
    model_to_latex,
    model_to_preview_html,
)

CHAPTER_START_RE = re.compile(r"^\\chapter\{", re.M)
HEADING_RE = re.compile(
    r"^\\(chapter|section|subsection|subsubsection)\{((?:[^{}]|\{[^{}]*\})*)\}\s*$"
)
BEGIN_ENV_RE = re.compile(
    r"^\\begin\{(table|figure|enumerate|itemize|quote|tabular|longtable|"
    r"equation\*?|align\*?|gather\*?|multline\*?|tikzpicture|minipage)\}"
)
INCLUDEGRAPHICS_RE = re.compile(r"\\includegraphics(?:\[[^\]]*\])?\{([^}]+)\}")
INCLUDEGRAPHICS_FULL_RE = re.compile(r"\\includegraphics(?:\[([^\]]*)\])?\{([^}]+)\}")
CAPTION_RE = re.compile(r"\\caption\{((?:[^{}]|\{[^{}]*\})*)\}")
LABEL_RE = re.compile(r"\\label\{([^}]+)\}")
DEFAULT_IMAGE_WIDTH = r"0.85\linewidth"
ITEM_RE = re.compile(r"^\\item\s*")
LATEX_CMD_SIMPLE = re.compile(
    r"\\(textbf|textit|emph|underline|texttt)\{((?:[^{}]|\{[^{}]*\})*)\}"
)
MATH_INLINE_RE = re.compile(r"\$([^$]+)\$")
DISPLAY_MATH_LINE_RE = re.compile(
    r"^(?:\\\[(.*?)\\\]|\$\$(.*?)\$\$)\s*$",
    re.S,
)
MATH_ENVS = frozenset(
    {"equation", "equation*", "align", "align*", "gather", "gather*", "multline", "multline*"}
)
LATEX_SPECIAL = re.compile(r"\\([#$%&_{}])")
_LAYOUT_LINE_RE = re.compile(
    r"^\\("
    r"clearpage|newpage|thispagestyle|pagestyle|fancyhf|fancyhead|fancyfoot|"
    r"lhead|rhead|lfoot|rfoot|renewcommand|setlength|vspace|vspace\*|hfill|"
    r"vfill|centering|raggedright|justifying|hspace|noindent|"
    r"fancypagestyle|begin\{titlepage\}|end\{titlepage\}|"
    r"begin\{center\}|end\{center\}|begin\{minipage\}|end\{minipage\}|"
    r"heiti|songti|zihao|fontsize|bfseries|raggedleft|rule"
    r")\b"
)


@dataclass
class ContentBlock:
    id: str
    type: str  # heading | text | list | table | image | formula | raw
    level: str = ""
    title: str = ""
    text: str = ""
    items: list[str] = field(default_factory=list)
    rows: list[list[str]] = field(default_factory=list)  # legacy simple grid
    caption: str = ""
    path: str = ""
    latex: str = ""
    label: str = ""  # LaTeX \\label{...} for cross-refs
    page: str = ""  # "" | "a3landscape" — dedicated landscape A3 page
    source_input: str = ""  # e.g. files/surroundings — write back via \\input
    table_model: dict[str, Any] = field(default_factory=dict)
    preview_html: str = ""
    # formula: level = display | inline | equation | align | ...
    # formula: text = math body (no delimiters)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _as_block(block: ContentBlock | dict[str, Any]) -> ContentBlock:
    if isinstance(block, ContentBlock):
        return block
    return ContentBlock(
        id=str(block.get("id") or ""),
        type=str(block.get("type") or "raw"),
        level=str(block.get("level") or ""),
        title=str(block.get("title") or ""),
        text=str(block.get("text") or ""),
        items=list(block.get("items") or []),
        rows=[list(r) for r in (block.get("rows") or [])],
        caption=str(block.get("caption") or ""),
        path=str(block.get("path") or ""),
        latex=str(block.get("latex") or ""),
        label=str(block.get("label") or ""),
        page=str(block.get("page") or ""),
        source_input=str(block.get("source_input") or ""),
        table_model=dict(block.get("table_model") or {}),
        preview_html=str(block.get("preview_html") or ""),
    )


_INPUT_CMD_RE = re.compile(r"\\(?:input|erpTabInput)\{([^}]+)\}")
_TABREF_MARK_RE = re.compile(r"§tabref\{([^}]+)\}§")
_FIGREF_MARK_RE = re.compile(r"§figref\{([^}]+)\}§")
_REF_MARK_RE = re.compile(r"§ref\{([^}]+)\}§")


def normalize_input_rel(rel: str) -> str:
    rel = (rel or "").replace("\\", "/").strip()
    if rel.endswith(".tex"):
        rel = rel[:-4]
    return rel


def resolve_input_path(latex_root: Path, rel: str) -> Path:
    rel = normalize_input_rel(rel)
    for cand in (latex_root / f"{rel}.tex", latex_root / rel):
        if cand.is_file():
            return cand
    return latex_root / f"{rel}.tex"


def read_input_file(latex_root: Path, rel: str) -> str:
    path = resolve_input_path(latex_root, rel)
    if not path.is_file():
        return ""
    text = path.read_text(encoding="utf-8")
    # drop leading personalization comment banner
    lines = text.splitlines()
    while lines and (not lines[0].strip() or lines[0].lstrip().startswith("%")):
        lines.pop(0)
    return "\n".join(lines).strip()


def write_input_file(latex_root: Path, rel: str, body: str, *, command: str = "") -> None:
    rel = normalize_input_rel(rel)
    path = resolve_input_path(latex_root, rel)
    path.parent.mkdir(parents=True, exist_ok=True)
    cmd = command or Path(rel).name
    header = f"% personalized \\{cmd} — do not edit structure in library template\n"
    # Keep a trailing ``%`` so the file's final newline is commented out when
    # ``\input`` is used inside ``tabular`` (avoids a phantom empty last row).
    content = (body or "").rstrip()
    if content and not content.endswith("%"):
        content += "%"
    path.write_text(header + content + "\n", encoding="utf-8")


def _ref_mark_to_latex(kind: str, label: str) -> str:
    lab = (label or "").strip()
    if not lab:
        return ""
    if kind == "tab":
        return f"表~\\ref{{{lab}}}"
    if kind == "fig":
        return f"图~\\ref{{{lab}}}"
    if lab.startswith("tab:") or lab.startswith("table:"):
        return f"表~\\ref{{{lab}}}"
    if lab.startswith("fig:") or lab.startswith("figure:"):
        return f"图~\\ref{{{lab}}}"
    return f"\\ref{{{lab}}}"


def latex_to_plain(text: str) -> str:
    if not text:
        return ""
    s = text
    s = re.sub(r"(?:表|图)?\s*~?\s*\\ref\{[^}]+\}", "", s)
    s = s.replace(r"~", " ")
    s = LATEX_CMD_SIMPLE.sub(r"\2", s)
    s = MATH_INLINE_RE.sub(r"\1", s)
    s = LATEX_SPECIAL.sub(r"\1", s)
    s = s.replace(r"\,", " ").replace(r"\;", " ").replace(r"\ ", " ")
    s = re.sub(r"\\[a-zA-Z]+\*?", "", s)
    s = s.replace("{", "").replace("}", "")
    s = re.sub(r"[ \t]+", " ", s)
    return s.strip()


_KW_MARK_RE = re.compile(r"§kw\{([a-zA-Z]+)\}§(.*?)§/kw§", re.S)


def kw_marks_to_commands(text: str) -> str:
    """``§kw{cmd}§…§/kw§`` → ``\\cmd{}``（保存回 LaTeX）。"""
    if not text or "§kw{" not in text:
        return text or ""
    return _KW_MARK_RE.sub(lambda m: f"\\{m.group(1)}{{}}", text)


def strip_kw_marks(text: str) -> str:
    """去掉标记，只保留展示文案。"""
    if not text or "§kw{" not in text:
        return text or ""
    return _KW_MARK_RE.sub(lambda m: m.group(2), text)


def _expand_macros_local(
    text: str,
    macros: dict[str, str] | None,
    *,
    mark: bool = False,
) -> str:
    """Expand ``\\cmd`` using macros.

    When ``mark=True``, wrap each top-level expansion as
    ``§kw{cmd}§value§/kw§`` so the editor can highlight personalized fields.
    Nested macros inside a value are expanded to plain text inside the mark.
    """
    if not text or not macros:
        return text or ""

    def repl(m: re.Match[str]) -> str:
        name = m.group(1)
        if name not in macros:
            return m.group(0)
        value = macros[name]
        # Expand nested macros inside the value without nested marks.
        value = _expand_macros_local(value, macros, mark=False)
        if not mark:
            return value
        safe = (value or "").replace("§", "")
        return f"§kw{{{name}}}§{safe}§/kw§"

    # TeX csnames are ASCII letters only. Do NOT use \\b: in Python 3, CJK
    # counts as a word char, so ``\\equipment项目`` would fail to expand and
    # later be stripped to ``项目``.
    out = text
    for _ in range(6):
        nxt = re.sub(r"\\([a-zA-Z]+)(?![a-zA-Z])", repl, out)
        if nxt == out:
            break
        out = nxt
    return out


def latex_to_editable_text(text: str, macros: dict[str, str] | None = None) -> str:
    """Convert LaTeX prose to editable rich text (**, *, __, zihao, $math$, refs)."""
    if not text:
        return ""
    # Mark personalized macros so the editor can highlight them.
    text = _expand_macros_local(text, macros, mark=True)

    maths: list[str] = []

    def _save(m: re.Match[str]) -> str:
        maths.append(m.group(0))
        return f"\x00M{len(maths) - 1}\x00"

    s = text
    # Preserve cross-refs before stripping TeX commands (otherwise only the label id remains).
    s = re.sub(r"表\s*~?\s*\\ref\{([^}]+)\}", r"§tabref{\1}§", s)
    s = re.sub(r"图\s*~?\s*\\ref\{([^}]+)\}", r"§figref{\1}§", s)
    s = re.sub(r"\\ref\{([^}]+)\}", r"§ref{\1}§", s)
    s = s.replace(r"~", " ")
    s = re.sub(r"\$[^$]+\$", _save, s)
    # {\zihao{n} content}
    s = re.sub(
        r"\{\\zihao\{([^}]+)\}\s*((?:[^{}]|\{[^{}]*\})*)\}",
        lambda m: f"§zihao{{{m.group(1)}}}§{m.group(2)}§/zihao§",
        s,
    )
    s = re.sub(r"\\zihao\{([^}]+)\}", lambda m: f"§zihao{{{m.group(1)}}}§", s)

    def _fmt(m: re.Match[str]) -> str:
        cmd, inner = m.group(1), m.group(2)
        if cmd == "textbf":
            return f"**{inner}**"
        if cmd in ("textit", "emph"):
            return f"*{inner}*"
        if cmd == "underline":
            return f"__{inner}__"
        return inner

    s = LATEX_CMD_SIMPLE.sub(_fmt, s)
    s = LATEX_SPECIAL.sub(r"\1", s)
    s = s.replace(r"\,", " ").replace(r"\;", " ").replace(r"\ ", " ")
    s = re.sub(r"\\[a-zA-Z]+\*?", "", s)
    # Keep §ref{...}§ braces; strip other braces
    placeholders: list[str] = []

    def _hold_ref(m: re.Match[str]) -> str:
        placeholders.append(m.group(0))
        return f"\x00R{len(placeholders) - 1}\x00"

    s = re.sub(r"§(?:tabref|figref|ref)\{[^}]+\}§", _hold_ref, s)
    s = re.sub(r"§kw\{[a-zA-Z]+\}§.*?§/kw§", _hold_ref, s, flags=re.S)
    s = s.replace("{", "").replace("}", "")
    for i, p in enumerate(placeholders):
        s = s.replace(f"\x00R{i}\x00", p)
    for i, m in enumerate(maths):
        s = s.replace(f"\x00M{i}\x00", m)
    # close dangling zihao open markers without content end
    s = re.sub(r"§zihao\{([^}]+)\}§(?!.*§/zihao§)", "", s)
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def rich_to_latex_fragment(text: str, macros: dict[str, str] | None = None) -> str:
    """Rich editable markers → LaTeX fragment (keeps ``$math$`` and soft refs)."""
    if not text:
        return ""
    from converter.md2latex import apply_variable_commands, escape_latex_plain_preserve_figure_refs

    maths: list[str] = []
    refs: list[str] = []

    def _save(m: re.Match[str]) -> str:
        maths.append(m.group(0))
        return f"\x00M{len(maths) - 1}\x00"

    def _save_ref(m: re.Match[str]) -> str:
        kind = "tab" if m.group(0).startswith("§tabref") else (
            "fig" if m.group(0).startswith("§figref") else "ref"
        )
        refs.append(_ref_mark_to_latex(kind, m.group(1)))
        return f"\x00R{len(refs) - 1}\x00"

    s = re.sub(r"\$[^$]+\$", _save, text)
    s = _TABREF_MARK_RE.sub(_save_ref, s)
    s = _FIGREF_MARK_RE.sub(_save_ref, s)
    s = _REF_MARK_RE.sub(_save_ref, s)
    # Personalized keyword marks → \\cmd{} before other transforms.
    s = kw_marks_to_commands(s)
    s = re.sub(
        r"§zihao\{([^}]+)\}§(.*?)§/zihao§",
        lambda m: "{\\zihao{" + m.group(1) + "} " + m.group(2) + "}",
        s,
        flags=re.S,
    )
    s = re.sub(r"\*\*(.+?)\*\*", lambda m: "\\textbf{" + m.group(1) + "}", s)
    s = re.sub(r"__(.+?)__", lambda m: "\\underline{" + m.group(1) + "}", s)
    s = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", lambda m: "\\textit{" + m.group(1) + "}", s)
    s = escape_latex_plain_preserve_figure_refs(s)
    for i, m in enumerate(maths):
        s = s.replace(f"\x00M{i}\x00", m)
    for i, r in enumerate(refs):
        s = s.replace(f"\x00R{i}\x00", r)
    if macros:
        value_to_cmd = {
            v: f"\\{k}"
            for k, v in macros.items()
            if v and len(v.strip()) >= 2 and not v.strip().startswith("\\")
        }
        s = apply_variable_commands(s, value_to_cmd)
    return s


def plain_to_latex_paragraph(text: str, macros: dict[str, str] | None = None) -> str:
    """Turn editable rich text into LaTeX paragraphs.

    Blank lines start a new paragraph. Lines that look like enumerated items
    ``（1）…`` also start a new paragraph even after a soft line-break, so
    pasted or Enter-separated lists keep their structure.
    """
    lines = [ln.strip() for ln in text.replace("\r\n", "\n").split("\n")]
    paras: list[str] = []
    buf: list[str] = []
    item_start = re.compile(
        r"^[（(]\s*\d+\s*[）)]|"
        r"^[（(]\s*[一二三四五六七八九十百千零〇两]+\s*[）)]|"
        r"^[•·●▪]|"
        r"^第[一二三四五六七八九十百千零〇两\d]+[条项条款]"
    )

    def flush() -> None:
        nonlocal buf
        if not buf:
            return
        # Soft-wrap join: empty for CJK; space only between ASCII alnum tokens.
        parts: list[str] = []
        for ln in buf:
            if not parts:
                parts.append(ln)
                continue
            prev = parts[-1]
            if (
                prev
                and ln
                and prev[-1].isascii()
                and prev[-1].isalnum()
                and ln[0].isascii()
                and ln[0].isalnum()
            ):
                parts.append(" " + ln)
            else:
                parts.append(ln)
        paras.append(rich_to_latex_fragment("".join(parts), macros=macros))
        buf = []

    for ln in lines:
        if not ln:
            flush()
            continue
        if item_start.match(ln) and buf:
            flush()
        buf.append(ln)
    flush()
    return "\n\n".join(paras)


def formula_body_from_chunk(chunk: str) -> tuple[str, str]:
    """Return (mode, body) from a formula latex chunk."""
    chunk = chunk.strip()
    m = re.match(
        r"\\begin\{(equation\*?|align\*?|gather\*?|multline\*)\}(.*)\\end\{\1\}",
        chunk,
        re.S,
    )
    if m:
        return m.group(1), m.group(2).strip()
    m = re.match(r"\\\[(.*)\\\]", chunk, re.S)
    if m:
        return "display", m.group(1).strip()
    m = re.match(r"\$\$(.*)\$\$", chunk, re.S)
    if m:
        return "display", m.group(1).strip()
    m = re.match(r"\$(.*)\$", chunk, re.S)
    if m:
        return "inline", m.group(1).strip()
    return "display", chunk


def formula_to_latex(body: str, mode: str = "display") -> str:
    body = (body or "").strip()
    mode = (mode or "display").strip()
    if mode == "inline":
        return f"${body}$"
    if mode in MATH_ENVS:
        return f"\\begin{{{mode}}}\n{body}\n\\end{{{mode}}}"
    return f"\\[\n{body}\n\\]"


def _extract_formula_block(chunk: str, block_id: str, mode: str = "") -> ContentBlock:
    detected, body = formula_body_from_chunk(chunk)
    level = mode or detected or "display"
    return ContentBlock(
        id=block_id,
        type="formula",
        level=level,
        text=body,
        latex=chunk.strip(),
    )


def _find_env_end(lines: list[str], start: int, env: str) -> int:
    begin = f"\\begin{{{env}}}"
    end = f"\\end{{{env}}}"
    depth = 0
    for i in range(start, len(lines)):
        if begin in lines[i]:
            depth += 1
        if end in lines[i]:
            depth -= 1
            if depth <= 0:
                return i
    return len(lines) - 1


def _split_tabular_rows(tabular_body: str) -> list[list[str]]:
    model = latex_table_to_model(
        "\\begin{tabular}{|l|}\\hline\n" + tabular_body + "\n\\end{tabular}"
    )
    # fallback unused
    return [[c.text for c in row if not c.skip] for row in model.cells]


def _extract_table_block(
    chunk: str,
    block_id: str,
    *,
    latex_root: Path | None = None,
    macros: dict[str, str] | None = None,
) -> ContentBlock:
    cap_m = CAPTION_RE.search(chunk)
    caption = latex_to_plain(cap_m.group(1)) if cap_m else ""
    lab_m = LABEL_RE.search(chunk)
    label = (lab_m.group(1) if lab_m else "").strip()

    body_input = ""
    header_rows = 0
    work = chunk
    m = _INPUT_CMD_RE.search(chunk)
    if m and latex_root is not None:
        rel = normalize_input_rel(m.group(1))
        if rel.startswith("files/"):
            body_input = rel
            header_rows = len(re.findall(r"\\\\", chunk[: m.start()]))
            file_body = read_input_file(latex_root, rel)
            work = chunk[: m.start()] + file_body + chunk[m.end() :]

    # Expand personalized macros so editor shows real values (表1.3-1 等),
    # and mark them for background highlighting in the preview.
    if macros:
        work = _expand_macros_local(work, macros, mark=True)

    model = latex_table_to_model(work, caption)
    if label and not getattr(model, "label", ""):
        model.label = label
    if body_input:
        model.body_input = body_input
        model.header_rows = header_rows
    simple_rows = []
    for row in model.cells:
        simple_rows.append([c.text if not c.skip else "" for c in row])
    return ContentBlock(
        id=block_id,
        type="table",
        caption=model.caption,
        label=label or getattr(model, "label", "") or "",
        rows=simple_rows or [["", ""]],
        latex=chunk.strip(),
        source_input=body_input,
        table_model=model.to_dict(),
        preview_html=model_to_preview_html(model),
    )


def _parse_includegraphics(chunk: str) -> tuple[str, str]:
    """Return (path, width) from the first ``\\includegraphics`` in chunk."""
    m = INCLUDEGRAPHICS_FULL_RE.search(chunk or "")
    if not m:
        return "", ""
    opts = (m.group(1) or "").strip()
    path = (m.group(2) or "").replace("\\", "/").strip()
    width = ""
    wm = re.search(r"width\s*=\s*([^,\]]+)", opts, re.I)
    if wm:
        width = wm.group(1).strip()
    return path, width


def _extract_figure_block(chunk: str, block_id: str, *, page: str = "") -> ContentBlock:
    cap_m = CAPTION_RE.search(chunk)
    caption = latex_to_plain(cap_m.group(1)) if cap_m else ""
    lab_m = LABEL_RE.search(chunk)
    label = (lab_m.group(1) if lab_m else "").strip()
    if r"\begin{tikzpicture}" in chunk or "flowbox" in chunk:
        return _extract_flowchart_block(chunk, block_id, caption, label=label)
    path, width = _parse_includegraphics(chunk)
    return ContentBlock(
        id=block_id,
        type="image",
        caption=caption,
        label=label,
        page=(page or "").strip(),
        path=path,
        level=width or DEFAULT_IMAGE_WIDTH,
        latex=chunk.strip(),
    )


def _extract_flowchart_block(
    chunk: str, block_id: str, caption: str = "", *, label: str = ""
) -> ContentBlock:
    """Extract TikZ flowbox flowchart into structured steps + rich preview."""
    model = latex_flowchart_to_model(chunk, caption, label=label)
    steps = [s.text for s in model.steps if s.text]
    return ContentBlock(
        id=block_id,
        type="flowchart",
        caption=model.caption,
        label=model.label,
        items=steps,
        latex=chunk.strip(),
        table_model=model.to_dict(),
        preview_html=flowchart_model_to_preview_html(model),
        text="\n".join(steps),
    )


def _extract_list_as_text_blocks(
    chunk: str,
    *,
    env: str,
    next_id,
    macros: dict[str, str] | None = None,
    source_input: str = "",
) -> list[ContentBlock]:
    """Turn enumerate/itemize into plain text paragraphs (no list environment).

    Ordered items become ``（1）…`` lines so users can insert figures/tables
    between them without nesting inside ``enumerate``.
    """
    items: list[str] = []
    for line in chunk.splitlines():
        if ITEM_RE.match(line.strip()):
            items.append(latex_to_editable_text(ITEM_RE.sub("", line.strip()), macros=macros))
    if not items:
        items = [""]
    ordered = env != "itemize"
    paras: list[str] = []
    for i, item in enumerate(items):
        body = (item or "").strip()
        if ordered:
            paras.append(f"（{i + 1}）{body}" if body else f"（{i + 1}）")
        else:
            paras.append(f"• {body}" if body else "•")
    return [
        ContentBlock(
            id=next_id(),
            type="text",
            text="\n\n".join(paras),
            source_input=source_input,
        )
    ]


def _is_layout_or_macro_line(line: str) -> bool:
    s = line.strip()
    if not s or s.startswith("%"):
        return True
    # A3 wrappers are handled by the dedicated parser — do not treat as silent raw.
    if s.startswith(r"\BeginAThreeLandscapePage") or s.startswith(r"\EndAThreeLandscapePage"):
        return False
    if _LAYOUT_LINE_RE.match(s):
        return True
    # Keep prose that contains inline math even if it has TeX commands inside $...$
    if "$" in s and re.search(r"[\u4e00-\u9fff]", s):
        return False
    if "\\" in s:
        cjk = len(re.findall(r"[\u4e00-\u9fff]", s))
        # Any Chinese means editable prose (e.g. 「（1）名称：\projectnamefull」).
        # Old threshold cjk<8 hid short keyword lines as invisible raw blocks.
        if cjk > 0:
            return False
        if not INCLUDEGRAPHICS_RE.search(s):
            return True
    return False


def split_chapter_preamble_body(content: str) -> tuple[str, str]:
    m = CHAPTER_START_RE.search(content)
    if m:
        return content[: m.start()], content[m.start() :]
    m2 = re.search(r"^\\section\{", content, re.M)
    if m2:
        return content[: m2.start()], content[m2.start() :]
    lines = content.splitlines(keepends=True)
    i = 0
    while i < len(lines) and (not lines[i].strip() or lines[i].lstrip().startswith("%")):
        i += 1
    return "".join(lines[:i]), "".join(lines[i:])


def parse_chapter_blocks(
    content: str,
    *,
    latex_root: Path | None = None,
    macros: dict[str, str] | None = None,
) -> tuple[str, list[ContentBlock]]:
    preamble, body = split_chapter_preamble_body(content)
    if not body.strip():
        return preamble, []

    body_stripped = body.strip()
    for env in ("titlepage", "center"):
        pattern = r"^\\begin\{" + env + r"\}(.*)\\end\{" + env + r"\}\s*$"
        m = re.match(pattern, body_stripped, re.S)
        if m:
            body_stripped = m.group(1).strip()
            break

    lines = body_stripped.splitlines()
    blocks: list[ContentBlock] = []
    i = 0
    text_buf: list[str] = []
    raw_buf: list[str] = []
    counter = 0
    last_files_input: str | None = None

    def flush_text(*, source_input: str = "") -> None:
        nonlocal counter
        joined = "\n".join(text_buf).strip()
        text_buf.clear()
        if not joined:
            return
        # Keep blank-line paragraph breaks; join soft line-wraps inside a para.
        paras: list[str] = []
        for part in re.split(r"\n\s*\n+", joined):
            part = part.strip()
            if not part:
                continue
            compacted = re.sub(r"\s*\n\s*", "", part)
            paras.append(latex_to_editable_text(compacted, macros=macros))
        if not paras:
            return
        counter += 1
        blocks.append(
            ContentBlock(
                id=f"b{counter}",
                type="text",
                text="\n\n".join(paras),
                latex=joined,
                source_input=source_input,
            )
        )

    def flush_raw() -> None:
        nonlocal counter
        joined = "\n".join(raw_buf).strip()
        raw_buf.clear()
        if not joined:
            return
        counter += 1
        blocks.append(ContentBlock(id=f"b{counter}", type="raw", latex=joined))

    def next_id() -> str:
        nonlocal counter
        counter += 1
        return f"b{counter}"

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        hm = HEADING_RE.match(stripped)
        if hm:
            flush_raw()
            flush_text()
            counter += 1
            blocks.append(
                ContentBlock(
                    id=f"b{counter}",
                    type="heading",
                    level=hm.group(1),
                    title=latex_to_plain(hm.group(2)),
                    latex=stripped,
                )
            )
            i += 1
            continue

        # Personalized bodies via \\input{files/...}
        inm = _INPUT_CMD_RE.match(stripped)
        if inm and latex_root is not None:
            rel = normalize_input_rel(inm.group(1))
            if rel.startswith("files/"):
                # Ignore accidental consecutive duplicate \\input{same file}
                if last_files_input == rel:
                    i += 1
                    continue
                last_files_input = rel
                flush_raw()
                flush_text()
                file_body = read_input_file(latex_root, rel)
                if file_body.strip():
                    _, nested = parse_chapter_blocks(
                        "%\n" + file_body + "\n",
                        latex_root=latex_root,
                        macros=macros,
                    )
                    for nb in nested:
                        nb.id = next_id()
                        nb.source_input = rel
                        blocks.append(nb)
                else:
                    blocks.append(
                        ContentBlock(
                            id=next_id(),
                            type="text",
                            text="",
                            source_input=rel,
                            latex=stripped,
                        )
                    )
                i += 1
                continue

        # Other chapter material breaks consecutive-input dedupe
        if stripped and not stripped.startswith("%"):
            last_files_input = None

        if stripped.startswith(r"\BeginAThreeLandscapePage"):
            flush_raw()
            flush_text()
            # Consume one or more figures until \EndAThreeLandscapePage so a
            # consecutive A3 run round-trips as separate image blocks.
            j = i + 1
            page_mode = "a3landscape"
            consumed_any = False
            while j < len(lines):
                while j < len(lines) and not lines[j].strip():
                    j += 1
                if j >= len(lines):
                    break
                if lines[j].strip().startswith(r"\EndAThreeLandscapePage"):
                    j += 1
                    break
                # Optional \clearpage between consecutive A3 figures
                if lines[j].strip() in {r"\clearpage", r"\newpage"}:
                    j += 1
                    continue
                env_m2 = BEGIN_ENV_RE.match(lines[j].strip())
                if not env_m2:
                    break
                env2 = env_m2.group(1)
                end_i = _find_env_end(lines, j, env2)
                chunk = "\n".join(lines[j : end_i + 1])
                counter += 1
                bid = f"b{counter}"
                if env2 == "figure":
                    blocks.append(_extract_figure_block(chunk, bid, page=page_mode))
                    consumed_any = True
                else:
                    blocks.append(
                        ContentBlock(id=bid, type="raw", latex="\n".join(lines[i : end_i + 1]).strip())
                    )
                    consumed_any = True
                j = end_i + 1
            if consumed_any:
                i = j
                continue
            # orphan begin — keep as raw
            counter += 1
            blocks.append(
                ContentBlock(id=f"b{counter}", type="raw", latex=stripped)
            )
            i += 1
            continue

        if stripped.startswith(r"\EndAThreeLandscapePage"):
            # orphan end (begin already consumed) — skip
            i += 1
            continue

        env_m = BEGIN_ENV_RE.match(stripped)
        if env_m:
            flush_raw()
            flush_text()
            env = env_m.group(1)
            end_i = _find_env_end(lines, i, env)
            chunk = "\n".join(lines[i : end_i + 1])
            counter += 1
            bid = f"b{counter}"
            if env in ("table", "tabular", "longtable"):
                blocks.append(
                    _extract_table_block(chunk, bid, latex_root=latex_root, macros=macros)
                )
            elif env == "figure":
                blocks.append(_extract_figure_block(chunk, bid))
            elif env == "tikzpicture":
                blocks.append(_extract_flowchart_block(chunk, bid))
            elif env == "minipage":
                inner = re.sub(r"\\begin\{minipage\}(\[[^\]]*\])?\{[^}]*\}|\\end\{minipage\}", "", chunk)
                blocks.append(
                    ContentBlock(
                        id=bid,
                        type="minipage",
                        text=latex_to_editable_text(inner, macros=macros),
                        latex=chunk.strip(),
                        level="0.45\\textwidth",
                    )
                )
            elif env in ("enumerate", "itemize"):
                counter -= 1  # bid unused; helper allocates via next_id
                blocks.extend(
                    _extract_list_as_text_blocks(
                        chunk, env=env, next_id=next_id, macros=macros
                    )
                )
            elif env in MATH_ENVS:
                blocks.append(_extract_formula_block(chunk, bid, env))
            elif env == "quote":
                inner = re.sub(r"\\begin\{quote\}|\\end\{quote\}", "", chunk)
                blocks.append(
                    ContentBlock(
                        id=bid,
                        type="text",
                        text=latex_to_editable_text(inner, macros=macros),
                        latex=chunk.strip(),
                    )
                )
            else:
                blocks.append(ContentBlock(id=bid, type="raw", latex=chunk.strip()))
            i = end_i + 1
            continue

        if stripped in (r"\clearpage", r"\newpage"):
            flush_raw()
            flush_text()
            counter += 1
            blocks.append(
                ContentBlock(
                    id=f"b{counter}",
                    type="pagebreak",
                    level="clearpage" if "clear" in stripped else "newpage",
                    latex=stripped,
                )
            )
            i += 1
            continue

        # Display math \[ ... \] or $$ ... $$ (possibly multi-line)
        if stripped.startswith(r"\[") or stripped.startswith("$$"):
            flush_raw()
            flush_text()
            end_token = r"\]" if stripped.startswith(r"\[") else "$$"
            end_i = i
            while end_i < len(lines) and end_token not in lines[end_i]:
                end_i += 1
            if end_i >= len(lines):
                end_i = i
            chunk = "\n".join(lines[i : end_i + 1])
            counter += 1
            blocks.append(_extract_formula_block(chunk, f"b{counter}", "display"))
            i = end_i + 1
            continue

        # Standalone inline-as-display: line is only $...$
        if re.fullmatch(r"\$[^$]+\$", stripped):
            flush_raw()
            flush_text()
            counter += 1
            blocks.append(_extract_formula_block(stripped, f"b{counter}", "display"))
            i += 1
            continue

        if INCLUDEGRAPHICS_RE.search(stripped) and not stripped.startswith("%"):
            flush_raw()
            flush_text()
            counter += 1
            path, width = _parse_includegraphics(stripped)
            blocks.append(
                ContentBlock(
                    id=f"b{counter}",
                    type="image",
                    path=path,
                    level=width or DEFAULT_IMAGE_WIDTH,
                    latex=stripped,
                )
            )
            i += 1
            continue

        # Swallow \tikzset{...} (and similar) as one raw block so style defs
        # don't leak into editable text before a flowchart figure.
        if stripped.startswith(r"\tikzset") or stripped.startswith(r"\tikzstyle"):
            flush_raw()
            flush_text()
            chunk_lines: list[str] = []
            depth = 0
            started = False
            j = i
            while j < len(lines):
                chunk_lines.append(lines[j])
                for ch in lines[j]:
                    if ch == "{":
                        depth += 1
                        started = True
                    elif ch == "}":
                        depth -= 1
                j += 1
                if started and depth <= 0:
                    break
            counter += 1
            blocks.append(
                ContentBlock(
                    id=f"b{counter}",
                    type="raw",
                    latex="\n".join(chunk_lines).strip(),
                )
            )
            i = j
            continue

        if stripped.startswith(r"\rule"):
            flush_raw()
            flush_text()
            counter += 1
            blocks.append(
                ContentBlock(
                    id=f"b{counter}",
                    type="image",
                    path="",
                    caption="",
                    latex=stripped,
                )
            )
            i += 1
            continue

        if _is_layout_or_macro_line(line):
            # Blank lines: keep as paragraph breaks inside prose; otherwise park in raw.
            if not stripped:
                if text_buf:
                    text_buf.append("")
                else:
                    raw_buf.append(line)
                i += 1
                continue
            if stripped.startswith("%"):
                if not text_buf:
                    raw_buf.append(line)
                i += 1
                continue
            if text_buf:
                flush_text()
            raw_buf.append(line)
            i += 1
            continue

        if raw_buf:
            flush_raw()
        text_buf.append(line)
        i += 1

    flush_raw()
    flush_text()
    return preamble, _merge_adjacent_text(blocks)


def _merge_adjacent_text(blocks: list[ContentBlock]) -> list[ContentBlock]:
    """Merge text blocks even when only raw/comment blocks sit between them."""
    out: list[ContentBlock] = []
    for b in blocks:
        if b.type == "raw":
            # Keep raw silently for round-trip; UI will hide it
            out.append(b)
            continue
        if b.type == "text":
            idx = len(out) - 1
            while idx >= 0 and out[idx].type == "raw":
                idx -= 1
            if (
                idx >= 0
                and out[idx].type == "text"
                and (out[idx].source_input or "") == (b.source_input or "")
            ):
                out[idx].text = (out[idx].text.rstrip() + "\n\n" + b.text.lstrip()).strip()
                out[idx].latex = (out[idx].latex.rstrip() + "\n\n" + b.latex.lstrip()).strip()
                continue
        out.append(b)
    return out


def visible_blocks(blocks: list[ContentBlock]) -> list[ContentBlock]:
    """Blocks shown in the document editor (raw layout kept only for save merge)."""
    return [b for b in blocks if b.type != "raw"]


def merge_visible_into_all(
    all_blocks: list[ContentBlock],
    visible: list[ContentBlock | dict[str, Any]],
) -> list[ContentBlock]:
    """Replace non-raw blocks in order with edited visible blocks; keep raw in place."""
    edited = [_as_block(b) for b in visible]
    ei = 0
    out: list[ContentBlock] = []
    for b in all_blocks:
        if b.type == "raw":
            out.append(b)
            continue
        if ei < len(edited):
            out.append(edited[ei])
            ei += 1
        else:
            out.append(b)
    while ei < len(edited):
        out.append(edited[ei])
        ei += 1
    return out


def _rows_to_html(rows: list[list[str]]) -> str:
    parts = ["<table>"]
    for row in rows:
        parts.append("<tr>")
        for cell in row:
            parts.append(f"<td>{html.escape(cell)}</td>")
        parts.append("</tr>")
    parts.append("</table>")
    return "".join(parts)


def _patch_or_insert_caption(tex: str, caption: str, *, position: str = "below") -> str:
    """Ensure a single ``\\caption{...}`` in a table/figure env at the right place."""
    tex = tex.strip()
    # Drop existing captions so we can place exactly one at the correct position.
    tex = re.sub(r"\\caption\{((?:[^{}]|\{[^{}]*\})*)\}\s*", "", tex)
    if not caption.strip():
        return tex

    cap_line = f"\\caption{{{convert_inline(caption)}}}"

    if position == "above":
        # after \centering / begin{table}… before \begin{tabular/longtable}
        m = re.search(r"\\begin\{(?:tabular|longtable)\}", tex)
        if m:
            return tex[: m.start()] + cap_line + "\n" + tex[m.start() :]
        return tex
    # below: after includegraphics / tikzpicture, before \end{figure}
    m = re.search(r"\\end\{figure\}", tex)
    if m:
        return tex[: m.start()].rstrip() + "\n" + cap_line + "\n" + tex[m.start() :]
    return tex + "\n" + cap_line


def _patch_or_insert_label(tex: str, label: str) -> str:
    tex = re.sub(r"\\label\{[^}]+\}\s*", "", tex)
    label = (label or "").strip()
    if not label:
        return tex
    lab = f"\\label{{{label}}}"
    if r"\caption{" in tex:
        return re.sub(
            r"(\\caption\{(?:[^{}]|\{[^{}]*\})*\})",
            rf"\1\n{lab}",
            tex,
            count=1,
        )
    return re.sub(r"(\\end\{(?:table|figure)\})", rf"{lab}\n\1", tex, count=1)


def block_to_latex(
    block: ContentBlock | dict[str, Any],
    *,
    latex_root: Path | None = None,
    macros: dict[str, str] | None = None,
    a3_role: str = "only",
) -> str:
    block = _as_block(block)

    if block.type == "heading":
        level = block.level if block.level in {
            "chapter", "section", "subsection", "subsubsection"
        } else "section"
        return f"\\{level}{{{rich_to_latex_fragment(block.title, macros=macros)}}}"

    if block.type == "text":
        return plain_to_latex_paragraph(block.text, macros=macros)

    if block.type == "list":
        # Legacy list blocks: emit plain paragraphs, never enumerate/itemize.
        ordered = block.level != "itemize"
        paras: list[str] = []
        items = block.items or [block.text or ""]
        for i, item in enumerate(items):
            body = (item or "").strip()
            if not body and not paras:
                continue
            if ordered:
                prefix = f"（{i + 1}）"
            else:
                prefix = "• "
            paras.append(
                plain_to_latex_paragraph(prefix + body, macros=macros)
                if body or ordered
                else ""
            )
        return "\n\n".join(p for p in paras if p)

    if block.type == "table":
        label = (block.label or "").strip()
        if block.table_model:
            model = TableModel.from_dict(block.table_model)
            if block.caption and not model.caption:
                model.caption = block.caption
            elif block.caption:
                model.caption = block.caption
            if label:
                model.label = label
            if macros:
                from converter.md2latex import apply_variable_commands

                value_to_cmd = {
                    v: f"\\{k}"
                    for k, v in macros.items()
                    if v and len(v.strip()) >= 2 and not v.strip().startswith("\\")
                }
                # Also map plain-text forms of multiline remarks (editor uses newlines).
                for k, v in list(macros.items()):
                    if not v or "\\\\" not in v:
                        continue
                    plain = v.replace(r"\\", "\n")
                    if plain.strip() and plain not in value_to_cmd:
                        value_to_cmd[plain] = f"\\{k}"
                for row in model.cells:
                    for cell in row:
                        if cell.skip or not (cell.text or "").strip():
                            continue
                        cell.text = kw_marks_to_commands(cell.text)
                        cell.text = apply_variable_commands(cell.text, value_to_cmd)
                        # Prefer bare macro when value fully matches a command.
                        t = (cell.text or "").strip()
                        if t.startswith("\\makecell"):
                            continue
                        for k, v in macros.items():
                            if not v:
                                continue
                            bare = f"\\{k}"
                            if (
                                t == bare
                                or t == f"{bare}{{}}"
                                or t == v.strip()
                                or t == v.replace(r"\\", "\n").strip()
                            ):
                                # 含 \\\\ 的备注宏必须包进 makecell，否则会拆断 tabular 行
                                if k == "sourceremark" or r"\\" in v:
                                    cell.text = f"\\makecell[l]{{{bare}}}"
                                else:
                                    cell.text = bare
                                break
            if model.body_input and latex_root is not None:
                write_input_file(
                    latex_root,
                    model.body_input,
                    model_body_rows_to_latex(model),
                    command=Path(normalize_input_rel(model.body_input)).name,
                )
            return model_to_latex(model)
        if block.latex and (r"\begin{tabular}" in block.latex or r"\begin{longtable}" in block.latex):
            out = _patch_or_insert_caption(block.latex, block.caption, position="above")
            return _patch_or_insert_label(out, label)
        model = TableModel(
            cols=[ColSpec(kind="c") for _ in range(max(len(r) for r in (block.rows or [["", ""]])))],
            cells=[
                [GridCell(text=c) for c in row]
                for row in (block.rows or [["", ""]])
            ],
            caption=block.caption,
            label=label,
        )
        return model_to_latex(model)

    if block.type == "formula":
        if block.latex and block.latex.strip().startswith("\\"):
            # Prefer regenerating from body so edits stick
            return formula_to_latex(block.text, block.level or "display")
        return formula_to_latex(block.text, block.level or "display")

    if block.type == "pagebreak":
        return "\\clearpage" if block.level == "clearpage" else "\\newpage"

    if block.type == "minipage":
        width = block.level or r"0.45\textwidth"
        body = plain_to_latex_paragraph(block.text, macros=macros) if block.text else ""
        return f"\\begin{{minipage}}{{{width}}}\n{body}\n\\end{{minipage}}"

    if block.type == "flowchart":
        if block.latex.strip():
            return block.latex.strip()
        if block.table_model and block.table_model.get("kind") == "flowchart":
            model = FlowModel.from_dict(block.table_model)
            model.caption = block.caption or model.caption
            model.label = (block.label or model.label or "").strip()
            return flowchart_model_to_latex(model)
        model = FlowModel(
            caption=block.caption, label=(block.label or "").strip(), steps=[]
        )
        from .flowchart_codec import FlowStep

        for i, t in enumerate(block.items or []):
            model.steps.append(FlowStep(id=f"n{i+1}", text=t))
        return flowchart_model_to_latex(model)

    if block.type == "image":
        path = (block.path or "").replace("\\", "/").strip()
        width = (block.level or "").strip() or DEFAULT_IMAGE_WIDTH
        label = (block.label or "").strip()
        page = (block.page or "").strip()
        if not path:
            return block.latex.strip() or "% (empty image)"
        # Always regenerate so page-mode wrappers stay in sync with the editor.
        return image_to_latex(
            path,
            block.caption or None,
            label=label or None,
            width=width,
            page=page,
            a3_role=a3_role if page == "a3landscape" else "only",
        )

    if block.type == "raw":
        return block.latex.strip()

    return block.latex.strip() or escape_latex_plain(block.text)


def _patch_includegraphics_width(tex: str, width: str, count: int = 1) -> str:
    """Set / update ``width=...`` on the first includegraphics."""
    width = (width or DEFAULT_IMAGE_WIDTH).strip()

    def repl(m: re.Match[str]) -> str:
        opts = m.group(1) or ""
        path = m.group(2)
        if re.search(r"width\s*=", opts, re.I):
            opts = re.sub(
                r"width\s*=\s*[^,\]]+",
                lambda _m: f"width={width}",
                opts,
                count=1,
                flags=re.I,
            )
        else:
            opts = f"width={width}" + ("," + opts if opts.strip() else "")
        return f"\\includegraphics[{opts}]{{{path}}}"

    return INCLUDEGRAPHICS_FULL_RE.sub(repl, tex, count=count)


def _patch_includegraphics(tex: str, path: str, count: int = 1) -> str:
    path = path.replace("\\", "/")

    def repl(m: re.Match[str]) -> str:
        opts = m.group(0)
        # keep optional [width=...] then replace path
        if "[" in opts:
            prefix = opts[: opts.index("{") + 1]
            return f"{prefix}{path}}}"
        return f"\\includegraphics{{{path}}}"

    return INCLUDEGRAPHICS_RE.sub(repl, tex, count=count)


def serialize_chapter(
    preamble: str,
    blocks: list[ContentBlock | dict[str, Any]],
    *,
    front_matter: bool = False,
    original_content: str | None = None,
    latex_root: Path | None = None,
    macros: dict[str, str] | None = None,
) -> str:
    """Rebuild chapter. Front matter patches images/text into original file."""
    norm = [_as_block(b) for b in blocks]

    if front_matter and original_content:
        out = original_content
        for b in norm:
            if b.type == "image" and b.path.strip():
                path = b.path.replace("\\", "/").strip()
                if b.latex and b.latex.strip().startswith(r"\rule") and b.latex.strip() in out:
                    out = out.replace(
                        b.latex.strip(),
                        f"\\includegraphics[width=0.75\\textwidth]{{{path}}}",
                        1,
                    )
                elif INCLUDEGRAPHICS_RE.search(out):
                    out = _patch_includegraphics(out, path, count=1)
            elif b.type == "text" and b.latex and b.latex.strip() in out and b.text.strip():
                out = out.replace(b.latex.strip(), plain_to_latex_paragraph(b.text, macros=macros), 1)
        return out if out.endswith("\n") else out + "\n"

    body_parts: list[str] = []
    emitted_inputs: set[str] = set()
    i = 0
    while i < len(norm):
        b = norm[i]
        si = (b.source_input or "").strip()
        # Standalone \\input{files/...} groups (not table body_input rows).
        if si and not ((b.table_model or {}).get("body_input") or "").strip():
            group = [b]
            j = i + 1
            while j < len(norm) and (norm[j].source_input or "").strip() == si:
                group.append(norm[j])
                j += 1
            parts: list[str] = []
            gi = 0
            while gi < len(group):
                gb = group[gi]
                if gb.type == "image" and (gb.page or "").strip() == "a3landscape":
                    gj = gi + 1
                    while (
                        gj < len(group)
                        and group[gj].type == "image"
                        and (group[gj].page or "").strip() == "a3landscape"
                    ):
                        gj += 1
                    run = group[gi:gj]
                    n = len(run)
                    for k, ab in enumerate(run):
                        if n == 1:
                            role = "only"
                        elif k == 0:
                            role = "first"
                        elif k == n - 1:
                            role = "last"
                        else:
                            role = "middle"
                        p = block_to_latex(
                            ab, latex_root=latex_root, macros=macros, a3_role=role
                        )
                        if p and p.strip():
                            parts.append(p)
                    gi = gj
                    continue
                p = block_to_latex(gb, latex_root=latex_root, macros=macros)
                if p and p.strip():
                    parts.append(p)
                gi += 1
            inner = "\n\n".join(parts)
            norm_si = normalize_input_rel(si)
            if latex_root is not None:
                write_input_file(
                    latex_root,
                    norm_si,
                    inner,
                    command=Path(norm_si).name,
                )
            # One \\input per file path — avoid duplicated figures from repeated inputs
            if norm_si not in emitted_inputs:
                body_parts.append(f"\\input{{{norm_si}}}")
                emitted_inputs.add(norm_si)
            i = j
            continue

        # Consecutive A3 landscape images share one paper-size session so
        # intervening pages are not restored to A4 between them.
        if b.type == "image" and (b.page or "").strip() == "a3landscape":
            j = i + 1
            while (
                j < len(norm)
                and norm[j].type == "image"
                and (norm[j].page or "").strip() == "a3landscape"
            ):
                j += 1
            run = norm[i:j]
            n = len(run)
            for k, ab in enumerate(run):
                if n == 1:
                    role = "only"
                elif k == 0:
                    role = "first"
                elif k == n - 1:
                    role = "last"
                else:
                    role = "middle"
                part = block_to_latex(
                    ab, latex_root=latex_root, macros=macros, a3_role=role
                )
                if part and part.strip():
                    body_parts.append(part)
            i = j
            continue

        part = block_to_latex(b, latex_root=latex_root, macros=macros)
        if part and part.strip():
            body_parts.append(part)
        i += 1

    body = "\n\n".join(body_parts)
    pre = preamble.rstrip()
    if pre:
        return pre + "\n\n" + body + "\n"
    return body + "\n"
