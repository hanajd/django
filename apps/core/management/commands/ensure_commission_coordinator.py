"""
创建或更新「委托统筹」账号：精简后台，可立项并管理团队内检测流程用户。

示例::

    python manage.py ensure_commission_coordinator \\
        --username coordinator \\
        --password '强密码' \\
        --email ops@example.com
"""

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand

from apps.core.library_access import ROLE_DEFAULT_PERMS_BY_CODE, COMMISSION_COORDINATOR_ROLE_CODE
from apps.core.models import Role, UserProfile


class Command(BaseCommand):
    help = "创建/更新委托统筹账号（commission_coordinator 角色）"

    def add_arguments(self, parser):
        parser.add_argument("--username", default="coordinator", help="登录用户名（默认 coordinator）")
        parser.add_argument("--password", required=True, help="登录密码")
        parser.add_argument("--email", default="", help="可选邮箱")
        parser.add_argument(
            "--first-name",
            default="委托",
            help="名（默认 委托）",
        )
        parser.add_argument(
            "--last-name",
            default="统筹",
            help="姓（默认 统筹）",
        )

    def handle(self, *args, **options):
        username = (options["username"] or "").strip()
        password = options["password"]
        if not username:
            self.stderr.write(self.style.ERROR("username 不能为空"))
            return

        perms = dict(ROLE_DEFAULT_PERMS_BY_CODE[COMMISSION_COORDINATOR_ROLE_CODE])
        role, created = Role.objects.update_or_create(
            code=COMMISSION_COORDINATOR_ROLE_CODE,
            defaults={
                "name": "委托统筹",
                "description": (
                    "精简后台：文件库（限定分类）、项目工作台、委托管理、"
                    "医院信息、仪器台账；可调用全部任务模板创建委托。"
                ),
                **perms,
            },
        )
        if created:
            self.stdout.write(self.style.SUCCESS(f"已创建角色：{role.name}"))
        else:
            for key, val in perms.items():
                setattr(role, key, val)
            role.save()
            self.stdout.write(self.style.SUCCESS(f"已更新角色权限：{role.name}"))

        user = User.objects.filter(username=username).first()
        if user is None:
            user = User.objects.create_user(
                username=username,
                email=options.get("email") or "",
                password=password,
                first_name=options.get("first_name") or "",
                last_name=options.get("last_name") or "",
            )
            self.stdout.write(self.style.SUCCESS(f"已创建用户：{username}"))
        else:
            user.set_password(password)
            if options.get("email"):
                user.email = options["email"]
            user.first_name = options.get("first_name") or user.first_name
            user.last_name = options.get("last_name") or user.last_name
            user.is_active = True
            user.save()
            self.stdout.write(self.style.SUCCESS(f"已更新用户：{username}"))

        profile, _ = UserProfile.objects.get_or_create(user=user)
        profile.role = role
        profile.perm_overrides = {}
        profile.save()

        self.stdout.write(
            self.style.SUCCESS(
                f"\n委托统筹账号就绪：{username}\n"
                "可见功能：文件库（待识别/现场记录/报告/附件/检测提交/回收站）、"
                "项目工作台、委托管理、医院信息管理、检测仪器台账、用户管理。\n"
                "无：文档识别、模板编辑器、任务模板库、角色/菜单/数据库管理。\n"
                "新建用户时仅可分配检测流程岗位角色。"
            )
        )
