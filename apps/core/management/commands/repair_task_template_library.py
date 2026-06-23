"""
补齐任务模板库目录缺口（对应分析中的二、三、四节）：
- 在空设备夹下创建报告/现场任务（created_by=test，test 同组可见可编辑）
- 将已有现场任务挂到正确设备夹下的报告
- 为缺现场记录的报告补链现场任务
- 绑定文件库最新 JSON/PDF
执行前自动备份数据库。
"""

from __future__ import annotations

import json
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils.text import slugify

from apps.core import pipeline_service
from apps.core.db_backup_service import backup_sqlite_database
from apps.core.equipment_device_type_service import (
    INSPECTION_TYPE_FOLDER_SPECS,
    find_device_type_folder,
    find_inspection_type_root_folder,
)
from apps.core.library_file_service import attach_files_to_tasks, library_file_exists_on_disk
from apps.core.library_task_template_binding_service import (
    infer_template_file_role,
    replace_task_template_file_binding,
)
from apps.core.models import LibraryFile, LibraryTask


def _unique_task_code(name: str, explicit: str = "") -> str:
    base = (slugify(explicit)[:64] if explicit else "") or slugify(name)[:64] or "task"
    code = base
    n = 0
    while LibraryTask.objects.filter(code=code).exists():
        n += 1
        suffix = f"-{n}"
        code = (base[: max(1, 64 - len(suffix))] + suffix)[:64]
    return code


def _get_test_user():
    User = get_user_model()
    user = User.objects.filter(username="test", is_active=True).first()
    if user is None:
        raise CommandError("未找到活跃用户 test，请先运行 ensure_party_a_demo")
    return user


def _device_folder(inspection_type: str, device: str):
    spec = next((s for s in INSPECTION_TYPE_FOLDER_SPECS if s["inspection_type"] == inspection_type), None)
    if spec is None:
        return None
    root = find_inspection_type_root_folder(spec)
    if root is None:
        return None
    return find_device_type_folder(root, device)


def _find_pdf_for_json(json_lf: LibraryFile) -> LibraryFile | None:
    if json_lf is None:
        return None
    try:
        obj = json.loads(
            pipeline_service.library_absolute_path(json_lf.relative_path).read_text(
                encoding="utf-8", errors="replace"
            )
        )
    except Exception:
        obj = {}
    pdf_name = ""
    if isinstance(obj, dict):
        pdf_name = str((obj.get("pdf") or {}).get("template_file_name") or "").strip()
        if not pdf_name:
            src = obj.get("source") if isinstance(obj.get("source"), dict) else {}
            pdf_name = str(src.get("template_file_name") or "").strip()
    candidates: list[LibraryFile] = []
    if pdf_name:
        candidates.extend(
            LibraryFile.objects.filter(
                category=LibraryFile.CATEGORY_TEMPLATE,
                deleted_at__isnull=True,
                original_name=pdf_name,
            )
        )
        if not pdf_name.lower().endswith(".pdf"):
            candidates.extend(
                LibraryFile.objects.filter(
                    category=LibraryFile.CATEGORY_TEMPLATE,
                    deleted_at__isnull=True,
                    original_name=f"{pdf_name}.pdf",
                )
            )
    stem = Path(json_lf.original_name or "").stem
    if stem:
        for lf in LibraryFile.objects.filter(
            category=LibraryFile.CATEGORY_TEMPLATE,
            deleted_at__isnull=True,
            original_name__istartswith=stem[:40],
        ):
            if (lf.original_name or "").lower().endswith(".pdf"):
                candidates.append(lf)
    seen: set[int] = set()
    for lf in candidates:
        if lf.pk in seen:
            continue
        seen.add(lf.pk)
        if library_file_exists_on_disk(lf):
            return lf
    return None


def _bind_template_files(task: LibraryTask, json_id: int | None, pdf_id: int | None, user) -> list[str]:
    """管理命令内绑定：优先走正式轮换（写历史），无权限时直接 attach。"""
    notes: list[str] = []

    def _bind_one(lf: LibraryFile) -> None:
        if not lf or not library_file_exists_on_disk(lf):
            return
        from apps.core.library_access import library_user_may_edit_library_task

        if library_user_may_edit_library_task(user, task):
            replace_task_template_file_binding(
                task=task, new_file=lf, user=user, source="repair_task_template_library"
            )
        else:
            attach_files_to_tasks([lf.pk], [task.pk], user)
        notes.append(f"task#{task.pk} {infer_template_file_role(lf) or 'file'}=#{lf.pk}")

    if json_id:
        lf = LibraryFile.objects.filter(
            pk=json_id, category=LibraryFile.CATEGORY_TEMPLATE, deleted_at__isnull=True
        ).first()
        if lf:
            _bind_one(lf)
            if pdf_id is None:
                pdf = _find_pdf_for_json(lf)
                if pdf:
                    pdf_id = pdf.pk
    if pdf_id:
        lf = LibraryFile.objects.filter(
            pk=pdf_id, category=LibraryFile.CATEGORY_TEMPLATE, deleted_at__isnull=True
        ).first()
        if lf and infer_template_file_role(lf):
            _bind_one(lf)
    return notes


def _ensure_report(
    *,
    folder,
    name: str,
    code: str,
    user,
    json_id: int | None = None,
    pdf_id: int | None = None,
    existing_report_id: int | None = None,
) -> tuple[LibraryTask, list[str], bool]:
    notes: list[str] = []
    created = False
    if existing_report_id:
        task = LibraryTask.objects.filter(pk=existing_report_id, output_target=LibraryTask.OUTPUT_REPORT).first()
        if task is None:
            raise CommandError(f"报告任务 #{existing_report_id} 不存在")
        if task.task_folder_id != folder.pk:
            task.task_folder = folder
            task.save(update_fields=["task_folder", "updated_at"])
            notes.append(f"报告#{task.pk} 移入文件夹#{folder.pk}")
    else:
        existing = LibraryTask.objects.filter(task_folder=folder, output_target=LibraryTask.OUTPUT_REPORT).first()
        if existing:
            task = existing
            notes.append(f"复用已有报告#{task.pk}")
        else:
            task = LibraryTask.objects.create(
                code=_unique_task_code(name, code),
                name=name,
                output_target=LibraryTask.OUTPUT_REPORT,
                task_folder=folder,
                created_by=user,
            )
            created = True
            notes.append(f"新建报告#{task.pk} {task.code}")
    notes.extend(_bind_template_files(task, json_id, pdf_id, user))
    return task, notes, created


def _ensure_site(
    *,
    report: LibraryTask,
    name: str,
    code: str,
    user,
    json_id: int | None = None,
    pdf_id: int | None = None,
    reuse_site_task_id: int | None = None,
) -> tuple[LibraryTask, list[str], bool]:
    notes: list[str] = []
    created = False
    if reuse_site_task_id:
        site = LibraryTask.objects.filter(pk=reuse_site_task_id, output_target=LibraryTask.OUTPUT_SITE_RECORD).first()
        if site is None:
            raise CommandError(f"现场任务 #{reuse_site_task_id} 不存在")
        if not site.report_target_tasks.filter(pk=report.pk).exists():
            site.report_target_tasks.add(report)
            notes.append(f"现场#{site.pk} 关联到报告#{report.pk}")
        task = site
    else:
        site = report.report_source_tasks.filter(output_target=LibraryTask.OUTPUT_SITE_RECORD).first()
        if site is None:
            site = report.report_target_tasks.filter(output_target=LibraryTask.OUTPUT_SITE_RECORD).first()
        if site:
            task = site
            notes.append(f"复用报告已有现场#{site.pk}")
        else:
            task = LibraryTask.objects.create(
                code=_unique_task_code(name, code),
                name=name,
                output_target=LibraryTask.OUTPUT_SITE_RECORD,
                created_by=user,
            )
            report.report_source_tasks.add(task)
            created = True
            notes.append(f"新建现场#{task.pk} {task.code}")
    notes.extend(_bind_template_files(task, json_id, pdf_id, user))
    return task, notes, created


class Command(BaseCommand):
    help = "补齐任务模板库设备夹缺口并绑定文件库模板（执行前自动备份）。"

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="仅打印计划，不写库")
        parser.add_argument("--skip-backup", action="store_true", help="跳过执行前自动备份")

    def handle(self, *args, **options):
        dry = bool(options.get("dry_run"))
        if not dry and not options.get("skip_backup"):
            bak = backup_sqlite_database(label="pre_repair_task_template_library")
            if not bak.get("ok"):
                raise CommandError(f"自动备份失败：{bak.get('error')}")
            self.stdout.write(self.style.SUCCESS(f"已自动备份：{bak['path']}"))

        user = _get_test_user()
        plan_notes: list[str] = []

        slot_specs = [
            {
                "inspection": "验收检测",
                "device": "口腔全景",
                "report": {
                    "name": "放射诊疗设备（口腔全景）工作场所放射防护检测（验收检测）",
                    "code": "panorama-accept-report",
                    "json_id": None,
                    "pdf_id": None,
                },
                "site": {
                    "name": "JXFS-JS008 口腔全景现场记录（验收检测）",
                    "code": "panorama-accept-site",
                    "json_id": 2802,
                    "pdf_id": 2792,
                    "reuse_site_task_id": None,
                },
            },
            {
                "inspection": "验收检测",
                "device": "加速器中的CBCT",
                "report": {
                    "name": "加速器中的CBCT质量控制检测（验收检测）",
                    "code": "cbct-linac-accept-report",
                    "json_id": None,
                },
                "site": {
                    "name": "JXFS-JS117 加速器CBCT现场记录（验收检测）",
                    "code": "cbct-linac-accept-site",
                    "json_id": 2038,
                    "reuse_site_task_id": 42,
                },
            },
            {
                "inspection": "状态检测",
                "device": "乳腺DR",
                "report": {
                    "name": "放射诊疗设备（乳腺DR）质量控制检测（状态检测）",
                    "code": "mammography-status-report",
                    "json_id": None,
                },
                "site": {
                    "name": "JXFS-JS005 乳腺DR现场记录（状态检测）",
                    "code": "mammography-status-site",
                    "json_id": 2098,
                    "reuse_site_task_id": 31,
                },
            },
            {
                "inspection": "状态检测",
                "device": "口腔CBCT",
                "report": {
                    "name": "放射诊疗设备（口腔CBCT）质量控制检测（状态检测）",
                    "code": "oral-cbct-status-report",
                    "json_id": None,
                },
                "site": {
                    "name": "JXFS-JS116 口腔CBCT现场记录（状态检测）",
                    "code": "oral-cbct-status-site",
                    "json_id": 2112,
                    "reuse_site_task_id": None,
                },
            },
            {
                "inspection": "状态检测",
                "device": "口腔全景",
                "report": {
                    "name": "放射诊疗设备（口腔全景）质量控制检测（状态检测）",
                    "code": "panorama-status-report",
                    "json_id": 1981,
                },
                "site": {
                    "name": "JXFS-JS008 口腔全景现场记录（状态检测）",
                    "code": "panorama-status-site",
                    "json_id": 2827,
                    "pdf_id": 2824,
                    "reuse_site_task_id": None,
                },
            },
            {
                "inspection": "状态检测",
                "device": "加速器中的CBCT",
                "report": {
                    "name": "加速器中的CBCT质量控制检测（状态检测）",
                    "code": "cbct-linac-status-report",
                    "json_id": 2044,
                    "pdf_id": 2032,
                    "existing_report_id": None,
                    "replace_report_id": 47,
                },
                "site": {
                    "name": "JXFS-JS117 加速器CBCT现场记录（状态检测）",
                    "code": "cbct-linac-status-site",
                    "json_id": 2039,
                    "reuse_site_task_id": 66,
                },
            },
        ]

        extra_binds = [
            {"task_id": 81, "json_id": 2091, "note": "武宁妇幼验收报告补绑 JSON"},
        ]

        if dry:
            self.stdout.write(self.style.WARNING("DRY-RUN："))
            for spec in slot_specs:
                self.stdout.write(f"[{spec['inspection']}] {spec['device']}")
                self.stdout.write(f"  报告: {spec['report']}")
                self.stdout.write(f"  现场: {spec['site']}")
            for b in extra_binds:
                self.stdout.write(f"  额外绑定: {b}")
            return

        with transaction.atomic():
            for spec in slot_specs:
                folder = _device_folder(spec["inspection"], spec["device"])
                if folder is None:
                    raise CommandError(f"未找到文件夹 [{spec['inspection']}] {spec['device']}")

                rep_cfg = spec["report"]
                replace_id = rep_cfg.get("replace_report_id")
                if replace_id:
                    old = LibraryTask.objects.filter(pk=replace_id).first()
                    if old and old.task_folder_id == folder.pk:
                        old.task_folder = None
                        old.save(update_fields=["task_folder", "updated_at"])
                        plan_notes.append(f"旧报告#{old.pk} 移出设备夹（内容不匹配）")

                report, rep_notes, _ = _ensure_report(
                    folder=folder,
                    name=rep_cfg["name"],
                    code=rep_cfg["code"],
                    user=user,
                    json_id=rep_cfg.get("json_id"),
                    pdf_id=rep_cfg.get("pdf_id"),
                    existing_report_id=rep_cfg.get("existing_report_id"),
                )
                plan_notes.extend(rep_notes)

                site_cfg = spec["site"]
                _, site_notes, _ = _ensure_site(
                    report=report,
                    name=site_cfg["name"],
                    code=site_cfg["code"],
                    user=user,
                    json_id=site_cfg.get("json_id"),
                    pdf_id=site_cfg.get("pdf_id"),
                    reuse_site_task_id=site_cfg.get("reuse_site_task_id"),
                )
                plan_notes.extend(site_notes)

            for bind in extra_binds:
                task = LibraryTask.objects.filter(pk=bind["task_id"]).first()
                if task is None:
                    continue
                plan_notes.extend(_bind_template_files(task, bind.get("json_id"), bind.get("pdf_id"), user))
                plan_notes.append(bind.get("note") or "")

        for line in plan_notes:
            self.stdout.write(line)
        self.stdout.write(self.style.SUCCESS(f"完成，共 {len(plan_notes)} 项操作（created_by=test）"))
