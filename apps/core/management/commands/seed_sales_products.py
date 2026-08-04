"""初始化销售产品目录（分类 / 产品线 / 示例产品）。"""
from django.core.management.base import BaseCommand

from apps.core.sales_product_service import ensure_default_taxonomy, seed_starter_products


class Command(BaseCommand):
    help = "确保产品分类/产品线存在，并幂等写入常见销售产品示例"

    def handle(self, *args, **options):
        ensure_default_taxonomy()
        stats = seed_starter_products(user=None)
        self.stdout.write(
            self.style.SUCCESS(
                f"分类/产品线已就绪；新增 {stats.get('created', 0)}，"
                f"更新 {stats.get('updated', 0)}，跳过 {stats.get('skipped', 0)}"
                + (f"；错误：{stats['error']}" if stats.get("error") else "")
            )
        )
