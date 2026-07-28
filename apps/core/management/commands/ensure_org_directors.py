"""
幂等创建三个组织「主任/主管」演示账号：行政、检测部主管、评价部主管。

示例::

    python manage.py ensure_org_directors
    python manage.py ensure_org_directors --password 'YourStrongPass'
"""

from __future__ import annotations

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand

from apps.core.models import Role, UserProfile
from apps.core.org_roles import (
    ORG_UNIT_ADMIN,
    ORG_UNIT_EVALUATION,
    ORG_UNIT_INSPECTION,
    ROLE_ADMIN_OFFICE,
    ROLE_DEPT_DIRECTOR_EVALUATION,
    ROLE_DEPT_DIRECTOR_INSPECTION,
    ROLE_TO_ORG_UNIT,
    ensure_org_roles_seeded,
)

# 默认账号（可按需改用户名参数覆盖）
DEFAULT_DIRECTORS = (
    {
        "username": "admin_director",
        "role_code": ROLE_ADMIN_OFFICE,
        "org_unit": ORG_UNIT_ADMIN,
        "last_name": "行政",
        "first_name": "主任",
        "department": "行政部",
        "position": "主任",
    },
    {
        "username": "inspection_director",
        "role_code": ROLE_DEPT_DIRECTOR_INSPECTION,
        "org_unit": ORG_UNIT_INSPECTION,
        "last_name": "检测部",
        "first_name": "主管",
        "department": "检测部",
        "position": "主管",
    },
    {
        "username": "evaluation_director",
        "role_code": ROLE_DEPT_DIRECTOR_EVALUATION,
        "org_unit": ORG_UNIT_EVALUATION,
        "last_name": "评价部",
        "first_name": "主管",
        "department": "评价部",
        "position": "主管",
    },
)

DEFAULT_PASSWORD = "Director@2026"


class Command(BaseCommand):
    help = "创建/更新行政、检测部主管、评价部主管三个组织账号"

    def add_arguments(self, parser):
        parser.add_argument(
            "--password",
            default=DEFAULT_PASSWORD,
            help=f"三个账号统一初始密码（默认 {DEFAULT_PASSWORD}）",
        )
        parser.add_argument(
            "--admin-username",
            default="admin_director",
            help="行政账号用户名",
        )
        parser.add_argument(
            "--inspection-username",
            default="inspection_director",
            help="检测部主管用户名",
        )
        parser.add_argument(
            "--evaluation-username",
            default="evaluation_director",
            help="评价部主管用户名",
        )

    def handle(self, *args, **options):
        ensure_org_roles_seeded()
        password = options["password"] or DEFAULT_PASSWORD
        username_overrides = {
            ROLE_ADMIN_OFFICE: (options.get("admin_username") or "").strip() or "admin_director",
            ROLE_DEPT_DIRECTOR_INSPECTION: (options.get("inspection_username") or "").strip()
            or "inspection_director",
            ROLE_DEPT_DIRECTOR_EVALUATION: (options.get("evaluation_username") or "").strip()
            or "evaluation_director",
        }

        rows = []
        for spec in DEFAULT_DIRECTORS:
            role_code = spec["role_code"]
            role = Role.objects.filter(code=role_code).first()
            if role is None:
                self.stderr.write(self.style.ERROR(f"角色不存在：{role_code}，请先 ensure_org_roles"))
                continue
            username = username_overrides[role_code]
            org_unit = spec["org_unit"] or ROLE_TO_ORG_UNIT.get(role_code, "")
            user = User.objects.filter(username=username).first()
            created = False
            if user is None:
                user = User.objects.create_user(
                    username=username,
                    password=password,
                    first_name=spec["first_name"],
                    last_name=spec["last_name"],
                )
                created = True
            else:
                user.set_password(password)
                user.first_name = spec["first_name"]
                user.last_name = spec["last_name"]
                user.is_active = True
                user.save()

            profile, _ = UserProfile.objects.get_or_create(user=user)
            profile.role = role
            profile.org_unit = org_unit
            profile.department = spec["department"]
            profile.position = spec["position"]
            profile.perm_overrides = {}
            profile.save()

            action = "创建" if created else "更新"
            self.stdout.write(
                self.style.SUCCESS(
                    f"{action}：{username} → {role.name}（{org_unit}）"
                )
            )
            rows.append((username, role.name, org_unit, spec["department"]))

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("组织主任/主管账号就绪："))
        for username, role_name, org_unit, dept in rows:
            self.stdout.write(f"  · {dept} / {role_name}: {username}")
        self.stdout.write(f"  初始密码: {password}")
        self.stdout.write("  （登录后请尽快修改密码）")
