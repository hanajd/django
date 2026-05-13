"""用户与扩展资料联动。"""
from django.contrib.auth.models import User
from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.core.models import InspectionCase, InspectionCaseWorkflowState, UserProfile


@receiver(post_save, sender=User)
def ensure_user_profile_exists(sender, instance, created, **kwargs):
    """保证每个 User 有对应 UserProfile，避免列表/模板访问 user.profile 报错。"""
    UserProfile.objects.get_or_create(user=instance)


@receiver(post_save, sender=InspectionCase)
def ensure_inspection_case_workflow_state(sender, instance, created, **kwargs):
    """新建案件时初始化流程状态为「检测员填写现场记录」。"""
    if not created:
        return
    InspectionCaseWorkflowState.objects.get_or_create(
        case=instance,
        defaults={"stage": InspectionCaseWorkflowState.STAGE_SITE_FILL},
    )
