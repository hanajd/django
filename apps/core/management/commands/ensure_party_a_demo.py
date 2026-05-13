"""
甲方演示账号：可走检测业务链（与《检测业务多角色与工作流说明》中的 App 侧角色一致），
通过「文件库仅本人数据」+ 仅绑定指定项目的任务分配，避免看到或操作其他账号的任务与项目数据。

使用前请先在后台或项目管理中准备好「演示项目」：挂载至少一个 LibraryTask（现场记录/报告等），
再执行本命令将该项目下全部任务分配给该用户。

示例::

    python manage.py ensure_party_a_demo \\
        --username party_a_demo \\
        --password '强密码' \\
        --project-code DEMO_PARTY_A \\
        --create-project-if-missing
"""

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand

from apps.core.models import (
    LibraryProject,
    LibraryTaskAssignment,
    Role,
    UserProfile,
)


# 与种子角色 authorized_signatory 对齐：能力覆盖登记、文件库、流程/HTMLPDF 等；
# 再通过 UserProfile.perm_overrides 收紧数据范围并关闭「分配文件库任务」；
# 通过 perm_library_task_templates_write 允许在任务模板库新建并维护本人创建的任务模板；
# 并关闭用户/角色/菜单管理与 party_a_demo_restrictions 标记，禁止演示账号为他人分配角色。
_BASE_ROLE_CODE = "authorized_signatory"

_PARTY_A_OVERRIDES = {
    "perm_file_scope_own_only": True,
    "perm_assign_tasks": False,
    "perm_library_task_templates_write": True,
    "perm_create_library_project": True,
    "party_a_demo_restrictions": True,
    "perm_manage_users": False,
    "perm_manage_roles": False,
    "perm_manage_menus": False,
}


class Command(BaseCommand):
    help = (
        "创建或更新甲方演示账号：全检测链能力 + 仅本人/已分配项目数据，"
        "不向他人分配文件库任务（perm_assign_tasks 关闭），"
        "但可在任务模板库新建并维护本人创建的任务模板（perm_library_task_templates_write）"
    )

    def add_arguments(self, parser):
        parser.add_argument("--username", default="party_a_demo", help="登录用户名")
        parser.add_argument(
            "--password",
            default="party_a_demo_change_me",
            help="初始密码（生产环境请使用强密码）",
        )
        parser.add_argument("--email", default="", help="可选邮箱")
        parser.add_argument(
            "--project-code",
            required=True,
            help="演示用文件库项目编码（须已挂载 LibraryTask；可用 --create-project-if-missing 自动建空项目）",
        )
        parser.add_argument(
            "--create-project-if-missing",
            action="store_true",
            help="若不存在该 code 的项目则创建一条启用中的空项目（仍需后续挂载任务或再次执行以分配）",
        )
        parser.add_argument(
            "--project-name",
            default="",
            help="与 --create-project-if-missing 联用：新建项目的显示名称，默认同 project-code",
        )

    def handle(self, *args, **options):
        username = (options["username"] or "").strip()
        password = options["password"]
        email = (options.get("email") or "").strip()
        project_code = (options["project_code"] or "").strip()
        create_if_missing = bool(options.get("create_project_if_missing"))
        project_name = (options.get("project_name") or "").strip()

        if not username:
            self.stderr.write(self.style.ERROR("username 不能为空"))
            return
        if not project_code:
            self.stderr.write(self.style.ERROR("--project-code 不能为空"))
            return

        role = Role.objects.filter(code=_BASE_ROLE_CODE).first()
        if role is None:
            self.stderr.write(
                self.style.ERROR(
                    f"数据库中不存在角色「{_BASE_ROLE_CODE}」，请先执行: python manage.py migrate"
                )
            )
            return

        project = LibraryProject.objects.filter(code=project_code).first()
        if project is None:
            if not create_if_missing:
                self.stderr.write(
                    self.style.ERROR(
                        f"未找到项目 code={project_code!r}；可加上 --create-project-if-missing 自动创建"
                    )
                )
                return
            display = project_name or project_code
            # created_by 在第二步绑定用户后再写
            project = LibraryProject.objects.create(
                code=project_code,
                name=display,
                description="甲方演示沙箱（由 ensure_party_a_demo 创建）",
                is_active=True,
                created_by=None,
            )
            self.stdout.write(self.style.WARNING(f"已新建空项目: {project.code} / {project.name}"))

        user, _ = User.objects.get_or_create(
            username=username,
            defaults={
                "email": email,
                "is_staff": False,
                "is_superuser": False,
            },
        )
        if email and user.email != email:
            user.email = email
        user.is_staff = False
        user.is_superuser = False
        user.set_password(password)
        user.save()

        prof, _ = UserProfile.objects.get_or_create(user=user)
        merged = dict(prof.perm_overrides) if isinstance(prof.perm_overrides, dict) else {}
        merged.update(_PARTY_A_OVERRIDES)
        prof.role = role
        prof.perm_overrides = merged
        prof.save(update_fields=["role", "perm_overrides", "updated_at"])

        if project.created_by_id is None:
            project.created_by = user
            project.save(update_fields=["created_by", "updated_at"])

        task_qs = project.library_tasks.all()
        n_tasks = task_qs.count()
        if n_tasks == 0:
            self.stdout.write(
                self.style.WARNING(
                    "当前项目未挂载任何文件库任务：请在「项目管理」中为该项目关联 LibraryTask 后，"
                    "重新执行本命令（相同参数）以写入任务分配。"
                )
            )
        else:
            created = 0
            for lt in task_qs:
                _, was_created = LibraryTaskAssignment.objects.get_or_create(
                    project=project,
                    library_task=lt,
                    assignee=user,
                    defaults={"assigned_by": None},
                )
                if was_created:
                    created += 1
            self.stdout.write(
                self.style.SUCCESS(
                    f"已在项目 {project.code} 上为「{username}」写入任务分配："
                    f"共 {n_tasks} 个模板，本次新建分配记录 {created} 条。"
                )
            )

        from apps.core.library_test_account import sync_project_for_solo_tester

        sync_project_for_solo_tester(project, user)
        self.stdout.write(
            self.style.SUCCESS(
                "已同步：五个流程岗位默认指向该用户；任务分配与模板文件关联已按项目当前挂载任务整理。"
            )
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"用户「{username}」已绑定角色 {_BASE_ROLE_CODE}；"
                "权限覆盖：业务登记、文件库、流程处理、HTMLPDF 等（随角色库配置）；"
                "数据范围：仅本人上传/产生的文件库记录，以及本项目中通过任务分配授权的数据；"
                "不向他人分配文件库任务（perm_assign_tasks 关闭），"
                "可在任务模板库新建并维护本人创建的任务模板（perm_library_task_templates_write）；"
                "不可进入用户/角色管理或为他人分配角色（party_a_demo_restrictions + 相关 perm 关闭）。"
            )
        )
        if password == "party_a_demo_change_me":
            self.stdout.write(
                self.style.WARNING(
                    "当前为默认密码，请务必使用 --password 指定强密码并在交付后督促甲方修改。"
                )
            )
