"""
甲方演示账号：可走检测业务链（与《检测业务多角色与工作流说明》中的 App 侧角色一致），
通过「文件库仅本人数据」+ 仅绑定指定项目的任务分配，避免看到或操作其他账号的任务与项目数据。

**不提供** ``--project-code`` 时：只创建/更新演示用户（登录名、密码、角色与 ``perm_overrides``），
并仍会在目标用户名为 ``test`` 时尝试将单独的 ``party_a_demo`` 重命名为 ``test``；**不会**查找项目、
写入任务分配或调用 ``sync_project_for_solo_tester``。适合仅需改登录名或密码、不想动项目编码的场景。

若库里仍是旧名 ``party_a_demo``，执行 ``python manage.py ensure_party_a_demo``（默认 ``--username test``）
即可在无其它「test」用户时自动重命名；若已存在重名，请在「用户管理」中手工处理。

需要把演示账号与项目绑定时，带上 ``--project-code``（及可选 ``--create-project-if-missing``）即可；
**默认不会**写入 ``LibraryTaskAssignment`` 或同步流程岗（便于 test 在「分配」页自行操作）；若仍需命令行一键写入，请加 ``--assign-project-tasks``。

示例::

    # 仅主账号 test
    python manage.py ensure_party_a_demo --username test --password '主账号密码'

    # 主账号 test + 五个同伴（test-1 … test-5，密码分别为 password111 … password555）
    python manage.py ensure_party_a_demo --username test --password '主账号密码' --provision-test-peers

    # 只创建/更新 test-1 … test-5（不改 test 主账号密码）
    python manage.py ensure_party_a_demo --provision-test-peers --skip-main-user

    # 用户 + 关联演示项目（不自动分配任务；登录后在项目「分配」页自行分配）
    python manage.py ensure_party_a_demo \\
        --username test \\
        --password '强密码' \\
        --project-code DEMO_PARTY_A \\
        --create-project-if-missing

    # 运维一键：仍写入任务分配并同步流程岗（旧行为）
    python manage.py ensure_party_a_demo \\
        --username test \\
        --password '强密码' \\
        --project-code DEMO_PARTY_A \\
        --assign-project-tasks
"""

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand

from apps.core.models import (
    LibraryProject,
    LibraryTaskAssignment,
    Role,
    UserProfile,
)


# 与种子角色 authorized_signatory 对齐：能力覆盖登记、文件库、流程/模板编辑器等；
# 再通过 UserProfile.perm_overrides 收紧数据范围并关闭「分配文件库任务」；
# 通过 perm_library_task_templates_write 允许在任务模板库新建并维护本人创建的任务模板；
# 并关闭用户/角色/菜单管理与 party_a_demo_restrictions 标记，禁止演示账号为他人分配角色。
_BASE_ROLE_CODE = "authorized_signatory"

_PARTY_A_OVERRIDES = {
    "perm_file_scope_own_only": True,
    "perm_assign_tasks": False,
    "perm_library_task_templates_write": True,
    "perm_create_library_project": True,
    "perm_biz_registry": True,
    "party_a_demo_restrictions": True,
    "perm_manage_users": False,
    "perm_manage_roles": False,
    "perm_manage_menus": False,
}


class Command(BaseCommand):
    help = (
        "创建或更新甲方演示账号：全检测链能力 + 仅本人/已分配项目数据。"
        "提供 --project-code 时默认只关联/创建项目，不自动写入任务分配（便于在界面「分配」体验流程）；"
        "加 --assign-project-tasks 可恢复命令行一键分配。"
        "不向他人分配文件库任务（perm_assign_tasks 关闭），"
        "但可在任务模板库新建并维护本人创建的任务模板（perm_library_task_templates_write）。"
        "省略 --project-code 时只更新用户与权限覆盖，不调整任何项目。"
    )

    def add_arguments(self, parser):
        parser.add_argument("--username", default="test", help="登录用户名（默认 test）")
        parser.add_argument(
            "--password",
            default="test_change_me",
            help="初始密码（生产环境请使用强密码）",
        )
        parser.add_argument("--email", default="", help="可选邮箱")
        parser.add_argument(
            "--project-code",
            default="",
            help=(
                "可选。演示用文件库项目编码；提供时须已挂载 LibraryTask（或用 --create-project-if-missing 建空项目）。"
                "省略则只更新用户/密码/角色与权限覆盖，不写任务分配、不改项目。"
            ),
        )
        parser.add_argument(
            "--create-project-if-missing",
            action="store_true",
            help="仅在与 --project-code 联用时生效：若不存在该 code 的项目则创建启用中的空项目",
        )
        parser.add_argument(
            "--project-name",
            default="",
            help="与 --create-project-if-missing 联用：新建项目的显示名称，默认同 project-code",
        )
        parser.add_argument(
            "--assign-project-tasks",
            action="store_true",
            help=(
                "仅在与 --project-code 联用且项目已挂载 LibraryTask 时生效："
                "写入该用户在本项目上的任务分配并同步流程岗位（旧版一键行为）。默认关闭，便于在网页「分配」中自行体验。"
            ),
        )
        parser.add_argument(
            "--provision-test-peers",
            action="store_true",
            help=(
                "额外创建 test-1 … test-5，权限与主账号相同，可互相编辑任务模板；"
                "默认密码为 password111、password222 … password555（可用 --peer-password-prefix 改前缀）。"
            ),
        )
        parser.add_argument(
            "--peer-password-prefix",
            default="password",
            help="与 --provision-test-peers 联用：同伴密码为 {前缀}111、{前缀}222 …（默认前缀 password）",
        )
        parser.add_argument(
            "--skip-main-user",
            action="store_true",
            help="与 --provision-test-peers 联用：不创建/更新 --username 主账号，仅处理 test-1 … test-5",
        )

    def handle(self, *args, **options):
        username = (options["username"] or "").strip()
        password = options["password"]
        email = (options.get("email") or "").strip()
        project_code = (options.get("project_code") or "").strip()
        create_if_missing = bool(options.get("create_project_if_missing"))
        project_name = (options.get("project_name") or "").strip()
        assign_project_tasks = bool(options.get("assign_project_tasks"))
        provision_test_peers = bool(options.get("provision_test_peers"))
        peer_password_prefix = (options.get("peer_password_prefix") or "password").strip()
        skip_main_user = bool(options.get("skip_main_user"))

        if not username and not (provision_test_peers and skip_main_user):
            self.stderr.write(self.style.ERROR("username 不能为空（或加 --provision-test-peers --skip-main-user 仅创建同伴）"))
            return
        if skip_main_user and not provision_test_peers:
            self.stderr.write(self.style.ERROR("--skip-main-user 须与 --provision-test-peers 一起使用"))
            return

        def _default_peer_password(index: int) -> str:
            """test-1 → password111，test-2 → password222，…"""
            suffix = str(index * 111)
            return f"{peer_password_prefix}{suffix}"

        def _upsert_peer(uname: str, peer_password: str) -> None:
            peer, _ = User.objects.get_or_create(
                username=uname,
                defaults={"email": email, "is_staff": False, "is_superuser": False},
            )
            peer.is_staff = False
            peer.is_superuser = False
            peer.set_password(peer_password)
            peer.save()
            pprof, _ = UserProfile.objects.get_or_create(user=peer)
            pmerged = dict(pprof.perm_overrides) if isinstance(pprof.perm_overrides, dict) else {}
            pmerged.update(_PARTY_A_OVERRIDES)
            pprof.role = role
            pprof.perm_overrides = pmerged
            pprof.save(update_fields=["role", "perm_overrides", "updated_at"])

        if create_if_missing and not project_code:
            self.stdout.write(
                self.style.WARNING("已忽略 --create-project-if-missing（未提供 --project-code）。")
            )

        # 默认演示名改为 test 后：若库里仍只有 party_a_demo，自动重命名，避免用户管理里一直显示旧名
        target_username = username
        legacy_demo = User.objects.filter(username__iexact="party_a_demo").first()
        if legacy_demo is not None and target_username.lower() == "test":
            other_test = (
                User.objects.filter(username__iexact="test")
                .exclude(pk=legacy_demo.pk)
                .first()
            )
            if other_test is not None:
                self.stderr.write(
                    self.style.ERROR(
                        "已存在另一用户名为「test」的账号，无法将「party_a_demo」自动重命名。"
                        "请在用户管理中删除或改名其中一方后再执行。"
                    )
                )
                return
            if legacy_demo.username != target_username:
                legacy_demo.username = target_username
                legacy_demo.save(update_fields=["username"])
                self.stdout.write(
                    self.style.SUCCESS(
                        f"已将数据库中的旧演示登录名「party_a_demo」重命名为「{target_username}」。"
                    )
                )

        role = Role.objects.filter(code=_BASE_ROLE_CODE).first()
        if role is None:
            self.stderr.write(
                self.style.ERROR(
                    f"数据库中不存在角色「{_BASE_ROLE_CODE}」，请先执行: python manage.py migrate"
                )
            )
            return

        user = None
        if not skip_main_user:
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

        if provision_test_peers:
            peer_lines: list[str] = []
            for i in range(1, 6):
                uname = f"test-{i}"
                pw = _default_peer_password(i)
                _upsert_peer(uname, pw)
                peer_lines.append(f"  {uname} / {pw}")
            self.stdout.write(
                self.style.SUCCESS(
                    "已创建/更新 test-1 … test-5（与 test 相同权限，可互相维护任务模板）：\n"
                    + "\n".join(peer_lines)
                )
            )

        if skip_main_user:
            if not project_code:
                return
            if user is None:
                user = User.objects.filter(username=username).first()
            if user is None:
                self.stderr.write(
                    self.style.ERROR(
                        f"未找到主账号「{username}」，绑定项目请先创建主账号或去掉 --skip-main-user"
                    )
                )
                return

        if not project_code:
            if not skip_main_user:
                self.stdout.write(
                    self.style.SUCCESS(
                        f"用户「{username}」已更新（角色 {_BASE_ROLE_CODE}、演示权限覆盖已写入）。"
                        "未提供 --project-code：已跳过项目查找、任务分配与流程岗位同步。"
                        "需要绑定演示项目时请再次执行并加上 --project-code。"
                    )
                )
                if password == "test_change_me":
                    self.stdout.write(
                        self.style.WARNING(
                            "当前为默认密码，请务必使用 --password 指定强密码并在交付后督促甲方修改。"
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
            project = LibraryProject.objects.create(
                code=project_code,
                name=display,
                description="甲方演示沙箱（由 ensure_party_a_demo 创建）",
                is_active=True,
                created_by=None,
            )
            self.stdout.write(self.style.WARNING(f"已新建空项目: {project.code} / {project.name}"))

        if project.created_by_id is None:
            project.created_by = user
            project.save(update_fields=["created_by", "updated_at"])

        if assign_project_tasks and project.primary_responsible_id != user.id:
            project.primary_responsible = user
            project.save(update_fields=["primary_responsible", "updated_at"])

        task_qs = project.library_tasks.all()
        n_tasks = task_qs.count()
        if n_tasks == 0:
            self.stdout.write(
                self.style.WARNING(
                    "当前项目未挂载任何文件库任务：请在「项目管理 → 任务与模板」中为该项目关联 LibraryTask 后，"
                    "在「分配」页面向本人分配；若需命令行一键写入，请重新执行并加 --assign-project-tasks。"
                )
            )
        elif assign_project_tasks:
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
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    f"已关联项目 {project.code}（已挂载 {n_tasks} 个任务模板）。"
                    f"未加 --assign-project-tasks：已跳过自动任务分配与流程岗同步；"
                    f"请使用「{username}」登录后在「项目管理 → 分配」中向本人分配以体验完整流程。"
                )
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"用户「{username}」已绑定角色 {_BASE_ROLE_CODE}；"
                "权限覆盖：业务登记、文件库、OCR 处理、模板编辑器等（随角色库配置）；"
                "数据范围：仅本人上传/产生的文件库记录，以及本项目中通过任务分配授权的数据；"
                "不向他人分配文件库任务（perm_assign_tasks 关闭），"
                "可在任务模板库新建并维护本人创建的任务模板（perm_library_task_templates_write）；"
                "不可进入用户/角色管理或为他人分配角色（party_a_demo_restrictions + 相关 perm 关闭）。"
            )
        )
        if password == "test_change_me":
            self.stdout.write(
                self.style.WARNING(
                    "当前为默认密码，请务必使用 --password 指定强密码并在交付后督促甲方修改。"
                )
            )
