"""
根据前端模板 JSON 生成模拟检测提交 JSON，并可预览 PDF 回填结果。

示例（JS009 / 项目 260001 / 任务 10）::

    python manage.py mock_inspection_submit \\
      --frontend-json media/file_library/templates/001-验收检测/01-CT/site/jxfs-js009-v30-xct202641-1/auxiliary/1f25ef3cdc25485294ec75beb090e85c.json \\
      --project 260001 \\
      --task-no 10 \\
      --output /tmp/mock_submit.json \\
      --backfill-preview /tmp/mock_backfill_preview.json

也可先 curl 保存 export-frontend-json 响应，再传入 --frontend-json。
"""
from __future__ import annotations

import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.api.mock_inspection_submit_generator import (
    build_mock_submit_from_frontend_template,
    enrich_mock_submit_with_task_binding,
    load_frontend_template_json,
    preview_backfill_fields,
    summarize_filled_fields_for_instruments,
)
from apps.api.inspection_pdf_service import _resolve_library_task_for_task_no
from apps.core.models import LibraryProject


class Command(BaseCommand):
    help = "从前端模板 JSON 生成模拟检测提交 JSON，并可选预览回填字段"

    def add_arguments(self, parser):
        parser.add_argument(
            "--frontend-json",
            required=True,
            help="export-frontend-json 或 auxiliary 前端模板 JSON 路径",
        )
        parser.add_argument("--project", default="260001", help="项目编号 projectId")
        parser.add_argument("--task-no", default="10", help="任务序号（如 10）或 case taskNo")
        parser.add_argument(
            "--report-type",
            default="ct_qc",
            help="reportType（与模板库一致，如 ct、radiationQc；或历史 ct_qc / xray_fluoroscopy）",
        )
        parser.add_argument(
            "--fill-ratio",
            type=float,
            default=1.0,
            help="0~1，模拟填写比例（1=模板可见栏位尽量全填）",
        )
        parser.add_argument("--output", "-o", help="写出模拟提交 JSON 路径")
        parser.add_argument(
            "--merge-task-binding",
            action="store_true",
            default=True,
            help="用任务/项目仪器绑定规范化 instruments（默认开启）",
        )
        parser.add_argument(
            "--no-merge-task-binding",
            action="store_false",
            dest="merge_task_binding",
            help="仅使用模板根级 instruments / instrumentKinds",
        )
        parser.add_argument(
            "--backfill-preview",
            help="写出回填摘要 JSON（含 f630/f631 等 content 抽样）",
        )
        parser.add_argument(
            "--backfill-fields",
            help="写出完整 filled_fields JSON（体积较大，调试用）",
        )
        parser.add_argument("--placeholder-map-id", default="", help="placeholderMapId（可选）")

    def handle(self, *args, **options):
        fe_path = Path(options["frontend_json"]).expanduser()
        if not fe_path.is_file():
            raise CommandError(f"前端模板不存在: {fe_path}")

        frontend_obj = load_frontend_template_json(fe_path)
        project_code = str(options["project"]).strip()
        task_no = str(options["task_no"]).strip()

        payload = build_mock_submit_from_frontend_template(
            frontend_obj,
            project_id=project_code,
            task_no=task_no,
            report_type=str(options["report_type"]).strip(),
            fill_ratio=float(options["fill_ratio"]),
        )

        project = LibraryProject.objects.filter(code=project_code).first()
        task_obj = None
        if project is not None:
            task_obj = _resolve_library_task_for_task_no(task_no, project)

        if options["merge_task_binding"] and project is not None and task_obj is not None:
            payload = enrich_mock_submit_with_task_binding(
                payload, project=project, library_task=task_obj
            )
            self.stdout.write(
                self.style.SUCCESS(
                    f"已合并任务绑定仪器: project={project_code} task={task_obj.pk}"
                )
            )
        elif options["merge_task_binding"]:
            self.stdout.write(
                self.style.WARNING(
                    "未找到项目/任务，跳过仪器绑定合并（仅使用模板内 instruments）"
                )
            )

        meta = payload.pop("_mockMeta", {})
        out_path = options.get("output")
        if out_path:
            Path(out_path).expanduser().write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            self.stdout.write(self.style.SUCCESS(f"模拟提交已写入: {out_path}"))
        else:
            self.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2))

        if meta:
            self.stdout.write(
                f"模拟统计: 扫描栏位 {meta.get('fieldCount')}，写入 {meta.get('filledCount')}"
            )

        if options.get("backfill_preview") or options.get("backfill_fields"):
            if project is None or task_obj is None:
                raise CommandError("回填预览需要有效 project + task-no")
            filled, msg = preview_backfill_fields(
                payload,
                project=project,
                library_task=task_obj,
                task_no=task_no,
                map_id=(options.get("placeholder_map_id") or "").strip() or None,
            )
            if msg != "ok":
                raise CommandError(msg)
            preview_path = options.get("backfill_preview")
            if preview_path:
                sample = summarize_filled_fields_for_instruments(filled)
                summary = {
                    "taskNo": task_no,
                    "projectId": project_code,
                    "samplePdfFields": sample,
                    "instrumentFieldCount": sum(
                        1
                        for r in filled
                        if isinstance(r, dict)
                        and str(r.get("pdfFieldId") or "") in ("f630", "f631")
                    ),
                }
                Path(preview_path).expanduser().write_text(
                    json.dumps(summary, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                self.stdout.write(self.style.SUCCESS(f"回填摘要: {preview_path}"))
                for pid, text in sample.items():
                    snippet = text[:120] + ("…" if len(text) > 120 else "")
                    self.stdout.write(f"  {pid}: {snippet}")

            fields_path = options.get("backfill_fields")
            if fields_path:
                Path(fields_path).expanduser().write_text(
                    json.dumps(filled, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                self.stdout.write(self.style.SUCCESS(f"filled_fields: {fields_path}"))
