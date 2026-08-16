"""主表格模板目录：按模板名选择栏目结构。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

from f1_eval_report.sections import gbzt, yp250075

# 模板 id → 模块
TABLE_TEMPLATES: Dict[str, Any] = {
    "gbzt": gbzt,
    "250075YP": yp250075,
}

DEFAULT_TABLE_TEMPLATE = "250075YP"

# 兼容旧导入：默认指向 250075YP
SECTION_BUILDERS = yp250075.SECTION_BUILDERS
HALF_LABEL_FMT = yp250075.HALF_LABEL_FMT


def list_table_templates() -> List[str]:
    return list(TABLE_TEMPLATES.keys())


def resolve_table_template(name: Optional[str] = None) -> str:
    key = (name or DEFAULT_TABLE_TEMPLATE).strip()
    if key not in TABLE_TEMPLATES:
        known = ", ".join(list_table_templates())
        raise KeyError(f"未知主表格模板: {key!r}（可选: {known}）")
    return key


def get_section_builders(
    template: Optional[str] = None,
) -> Sequence[Tuple[str, Any]]:
    mod = TABLE_TEMPLATES[resolve_table_template(template)]
    return mod.SECTION_BUILDERS


def all_row_definitions(template: Optional[str] = None) -> List[Dict[str, Any]]:
    mod = TABLE_TEMPLATES[resolve_table_template(template)]
    return mod.all_row_definitions()
