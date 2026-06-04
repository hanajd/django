"""用户与扩展资料联动。"""
from django.contrib.auth.models import User
from django.db.models.signals import m2m_changed, post_save
from django.dispatch import receiver

from apps.core.menu_context_cache import invalidate_menu_context_cache
from apps.core.models import InspectionCase, InspectionCaseWorkflowState, Menu, Role, UserProfile


@receiver(post_save, sender=User)
def ensure_user_profile_exists(sender, instance, created, **kwargs):
    """保证每个 User 有对应 UserProfile，避免列表/模板访问 user.profile 报错。"""
    UserProfile.objects.get_or_create(user=instance)


@receiver(post_save, sender=Menu)
@receiver(post_save, sender=Role)
@receiver(post_save, sender=UserProfile)
def invalidate_menu_context_on_access_change(sender, **kwargs):
    invalidate_menu_context_cache()


@receiver(m2m_changed, sender=Menu.roles.through)
def invalidate_menu_context_on_menu_roles_change(sender, **kwargs):
    invalidate_menu_context_cache()


@receiver(post_save, sender=InspectionCase)
def ensure_inspection_case_workflow_state(sender, instance, created, **kwargs):
    """新建案件时初始化流程状态为「检测员填写现场记录」。"""
    if not created:
        return
    InspectionCaseWorkflowState.objects.get_or_create(
        case=instance,
        defaults={"stage": InspectionCaseWorkflowState.STAGE_SITE_FILL},
    )
