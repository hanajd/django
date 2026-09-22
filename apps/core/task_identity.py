"""Stable identity helpers for project inspection tasks.

``taskNo`` is a display/order value and can change when project tasks are
reordered.  New inspection data uses the immutable IDs represented by a
``taskKey`` instead.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from apps.core.models import LibraryProjectEquipment, LibraryTask


TASK_KEY_RE = re.compile(r"^P(?P<project>\d+)-E(?P<equipment>\d+)-(?P<target>[SR])(?P<task>\d+)$")


@dataclass(frozen=True)
class ParsedTaskKey:
    project_id: int
    project_equipment_id: int
    output_target: str
    library_task_id: int


def build_task_key(
    *,
    project_id: int,
    project_equipment_id: int,
    library_task_id: int,
    output_target: str,
) -> str:
    target = "R" if output_target == LibraryTask.OUTPUT_REPORT else "S"
    return f"P{int(project_id)}-E{int(project_equipment_id)}-{target}{int(library_task_id)}"


def parse_task_key(value: str | None) -> ParsedTaskKey | None:
    match = TASK_KEY_RE.fullmatch(str(value or "").strip())
    if match is None:
        return None
    target = match.group("target")
    return ParsedTaskKey(
        project_id=int(match.group("project")),
        project_equipment_id=int(match.group("equipment")),
        output_target=(
            LibraryTask.OUTPUT_REPORT if target == "R" else LibraryTask.OUTPUT_SITE_RECORD
        ),
        library_task_id=int(match.group("task")),
    )


def task_links_for_library_task(project, library_task: LibraryTask | None):
    """Return project equipment links that own the library task.

    A report task is owned directly by ``report_task``.  A site task is owned
    through the report's ``report_source_tasks`` relation.
    """
    if project is None or library_task is None:
        return []
    qs = LibraryProjectEquipment.objects.filter(project_id=project.pk)
    if library_task.output_target == LibraryTask.OUTPUT_REPORT:
        qs = qs.filter(report_task_id=library_task.pk)
    else:
        qs = qs.filter(report_task__report_source_tasks=library_task)
    return list(qs.order_by("sort_order", "id").distinct())


def task_key_for_library_task(
    project,
    library_task: LibraryTask | None,
    *,
    project_equipment_id: int | None = None,
) -> str:
    """Build a task key when the project-task ownership is unambiguous.

    ``E0`` is used only for old projects which have no project-equipment
    configuration.  It still gives those projects a stable project/task
    identity without guessing an equipment link.
    """
    if project is None or library_task is None:
        return ""
    links = task_links_for_library_task(project, library_task)
    if project_equipment_id:
        links = [x for x in links if int(x.pk) == int(project_equipment_id)]
    if len(links) > 1:
        return ""
    if not links and LibraryProjectEquipment.objects.filter(project_id=project.pk).exists():
        # Once a project uses equipment links, an unowned task is unsafe to route.
        return ""
    equipment_id = int(links[0].pk) if links else 0
    return build_task_key(
        project_id=project.pk,
        project_equipment_id=equipment_id,
        library_task_id=library_task.pk,
        output_target=library_task.output_target,
    )


def report_task_for_site_task(project, site_task: LibraryTask | None):
    """Return the single configured report task for a site task, if unambiguous."""
    if project is None or site_task is None:
        return None
    report_tasks = list(
        LibraryTask.objects.filter(
            projects=project,
            output_target=LibraryTask.OUTPUT_REPORT,
            report_source_tasks=site_task,
        )
        .order_by("code", "id")
        .distinct()
    )
    if len(report_tasks) != 1:
        return None
    return report_tasks[0]


def resolve_library_task_for_key(project, task_key: str | None):
    """Resolve and validate a stable task key within the supplied project."""
    parsed = parse_task_key(task_key)
    if parsed is None or project is None or int(project.pk) != parsed.project_id:
        return None
    task = LibraryTask.objects.filter(
        pk=parsed.library_task_id,
        output_target=parsed.output_target,
        projects=project,
    ).first()
    if task is None:
        return None
    if parsed.project_equipment_id:
        links = task_links_for_library_task(project, task)
        if not any(int(x.pk) == parsed.project_equipment_id for x in links):
            return None
    elif task_links_for_library_task(project, task):
        # E0 is reserved for projects without the newer equipment binding.
        return None
    return task
