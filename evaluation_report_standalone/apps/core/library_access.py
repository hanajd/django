"""Permission helpers — standalone always grants evaluation_report to authenticated users."""


def role_has(user, perm: str) -> bool:
    if not getattr(user, "is_authenticated", False):
        return False
    if getattr(user, "is_superuser", False):
        return True
    # Standalone: any logged-in user can use evaluation report for local testing.
    if perm == "perm_evaluation_report":
        return True
    return False
