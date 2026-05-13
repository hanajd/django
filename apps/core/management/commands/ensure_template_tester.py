"""
创建或更新「模板 + HTMLPDF」测试账号（无流程处理管线权限）。

示例::

    python manage.py ensure_template_tester --username htmlpdf_test --password '你的密码'
"""

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand

from apps.core.library_access import ROLE_DEFAULT_PERMS_BY_CODE
from apps.core.models import Role, UserProfile


class Command(BaseCommand):
    help = "创建 template_tester 角色并绑定用户（文件库模板上传 + HTMLPDF，不含 MinerU/Ollama）"

    def add_arguments(self, parser):
        parser.add_argument("--username", default="htmlpdf_tester", help="登录用户名")
        parser.add_argument(
            "--password",
            default="htmlpdf_tester_change_me",
            help="初始密码（部署后请立即修改）",
        )
        parser.add_argument("--email", default="", help="可选邮箱")

    def handle(self, *args, **options):
        username = (options["username"] or "").strip()
        password = options["password"]
        email = (options.get("email") or "").strip()
        if not username:
            self.stderr.write(self.style.ERROR("username 不能为空"))
            return

        defaults = dict(ROLE_DEFAULT_PERMS_BY_CODE["template_tester"])
        role, created = Role.objects.get_or_create(
            code="template_tester",
            defaults={
                "name": "模板与 HTMLPDF 测试",
                "description": "仅文件库模板分类上传与 HTMLPDF 编辑器及相关 API",
                **defaults,
            },
        )
        if not created:
            for k, v in defaults.items():
                setattr(role, k, v)
            if not (role.name or "").strip():
                role.name = "模板与 HTMLPDF 测试"
            role.save()
            self.stdout.write("已更新角色 template_tester 权限矩阵")

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
        UserProfile.objects.update_or_create(user=user, defaults={"role": role})

        self.stdout.write(
            self.style.SUCCESS(
                f"用户「{username}」已绑定角色 template_tester；"
                "拥有：文件库访问、向模板等分类上传、HTMLPDF 编辑器；"
                "不拥有：流程处理、手动导出 PDF/报告（仍属管线侧能力）。"
            )
        )
        if password == "htmlpdf_tester_change_me":
            self.stdout.write(
                self.style.WARNING("当前为默认密码，请在生产环境使用 --password 指定强密码并提醒用户修改。")
            )
