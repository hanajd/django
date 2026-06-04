import re
import logging

logger = logging.getLogger(__name__)


def _clean_md_light(md_raw: str) -> str:
    if not md_raw:
        return ""
    text = re.sub(r"```[\s\S]*?```", " ", md_raw)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"!\[.*?\]\(.*?\)", "", text)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text.strip())
    return text.strip()


def clean_md_content(md_raw: str) -> str:
    if not md_raw:
        return ""
    text = md_raw
    text = re.sub(r"```[\s\S]*?```", " ", text)
    text = re.sub(r"<[^>]+>", " ", text)
    lines_out = []
    for line in text.splitlines():
        s = line.strip()
        if not s:
            lines_out.append("")
            continue
        if s.startswith("|") or re.match(r"^:?-+:?\s*(\|:?-+:?)+\s*$", s):
            continue
        if re.match(r"^\|.*\|$", s):
            continue
        lines_out.append(line)
    text = "\n".join(lines_out)
    text = re.sub(r"!\[.*?\]\(.*?\)", "", text)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"^\s*#{1,6}\s+", "", text, flags=re.MULTILINE)
    useless_text = [
        "Shanghai United Imaging Healthcare",
        "Product Configuration:",
        "Configuration 1",
        "CTC",
        "（其他内容详见说明书）",
    ]
    for ut in useless_text:
        text = text.replace(ut, "")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text.strip())
    out = text.strip()
    return out if out else _clean_md_light(md_raw)
