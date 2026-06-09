"""医院设备类型与任务模板库自动绑定。"""
from __future__ import annotations

import re

from apps.core.library_task_folder_service import task_folders_active_queryset
from apps.core.models import LibraryTask, LibraryTaskFolder

# 医院信息录入时的设备类型（与任务模板库「设备类型」文件夹名称一致）
EQUIPMENT_DEVICE_TYPE_CHOICES: tuple[str, ...] = (
    "CT",
    "DR",
    "DSA",
    "C形臂",
    "胃肠机",
    "动态DR",
    "乳腺DR",
    "口腔CBCT",
    "口腔全景",
    "口内牙片机",
    "加速器中的CBCT",
)

# 任务模板库顶层「检测类型」文件夹 → 医院信息中的检测类别名称
INSPECTION_TYPE_FOLDER_SPECS: tuple[dict[str, str], ...] = (
    {"folder_code": "001", "folder_label": "验收检测", "inspection_type": "验收检测"},
    {"folder_code": "002", "folder_label": "状态检测", "inspection_type": "状态检测"},
)


def normalize_device_type(raw: str) -> str | None:
    val = (raw or "").strip()
    if not val:
        return None
    if val == "C型臂":
        val = "C形臂"
    if val in EQUIPMENT_DEVICE_TYPE_CHOICES:
        return val
    return None


def _strip_folder_code_prefix(name: str) -> str:
    """
    去掉委托方文件夹名前的序号前缀。
    支持「001 验收检测」「01 CT」「02DR」「03动态DR」等（序号与名称之间可有可无空格）。
    """
    return re.sub(r"^\d+\s*", "", (name or "").strip()).strip()


def folder_name_matches_device_type(folder_name: str, device_type: str) -> bool:
    """任务库设备类型子文件夹名（如 01 CT、02DR）与医院设备类型是否一致。"""
    dt = (device_type or "").strip()
    if not dt:
        return False
    raw = (folder_name or "").strip()
    if not raw:
        return False
    if raw == dt:
        return True
    return _strip_folder_code_prefix(raw) == dt


def folder_name_matches_inspection_spec(folder_name: str, spec: dict[str, str]) -> bool:
    raw = (folder_name or "").strip()
    if not raw:
        return False
    label = (spec.get("folder_label") or "").strip()
    if not label:
        return False
    key = _strip_folder_code_prefix(raw)
    if key == label:
        return True
    code = (spec.get("folder_code") or "").strip()
    if code and raw.startswith(code):
        return label in raw or label in key
    return label in raw


def find_inspection_type_root_folder(spec: dict[str, str]) -> LibraryTaskFolder | None:
    for row in task_folders_active_queryset().filter(parent__isnull=True).order_by(
        "sort_order", "name", "id"
    ):
        if folder_name_matches_inspection_spec(row.name, spec):
            return row
    return None


def find_device_type_folder(
    detection_folder: LibraryTaskFolder, device_type: str
) -> LibraryTaskFolder | None:
    dt = (device_type or "").strip()
    if not dt:
        return None
    for child in detection_folder.children.filter(is_active=True).order_by(
        "sort_order", "name", "id"
    ):
        if folder_name_matches_device_type(child.name or "", dt):
            return child
    return None


def pick_report_task_for_device_folder(device_folder: LibraryTaskFolder) -> LibraryTask | None:
    return (
        LibraryTask.objects.filter(
            task_folder=device_folder,
            output_target=LibraryTask.OUTPUT_REPORT,
        )
        .order_by("code", "id")
        .first()
    )


def auto_report_task_bindings_for_device_type(
    device_type: str,
) -> tuple[list[dict], str | None]:
    """
    按设备类型在任务模板库中解析验收/状态检测分类下对应报告模板。
    文件夹名支持委托方序号前缀（如 001 验收检测、01 CT、02DR）。
    返回 ([{inspection_type, report_task_id}, ...], 错误信息)。
    """
    dt = normalize_device_type(device_type)
    if dt is None:
        return [], "请选择有效的设备类型"

    bindings: list[dict] = []
    missing: list[str] = []

    for spec in INSPECTION_TYPE_FOLDER_SPECS:
        itype = spec["inspection_type"]
        det = find_inspection_type_root_folder(spec)
        if det is None:
            missing.append(
                f"任务库缺少「{spec['folder_label']}」分类"
                f"（常见文件夹名：{spec['folder_code']} {spec['folder_label']}）"
            )
            continue
        dev_folder = find_device_type_folder(det, dt)
        if dev_folder is None:
            missing.append(f"「{itype}」下无「{dt}」文件夹")
            continue
        report = pick_report_task_for_device_folder(dev_folder)
        if report is None:
            missing.append(f"「{itype}/{dt}」下无报告模板")
            continue
        bindings.append({"inspection_type": itype, "report_task_id": report.pk})

    if not bindings:
        detail = "；".join(missing) if missing else "未找到可绑定的报告模板"
        return [], f"无法自动绑定模板：{detail}。请先在任务模板库维护目录，或在编辑设备时手动绑定。"

    return bindings, None


def inspection_type_labels_for_catalog() -> list[str]:
    """任务模板库目录顶层名称（供 UI 提示）。"""
    return [spec["inspection_type"] for spec in INSPECTION_TYPE_FOLDER_SPECS]


def infer_inspection_type_for_report_task(task: LibraryTask | None) -> str:
    """根据报告模板所在任务文件夹路径推断检测类别（兼容仅写了 report_task_id 的旧数据）。"""
    if task is None or not task.task_folder_id:
        return ""
    folder = task.task_folder
    if folder is None:
        return ""
    chain = folder.ancestors_chain()
    if not chain:
        return ""
    root_name = chain[0].name or ""
    for spec in INSPECTION_TYPE_FOLDER_SPECS:
        if folder_name_matches_inspection_spec(root_name, spec):
            return spec["inspection_type"]
    return ""
