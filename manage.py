#!/usr/bin/env python
"""Django 的命令行工具"""
import hashlib
import os
import sys


def _patch_graal_hashlib_md5() -> None:
    """
    GraalVM Python 的 hashlib.md5 文本签名为 ``usedforsecurity=?``，
    Django 在 inspect.signature 时会直接 ValueError，导致 manage.py 无法启动。
    用带合法签名的包装函数替换后再导入 Django。
    """
    real_md5 = hashlib.md5
    # 已包装过则跳过
    if getattr(real_md5, "__graal_md5_patched__", False):
        return

    def md5(data=b"", *, usedforsecurity=True):  # noqa: ARG001
        try:
            return real_md5(data, usedforsecurity=usedforsecurity)
        except TypeError:
            return real_md5(data)

    md5.__graal_md5_patched__ = True  # type: ignore[attr-defined]
    hashlib.md5 = md5  # type: ignore[assignment]


# 必须在导入 Django 之前执行（含 runserver 子进程重载）
_patch_graal_hashlib_md5()


def main():
    """运行管理任务"""
    _patch_graal_hashlib_md5()
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
