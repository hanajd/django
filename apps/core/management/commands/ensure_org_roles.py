"""
幂等写入新业务线组织角色（行政 / 检测·评价主管与员工）。

示例::

    python manage.py ensure_org_roles
"""

from django.core.management.base import BaseCommand

from apps.core.org_roles import ORG_ROLE_SEED, ensure_org_roles_seeded


class Command(BaseCommand):
    help = "创建/更新行政与检测/评价部门组织角色"

    def handle(self, *args, **options):
        created = ensure_org_roles_seeded()
        if created:
            self.stdout.write(self.style.SUCCESS(f"已新建角色：{', '.join(created)}"))
        else:
            self.stdout.write(self.style.SUCCESS("组织角色已存在，已按种子同步权限"))
        for code, meta in ORG_ROLE_SEED.items():
            self.stdout.write(f"  - {code}: {meta['name']} ({meta['org_unit']})")
