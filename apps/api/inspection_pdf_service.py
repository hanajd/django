"""检测提交数据合并、现场记录加载与任务解析；报告模板回填与 PDF 生成见 `inspection_report_make`。"""
from __future__ import annotations

import copy
import json as json_std
import re
from datetime import datetime
from typing import Sequence

from django.db.models import Q
from django.utils import timezone

from apps.core import pipeline_service
from apps.core.models import (
    InspectionCase,
    InspectionSubmission,
    LibraryFile,
    LibraryProject,
    LibraryTask,
    LibraryTaskAssignment,
)

AUTO_TASK_PREFIX = "ASG-"


def site_record_task_count_for_project(project) -> int:
    """
    项目任务管理（library_project.library_tasks）中、输出目标为「现场记录」的任务数量。
    与文件库勾选份数无关，表示该项目在后台配置的现场记录类任务槽位数。
    """
    if project is None:
        return 0
    return int(
        project.library_tasks.filter(output_target=LibraryTask.OUTPUT_SITE_RECORD).distinct().count()
    )


def resolve_manual_report_device_count(distinct_inspection_case_count: int) -> int:
    """
    文件库多选合并导出**一份**报告 PDF 时的「受检设备台数」：
    按勾选记录所关联的**不同 inspection 案件（报告/受检设备）份数**计；
    与「每份报告对应一台受检设备、合成报告台数=合并的报告份数」一致。
    可与 `site_record_task_count_for_project(project)` 对照，校验现场记录类任务配置是否覆盖案件规模。
    """
    return max(0, int(distinct_inspection_case_count))


def _resolve_library_task_for_task_no(task_no: str, project=None):
    assignment_id = _parse_assignment_task_no(task_no)
    if assignment_id:
        qs = LibraryTaskAssignment.objects.select_related("library_task").filter(pk=assignment_id)
        if project is not None and getattr(project, "pk", None):
            qs = qs.filter(project_id=project.pk)
        row = qs.first()
        if not row:
            return None
        return row.library_task if row.library_task_id else None
    # InspectionCase.case_no 常与 _InspectionTaskAccessMixin._build_project_task_no 一致，为项目内任务序号 "01"…
    if project is not None and getattr(project, "pk", None):
        raw = str(task_no or "").strip()
        try:
            idx = int(raw)
        except ValueError:
            return None
        if idx > 0:
            tasks = list(project.library_tasks.order_by("code", "id"))
            if idx <= len(tasks):
                return tasks[idx - 1]
    return None


def _parse_assignment_task_no(task_no: str):
    if not (task_no or "").startswith(AUTO_TASK_PREFIX):
        return None
    raw = (task_no or "")[len(AUTO_TASK_PREFIX) :].strip()
    try:
        aid = int(raw)
    except ValueError:
        return None
    return aid if aid > 0 else None


def _display_task_no(task_no: str, project=None) -> str:
    raw = str(task_no or "").strip()
    if raw and raw.isdigit() and len(raw) <= 2:
        return f"{int(raw):02d}"
    task_obj = _resolve_library_task_for_task_no(raw, project)
    if task_obj is None or project is None:
        return raw
    task_ids = list(project.library_tasks.order_by("code", "id").values_list("id", flat=True))
    try:
        idx = task_ids.index(task_obj.id) + 1
    except ValueError:
        return raw
    return f"{idx:02d}"


def _inspection_serial_for_case(inspection_case, project) -> str | None:
    """
    同一文件库项目下，本案件在「报告/受检设备」序列中的序号（01 起，按案件创建顺序）。
    与项目内 library_tasks 列表位置脱钩，避免报告任务插在中间导致受检编号错位。
    """
    if inspection_case is None:
        return None
    lp_id = getattr(inspection_case, "library_project_id", None)
    if lp_id is None and project is not None:
        lp_id = getattr(project, "pk", None)
    if lp_id is None:
        return None
    ids = list(
        InspectionCase.objects.filter(library_project_id=lp_id)
        .order_by("created_at", "id")
        .values_list("id", flat=True)
    )
    try:
        idx = ids.index(int(inspection_case.pk)) + 1
    except (ValueError, TypeError):
        return None
    return f"{idx:02d}"


def display_inspected_no_for_fill(inspection_case, project, task_no: str) -> str:
    """模板/派生映射中的受检编号展示：优先项目内案件序号，否则回退为任务号展示规则。"""
    serial = _inspection_serial_for_case(inspection_case, project)
    if serial is not None:
        return serial
    return _display_task_no(task_no, project)


def _display_project_id(project) -> str:
    if project is None:
        return ""
    code = str(getattr(project, "code", "") or "").strip()
    if re.fullmatch(r"\d{8}", code):
        return code
    created_at = getattr(project, "created_at", None) or datetime.now()
    if timezone.is_naive(created_at):
        created_at = timezone.make_aware(created_at, timezone.get_current_timezone())
    dt = timezone.localtime(created_at)
    prefix = dt.strftime("%Y%m")
    month_start = dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if dt.month == 12:
        next_month_start = dt.replace(year=dt.year + 1, month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
    else:
        next_month_start = dt.replace(month=dt.month + 1, day=1, hour=0, minute=0, second=0, microsecond=0)
    ids = list(
        LibraryProject.objects.filter(created_at__gte=month_start, created_at__lt=next_month_start)
        .order_by("created_at", "id")
        .values_list("id", flat=True)
    )
    try:
        idx = ids.index(project.id) + 1
    except ValueError:
        idx = 1
    return f"{prefix}{idx:02d}"


def _pick_submit_generation_tasks(task_no: str, project):
    assignment_task = _resolve_library_task_for_task_no(task_no, project)
    if assignment_task and assignment_task.output_target in (LibraryTask.OUTPUT_SITE_RECORD, LibraryTask.OUTPUT_REPORT):
        # 仅生成当前案件/提交所对应文件库任务的 PDF，避免挂到同项目其它 output 任务上
        return [assignment_task]
    picked = {}
    project_tasks = (
        project.library_tasks.filter(output_target__in=(LibraryTask.OUTPUT_SITE_RECORD, LibraryTask.OUTPUT_REPORT))
        .order_by("code", "id")
        .distinct()
    )
    for t in project_tasks:
        if t.output_target not in picked:
            picked[t.output_target] = t
    return [picked[k] for k in (LibraryTask.OUTPUT_SITE_RECORD, LibraryTask.OUTPUT_REPORT) if k in picked]


def accumulate_inspection_payloads_first_wins(ordered_payloads: Sequence[dict]) -> dict:
    """
    多份现场记录/检测提交 JSON 按勾选顺序汇总：同一顶层键（如 hospitalInfo、testResult）
    仅在首次出现时写入结果，后续文件中的同键整段丢弃，以便「映射到报告里同一业务对象」时
    统一采用第一份现场记录的内容。未出现过的键仍由后续文件补齐（如首份缺省字段由后份补上仅限新键）。
    """
    merged: dict = {}
    for payload in ordered_payloads:
        if not isinstance(payload, dict):
            continue
        for k, v in payload.items():
            if k in merged:
                continue
            merged[k] = copy.deepcopy(v)
    return merged


def accumulate_inspection_payloads_ordered_merge(ordered_payloads: Sequence[dict]) -> dict:
    """
    多份检测提交 JSON 按勾选顺序合并为 **一份报告数据源**（与 first_wins 不同）：
    - **dynamicData**：同键不得后勾覆盖先勾（不同模板下 f1、f2… 语义不同，覆盖会整表串位）；后份仅追加先份没有的键。
    - **reportInfo / hospitalInfo / equipmentInfo**：先份非空优先，后份只补缺，与 taskNo 锚定第一份一致。
    - **testResult**：嵌套 dict 仍按后勾覆盖先勾，便于拼多段检测结果。
    - **signatures / conclusion / instruments**：后勾覆盖先勾。
    - 其余顶层键仍按深度合并、后勾优先。
    - 合并完成后写回第一份 **taskNo / projectId**，以及 **templateId / templateVersion / reportType / steps**（避免模板身份被最后一份覆盖）。
    """
    merged: dict = {}
    first: dict | None = None
    for payload in ordered_payloads:
        if not isinstance(payload, dict):
            continue
        if first is None:
            first = payload
            merged = copy.deepcopy(payload)
            continue
        merged = _merge_inspection_payload_for_report_accumulator(merged, payload)
    if isinstance(first, dict) and isinstance(merged, dict):
        for key in ("taskNo", "projectId"):
            if key in first and first[key] not in (None, ""):
                merged[key] = copy.deepcopy(first[key])
        for key in ("templateId", "templateVersion", "reportType", "steps"):
            if key in first:
                merged[key] = copy.deepcopy(first[key])
    return merged


def _deep_merge_payload_dicts(base: dict, incoming: dict) -> dict:
    """深度合并字典：incoming 覆盖 base 同名字段；嵌套 dict 递归合并。"""
    out: dict = dict(base) if isinstance(base, dict) else {}
    for k, v in (incoming or {}).items():
        if isinstance(v, dict):
            cur = out.get(k) if isinstance(out.get(k), dict) else {}
            if not isinstance(cur, dict):
                cur = {}
            out[k] = _deep_merge_payload_dicts(cur, v)
            continue
        if isinstance(v, list):
            if v or k not in out:
                out[k] = v
            continue
        if v in (None, "") and k in out:
            continue
        out[k] = v
    return out


def _merge_dynamic_data_preserve_first(base: dict | None, incoming: dict | None) -> dict:
    """
    dynamicData 以 pdfFieldId / 扁平键为主；不同模板（JS-001 / JS-117…）下同名的 f1、f14 语义不同。
    多份提交若按深度合并「后勾覆盖先勾」，会把后一份模板的控件值写进先一份的域，导致报告回填完全串位。
    策略：先勾选的键保留；后勾选仅追加先份中尚不存在的键（如带 task 前缀的矩阵键）。
    """
    out = copy.deepcopy(base) if isinstance(base, dict) else {}
    inc = incoming if isinstance(incoming, dict) else {}
    for k, v in inc.items():
        if k not in out:
            out[k] = copy.deepcopy(v)
    return out


def _deep_merge_dicts_fill_missing(base: dict | None, incoming: dict | None) -> dict:
    """
    嵌套 dict：incoming 只在 base 侧缺失或为空（None / \"\"）时写入；双方均有非空标量时保留 base。
    与「taskNo 锚定第一份」一致，避免后一份受检单位/设备信息整体覆盖先份。
    """
    out = copy.deepcopy(base) if isinstance(base, dict) else {}
    inc = incoming if isinstance(incoming, dict) else {}
    for k, v in inc.items():
        if k not in out:
            out[k] = copy.deepcopy(v)
            continue
        cur = out[k]
        if isinstance(v, dict) and isinstance(cur, dict):
            out[k] = _deep_merge_dicts_fill_missing(cur, v)
            continue
        if isinstance(v, list):
            if (not isinstance(cur, list) or len(cur) == 0) and v:
                out[k] = copy.deepcopy(v)
            continue
        if cur in (None, "") and v not in (None, ""):
            out[k] = copy.deepcopy(v)
    return out


def _merge_payload_test_result(base_tr, incoming_tr):
    """与 _deep_merge_payload_dicts 在单键 testResult 上的行为一致（含 null 不冲掉已有块）。"""
    if isinstance(incoming_tr, dict):
        cur = base_tr if isinstance(base_tr, dict) else {}
        return _deep_merge_payload_dicts(cur, copy.deepcopy(incoming_tr))
    if incoming_tr in (None, ""):
        return base_tr
    return copy.deepcopy(incoming_tr)


def _merge_inspection_payload_for_report_accumulator(base: dict, incoming: dict) -> dict:
    """
    将后一份检测提交并入已累积的 merged（第一份已整体拷贝在 base 中）。
    与「全量 _deep_merge_payload_dicts」不同：隔离 dynamicData / 基本信息块，避免多模板混填。
    """
    out = copy.deepcopy(base)
    out["dynamicData"] = _merge_dynamic_data_preserve_first(
        out.get("dynamicData") if isinstance(out.get("dynamicData"), dict) else {},
        incoming.get("dynamicData") if isinstance(incoming.get("dynamicData"), dict) else {},
    )
    for blk in ("reportInfo", "hospitalInfo", "equipmentInfo"):
        if isinstance(incoming.get(blk), dict):
            cur = out.get(blk) if isinstance(out.get(blk), dict) else {}
            out[blk] = _deep_merge_dicts_fill_missing(cur, incoming[blk])
    if "testResult" in incoming:
        out["testResult"] = _merge_payload_test_result(out.get("testResult"), incoming["testResult"])
    for blk in ("signatures", "conclusion", "instruments"):
        if blk not in incoming:
            continue
        bi = incoming[blk]
        bo = out.get(blk)
        if isinstance(bi, dict):
            bd = bo if isinstance(bo, dict) else {}
            out[blk] = _deep_merge_payload_dicts(bd, copy.deepcopy(bi))
        elif isinstance(bi, list):
            if bi or blk not in out:
                out[blk] = copy.deepcopy(bi)
        elif bi not in (None, "") or blk not in out:
            out[blk] = copy.deepcopy(bi)
    skip = {
        "dynamicData",
        "reportInfo",
        "hospitalInfo",
        "equipmentInfo",
        "testResult",
        "signatures",
        "conclusion",
        "instruments",
        "taskNo",
        "projectId",
    }
    for k, v in incoming.items():
        if k in skip:
            continue
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge_payload_dicts(out[k], copy.deepcopy(v))
            continue
        if isinstance(v, list):
            if v or k not in out:
                out[k] = copy.deepcopy(v)
            continue
        if v in (None, "") and k in out:
            continue
        out[k] = copy.deepcopy(v)
    return out


def _task_no_strings_for_library_task(project, library_task: LibraryTask | None) -> list[str]:
    """
    项目内指向同一 LibraryTask 的 taskNo 可能为序号「01」…或分配号「ASG-{assignment_id}」。
    用于将检测提交 JSON 文件名 `{taskNo}_submit_*.json` 与现场记录任务对齐。
    """
    if project is None or library_task is None:
        return []
    task_ids = list(project.library_tasks.order_by("code", "id").values_list("id", flat=True))
    out: list[str] = []
    try:
        idx = task_ids.index(library_task.id) + 1
        out.append(f"{idx:02d}")
        out.append(str(idx))
    except ValueError:
        pass
    for aid in LibraryTaskAssignment.objects.filter(
        project_id=project.pk, library_task_id=library_task.pk
    ).values_list("id", flat=True):
        out.append(f"{AUTO_TASK_PREFIX}{aid}")
    return list(dict.fromkeys([x for x in out if x]))


def _site_record_json_files_for_task(
    project,
    site_task: LibraryTask,
    candidates: Sequence[LibraryFile],
) -> list[LibraryFile]:
    """从候选文件中挑出属于现场记录任务 site_task 的 JSON（旧：task M2M；新：检测提交文件名含 taskNo）。"""
    nos = [n.lower() for n in _task_no_strings_for_library_task(project, site_task)]
    out: list[LibraryFile] = []
    seen: set[int] = set()
    for lf in candidates:
        pk = int(lf.pk)
        if pk in seen:
            continue
        nm = (lf.original_name or "").lower()
        if not nm.endswith(".json"):
            continue
        if any(t.pk == site_task.pk for t in lf.library_tasks.all()):
            seen.add(pk)
            out.append(lf)
            continue
        if lf.category != LibraryFile.CATEGORY_INSPECTION_SUBMIT:
            continue
        if nos and any(nm.startswith(f"{tn}_submit_") for tn in nos):
            seen.add(pk)
            out.append(lf)
    return out


def _dedupe_inspection_cases_preserve_order(cases: Sequence[InspectionCase]) -> list[InspectionCase]:
    seen: set[int] = set()
    out: list[InspectionCase] = []
    for c in cases:
        if c.pk in seen:
            continue
        seen.add(int(c.pk))
        out.append(c)
    return out


def _load_site_record_json_merged_only(
    cases: Sequence[InspectionCase],
    project,
    report_task=None,
) -> tuple[dict, bool, list, str]:
    """
    合并「现场记录类文件库任务」关联的 .json（按报告来源任务）。
    判定依据是任务的 output_target=现场记录，而非文件库 category（文件库「现场记录」页主要存放导出的 PDF）。

    可在同一项目下跨多个案件收集：每个来源任务下，合并其对应的检测提交 .json
    （文件名 `{taskNo}_submit_*.json`，taskNo 与项目内该现场记录任务序号或分配号一致），
    并兼容旧数据：仍挂在任务模板 M2M 上的 .json。

    若按来源任务一条都匹配不到，则回退为：同案件/项目下所有检测提交类 .json 及仍带现场记录任务 M2M 的 .json。

    返回 (merged, any_loaded, missing_task_codes, load_hint)。
    load_hint 为给人看的说明（非错误码）。
    """
    cases_list = _dedupe_inspection_cases_preserve_order(cases)
    if not cases_list:
        return {}, False, [], ""
    case_pks = [int(c.pk) for c in cases_list]
    source_tasks = _report_site_record_source_tasks(project, report_task)
    cand_qs = (
        LibraryFile.objects.filter(
            link_entity=LibraryFile.LINK_ENTITY_INSPECTION_CASE,
            link_object_id__in=case_pks,
            projects=project,
        )
        .filter(
            Q(category=LibraryFile.CATEGORY_INSPECTION_SUBMIT)
            | Q(library_tasks__output_target=LibraryTask.OUTPUT_SITE_RECORD)
        )
        .order_by("created_at", "pk")
        .distinct()
        .prefetch_related("library_tasks")
    )
    candidates = list(cand_qs)
    merged: dict = {}
    used = False
    missing_codes: list = []
    load_hint = ""
    for st in source_tasks:
        rows = _site_record_json_files_for_task(project, st, candidates)
        if not rows:
            missing_codes.append(st.code)
            continue
        for lf in rows:
            try:
                p = pipeline_service.library_absolute_path(lf.relative_path)
                payload = json_std.loads(p.read_text(encoding="utf-8", errors="replace"))
            except Exception as exc:
                return {}, False, [f"读取失败:{exc}"], ""
            if not isinstance(payload, dict):
                return {}, False, ["现场记录 JSON 结构无效"], ""
            merged = _deep_merge_payload_dicts(merged, payload)
            used = True
    if not used:
        loose_rows: list[LibraryFile] = []
        seen_loose: set[int] = set()
        for lf in candidates:
            pk = int(lf.pk)
            if pk in seen_loose:
                continue
            nm = (lf.original_name or "").lower()
            if not nm.endswith(".json"):
                continue
            if lf.category == LibraryFile.CATEGORY_INSPECTION_SUBMIT:
                loose_rows.append(lf)
                seen_loose.add(pk)
                continue
            if any(t.output_target == LibraryTask.OUTPUT_SITE_RECORD for t in lf.library_tasks.all()):
                loose_rows.append(lf)
                seen_loose.add(pk)
        if loose_rows:
            for lf in loose_rows:
                try:
                    p = pipeline_service.library_absolute_path(lf.relative_path)
                    payload = json_std.loads(p.read_text(encoding="utf-8", errors="replace"))
                except Exception as exc:
                    return {}, False, [f"读取失败:{exc}"], ""
                if not isinstance(payload, dict):
                    return {}, False, ["现场记录 JSON 结构无效"], ""
                merged = _deep_merge_payload_dicts(merged, payload)
                used = True
            missing_codes = []
            load_hint = (
                "提示：未能按报告来源任务精确匹配 JSON，已合并所选案件下、"
                "项目关联的检测提交 .json（及旧版挂在现场记录任务上的 .json）。"
                "建议在项目中为各现场记录任务使用一致的 taskNo 提交，以便报告按来源任务拆分合并。"
            )
    return merged, used, missing_codes, load_hint


SUBMIT_PAYLOAD_REQUIRED_KEYS = frozenset({"reportInfo", "hospitalInfo", "equipmentInfo", "testResult"})


def resolve_submit_payload_for_report(task_no: str, case: InspectionCase, project) -> dict | None:
    """
    解析用于报告回填的检测提交正文：优先同 taskNo 的 InspectionSubmission，其次同案件最新检测提交 .json 文件。
    """
    tn = (task_no or "").strip()
    if tn:
        sub = (
            InspectionSubmission.objects.filter(task_no=tn, case_id=case.pk, project_id=project.pk)
            .order_by("-submitted_at", "-updated_at", "-id")
            .first()
        )
        if sub is not None:
            raw = sub.raw_payload if isinstance(sub.raw_payload, dict) else {}
            if raw and SUBMIT_PAYLOAD_REQUIRED_KEYS.issubset(raw.keys()):
                return raw
            fb = {
                "taskNo": sub.task_no,
                "projectId": getattr(project, "code", "") or "",
                "reportInfo": sub.report_info or {},
                "hospitalInfo": sub.hospital_info or {},
                "equipmentInfo": sub.equipment_info or {},
                "testResult": sub.test_result or {},
                "conclusion": sub.conclusion or {},
            }
            if sub.submitted_at:
                fb["submittedAt"] = sub.submitted_at.isoformat()
            if SUBMIT_PAYLOAD_REQUIRED_KEYS.issubset(fb.keys()):
                return fb
    for lf in (
        LibraryFile.objects.filter(
            category=LibraryFile.CATEGORY_INSPECTION_SUBMIT,
            link_entity=LibraryFile.LINK_ENTITY_INSPECTION_CASE,
            link_object_id=case.pk,
            projects=project,
        )
        .order_by("-created_at", "-id")
        .distinct()
    ):
        if not (lf.original_name or "").lower().endswith(".json"):
            continue
        try:
            p = pipeline_service.library_absolute_path(lf.relative_path)
            data = json_std.loads(p.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            continue
        if isinstance(data, dict) and SUBMIT_PAYLOAD_REQUIRED_KEYS.issubset(data.keys()):
            return data
    return None


def _single_valid_submit_json_payload_for_case(case: InspectionCase, project) -> dict | None:
    """同案件、项目下若仅有唯一一份完整检测提交 JSON，则返回其正文；多份或没有则 None。"""
    found: dict | None = None
    n = 0
    for lf in (
        LibraryFile.objects.filter(
            category=LibraryFile.CATEGORY_INSPECTION_SUBMIT,
            link_entity=LibraryFile.LINK_ENTITY_INSPECTION_CASE,
            link_object_id=case.pk,
            projects=project,
        )
        .order_by("-created_at", "-id")
        .distinct()
    ):
        if not (lf.original_name or "").lower().endswith(".json"):
            continue
        try:
            p = pipeline_service.library_absolute_path(lf.relative_path)
            data = json_std.loads(p.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            continue
        if not isinstance(data, dict) or not SUBMIT_PAYLOAD_REQUIRED_KEYS.issubset(data.keys()):
            continue
        n += 1
        found = data
        if n > 1:
            return None
    return found


def resolve_submit_payload_for_site_record_pdf_lf(lf: LibraryFile) -> tuple[dict | None, InspectionCase | None, str]:
    """
    从文件库「现场记录」中已回填导出的 PDF 定位案件与 taskNo，解析出与同 taskNo 检测提交 JSON 等效的数据。
    优先从文件名解析 taskNo：支持旧版「{taskNo}-现场记录.pdf」与新版「…__task__{taskNo}-现场记录.pdf」
    （与 build_exported_inspection_pdf_original_name / _persist_filled_pdf_from_submit 一致，taskNo 中「/」在文件名里为「_」）；
    若仍无法匹配且该案件下仅有唯一一份合格检测提交 JSON，则回退使用该 JSON。
    """
    if lf.category != LibraryFile.CATEGORY_SITE_RECORD:
        return None, None, "不是「现场记录」分类文件"
    name = (lf.original_name or "").strip()
    if not name.lower().endswith(".pdf"):
        return None, None, "不是 PDF 文件"
    if lf.link_entity != LibraryFile.LINK_ENTITY_INSPECTION_CASE or not lf.link_object_id:
        return None, None, "未关联 inspection_case"
    case = (
        InspectionCase.objects.select_related("library_project")
        .filter(pk=int(lf.link_object_id))
        .first()
    )
    if case is None or not case.library_project_id:
        return None, None, "关联案件或项目不存在"
    project = case.library_project

    marker = "-现场记录.pdf"
    lower = name.lower()
    idx = lower.rfind(marker.lower())
    if idx >= 0:
        base = name[:idx].strip()
        if "__task__" in base:
            tail = base.rsplit("__task__", 1)[-1].strip()
            if tail:
                base = tail
        candidates: list[str] = []
        if base:
            candidates.append(base)
            if "_" in base:
                candidates.append(base.replace("_", "/"))
        for tn in dict.fromkeys(candidates):
            payload = resolve_submit_payload_for_report(tn, case, project)
            if payload and SUBMIT_PAYLOAD_REQUIRED_KEYS.issubset(payload.keys()):
                return payload, case, ""

    fb = _single_valid_submit_json_payload_for_case(case, project)
    if fb is not None:
        return fb, case, ""

    return (
        None,
        case,
        "无法匹配检测提交数据：请使用系统导出的现场记录 PDF（文件名中含任务编号，"
        "旧版形如「{taskNo}-现场记录.pdf」，新版在「__task__」之后为任务编号），"
        "或确保该案件下仅有唯一一份检测提交 JSON。",
    )


def load_report_payload_for_manual_export(
    cases: Sequence[InspectionCase],
    project,
    report_task,
    submit_payload: dict,
    *,
    submit_merge_is_authoritative: bool = False,
) -> tuple[dict | None, str]:
    """
    手动导出报告数据源：以检测提交 JSON（submit_payload）为基准，与同项目下若干案件的现场记录 JSON 深度合并；
    同路径字段以检测提交为准（后合并覆盖）。
    无现场记录 JSON 时，仅使用 submit_payload。

    submit_merge_is_authoritative：为 True 时（文件库「手动导出报告」勾选多份现场记录 JSON 场景），
    不再读取文件库中另行挂载到现场记录任务上的 .json，仅以 submit_payload（已按勾选顺序叠成一份）
    作为报告填数来源；模板占位符与字段映射仍由报告任务及关联现场记录任务模板提供。
    """
    if not isinstance(submit_payload, dict) or not submit_payload:
        return None, "检测提交 JSON 无效"
    if not SUBMIT_PAYLOAD_REQUIRED_KEYS.issubset(submit_payload.keys()):
        return None, "检测提交 JSON 缺少 reportInfo/hospitalInfo/equipmentInfo/testResult"
    cases_list = _dedupe_inspection_cases_preserve_order(cases)
    if not cases_list:
        return None, "未指定检测案件"
    if submit_merge_is_authoritative:
        return dict(submit_payload), (
            "已按勾选顺序将多份检测提交合并为单一数据源（testResult 等仍后份补全/覆盖先份；"
            "报告回填时 dynamicData 按「每一份 + 其对应现场模板」单独解析为占位符/id 词条后再汇总，避免不同模板下 f 号混用串位；"
            "reportInfo/hospitalInfo/equipmentInfo 先份非空优先；"
            "taskNo/projectId/templateId 等仍以第一份为准）；未再读取文件库中另行挂载的现场记录任务 JSON。"
        )
    site_merged, site_loaded, missing_codes, site_load_hint = _load_site_record_json_merged_only(
        cases_list, project, report_task
    )
    if missing_codes and any(str(x).startswith("读取失败") for x in missing_codes):
        return None, missing_codes[0] if missing_codes else "现场记录读取失败"
    if missing_codes and any(x == "现场记录 JSON 结构无效" for x in missing_codes):
        return None, "现场记录 JSON 结构无效"
    out = _deep_merge_payload_dicts(site_merged, submit_payload)
    hints: list[str] = []
    if site_load_hint:
        hints.append(site_load_hint)
    if site_loaded and missing_codes:
        hints.append(f"部分来源任务缺少现场记录 JSON: {', '.join(str(x) for x in missing_codes[:10])}")
    if site_loaded:
        hints.append("已合并现场记录 JSON 与检测提交（提交内容优先）")
        if len(cases_list) > 1:
            hints.append(f"现场记录来源案件数：{len(cases_list)}")
    return out, "; ".join(hints)


def _load_site_record_payload_for_report(case: InspectionCase, project, report_task=None):
    """
    读取用于生成报告的数据（优先关联「输出目标=现场记录」任务的 .json，不按文件库 category 判定）。
    若报告任务配置了 report_source_tasks，则按来源任务逐个读取最新现场记录 JSON 并做深度整合。

    说明：App 提交成功时通常会落库「检测提交」JSON 并生成现场记录 **PDF**（多归入文件库「现场记录」分类），
    结构化 JSON 需绑定到现场记录类任务才会参与报告合并。
    当没有任何可用的现场记录 JSON 时，依次回退：① 同案件最新检测提交 JSON 文件；② 数据库中最新已提交记录。
    """
    merged, used, missing_codes, load_hint = _load_site_record_json_merged_only((case,), project, report_task)
    for mc in missing_codes:
        mcs = str(mc)
        if mcs.startswith("读取失败:"):
            return None, mcs
        if mcs == "现场记录 JSON 结构无效":
            return None, "现场记录 JSON 结构无效"
    if used:
        parts = []
        if load_hint:
            parts.append(load_hint)
        if missing_codes:
            parts.append(f"部分来源任务缺少现场记录 JSON: {', '.join(str(x) for x in missing_codes[:10])}")
        return merged, "; ".join(parts) if parts else ""
    submit_qs = (
        LibraryFile.objects.filter(
            category=LibraryFile.CATEGORY_INSPECTION_SUBMIT,
            link_entity=LibraryFile.LINK_ENTITY_INSPECTION_CASE,
            link_object_id=case.pk,
            projects=project,
        )
        .order_by("-created_at", "-id")
        .distinct()
    )
    for lf in submit_qs:
        if not (lf.original_name or "").lower().endswith(".json"):
            continue
        try:
            p = pipeline_service.library_absolute_path(lf.relative_path)
            fb = json_std.loads(p.read_text(encoding="utf-8", errors="replace"))
        except Exception as exc:
            return None, f"检测提交 JSON 回退读取失败: {exc}"
        if not isinstance(fb, dict) or not fb:
            continue
        return fb, f"未找到现场记录 JSON，已回退使用检测提交文件：{lf.original_name}"
    sub = (
        InspectionSubmission.objects.filter(case_id=case.pk, project_id=project.pk)
        .order_by("-submitted_at", "-updated_at", "-id")
        .first()
    )
    if sub is not None:
        raw = sub.raw_payload if isinstance(sub.raw_payload, dict) else {}
        if raw:
            return raw, "未找到现场记录 JSON，已回退使用数据库中的检测提交原始数据"
        fb2 = {
            "taskNo": sub.task_no,
            "projectId": getattr(project, "code", "") or "",
            "reportInfo": sub.report_info or {},
            "hospitalInfo": sub.hospital_info or {},
            "equipmentInfo": sub.equipment_info or {},
            "testResult": sub.test_result or {},
            "conclusion": sub.conclusion or {},
        }
        if sub.submitted_at:
            fb2["submittedAt"] = sub.submitted_at.isoformat()
        return fb2, "未找到现场记录 JSON，已回退使用数据库中的检测提交拆分字段"
    return None, "未找到可用的现场记录 JSON（亦无检测提交可回退）"


def _friendly_library_report_stem(original_name: str) -> str:
    """去掉 .pdf 与文件库导出尾缀 ``__task__…-报告``，便于作合并用展示标题。"""
    n = (original_name or "").strip()
    if n.lower().endswith(".pdf"):
        n = n[:-4]
    m = re.search(r"__task__.+$", n)
    if m:
        n = n[: m.start()].strip("_")
    return (n.strip() or (original_name or "").strip())[:200]


def _resolve_submit_payload_for_report_merge(case: InspectionCase, project) -> dict | None:
    """报告合并：为关联案件的报告 PDF 尽量解析出完整检测提交（库 JSON → DB → taskNo 回退）。"""
    for lf in (
        LibraryFile.objects.filter(
            category=LibraryFile.CATEGORY_INSPECTION_SUBMIT,
            link_entity=LibraryFile.LINK_ENTITY_INSPECTION_CASE,
            link_object_id=case.pk,
            projects=project,
        )
        .order_by("-created_at", "-id")
        .distinct()
    ):
        if not (lf.original_name or "").lower().endswith(".json"):
            continue
        try:
            p = pipeline_service.library_absolute_path(lf.relative_path)
            data = json_std.loads(p.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            continue
        if isinstance(data, dict) and SUBMIT_PAYLOAD_REQUIRED_KEYS.issubset(data.keys()):
            return data
    sub = (
        InspectionSubmission.objects.filter(case_id=case.pk, project_id=project.pk)
        .order_by("-submitted_at", "-updated_at", "-id")
        .first()
    )
    if sub is not None:
        raw = sub.raw_payload if isinstance(sub.raw_payload, dict) else {}
        if isinstance(raw, dict) and raw and SUBMIT_PAYLOAD_REQUIRED_KEYS.issubset(raw.keys()):
            return raw
        fb = {
            "taskNo": sub.task_no,
            "projectId": getattr(project, "code", "") or "",
            "reportInfo": sub.report_info or {},
            "hospitalInfo": sub.hospital_info or {},
            "equipmentInfo": sub.equipment_info or {},
            "testResult": sub.test_result or {},
            "conclusion": sub.conclusion or {},
        }
        if sub.submitted_at:
            fb["submittedAt"] = sub.submitted_at.isoformat()
        if SUBMIT_PAYLOAD_REQUIRED_KEYS.issubset(fb.keys()):
            return fb
    tn = str(case.case_no or "").strip()
    if tn:
        got = resolve_submit_payload_for_report(tn, case, project)
        if isinstance(got, dict) and SUBMIT_PAYLOAD_REQUIRED_KEYS.issubset(got.keys()):
            return got
    return None


def collect_report_merge_source_rows(files_ordered: Sequence[LibraryFile]) -> list[dict]:
    """
    报告合并：按勾选顺序收集每份报告 PDF 的可溯源信息。
    traced=True 表示已关联 inspection_case 且解析到完整检测提交 JSON。
    """
    rows: list[dict] = []
    for lf in files_ordered:
        row: dict = {
            "file_id": int(lf.pk),
            "original_name": lf.original_name or "",
            "traced": False,
            "submit": None,
            "case_id": None,
            "path": str(pipeline_service.library_absolute_path(lf.relative_path)),
        }
        if lf.link_entity == LibraryFile.LINK_ENTITY_INSPECTION_CASE and lf.link_object_id:
            case = (
                InspectionCase.objects.select_related("library_project")
                .filter(pk=int(lf.link_object_id))
                .first()
            )
            if case is not None and case.library_project_id:
                project = case.library_project
                payload = _resolve_submit_payload_for_report_merge(case, project)
                if isinstance(payload, dict) and SUBMIT_PAYLOAD_REQUIRED_KEYS.issubset(payload.keys()):
                    row["traced"] = True
                    row["submit"] = payload
                    row["case_id"] = int(case.pk)
        row["title_line"] = _per_report_title_line_for_merge(row)
        row["inspected_org_hint"] = _inspected_org_from_submit_row(row)
        rows.append(row)
    return rows


def _inspected_org_from_submit_row(row: dict) -> str:
    sub = row.get("submit")
    if not isinstance(sub, dict):
        return ""
    hi = sub.get("hospitalInfo") or {}
    if not isinstance(hi, dict):
        return ""
    # 多数模板「受检单位」在 inspection2，「委托/申请单位」在 inspection；优先取受检侧
    for k in (
        "inspection2",
        "inspection",
        "inspectedUnit",
        "inspectedOrganization",
        "hospitalName",
        "entityName",
        "commissionedUnit",
    ):
        v = hi.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def _per_report_title_line_for_merge(row: dict) -> str:
    """单份报告在合并命名中使用的「报告名称」候选串（用于抽取设备类型，见 pdf_merge.MERGED_REPORT_DEVICE_TYPE_LABELS）。"""
    sub = row.get("submit")
    if isinstance(sub, dict):
        ei = sub.get("equipmentInfo") or {}
        if isinstance(ei, dict):
            for k in ("deviceName", "equipmentName", "deviceType", "model"):
                v = ei.get(k)
                if isinstance(v, str) and v.strip():
                    t = v.strip()
                    if "质量控制" in t:
                        return t[:200]
                    return f"{t}质量控制检测"[:200]
    return _friendly_library_report_stem(row.get("original_name") or "")


def build_report_merge_overlay(files_ordered: Sequence[LibraryFile], merge_time=None) -> tuple[dict, str]:
    """
    构造合并 PDF 封面/「一、项目基本情况」页叠印所需字段；返回 (overlay_dict, hint)。

    ``files_ordered`` 须与合并 PDF 的 ``pdf_paths`` 顺序一致：**首项即「第一份小报告」**，
    其前 3 页用作合并稿封面、声明、基本情况版式；叠印字段中的 ``project_name_combined`` 为
    **受检单位名称 + 合并报告名称**（与封面两行项目名称总语义一致）。

    overlay 键由 utils.pdf_merge.apply_merged_report_merge_overlay 消费。
    """
    from utils.pdf_merge import build_merged_report_overlay_fields

    rows = collect_report_merge_source_rows(files_ordered)
    if not rows:
        return {}, "无有效报告行"
    mt = merge_time if merge_time is not None else timezone.now()
    try:
        lt = timezone.localtime(mt)
    except Exception:
        lt = mt
    merge_date_str = f"{lt.year}年{lt.month}月{lt.day}日"
    traced_n = sum(1 for r in rows if r.get("traced"))
    hint_parts: list[str] = []
    if traced_n == len(rows):
        hint_parts.append("封面与基本情况已按检测提交与任务模板溯源数据重写")
    elif traced_n > 0:
        hint_parts.append(f"部分报告已溯源检测提交（{traced_n}/{len(rows)}），其余自 PDF 文本识别补全")
    else:
        hint_parts.append("所选报告未关联案件或缺少检测提交 JSON，封面与基本情况已按 PDF 文本识别补全")
    overlay = build_merged_report_overlay_fields(rows, merge_date_str=merge_date_str)
    return overlay, "；".join(hint_parts)


from apps.api.inspection_report_make import (  # noqa: E402
    _build_filled_template_fields_for_task,
    _build_filled_template_fields_from_submit,
    _persist_filled_pdf_from_submit,
    _report_site_record_source_tasks,
    _resolve_report_task_for_case,
)
