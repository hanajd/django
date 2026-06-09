"""
修复 repair_task_template_library 造成的任务模板问题：
- 报告任务无绑定文件（无法下载/预览/编辑）
- 报告绑定了错误设备类型的模板
- 现场任务 PDF/JSON 文件名 stem 不一致导致无法配对
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.core.db_backup_service import backup_sqlite_database
from apps.core.library_file_service import attach_files_to_tasks, detach_files_from_tasks
from apps.core.library_task_template_binding_service import (
    get_task_template_pair,
    infer_template_file_role,
    replace_task_template_file_binding,
)
from apps.core.models import LibraryFile, LibraryTask


def _get_test_user():
    User = get_user_model()
    user = User.objects.filter(username="test", is_active=True).first()
    if user is None:
        raise CommandError("未找到 test 用户")
    return user


def _site_tasks_for_report(report: LibraryTask) -> list[LibraryTask]:
    sites = list(
        report.report_source_tasks.filter(output_target=LibraryTask.OUTPUT_SITE_RECORD)
    )
    if not sites:
        sites = list(
            report.report_target_tasks.filter(output_target=LibraryTask.OUTPUT_SITE_RECORD)
        )
    return sites


def _template_file_ids(task: LibraryTask) -> list[int]:
    return list(
        task.library_files.filter(category=LibraryFile.CATEGORY_TEMPLATE)
        .order_by("id")
        .values_list("id", flat=True)
    )


def _bind_file(task: LibraryTask, lf: LibraryFile, user) -> str:
    from apps.core.library_access import library_user_may_edit_library_task

    if library_user_may_edit_library_task(user, task):
        replace_task_template_file_binding(
            task=task,
            new_file=lf,
            user=user,
            source="fix_repaired_task_templates",
        )
    else:
        attach_files_to_tasks([lf.pk], [task.pk], user)
    return f"task#{task.pk} <- file#{lf.pk} ({infer_template_file_role(lf)})"


def _unbind_file(task: LibraryTask, file_id: int) -> str | None:
    if not task.library_files.filter(pk=file_id).exists():
        return None
    detach_files_from_tasks([file_id], [task.pk])
    return f"task#{task.pk} x file#{file_id}"


class Command(BaseCommand):
    help = "修复还原后的任务模板绑定（报告补文件、纠错绑、PDF 配对）。"

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--skip-backup", action="store_true")

    def handle(self, *args, **options):
        dry = bool(options.get("dry_run"))
        if not dry and not options.get("skip_backup"):
            bak = backup_sqlite_database(label="pre_fix_repaired_task_templates")
            if not bak.get("ok"):
                raise CommandError(bak.get("error"))
            self.stdout.write(self.style.SUCCESS(f"已备份：{bak['path']}"))

        user = _get_test_user()
        notes: list[str] = []

        def _plan() -> None:
            # 2) 口腔全景状态报告#89：移除误绑的胃肠机模板
            wrong_on_89 = [1981, 1972]
            if dry:
                notes.append(f"report#89 将移除 files={wrong_on_89}")
            else:
                report89 = LibraryTask.objects.filter(pk=89).first()
                if report89:
                    for fid in wrong_on_89:
                        msg = _unbind_file(report89, fid)
                        if msg:
                            notes.append(msg)

            # 3) 现场任务 PDF 配对修正
            site_pdf_fixes = [
                (42, 2038, 2026),
                (66, 2039, 2026),
            ]
            for task_id, json_id, pdf_id in site_pdf_fixes:
                if dry:
                    notes.append(f"site#{task_id} json=#{json_id} pdf=#{pdf_id}")
                    continue
                site = LibraryTask.objects.filter(pk=task_id).first()
                if site is None:
                    continue
                pdf_lf = LibraryFile.objects.filter(pk=pdf_id).first()
                json_lf = LibraryFile.objects.filter(pk=json_id).first()
                if pdf_lf:
                    notes.append(_bind_file(site, pdf_lf, user))
                if json_lf:
                    notes.append(_bind_file(site, json_lf, user))

            # 1) 报告任务：仅当尚无模板时，从现场任务同步（避免覆盖已有报告模板）
            report_ids = [83, 85, 86, 87, 89]
            for rid in report_ids:
                report = LibraryTask.objects.filter(
                    pk=rid, output_target=LibraryTask.OUTPUT_REPORT
                ).first()
                if report is None:
                    continue
                if _template_file_ids(report):
                    notes.append(f"跳过 report#{rid}：已有模板绑定")
                    continue
                sites = _site_tasks_for_report(report)
                if not sites:
                    notes.append(f"跳过 report#{rid}：无关联现场任务")
                    continue
                source_ids: list[int] = []
                for site in sites:
                    source_ids.extend(_template_file_ids(site))
                source_ids = list(dict.fromkeys(source_ids))
                if dry:
                    notes.append(f"report#{rid} 将同步 files={source_ids}")
                    continue
                for fid in source_ids:
                    lf = LibraryFile.objects.filter(pk=fid).first()
                    if lf is None:
                        continue
                    notes.append(_bind_file(report, lf, user))

        if dry:
            _plan()
            for line in notes:
                self.stdout.write(line)
            return

        with transaction.atomic():
            _plan()

        for line in notes:
            self.stdout.write(line)

        self.stdout.write(self.style.SUCCESS("修复完成。正在校验…"))
        for tid in [83, 84, 85, 42, 86, 31, 87, 88, 89, 59, 90, 66]:
            t = LibraryTask.objects.filter(pk=tid).first()
            if not t:
                continue
            pdf, js = get_task_template_pair(t)
            self.stdout.write(
                f"  task#{tid} {t.output_target}: pdf={pdf.pk if pdf else '-'} "
                f"json={js.pk if js else '-'} files={_template_file_ids(t)}"
            )
