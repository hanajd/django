"""Parse / preview / serialize TikZ flowbox flowcharts for the structured editor."""

from __future__ import annotations

import html
import re
from dataclasses import asdict, dataclass, field
from typing import Any

from converter.md2latex import convert_inline


@dataclass
class FlowStep:
    id: str = ""
    text: str = ""
    left: str = ""
    right: str = ""
    side: str = ""
    side_label: str = ""
    pass_label: str = ""
    # mid-branch leaving the downward arrow after this step
    mid_side: str = ""
    mid_label: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> FlowStep:
        data = data or {}
        return cls(
            id=str(data.get("id") or ""),
            text=str(data.get("text") or ""),
            left=str(data.get("left") or ""),
            right=str(data.get("right") or ""),
            side=str(data.get("side") or ""),
            side_label=str(data.get("side_label") or ""),
            pass_label=str(data.get("pass_label") or ""),
            mid_side=str(data.get("mid_side") or ""),
            mid_label=str(data.get("mid_label") or ""),
        )


@dataclass
class FlowModel:
    caption: str = ""
    label: str = ""
    steps: list[FlowStep] = field(default_factory=list)
    latex: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": "flowchart",
            "caption": self.caption,
            "label": self.label,
            "steps": [s.to_dict() for s in self.steps],
            "latex": self.latex,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> FlowModel:
        data = data or {}
        steps_raw = data.get("steps") or []
        steps = [
            FlowStep.from_dict(s if isinstance(s, dict) else {"text": str(s)}) for s in steps_raw
        ]
        if not steps and data.get("items"):
            steps = [FlowStep(id=f"n{i + 1}", text=str(t)) for i, t in enumerate(data["items"])]
        return cls(
            caption=str(data.get("caption") or ""),
            label=str(data.get("label") or ""),
            steps=steps,
            latex=str(data.get("latex") or ""),
        )


_GREEK = {
    "alpha": "α",
    "beta": "β",
    "gamma": "γ",
    "delta": "δ",
    "mu": "μ",
    "pi": "π",
    "sigma": "σ",
    "theta": "θ",
}


def _plain_node_text(inner: str) -> str:
    s = inner.replace(r"\\", "\n")
    s = re.sub(r"\$([^$]+)\$", r"\1", s)
    s = re.sub(
        r"\\([a-zA-Z]+)\*?(?:\[[^\]]*\])?(?:\{([^{}]*)\})?",
        lambda m: m.group(2) if m.group(2) is not None else _GREEK.get(m.group(1), ""),
        s,
    )
    s = s.replace("{", "").replace("}", "")
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r" *\n *", "\n", s)
    return s.strip()


def _read_brace_content(s: str, open_i: int) -> tuple[str, int]:
    if open_i >= len(s) or s[open_i] != "{":
        return "", open_i
    depth = 0
    j = open_i
    while j < len(s):
        if s[j] == "{":
            depth += 1
        elif s[j] == "}":
            depth -= 1
            if depth == 0:
                return s[open_i + 1 : j], j + 1
        j += 1
    return s[open_i + 1 :], len(s)


def _classify(opts: str) -> str:
    if "flowbox" in opts:
        return "flow"
    if "sidebox" in opts:
        return "side"
    if "lefttext" in opts:
        return "left"
    if "righttext" in opts:
        return "right"
    return "other"


def _of_ref(opts: str) -> str:
    om = re.search(r"\bof\s+([a-zA-Z0-9]+)", opts)
    return om.group(1) if om else ""


def _extract_nodes(chunk: str) -> list[dict[str, Any]]:
    nodes: list[dict[str, Any]] = []

    for m in re.finditer(r"\\node\s*\[([^\]]*)\]\s*\(([^)]+)\)\s*\{", chunk):
        opts = m.group(1)
        nid = m.group(2).strip()
        inner, _ = _read_brace_content(chunk, m.end() - 1)
        nodes.append(
            {
                "kind": _classify(opts),
                "id": nid,
                "text": _plain_node_text(inner),
                "of": _of_ref(opts),
            }
        )

    for m in re.finditer(r"\\node\s*\[([^\]]*)\]\s*\{", chunk):
        bracket = chunk.find("]", m.start())
        brace = m.end() - 1
        mid = chunk[bracket + 1 : brace] if bracket >= 0 else ""
        if "(" in mid and ")" in mid:
            continue
        opts = m.group(1)
        kind = _classify(opts)
        if kind not in {"left", "right"}:
            continue
        inner, _ = _read_brace_content(chunk, brace)
        nodes.append(
            {
                "kind": kind,
                "id": "",
                "text": _plain_node_text(inner),
                "of": _of_ref(opts),
            }
        )

    for m in re.finditer(r"(?<![\\a-zA-Z])node\s*\[([^\]]*)\]\s*\(([^)]+)\)\s*\{", chunk):
        opts = m.group(1)
        if "sidebox" not in opts and "flowbox" not in opts:
            continue
        nid = m.group(2).strip()
        if any(n["id"] == nid for n in nodes):
            continue
        inner, _ = _read_brace_content(chunk, m.end() - 1)
        nodes.append(
            {
                "kind": "side" if "sidebox" in opts else "flow",
                "id": nid,
                "text": _plain_node_text(inner),
                "of": _of_ref(opts),
            }
        )
    return nodes


def _extract_draws(chunk: str) -> list[dict[str, Any]]:
    """Parse each \\draw[...] ...; into endpoints / labels / inline side nodes."""
    draws: list[dict[str, Any]] = []
    for m in re.finditer(r"\\draw\[[^\]]*\]\s*(.*?);", chunk, re.S):
        full = m.group(0)
        body = " ".join(m.group(1).split())

        label = ""
        for nm in re.finditer(r"node\[([^\]]*)\]\s*\{", body):
            opts = nm.group(1)
            if "sidebox" in opts:
                continue
            inner, _ = _read_brace_content(body, nm.end() - 1)
            label = _plain_node_text(inner)
            break

        # TikZ uses (n1.south) — id and anchor share one pair of parentheses
        pts = re.findall(r"\(([a-zA-Z0-9]+)(?:\.[a-zA-Z]+)?\)", body)

        side_id = ""
        side_text = ""
        sm = re.search(r"node\[[^\]]*sidebox[^\]]*\]\s*\(([^)]+)\)\s*\{", full)
        if sm:
            side_id = sm.group(1).strip()
            idx = full.find("{", full.find(f"({side_id})"))
            if idx >= 0:
                inner, _ = _read_brace_content(full, idx)
                side_text = _plain_node_text(inner)

        coord = ""
        cm = re.search(r"coordinate\[[^\]]*\]\s*\(([^)]+)\)", body)
        if cm:
            coord = cm.group(1)

        src = pts[0] if pts else ""
        dst = ""
        if side_id:
            dst = side_id
        elif len(pts) >= 2:
            dst = pts[-1]
            if coord and dst == coord:
                candidates = [p for p in pts[1:] if p != coord]
                dst = candidates[0] if candidates else ""

        draws.append(
            {
                "from": src,
                "to": dst,
                "label": label,
                "side_id": side_id,
                "side_text": side_text,
                "coord": coord,
                "raw": body,
            }
        )
    return draws


def latex_flowchart_to_model(
    chunk: str, caption: str = "", *, label: str = ""
) -> FlowModel:
    if not caption:
        cap_m = re.search(r"\\caption\{((?:[^{}]|\{[^{}]*\})*)\}", chunk)
        if cap_m:
            caption = _plain_node_text(cap_m.group(1))
    if not label:
        lab_m = re.search(r"\\label\{([^}]+)\}", chunk)
        if lab_m:
            label = lab_m.group(1).strip()

    nodes = _extract_nodes(chunk)
    draws = _extract_draws(chunk)
    flows = [n for n in nodes if n["kind"] == "flow"]
    by_id = {n["id"]: n for n in nodes if n["id"]}

    # map coordinate name → edge between two flow nodes
    coord_owner: dict[str, str] = {}  # coord -> from_flow_id
    for d in draws:
        if d["coord"] and d["from"] in {f["id"] for f in flows}:
            coord_owner[d["coord"]] = d["from"]

    steps: list[FlowStep] = []
    for flow in flows:
        fid = flow["id"]
        step = FlowStep(id=fid, text=flow["text"])
        for n in nodes:
            if n["of"] != fid:
                continue
            if n["kind"] == "left":
                step.left = n["text"]
            elif n["kind"] == "right":
                step.right = n["text"]
            elif n["kind"] == "side":
                step.side = n["text"]
        for d in draws:
            if d["from"] == fid and d["side_id"]:
                step.side = d["side_text"] or step.side or by_id.get(d["side_id"], {}).get("text", "")
                if d["label"]:
                    step.side_label = d["label"]
            elif d["from"] == fid and d["to"] in by_id and by_id[d["to"]]["kind"] == "side":
                step.side = by_id[d["to"]]["text"] or step.side
                if d["label"]:
                    step.side_label = d["label"]
        steps.append(step)

    flow_ids = {s.id for s in steps}
    step_by_id = {s.id: s for s in steps}

    mid_owned_ids: set[str] = set()
    for d in draws:
        # downward labeled edges between flow nodes
        if d["from"] in flow_ids and d["to"] in flow_ids and d["label"]:
            if d["to"] in step_by_id:
                step_by_id[d["to"]].pass_label = d["label"]

        # mid-branch from a coordinate on the vertical edge (e.g. mid → n5out)
        owner = ""
        if d["from"] in coord_owner:
            owner = coord_owner[d["from"]]
        if owner and owner in step_by_id and (d["side_text"] or d["side_id"]):
            st = step_by_id[owner]
            text = d["side_text"] or by_id.get(d["side_id"], {}).get("text", "")
            if text:
                st.mid_side = text
                if d["label"]:
                    st.mid_label = d["label"]
                if d["side_id"]:
                    mid_owned_ids.add(d["side_id"])
                # clear accidental east-side copy of the same mid box
                if st.side == text and not st.side_label:
                    st.side = ""

    for n in nodes:
        if n["kind"] != "side" or not n["id"] or n["id"] in mid_owned_ids:
            continue
        used = any(n["text"] in (s.side, s.mid_side) for s in steps)
        if used:
            continue
        base = re.sub(r"out$", "", n["id"])
        if base in step_by_id:
            st = step_by_id[base]
            if not st.side:
                st.side = n["text"]
            elif not st.mid_side:
                st.mid_side = n["text"]

    return FlowModel(caption=caption, label=label, steps=steps, latex=chunk.strip())


def _br(text: str) -> str:
    return html.escape(text).replace("\n", "<br>")


def model_to_preview_html(model: FlowModel) -> str:
    """Preview layout aligned to TikZ: left notes | main column | right notes/sideboxes.

    Vertical arrows sit under the main column; pass labels sit to the right of the
    shaft; east sideboxes use a labeled horizontal arrow; mid-branches leave from
    the middle of the vertical edge (e.g. n5→n6 → 患者离开).
    """
    parts = ['<div class="se-flow se-flow-rich" title="双击编辑">']
    if not model.steps:
        parts.append('<div class="se-flow-step">（空流程图）</div>')
        parts.append("</div>")
        return "".join(parts)

    for i, step in enumerate(model.steps):
        if i:
            prev = model.steps[i - 1]
            parts.append('<div class="se-flow-vlink">')
            parts.append('<div class="se-flow-vlink-left"></div>')
            parts.append('<div class="se-flow-vlink-mid">')
            # Upper half of vertical shaft
            parts.append('<div class="se-flow-vshaft">')
            parts.append('<span class="se-flow-vline" aria-hidden="true"></span>')
            if step.pass_label:
                parts.append(
                    f'<span class="se-flow-pass">{html.escape(step.pass_label)}</span>'
                )
            parts.append("</div>")
            # Mid-branch leaving the shaft (owned by previous step)
            if prev.mid_side:
                parts.append('<div class="se-flow-mid-branch">')
                parts.append('<span class="se-flow-hline" aria-hidden="true"></span>')
                parts.append('<span class="se-flow-arrowhead-h" aria-hidden="true"></span>')
                if prev.mid_label:
                    parts.append(
                        f'<span class="se-flow-edge-lab">{html.escape(prev.mid_label)}</span>'
                    )
                parts.append(
                    f'<div class="se-flow-step side">{_br(prev.mid_side)}</div>'
                )
                parts.append("</div>")
            # Lower half + arrow head
            parts.append('<div class="se-flow-vshaft tip">')
            parts.append('<span class="se-flow-vline" aria-hidden="true"></span>')
            parts.append('<span class="se-flow-arrowhead" aria-hidden="true"></span>')
            parts.append("</div>")
            parts.append("</div>")  # vlink-mid
            parts.append('<div class="se-flow-vlink-right"></div>')
            parts.append("</div>")  # vlink

        parts.append('<div class="se-flow-unit">')
        # left annotation
        parts.append('<div class="se-flow-side left">')
        if step.left:
            parts.append(f'<div class="se-flow-note">{_br(step.left)}</div>')
        parts.append("</div>")
        # main box
        parts.append('<div class="se-flow-main">')
        parts.append(f'<div class="se-flow-step">{_br(step.text)}</div>')
        parts.append("</div>")
        # right: close notes + east sidebox branch
        parts.append('<div class="se-flow-side right">')
        if step.right:
            parts.append(f'<div class="se-flow-note near">{_br(step.right)}</div>')
        if step.side:
            parts.append('<div class="se-flow-h-branch">')
            parts.append('<div class="se-flow-h-edge">')
            if step.side_label:
                parts.append(
                    f'<span class="se-flow-edge-lab">{html.escape(step.side_label)}</span>'
                )
            parts.append('<span class="se-flow-hline" aria-hidden="true"></span>')
            parts.append('<span class="se-flow-arrowhead-h" aria-hidden="true"></span>')
            parts.append("</div>")
            parts.append(f'<div class="se-flow-step side">{_br(step.side)}</div>')
            parts.append("</div>")
        parts.append("</div>")  # right
        parts.append("</div>")  # unit

    parts.append("</div>")
    return "".join(parts)


def model_to_latex(model: FlowModel) -> str:
    """Prefer original latex on the model; otherwise regenerate a compact TikZ figure."""
    if model.latex.strip() and r"\begin{tikzpicture}" in model.latex:
        return model.latex.strip()
    lines = [
        r"\begin{figure}[H]",
        r"\centering",
        r"\tikzset{",
        r"  flowbox/.style={rectangle, draw, align=center, text width=5cm, inner sep=6pt, font=\small},",
        r"  sidebox/.style={rectangle, draw, align=center, text width=3cm, inner sep=6pt, font=\small},",
        r"  arr/.style={->, >=Stealth, thick},",
        r"  lefttext/.style={align=right, font=\footnotesize, text width=4cm},",
        r"  righttext/.style={align=left, font=\footnotesize}",
        r"}",
        r"\begin{tikzpicture}[node distance=6mm]",
    ]
    prev = None
    for i, step in enumerate(model.steps):
        nid = step.id or f"n{i + 1}"
        text = convert_inline(step.text.replace("\n", r"\\"))
        lines.append(rf"\node[flowbox] ({nid}) {{{text}}};")
        if prev:
            if step.pass_label:
                lab = convert_inline(step.pass_label)
                lines.append(
                    rf"\draw[arr] ({prev}.south) -- node[right, font=\small]{{{lab}}} ({nid}.north);"
                )
            else:
                lines.append(rf"\draw[arr] ({prev}.south) -- ({nid}.north);")
        if step.left:
            left = convert_inline(step.left.replace("\n", r"\\"))
            lines.append(rf"\node[lefttext, left=4mm of {nid}] {{{left}}};")
        if step.right:
            right = convert_inline(step.right.replace("\n", r"\\"))
            lines.append(rf"\node[righttext, right=4mm of {nid}] {{{right}}};")
        if step.side:
            sid = f"{nid}out"
            side = convert_inline(step.side.replace("\n", r"\\"))
            lines.append(rf"\node[sidebox, right=14mm of {nid}] ({sid}) {{{side}}};")
            if step.side_label:
                slab = convert_inline(step.side_label)
                lines.append(
                    rf"\draw[arr] ({nid}.east) -- node[above, font=\small]{{{slab}}} ({sid}.west);"
                )
            else:
                lines.append(rf"\draw[arr] ({nid}.east) -- ({sid}.west);")
        prev = nid
    lines.append(r"\end{tikzpicture}")
    if model.caption:
        lines.append(f"\\caption{{{convert_inline(model.caption)}}}")
    if (model.label or "").strip():
        lines.append(f"\\label{{{model.label.strip()}}}")
    lines.append(r"\end{figure}")
    return "\n".join(lines)


def patch_flowchart_texts(original_latex: str, model: FlowModel) -> str:
    """If model carries full latex, use it; else keep original."""
    if model.latex.strip():
        return model.latex.strip()
    return original_latex.strip() if original_latex.strip() else model_to_latex(model)
