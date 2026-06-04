#!/usr/bin/env python
"""Django 的命令行工具"""
import os
import sys


def main():
    """运行管理任务"""
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'tablet_backend.settings')
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "无法导入 Django。请确保已安装 Django 并在 PYTHONPATH 环境变量中可用。"
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == '__main__':
    main()
