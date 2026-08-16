"""library_access 桩（独立开发版）。

真实后台的 apps/core/library_access.py 依赖 Role/UserProfile 等模型，
F.1 评价报告表功能只用到其中的 role_has(user, "perm_file_library")。
独立开发时放行所有已登录用户；回嵌真实后台时【删除本文件】，
f1_eval_views.py 中的 `from apps.core.library_access import role_has`
会自动接回真实实现，无需改动任何 f1_eval_*.py 代码。
"""


def role_has(user, perm_key: str) -> bool:
    """独立版：已登录即视为拥有权限。"""
    return bool(getattr(user, "is_authenticated", False))
