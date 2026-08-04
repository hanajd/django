"""
核心模型定义
包含用户扩展、角色、菜单等模型
"""
from django.db import models
from django.contrib.auth.models import User
from django.utils.translation import gettext_lazy as _


class Role(models.Model):
    """角色模型"""
    ROLE_CHOICES = (
        ('super_admin', _('超级管理员')),
        ('admin', _('普通管理员')),
        ('app_user', _('App 用户（兼容旧版，等同检测侧参与人）')),
        ('field_inspector', _('检测员')),
        ('site_reviewer', _('校核员')),
        ('report_author', _('编制人')),
        ('report_auditor', _('审核人')),
        ('authorized_signatory', _('授权签字人')),
        ('template_editor', _('模板编辑')),
        ('template_tester', _('模板编辑器（测试）')),
        ('commission_coordinator', _('委托统筹')),
        ('admin_office', _('行政')),
        ('dept_director_inspection', _('检测部主管')),
        ('dept_staff_inspection', _('检测部员工')),
        ('dept_director_evaluation', _('评价部主管')),
        ('dept_staff_evaluation', _('评价部员工')),
    )
    
    name = models.CharField(
        max_length=50,
        unique=True,
        verbose_name=_('角色名称')
    )
    code = models.CharField(
        max_length=50,
        choices=ROLE_CHOICES,
        unique=True,
        verbose_name=_('角色代码')
    )
    description = models.TextField(
        blank=True,
        verbose_name=_('描述')
    )
    # 基础功能权限（在「编辑角色」中由超级管理员等调整）
    perm_manage_users = models.BooleanField(default=False, verbose_name=_('用户管理'))
    perm_manage_roles = models.BooleanField(default=False, verbose_name=_('角色管理'))
    perm_manage_menus = models.BooleanField(default=False, verbose_name=_('菜单管理'))
    perm_file_library = models.BooleanField(default=True, verbose_name=_('文件库访问'))
    perm_file_upload = models.BooleanField(default=True, verbose_name=_('文件上传'))
    perm_file_upload_attachment = models.BooleanField(
        default=True,
        verbose_name=_('附件上传'),
        help_text=_('仅控制「附件」分类；可与「文件上传」分开授权'),
    )
    perm_file_download = models.BooleanField(default=True, verbose_name=_('文件下载'))
    perm_file_preview = models.BooleanField(default=True, verbose_name=_('文件预览'))
    perm_file_delete = models.BooleanField(default=True, verbose_name=_('文件删除'))
    perm_file_scope_own_only = models.BooleanField(
        default=False,
        verbose_name=_('文件库仅本人数据'),
        help_text=_('开启后仅能访问 created_by 为当前用户的库文件'),
    )
    perm_process_pipeline = models.BooleanField(default=False, verbose_name=_('OCR处理'))
    perm_htmlpdf = models.BooleanField(
        default=False,
        verbose_name=_('模板编辑器'),
        help_text=_('进入模板编辑器及相关 API；不含 MinerU/Ollama OCR 处理'),
    )
    perm_assign_tasks = models.BooleanField(
        default=False,
        verbose_name=_('分配文件库任务'),
        help_text=_('创建任务模板、维护模板绑定、关联项目任务、向 App 用户分配任务'),
    )
    perm_create_library_project = models.BooleanField(
        default=False,
        verbose_name=_('创建检测项目'),
        help_text=_('在项目工作台新建检测项目；不含向他人分配任务'),
    )
    perm_biz_registry = models.BooleanField(
        default=False,
        verbose_name=_('业务登记'),
        help_text=_('维护受检单位/设备/联系人及案件、原始记录、报告与文件联动'),
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name=_('创建时间')
    )
    updated_at = models.DateTimeField(
        auto_now=True,
        verbose_name=_('更新时间')
    )

    class Meta:
        verbose_name = _('角色')
        verbose_name_plural = verbose_name

    def __str__(self):
        return self.name


class UserProfile(models.Model):
    """用户扩展模型"""
    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name='profile',
        verbose_name=_('用户')
    )
    phone = models.CharField(
        max_length=20,
        blank=True,
        null=True,
        verbose_name=_('手机号')
    )
    avatar = models.ImageField(
        upload_to='avatars/',
        blank=True,
        null=True,
        verbose_name=_('头像')
    )
    department = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        verbose_name=_('部门')
    )
    org_unit = models.CharField(
        max_length=32,
        blank=True,
        default="",
        db_index=True,
        verbose_name=_("组织部门"),
        help_text=_("结构化：admin_office / inspection / evaluation；新业务线角色使用"),
    )
    position = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        verbose_name=_('职位')
    )
    employee_no = models.CharField(
        max_length=64,
        blank=True,
        default="",
        db_index=True,
        verbose_name=_("工号"),
    )
    role = models.ForeignKey(
        Role,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='user_profiles',
        verbose_name=_('角色')
    )
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='user_profiles_created',
        verbose_name=_('创建者'),
        help_text=_('后台创建该账号的操作人；委托统筹仅可管理本人创建的用户'),
    )
    perm_overrides = models.JSONField(
        default=dict,
        blank=True,
        verbose_name=_('权限个性化覆盖'),
        help_text=_('按权限键存储 true/false；未出现的键继承角色。空对象表示完全跟随角色'),
    )
    file_library_quota_bytes = models.BigIntegerField(
        default=5368709120,
        verbose_name=_('文件库容量配额（字节）'),
        help_text=_('默认 5GiB（5368709120）。超级用户或角色为超级管理员/普通管理员时不校验配额'),
    )
    web_session_key = models.CharField(
        max_length=64,
        blank=True,
        null=True,
        verbose_name=_('当前 Web 会话键'),
        help_text=_('用于单浏览器会话：新浏览器登录后台后会更新，旧会话随即失效；与平板 JWT 无关'),
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name=_('创建时间')
    )
    updated_at = models.DateTimeField(
        auto_now=True,
        verbose_name=_('更新时间')
    )

    class Meta:
        verbose_name = _('用户资料')
        verbose_name_plural = verbose_name

    def __str__(self):
        return f"{self.user.username} - {self.phone or '无手机号'}"


class UserInviteToken(models.Model):
    """部门主管发出的员工自助注册邀请链接。"""

    token = models.CharField(max_length=64, unique=True, db_index=True, verbose_name=_("令牌"))
    created_by = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="user_invite_tokens_created",
        verbose_name=_("邀请人"),
    )
    org_unit = models.CharField(max_length=32, db_index=True, verbose_name=_("组织部门"))
    staff_role_code = models.CharField(
        max_length=50,
        db_index=True,
        verbose_name=_("注册后角色代码"),
        help_text=_("一般为 dept_staff_inspection / dept_staff_evaluation"),
    )
    expires_at = models.DateTimeField(db_index=True, verbose_name=_("过期时间"))
    is_active = models.BooleanField(default=True, db_index=True, verbose_name=_("有效"))
    use_count = models.PositiveIntegerField(default=0, verbose_name=_("已使用次数"))
    last_used_at = models.DateTimeField(null=True, blank=True, verbose_name=_("最近使用时间"))
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("创建时间"))

    class Meta:
        verbose_name = _("用户邀请令牌")
        verbose_name_plural = verbose_name
        ordering = ["-created_at"]

    def is_usable(self) -> bool:
        from django.utils import timezone

        if not self.is_active:
            return False
        if self.expires_at and self.expires_at <= timezone.now():
            return False
        return True

    def __str__(self):
        return f"{self.token[:8]}… / {self.org_unit} / {self.staff_role_code}"


class Menu(models.Model):
    """菜单模型（支持树形结构）"""
    name = models.CharField(
        max_length=100,
        verbose_name=_('菜单名称')
    )
    path = models.CharField(
        max_length=200,
        blank=True,
        verbose_name=_('路由路径')
    )
    icon = models.CharField(
        max_length=50,
        blank=True,
        verbose_name=_('图标')
    )
    parent = models.ForeignKey(
        'self',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='children',
        verbose_name=_('父级菜单')
    )
    sort_order = models.IntegerField(
        default=0,
        verbose_name=_('排序')
    )
    roles = models.ManyToManyField(
        Role,
        blank=True,
        related_name='menus',
        verbose_name=_('允许访问的角色')
    )
    is_visible = models.BooleanField(
        default=True,
        verbose_name=_('是否可见')
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name=_('创建时间')
    )
    updated_at = models.DateTimeField(
        auto_now=True,
        verbose_name=_('更新时间')
    )

    class Meta:
        verbose_name = _('菜单')
        verbose_name_plural = verbose_name
        ordering = ['sort_order', 'id']

    def __str__(self):
        return self.name

    def get_children(self):
        """获取子菜单"""
        return self.children.filter(is_visible=True)


class ActiveLibraryFileManager(models.Manager):
    """默认查询排除回收站中的记录（deleted_at 非空）。"""

    def get_queryset(self):
        return super().get_queryset().filter(deleted_at__isnull=True)


class LibraryFile(models.Model):
    """File library entry: user uploads, JSON outputs, or pipeline temp artifacts."""
    CATEGORY_UPLOAD = 'upload'
    CATEGORY_JSON = 'json'
    CATEGORY_TEMP = 'temp'
    CATEGORY_TEMPLATE = 'template'
    CATEGORY_SITE_RECORD = 'site_record'
    CATEGORY_REPORT = 'report'
    CATEGORY_ATTACHMENT = 'attachment'
    CATEGORY_INSPECTION_SUBMIT = 'inspection_submit'
    CATEGORY_CHOICES = (
        (CATEGORY_UPLOAD, _('OCR文件')),
        (CATEGORY_JSON, _('JSON 文件')),
        (CATEGORY_TEMPLATE, _('模板')),
        (CATEGORY_SITE_RECORD, _('现场记录')),
        (CATEGORY_REPORT, _('报告')),
        (CATEGORY_ATTACHMENT, _('附件')),
        (CATEGORY_INSPECTION_SUBMIT, _('检测提交')),
        (CATEGORY_TEMP, _('临时文件')),
    )

    LINK_ENTITY_NONE = ''
    LINK_ENTITY_INSPECTION_CASE = 'inspection_case'
    LINK_ENTITY_SITE_RECORD = 'site_record'
    LINK_ENTITY_REPORT = 'report'

    original_name = models.CharField(max_length=255, verbose_name=_('原始文件名'))
    relative_path = models.CharField(max_length=512, verbose_name=_('库内相对路径'))
    category = models.CharField(
        max_length=20,
        choices=CATEGORY_CHOICES,
        db_index=True,
        verbose_name=_('分类'),
    )
    batch_id = models.CharField(max_length=64, blank=True, default='', db_index=True, verbose_name=_('批次 ID'))
    content_sha256 = models.CharField(
        max_length=64,
        blank=True,
        default='',
        db_index=True,
        verbose_name=_('内容哈希'),
    )
    size = models.BigIntegerField(default=0, verbose_name=_('大小(字节)'))
    link_entity = models.CharField(
        max_length=32,
        blank=True,
        default='',
        db_index=True,
        verbose_name=_('关联业务类型'),
        help_text=_('inspection_case / site_record / report 等，空表示未绑定'),
    )
    link_object_id = models.PositiveBigIntegerField(
        null=True,
        blank=True,
        db_index=True,
        verbose_name=_('关联业务主键'),
    )
    created_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='library_files',
        verbose_name=_('创建者'),
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_('创建时间'))
    deleted_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        verbose_name=_('移入回收站时间'),
        help_text=_('非空表示文件在回收站；超过约 30 天保留期后由系统自动彻底删除'),
    )
    projects = models.ManyToManyField(
        "LibraryProject",
        through="LibraryFileProject",
        blank=True,
        related_name="library_files",
        verbose_name=_("关联项目"),
    )
    library_tasks = models.ManyToManyField(
        "LibraryTask",
        through="LibraryFileTask",
        blank=True,
        related_name="library_files",
        verbose_name=_("关联任务模板"),
        help_text=_("仅「模板」类应挂任务模板；其它分类请通过项目关联。模板在任务挂到项目时可同步到项目。"),
    )

    objects = ActiveLibraryFileManager()
    all_objects = models.Manager()

    class Meta:
        verbose_name = _('文件库文件')
        verbose_name_plural = verbose_name
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['link_entity', 'link_object_id'], name='libraryfile_link_idx'),
        ]

    def __str__(self):
        return f"{self.get_category_display()}: {self.original_name}"

    @property
    def preview_kind(self) -> str:
        name = self.original_name or ""
        if "." not in name:
            name = self.relative_path.rsplit("/", 1)[-1]
        if "." not in name:
            return "unsupported"
        ext = name.rsplit(".", 1)[-1].lower()
        if ext in ("png", "jpg", "jpeg", "gif", "webp", "bmp"):
            return "image"
        if ext == "pdf":
            return "pdf"
        if ext == "json":
            return "json"
        if ext in ("md", "markdown"):
            return "markdown"
        return "unsupported"


class LibraryTaskFolder(models.Model):
    """检测任务分类（可嵌套）：其下挂报告模板，现场记录模板挂在报告下。"""

    parent = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="children",
        verbose_name=_("上级分类"),
    )
    name = models.CharField(max_length=128, verbose_name=_("分类名称"))
    sort_order = models.PositiveIntegerField(default=0, verbose_name=_("排序"))
    notes = models.CharField(max_length=500, blank=True, default="", verbose_name=_("备注"))
    is_active = models.BooleanField(default=True, verbose_name=_("启用"))
    created_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="library_task_folders_created",
        verbose_name=_("创建者"),
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("创建时间"))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_("更新时间"))

    class Meta:
        verbose_name = _("检测任务分类")
        verbose_name_plural = verbose_name
        ordering = ["sort_order", "name", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["parent", "name"],
                name="uniq_task_folder_sibling_name",
            ),
        ]

    def __str__(self):
        return self.name

    def folder_segment(self) -> str:
        return f"f-{self.pk}"

    def ancestors_chain(self) -> list["LibraryTaskFolder"]:
        chain: list[LibraryTaskFolder] = []
        node: LibraryTaskFolder | None = self
        while node is not None:
            chain.append(node)
            node = node.parent
        chain.reverse()
        return chain

    def folder_path(self) -> str:
        return "/".join(n.folder_segment() for n in self.ancestors_chain())


class LibraryTask(models.Model):
    """文件库任务模板：与项目多对多；直接挂载的文件应为「模板」类。其它文件通过项目关联。"""

    OUTPUT_SITE_RECORD = "site_record"
    OUTPUT_REPORT = "report"
    OUTPUT_TARGET_CHOICES = (
        (OUTPUT_SITE_RECORD, _("现场记录")),
        (OUTPUT_REPORT, _("报告")),
    )

    code = models.SlugField(max_length=64, unique=True, db_index=True, verbose_name=_("任务编码"))
    name = models.CharField(max_length=128, verbose_name=_("任务名称"))
    output_target = models.CharField(
        max_length=20,
        choices=OUTPUT_TARGET_CHOICES,
        default=OUTPUT_SITE_RECORD,
        db_index=True,
        verbose_name=_("PDF 输出目标"),
        help_text=_("决定该任务生成的 PDF 默认保存到现场记录或报告分类"),
    )
    task_folder = models.ForeignKey(
        LibraryTaskFolder,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="report_tasks",
        verbose_name=_("所属任务分类"),
        help_text=_("仅报告类模板需选择；现场记录通过报告关联"),
    )
    report_source_tasks = models.ManyToManyField(
        "self",
        symmetrical=False,
        blank=True,
        related_name="report_target_tasks",
        verbose_name=_("报告来源现场记录任务"),
        help_text=_("当本任务输出为报告时，可指定一个或多个现场记录任务作为报告填充来源"),
    )
    created_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="library_tasks_created",
        verbose_name=_("创建者"),
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("创建时间"))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_("更新时间"))
    bound_instrument_ids = models.JSONField(
        default=dict,
        blank=True,
        verbose_name=_("模板默认检测仪器"),
        help_text=_(
            "JSON：bindingMode=kinds 时仅存仪器种类（name/model），具体编号在项目派工时写入 "
            "LibraryProject.assigned_instrument_ids。兼容旧版直接绑主键 id。"
        ),
    )

    class Meta:
        verbose_name = _("文件库任务")
        verbose_name_plural = verbose_name
        ordering = ["code"]

    def __str__(self):
        return f"{self.code} · {self.name}"


class LibraryTaskAssignment(models.Model):
    """管理员向 App 用户分配文件库任务（文件由关联的 LibraryTask 提供）。"""

    library_task = models.ForeignKey(
        LibraryTask,
        on_delete=models.CASCADE,
        related_name="assignments",
        verbose_name=_("任务"),
    )
    assignee = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="received_library_task_assignments",
        verbose_name=_("接收用户"),
    )
    project = models.ForeignKey(
        "LibraryProject",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="task_assignments",
        verbose_name=_("项目"),
    )
    assigned_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sent_library_task_assignments",
        verbose_name=_("分配人"),
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("创建时间"))

    class Meta:
        verbose_name = _("文件库任务分配")
        verbose_name_plural = verbose_name
        ordering = ["-created_at"]

    def __str__(self):
        if self.project_id:
            return f"{self.library_task.code} / {self.project.code} → {self.assignee.username}"
        return f"{self.library_task.code} → {self.assignee.username}"


class LibraryOCRProcessTask(models.Model):
    """OCR 上传后的异步流程任务。"""

    STATUS_PENDING = "pending"
    STATUS_RUNNING = "running"
    STATUS_SUCCESS = "success"
    STATUS_FAILED = "failed"
    STATUS_CHOICES = (
        (STATUS_PENDING, _("待处理")),
        (STATUS_RUNNING, _("处理中")),
        (STATUS_SUCCESS, _("已完成")),
        (STATUS_FAILED, _("失败")),
    )

    status = models.CharField(
        max_length=16,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING,
        db_index=True,
        verbose_name=_("状态"),
    )
    created_by = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="library_ocr_tasks",
        verbose_name=_("发起用户"),
    )
    project = models.ForeignKey(
        "LibraryProject",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="ocr_tasks",
        verbose_name=_("项目"),
    )
    source_file_ids = models.JSONField(default=list, blank=True, verbose_name=_("源文件ID列表"))
    generated_json_file_ids = models.JSONField(
        default=list,
        blank=True,
        verbose_name=_("生成JSON文件ID列表"),
    )
    batch_id = models.CharField(max_length=64, blank=True, default="", verbose_name=_("批次ID"))
    error_message = models.TextField(blank=True, default="", verbose_name=_("错误信息"))
    result_summary = models.JSONField(default=dict, blank=True, verbose_name=_("结果摘要"))
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("创建时间"))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_("更新时间"))

    class Meta:
        verbose_name = _("OCR流程任务")
        verbose_name_plural = verbose_name
        ordering = ["-created_at"]

    def __str__(self):
        return f"OCRTask#{self.pk} {self.status}"


class CommissionOrganization(models.Model):
    """委托单位：医院（必选）→ 院区（可选）→ 科室（可选）。"""

    LEVEL_HOSPITAL = "hospital"
    LEVEL_CAMPUS = "campus"
    LEVEL_DEPARTMENT = "department"
    LEVEL_CHOICES = (
        (LEVEL_HOSPITAL, _("医院")),
        (LEVEL_CAMPUS, _("院区")),
        (LEVEL_DEPARTMENT, _("科室")),
    )
    _LEVEL_PREFIX = {
        LEVEL_HOSPITAL: "h",
        LEVEL_CAMPUS: "c",
        LEVEL_DEPARTMENT: "d",
    }

    level = models.CharField(
        max_length=16,
        choices=LEVEL_CHOICES,
        default=LEVEL_HOSPITAL,
        db_index=True,
        verbose_name=_("层级"),
    )
    parent = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="children",
        verbose_name=_("上级"),
    )
    name = models.CharField(max_length=255, db_index=True, verbose_name=_("本级名称"))
    notes = models.CharField(max_length=500, blank=True, default="", verbose_name=_("备注"))
    address = models.CharField(max_length=512, blank=True, default="", verbose_name=_("地址/位置"))
    introduction = models.TextField(blank=True, default="", verbose_name=_("简介"))
    merged_report_file = models.ForeignKey(
        "LibraryFile",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="commission_orgs_merged_report",
        verbose_name=_("合并报告"),
        help_text=_("绑定该委托单位下属项目中已生成并归入文件库「报告」分类的 PDF 等成品文件"),
    )
    is_active = models.BooleanField(default=True, verbose_name=_("启用"))
    created_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="commission_organizations",
        verbose_name=_("创建者"),
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("创建时间"))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_("更新时间"))

    class Meta:
        verbose_name = _("委托单位")
        verbose_name_plural = verbose_name
        ordering = ["level", "name", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["name"],
                condition=models.Q(parent__isnull=True, is_active=True),
                name="uniq_commission_hospital_name_active",
            ),
            models.UniqueConstraint(
                fields=["parent", "name"],
                condition=models.Q(parent__isnull=False, is_active=True),
                name="uniq_commission_org_sibling_name_active",
            ),
        ]

    def __str__(self):
        return self.full_display_name

    @property
    def level_prefix(self) -> str:
        return self._LEVEL_PREFIX.get(self.level, "o")

    def ancestors_chain(self) -> list["CommissionOrganization"]:
        chain: list[CommissionOrganization] = []
        node: CommissionOrganization | None = self
        while node is not None:
            chain.append(node)
            node = node.parent
        chain.reverse()
        return chain

    @property
    def full_display_name(self) -> str:
        parts = [n.name.strip() for n in self.ancestors_chain() if (n.name or "").strip()]
        return " · ".join(parts) if parts else ""

    @property
    def level_label(self) -> str:
        return dict(self.LEVEL_CHOICES).get(self.level, self.level)

    def folder_segment(self) -> str:
        return f"{self.level_prefix}-{self.pk}"

    def folder_path(self) -> str:
        return "/".join(n.folder_segment() for n in self.ancestors_chain())

    def hospital_root(self) -> "CommissionOrganization":
        chain = self.ancestors_chain()
        return chain[0] if chain else self

    def allowed_child_levels(self) -> list[str]:
        if self.level == self.LEVEL_HOSPITAL:
            return [self.LEVEL_CAMPUS, self.LEVEL_DEPARTMENT]
        if self.level == self.LEVEL_CAMPUS:
            return [self.LEVEL_DEPARTMENT]
        return []

    @property
    def merged_report_label(self) -> str:
        if self.level == self.LEVEL_HOSPITAL:
            return _("医院总合并报告")
        if self.level == self.LEVEL_CAMPUS:
            return _("院区合并报告")
        if self.level == self.LEVEL_DEPARTMENT:
            return _("科室合并报告")
        return _("合并报告")


class CommissionOrgContact(models.Model):
    """委托单位联系人（常用于科室，也可挂在院区/医院）。"""

    organization = models.ForeignKey(
        CommissionOrganization,
        on_delete=models.CASCADE,
        related_name="contacts",
        verbose_name=_("所属层级"),
    )
    name = models.CharField(max_length=128, verbose_name=_("联系人"))
    phone = models.CharField(max_length=64, blank=True, default="", verbose_name=_("联系电话"))
    title = models.CharField(max_length=128, blank=True, default="", verbose_name=_("职务"))
    sort_order = models.PositiveIntegerField(default=0, verbose_name=_("排序"))
    is_active = models.BooleanField(default=True, verbose_name=_("启用"))
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("创建时间"))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_("更新时间"))

    class Meta:
        verbose_name = _("委托单位联系人")
        verbose_name_plural = verbose_name
        ordering = ["sort_order", "id"]

    def __str__(self):
        return f"{self.name} ({self.phone or '-'})"


class CommissionOrgEquipment(models.Model):
    """委托单位下属检测设备（可挂医院 / 院区 / 科室）；每台可关联已完成检测报告。"""

    department = models.ForeignKey(
        CommissionOrganization,
        on_delete=models.CASCADE,
        related_name="equipments",
        verbose_name=_("所属机构"),
        help_text=_("可挂在医院、院区或科室节点下；未选科室时挂在当前浏览的医院/院区。"),
    )
    name = models.CharField(max_length=255, verbose_name=_("设备名称"))
    model = models.CharField(max_length=255, blank=True, default="", verbose_name=_("设备型号"))
    serial_no = models.CharField(max_length=255, blank=True, default="", verbose_name=_("设备编号"))
    manufacturer = models.CharField(max_length=255, blank=True, default="", verbose_name=_("生产厂家"))
    location = models.CharField(max_length=512, blank=True, default="", verbose_name=_("设备位置"))
    notes = models.CharField(max_length=500, blank=True, default="", verbose_name=_("备注"))
    device_type = models.CharField(
        max_length=128,
        blank=True,
        default="",
        verbose_name=_("设备类型"),
        help_text=_("与任务模板库「设备类型」文件夹对应（如 01 CT、02DR），匹配时忽略前导序号"),
    )
    instance_no = models.PositiveIntegerField(
        default=1,
        verbose_name=_("同类型台次"),
        help_text=_("同一挂载节点、同一设备类型下的序号，展示为设备1、设备2…"),
    )
    report_task_bindings = models.JSONField(
        default=list,
        blank=True,
        verbose_name=_("检测类型与报告模板"),
        help_text=_('如 [{"inspection_type":"验收检测","report_task_id":1}]；未配置时使用默认 report_task'),
    )
    report_file = models.ForeignKey(
        "LibraryFile",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="commission_org_equipments",
        verbose_name=_("单台设备报告"),
        help_text=_("该科室下属项目中已生成并归入文件库「报告」分类的成品文件（最近一次）"),
    )
    report_task = models.ForeignKey(
        "LibraryTask",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="commission_org_equipments",
        verbose_name=_("检测任务模板"),
        help_text=_("未检测设备绑定的现场记录/报告任务模板，便于加入新项目开展检测"),
    )
    last_commission_no = models.CharField(
        max_length=128,
        blank=True,
        default="",
        verbose_name=_("最近委托编号"),
        help_text=_("最近一次完成检测对应的案件/任务编号"),
    )
    last_inspected_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_("最近检测时间"),
    )
    sort_order = models.PositiveIntegerField(default=0, verbose_name=_("排序"))
    is_active = models.BooleanField(default=True, verbose_name=_("启用"))
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("创建时间"))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_("更新时间"))

    class Meta:
        verbose_name = _("委托单位设备")
        verbose_name_plural = verbose_name
        ordering = ["department_id", "device_type", "instance_no", "sort_order", "id"]

    def __str__(self):
        return self.name

    @property
    def instance_label(self) -> str:
        return f"设备{self.instance_no or 1}"

    @property
    def has_completed_inspection(self) -> bool:
        return bool(self.report_file_id or self.last_inspected_at)


class CommissionOrgEquipmentHistory(models.Model):
    """设备历次检测记录（报告成品与提交快照）。"""

    equipment = models.ForeignKey(
        CommissionOrgEquipment,
        on_delete=models.CASCADE,
        related_name="inspection_histories",
        verbose_name=_("设备"),
    )
    report_file = models.ForeignKey(
        "LibraryFile",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="equipment_history_entries",
        verbose_name=_("报告文件"),
    )
    library_project = models.ForeignKey(
        "LibraryProject",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="equipment_history_entries",
        verbose_name=_("所属项目"),
    )
    inspection_case = models.ForeignKey(
        "InspectionCase",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="equipment_history_entries",
        verbose_name=_("检验案件"),
    )
    commission_no = models.CharField(
        max_length=128,
        blank=True,
        default="",
        db_index=True,
        verbose_name=_("委托编号"),
    )
    inspected_at = models.DateTimeField(null=True, blank=True, verbose_name=_("检测完成时间"))
    equipment_info = models.JSONField(default=dict, blank=True, verbose_name=_("设备信息快照"))
    test_result = models.JSONField(default=dict, blank=True, verbose_name=_("检测结果快照"))
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("记录时间"))

    class Meta:
        verbose_name = _("委托单位设备检测历史")
        verbose_name_plural = verbose_name
        ordering = ["-inspected_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["equipment", "report_file"],
                condition=models.Q(report_file__isnull=False),
                name="uniq_equipment_history_report_file",
            ),
        ]

    def __str__(self):
        return f"{self.equipment_id} / {self.commission_no or '-'}"


class LibraryProjectEquipment(models.Model):
    """项目委托设备：一次检测委托可包含一台或多台同类型设备。"""

    project = models.ForeignKey(
        "LibraryProject",
        on_delete=models.CASCADE,
        related_name="project_equipments",
        verbose_name=_("所属项目"),
    )
    equipment = models.ForeignKey(
        CommissionOrgEquipment,
        on_delete=models.CASCADE,
        related_name="project_links",
        verbose_name=_("受检设备"),
    )
    inspection_type = models.CharField(
        max_length=64,
        blank=True,
        default="",
        verbose_name=_("本次检测类型"),
        help_text=_(
            "本次委托该设备执行的检测类型（如验收检测、状态检测），"
            "对应设备主数据 report_task_bindings"
        ),
    )
    report_task = models.ForeignKey(
        "LibraryTask",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="project_equipment_links",
        verbose_name=_("报告/检测任务模板"),
        help_text=_("本项目内该设备使用的任务模板链根节点，默认取自设备主数据"),
    )
    sort_order = models.PositiveIntegerField(default=0, verbose_name=_("排序"))
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("加入时间"))

    class Meta:
        verbose_name = _("项目委托设备")
        verbose_name_plural = verbose_name
        ordering = ["sort_order", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["project", "equipment", "inspection_type"],
                name="uniq_project_equipment_inspection_type",
            ),
        ]

    def __str__(self):
        return f"{self.project_id} / {self.equipment_id}"


class LibraryProject(models.Model):
    """文件库项目：用于隔离与筛选跨分类文件。"""

    BUSINESS_LINE_INSPECTION = "inspection"
    BUSINESS_LINE_PRE_EVAL = "pre_eval"
    BUSINESS_LINE_CONTROL_EFFECT = "control_effect"
    BUSINESS_LINE_CHOICES = (
        (BUSINESS_LINE_INSPECTION, _("检测报告")),
        (BUSINESS_LINE_PRE_EVAL, _("预评价报告")),
        (BUSINESS_LINE_CONTROL_EFFECT, _("控制效果评价")),
    )

    code = models.CharField(max_length=64, unique=True, db_index=True, verbose_name=_("项目编码"))
    name = models.CharField(max_length=128, db_index=True, verbose_name=_("项目名称"))
    business_line = models.CharField(
        max_length=32,
        choices=BUSINESS_LINE_CHOICES,
        default=BUSINESS_LINE_INSPECTION,
        db_index=True,
        verbose_name=_("业务线"),
        help_text=_("inspection=检测；pre_eval/control_effect 本期预留"),
    )
    owning_org_unit = models.CharField(
        max_length=32,
        blank=True,
        default="",
        db_index=True,
        verbose_name=_("接收部门"),
        help_text=_("admin_office / inspection / evaluation；行政提交到部门后写入"),
    )
    commission_org = models.ForeignKey(
        CommissionOrganization,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="library_projects",
        verbose_name=_("委托单位"),
    )
    commission_organization = models.CharField(
        max_length=255,
        blank=True,
        default="",
        db_index=True,
        verbose_name=_("委托单位名称"),
        help_text=_("与 commission_org.name 同步，便于检索与历史兼容"),
    )
    description = models.CharField(max_length=255, blank=True, default="", verbose_name=_("描述"))
    is_active = models.BooleanField(default=True, verbose_name=_("启用"))
    created_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="library_projects",
        verbose_name=_("创建者"),
    )
    primary_responsible = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="primary_responsible_library_projects",
        verbose_name=_("主要负责人"),
        help_text=_("本项目业务主责人，拥有该项目工作台内的完整配置与分配权限"),
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("创建时间"))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_("更新时间"))
    library_tasks = models.ManyToManyField(
        LibraryTask,
        blank=True,
        related_name="projects",
        verbose_name=_("关联任务"),
        help_text=_("项目启用哪些任务；分配任务时仅能选择已关联的任务"),
    )
    assigned_instrument_ids = models.JSONField(
        default=dict,
        blank=True,
        verbose_name=_("已分配检测仪器"),
        help_text=_(
            "人员派工时为项目确定的具体仪器编号："
            '{"shareMode":"per_task","qualityControl":[id,...],'
            '"radiationProtection":[id,...],"byTask":{"任务id":{...}}}。'
            "任务模板仅绑种类时不含编号。"
        ),
    )
    instrument_share_across_tasks = models.BooleanField(
        default=False,
        verbose_name=_("同种仪器跨模板共用"),
        help_text=_(
            "未勾选：各现场记录任务模板分别分配物理编号（同模板下多台设备自动共用）。"
            "勾选：多个任务模板若要求同种仪器，整项目只分配一台。"
        ),
    )

    class Meta:
        verbose_name = _("文件库项目")
        verbose_name_plural = verbose_name
        ordering = ["-updated_at", "-id"]

    def __str__(self):
        return f"{self.code} - {self.name}"


class LibraryProjectUserRevocation(models.Model):
    """取消某用户在某项目上的「编辑类」权限（仍可下载、预览）；由管理员在项目管理中操作。"""

    project = models.ForeignKey(
        LibraryProject,
        on_delete=models.CASCADE,
        related_name="user_revocations",
        verbose_name=_("项目"),
    )
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="library_project_revocations",
        verbose_name=_("用户"),
    )
    revoked_at = models.DateTimeField(auto_now_add=True, verbose_name=_("取消时间"))
    revoked_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="library_project_revocations_made",
        verbose_name=_("操作人"),
    )

    class Meta:
        verbose_name = _("项目用户编辑取消")
        verbose_name_plural = verbose_name
        ordering = ["-revoked_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["project", "user"],
                name="uniq_library_project_user_revocation",
            )
        ]

    def __str__(self):
        return f"{self.project.code} / {self.user.username}"


class LibraryFileProject(models.Model):
    """文件-项目关联（同一文件可被多个项目复用）。"""

    library_file = models.ForeignKey(
        LibraryFile,
        on_delete=models.CASCADE,
        related_name="project_links",
        verbose_name=_("文件"),
    )
    project = models.ForeignKey(
        LibraryProject,
        on_delete=models.CASCADE,
        related_name="file_links",
        verbose_name=_("项目"),
    )
    created_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="library_file_project_links",
        verbose_name=_("关联人"),
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("创建时间"))

    class Meta:
        verbose_name = _("文件项目关联")
        verbose_name_plural = verbose_name
        constraints = [
            models.UniqueConstraint(
                fields=["library_file", "project"], name="uniq_library_file_project"
            )
        ]


class LibraryFileTask(models.Model):
    """文件-任务模板关联（业务上仅用于模板文件；兼容历史非模板数据）。"""

    library_file = models.ForeignKey(
        LibraryFile,
        on_delete=models.CASCADE,
        related_name="task_links",
        verbose_name=_("文件"),
    )
    library_task = models.ForeignKey(
        LibraryTask,
        on_delete=models.CASCADE,
        related_name="file_links",
        verbose_name=_("任务"),
    )
    created_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="library_file_task_links",
        verbose_name=_("关联人"),
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("创建时间"))

    class Meta:
        verbose_name = _("文件任务关联")
        verbose_name_plural = verbose_name
        constraints = [
            models.UniqueConstraint(
                fields=["library_file", "library_task"],
                name="uniq_library_file_task",
            )
        ]


class LibraryTaskTemplateBindingHistory(models.Model):
    """任务模板文件绑定轮换历史（编辑器保存新 JSON 等场景），供回溯到旧版模板。"""

    ROLE_PDF = "pdf"
    ROLE_JSON = "json"
    ROLE_CHOICES = [
        (ROLE_PDF, _("PDF 模板")),
        (ROLE_JSON, _("JSON 模板")),
    ]

    library_task = models.ForeignKey(
        LibraryTask,
        on_delete=models.CASCADE,
        related_name="template_binding_history",
        verbose_name=_("任务模板"),
    )
    library_file = models.ForeignKey(
        LibraryFile,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="template_binding_history_rows",
        verbose_name=_("被替换的模板文件"),
    )
    file_id_snapshot = models.PositiveIntegerField(verbose_name=_("文件 ID 快照"))
    original_name_snapshot = models.CharField(
        max_length=512,
        blank=True,
        default="",
        verbose_name=_("文件名快照"),
    )
    file_role = models.CharField(
        max_length=8,
        choices=ROLE_CHOICES,
        db_index=True,
        verbose_name=_("模板角色"),
    )
    replaced_at = models.DateTimeField(auto_now_add=True, db_index=True, verbose_name=_("替换时间"))
    replaced_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="template_binding_history_actions",
        verbose_name=_("操作人"),
    )
    replaced_by_file = models.ForeignKey(
        LibraryFile,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        verbose_name=_("替换为的新文件"),
    )
    source = models.CharField(
        max_length=64,
        blank=True,
        default="",
        verbose_name=_("来源"),
        help_text=_("如 editor_export_json、restore_history"),
    )

    class Meta:
        verbose_name = _("任务模板绑定历史")
        verbose_name_plural = verbose_name
        ordering = ["-replaced_at", "-id"]
        indexes = [
            models.Index(fields=["library_task", "file_role", "-replaced_at"]),
        ]

    def __str__(self):
        return f"task={self.library_task_id} {self.file_role} #{self.file_id_snapshot}"

    @property
    def file_still_available(self) -> bool:
        lf = self.library_file
        if lf is None or lf.deleted_at:
            return False
        from apps.core.library_file_service import library_file_exists_on_disk

        return library_file_exists_on_disk(lf)


class InspectedOrganization(models.Model):
    """受检单位主数据。"""

    name = models.CharField(max_length=255, db_index=True, verbose_name=_('单位名称'))
    address = models.CharField(max_length=512, blank=True, default='', verbose_name=_('地址'))
    credit_code = models.CharField(
        max_length=32,
        blank=True,
        default='',
        db_index=True,
        verbose_name=_('统一社会信用代码'),
    )
    created_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='inspected_organizations',
        verbose_name=_('创建者'),
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_('创建时间'))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_('更新时间'))

    class Meta:
        verbose_name = _('受检单位')
        verbose_name_plural = verbose_name
        ordering = ['-updated_at', '-id']

    def __str__(self):
        return self.name


class BizContact(models.Model):
    """委托方联系人等。"""

    organization = models.ForeignKey(
        InspectedOrganization,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='contacts',
        verbose_name=_('所属单位'),
    )
    name = models.CharField(max_length=128, db_index=True, verbose_name=_('姓名'))
    phone = models.CharField(max_length=64, blank=True, default='', db_index=True, verbose_name=_('联系电话'))
    title = models.CharField(max_length=128, blank=True, default='', verbose_name=_('职务'))
    created_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='biz_contacts',
        verbose_name=_('创建者'),
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_('创建时间'))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_('更新时间'))

    class Meta:
        verbose_name = _('业务联系人')
        verbose_name_plural = verbose_name
        ordering = ['-updated_at', '-id']

    def __str__(self):
        return f"{self.name} ({self.phone or '-'})"


class BizDevice(models.Model):
    """设备主数据。"""

    organization = models.ForeignKey(
        InspectedOrganization,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='devices',
        verbose_name=_('关联单位'),
    )
    name = models.CharField(max_length=255, db_index=True, verbose_name=_('设备名称'))
    model = models.CharField(max_length=255, blank=True, default='', db_index=True, verbose_name=_('设备型号'))
    serial_no = models.CharField(max_length=255, blank=True, default='', db_index=True, verbose_name=_('设备编号'))
    manufacturer = models.CharField(max_length=255, blank=True, default='', verbose_name=_('生产厂家'))
    created_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='biz_devices',
        verbose_name=_('创建者'),
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_('创建时间'))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_('更新时间'))

    class Meta:
        verbose_name = _('设备')
        verbose_name_plural = verbose_name
        ordering = ['-updated_at', '-id']

    def __str__(self):
        return f"{self.name} / {self.model}"


class InspectionCase(models.Model):
    """一次检验/业务案件，串联单位、联系人、设备与记录。"""

    case_no = models.CharField(max_length=64, unique=True, db_index=True, verbose_name=_('案件编号'))
    inspected_organization = models.ForeignKey(
        InspectedOrganization,
        on_delete=models.PROTECT,
        related_name='inspection_cases',
        verbose_name=_('受检单位'),
    )
    primary_contact = models.ForeignKey(
        BizContact,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='primary_inspection_cases',
        verbose_name=_('主联系人'),
    )
    devices = models.ManyToManyField(BizDevice, blank=True, related_name='inspection_cases', verbose_name=_('关联设备'))
    notes = models.TextField(blank=True, default='', verbose_name=_('备注'))
    library_project = models.ForeignKey(
        LibraryProject,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='inspection_cases',
        verbose_name=_('文件库项目'),
        help_text=_('可选；用于将现场记录/报告的创建者解析为该项目下的任务接收人'),
    )
    created_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='inspection_cases',
        verbose_name=_('创建者'),
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_('创建时间'))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_('更新时间'))

    class Meta:
        verbose_name = _('检验案件')
        verbose_name_plural = verbose_name
        ordering = ['-created_at']

    def __str__(self):
        return self.case_no


class SiteRecord(models.Model):
    """现场原始记录（结构化字段 + 可选 JSON 扩展）。"""

    case = models.ForeignKey(
        InspectionCase,
        on_delete=models.CASCADE,
        related_name='site_records',
        verbose_name=_('所属案件'),
    )
    record_no = models.CharField(max_length=128, unique=True, db_index=True, verbose_name=_('原始记录编号'))
    record_date = models.DateField(null=True, blank=True, verbose_name=_('记录日期'))
    payload_json = models.JSONField(default=dict, blank=True, verbose_name=_('扩展数据(JSON)'))
    library_task = models.ForeignKey(
        LibraryTask,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='site_records',
        verbose_name=_('文件库任务'),
        help_text=_('可选；指定时按该任务下的分配记录解析创建者'),
    )
    created_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='site_records',
        verbose_name=_('创建者'),
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_('创建时间'))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_('更新时间'))

    class Meta:
        verbose_name = _('现场原始记录')
        verbose_name_plural = verbose_name
        ordering = ['-created_at']

    def __str__(self):
        return self.record_no


class Report(models.Model):
    """报告；与原始记录、案件联动。"""

    STATUS_DRAFT = 'draft'
    STATUS_ISSUED = 'issued'
    STATUS_CHOICES = (
        (STATUS_DRAFT, _('草稿')),
        (STATUS_ISSUED, _('已签发')),
    )

    case = models.ForeignKey(
        InspectionCase,
        on_delete=models.CASCADE,
        related_name='reports',
        verbose_name=_('所属案件'),
    )
    site_record = models.ForeignKey(
        SiteRecord,
        on_delete=models.PROTECT,
        related_name='reports',
        verbose_name=_('对应原始记录'),
    )
    report_no = models.CharField(max_length=128, unique=True, db_index=True, verbose_name=_('报告编号'))
    version = models.PositiveSmallIntegerField(default=1, verbose_name=_('版本'))
    status = models.CharField(
        max_length=16,
        choices=STATUS_CHOICES,
        default=STATUS_DRAFT,
        db_index=True,
        verbose_name=_('状态'),
    )
    issued_at = models.DateTimeField(null=True, blank=True, verbose_name=_('签发时间'))
    library_task = models.ForeignKey(
        LibraryTask,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='reports',
        verbose_name=_('文件库任务'),
        help_text=_('可选；指定时按该任务下的分配记录解析创建者'),
    )
    created_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='reports',
        verbose_name=_('创建者'),
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_('创建时间'))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_('更新时间'))

    class Meta:
        verbose_name = _('报告')
        verbose_name_plural = verbose_name
        ordering = ['-created_at']

    def __str__(self):
        return self.report_no

    def clean(self):
        from django.core.exceptions import ValidationError

        if self.site_record_id and self.case_id and self.site_record.case_id != self.case_id:
            raise ValidationError({'site_record': _('原始记录必须属于同一案件。')})


class InspectionSubmission(models.Model):
    """前端检测报告提交记录（按 taskNo 绑定后台案件与项目）。"""

    STATUS_PENDING = "pending"
    STATUS_IN_PROGRESS = "in_progress"
    STATUS_SUBMITTED = "submitted"
    STATUS_APPROVED = "approved"
    STATUS_REJECTED = "rejected"
    STATUS_CHOICES = (
        (STATUS_PENDING, _("待检测")),
        (STATUS_IN_PROGRESS, _("检测中")),
        (STATUS_SUBMITTED, _("已提交")),
        (STATUS_APPROVED, _("已通过")),
        (STATUS_REJECTED, _("已驳回")),
    )

    task_no = models.CharField(max_length=64, unique=True, db_index=True, verbose_name=_("任务编号"))
    report_type = models.CharField(max_length=64, db_index=True, verbose_name=_("报告类型"))
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING,
        db_index=True,
        verbose_name=_("状态"),
    )
    case = models.ForeignKey(
        InspectionCase,
        on_delete=models.PROTECT,
        related_name="inspection_submissions",
        verbose_name=_("关联案件"),
    )
    project = models.ForeignKey(
        LibraryProject,
        on_delete=models.PROTECT,
        related_name="inspection_submissions",
        verbose_name=_("关联项目"),
    )
    created_at_remote = models.DateTimeField(verbose_name=_("客户端创建时间"))
    updated_at_remote = models.DateTimeField(verbose_name=_("客户端更新时间"))
    report_info = models.JSONField(default=dict, blank=True, verbose_name=_("报告信息"))
    hospital_info = models.JSONField(default=dict, blank=True, verbose_name=_("医院信息"))
    equipment_info = models.JSONField(default=dict, blank=True, verbose_name=_("设备信息"))
    test_result = models.JSONField(default=dict, blank=True, verbose_name=_("检测结果"))
    conclusion = models.JSONField(default=dict, blank=True, verbose_name=_("结论"))
    raw_payload = models.JSONField(default=dict, blank=True, verbose_name=_("原始请求体"))
    sign_author_png = models.BinaryField(null=True, blank=True, verbose_name=_("编制人签名PNG"))
    sign_reviewer_png = models.BinaryField(null=True, blank=True, verbose_name=_("审核人签名PNG"))
    sign_approver_png = models.BinaryField(null=True, blank=True, verbose_name=_("批准人签名PNG"))
    sign_date = models.DateTimeField(null=True, blank=True, verbose_name=_("签发日期"))
    started_at = models.DateTimeField(null=True, blank=True, verbose_name=_("开始检测时间"))
    draft_saved_at = models.DateTimeField(null=True, blank=True, verbose_name=_("草稿保存时间"))
    submitted_at = models.DateTimeField(null=True, blank=True, verbose_name=_("提交时间"))
    created_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="inspection_submissions",
        verbose_name=_("提交用户"),
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("创建时间"))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_("更新时间"))

    class Meta:
        verbose_name = _("检测报告提交")
        verbose_name_plural = verbose_name
        ordering = ["-updated_at", "-id"]

    def __str__(self):
        return self.task_no


class InspectionSubmissionInstrument(models.Model):
    """检测报告中的仪器列表。"""

    submission = models.ForeignKey(
        InspectionSubmission,
        on_delete=models.CASCADE,
        related_name="instruments",
        verbose_name=_("提交记录"),
    )
    name = models.CharField(max_length=128, verbose_name=_("仪器名称"))
    identifier = models.CharField(max_length=128, verbose_name=_("仪器编号"))
    certificate_no = models.CharField(max_length=128, blank=True, default="", verbose_name=_("证书号"))
    valid_until = models.DateTimeField(null=True, blank=True, verbose_name=_("有效期至"))
    enabled = models.BooleanField(default=True, verbose_name=_("启用"))

    class Meta:
        verbose_name = _("检测报告仪器")
        verbose_name_plural = verbose_name
        ordering = ["id"]

    def __str__(self):
        return f"{self.submission.task_no} / {self.name}"


class LibraryProjectWorkflowMember(models.Model):
    """项目内流程参与人：同一岗位可有多人；同一用户在同一项目同一岗位仅登记一次。"""

    ROLE_FIELD_INSPECTOR = "field_inspector"
    ROLE_SITE_REVIEWER = "site_reviewer"
    ROLE_REPORT_AUTHOR = "report_author"
    ROLE_REPORT_AUDITOR = "report_auditor"
    ROLE_AUTH_SIGNATORY = "authorized_signatory"
    WORKFLOW_ROLE_CHOICES = (
        (ROLE_FIELD_INSPECTOR, _("检测员")),
        (ROLE_SITE_REVIEWER, _("校核员")),
        (ROLE_REPORT_AUTHOR, _("编制人")),
        (ROLE_REPORT_AUDITOR, _("审核人")),
        (ROLE_AUTH_SIGNATORY, _("授权签字人")),
    )

    project = models.ForeignKey(
        LibraryProject,
        on_delete=models.CASCADE,
        related_name="workflow_members",
        verbose_name=_("项目"),
    )
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="library_project_workflow_memberships",
        verbose_name=_("用户"),
    )
    workflow_role = models.CharField(
        max_length=32,
        choices=WORKFLOW_ROLE_CHOICES,
        db_index=True,
        verbose_name=_("流程岗位"),
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("创建时间"))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_("更新时间"))

    @classmethod
    def default_workflow_role_for_user(cls, user) -> str:
        """
        将用户全局 Role.code 映射为项目内 workflow_role。
        五类检测流程角色一一对应；旧版 app_user 等与未识别代码默认映射为检测员（field_inspector）。
        """
        prof = getattr(user, "profile", None)
        role = getattr(prof, "role", None) if prof else None
        code = (getattr(role, "code", None) or "").strip()
        known = frozenset(
            {
                cls.ROLE_FIELD_INSPECTOR,
                cls.ROLE_SITE_REVIEWER,
                cls.ROLE_REPORT_AUTHOR,
                cls.ROLE_REPORT_AUDITOR,
                cls.ROLE_AUTH_SIGNATORY,
            }
        )
        if code in known:
            return code
        return cls.ROLE_FIELD_INSPECTOR

    @classmethod
    def ensure_for_project_assignment(cls, project, user):
        """
        项目任务分配给某用户后：按该用户全局角色将其加入对应流程岗位（不挤占同岗位其他参与人）。
        """
        if project is None or user is None:
            return None
        wf = cls.default_workflow_role_for_user(user)
        obj, _created = cls.objects.get_or_create(
            project=project,
            user=user,
            workflow_role=wf,
        )
        return obj

    class Meta:
        verbose_name = _("项目流程成员")
        verbose_name_plural = verbose_name
        ordering = ["project_id", "workflow_role", "user_id"]
        constraints = [
            models.UniqueConstraint(
                fields=["project", "user", "workflow_role"],
                name="uniq_project_workflow_member_user_role",
            ),
        ]

    def __str__(self):
        return f"{self.project.code} / {self.user.username} / {self.workflow_role}"


class InspectionCaseWorkflowState(models.Model):
    """检验案件在「现场记录 → 报告签发」链上的当前环节（与业务登记案件一对一）。"""

    STAGE_SITE_FILL = "SITE_FILL"
    STAGE_SITE_REVIEW = "SITE_REVIEW"
    STAGE_REPORT_DRAFT = "REPORT_DRAFT"
    STAGE_REPORT_AUDIT = "REPORT_AUDIT"
    STAGE_REPORT_SIGN = "REPORT_SIGN"
    STAGE_ISSUED = "ISSUED"
    STAGE_CHOICES = (
        (STAGE_SITE_FILL, _("检测员填写现场记录")),
        (STAGE_SITE_REVIEW, _("校核员校核现场记录")),
        (STAGE_REPORT_DRAFT, _("编制人编制报告")),
        (STAGE_REPORT_AUDIT, _("审核人审核报告")),
        (STAGE_REPORT_SIGN, _("授权签字人签发")),
        (STAGE_ISSUED, _("已签发")),
    )

    case = models.OneToOneField(
        InspectionCase,
        on_delete=models.CASCADE,
        primary_key=True,
        related_name="workflow_state",
        verbose_name=_("案件"),
    )
    stage = models.CharField(
        max_length=32,
        choices=STAGE_CHOICES,
        default=STAGE_SITE_FILL,
        db_index=True,
        verbose_name=_("当前环节"),
    )
    return_reason = models.TextField(blank=True, default="", verbose_name=_("最近一次退回说明"))
    issue_date = models.DateField(null=True, blank=True, verbose_name=_("签发日期"))
    updated_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="inspection_case_workflow_updates",
        verbose_name=_("最后操作人"),
    )
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_("环节更新时间"))

    class Meta:
        verbose_name = _("案件流程状态")
        verbose_name_plural = verbose_name

    def __str__(self):
        return f"{self.case_id} / {self.stage}"


# 报告 PDF 第三页签字槽（使用审计 / 叠印记录）
REPORT_SIGNATURE_SLOT_CHOICES = (
    ("reportAuthor", _("报告编制人签字位")),
    ("reportAuditor", _("报告审核人签字位")),
    ("authorizedSignatory", _("报告授权人签字位")),
    ("issueDate", _("报告签发日期")),
)


class CaseReportSignatureRecord(models.Model):
    """报告环节电子签字记录：每次叠印生成新版本报告并留痕。"""

    SLOT_REPORT_AUTHOR = "reportAuthor"
    SLOT_REPORT_AUDITOR = "reportAuditor"
    SLOT_AUTHORIZED_SIGNATORY = "authorizedSignatory"
    SLOT_ISSUE_DATE = "issueDate"
    SLOT_CHOICES = REPORT_SIGNATURE_SLOT_CHOICES

    case = models.ForeignKey(
        InspectionCase,
        on_delete=models.CASCADE,
        related_name="report_signature_records",
        verbose_name=_("案件"),
    )
    project = models.ForeignKey(
        "LibraryProject",
        on_delete=models.CASCADE,
        related_name="report_signature_records",
        verbose_name=_("项目"),
    )
    submission = models.ForeignKey(
        "InspectionSubmission",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="report_signature_records",
        verbose_name=_("检测提交"),
    )
    task_no = models.CharField(max_length=64, blank=True, default="", db_index=True, verbose_name=_("任务编号"))
    workflow_stage = models.CharField(
        max_length=32,
        blank=True,
        default="",
        verbose_name=_("签字时环节"),
    )
    slot = models.CharField(max_length=32, choices=SLOT_CHOICES, db_index=True, verbose_name=_("签字位"))
    signer = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="report_signature_records",
        verbose_name=_("签名人"),
    )
    user_signature = models.ForeignKey(
        "UserSignature",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="report_signature_records",
        verbose_name=_("签名版本"),
    )
    signature_sha256 = models.CharField(max_length=64, blank=True, default="", verbose_name=_("签名内容哈希"))
    report_file = models.ForeignKey(
        "LibraryFile",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="report_signature_records",
        verbose_name=_("叠印后报告"),
    )
    report_sha256_before = models.CharField(max_length=64, blank=True, default="", verbose_name=_("叠印前报告哈希"))
    report_sha256_after = models.CharField(max_length=64, blank=True, default="", verbose_name=_("叠印后报告哈希"))
    overlay_page = models.PositiveSmallIntegerField(default=0, verbose_name=_("叠印页码"))
    overlay_rect = models.JSONField(default=list, blank=True, verbose_name=_("叠印区域"))
    sign_version = models.PositiveIntegerField(default=1, verbose_name=_("签字序号"))
    signed_at = models.DateTimeField(auto_now_add=True, db_index=True, verbose_name=_("签字时间"))

    class Meta:
        verbose_name = _("报告签字记录")
        verbose_name_plural = verbose_name
        ordering = ["case_id", "task_no", "sign_version", "id"]
        indexes = [
            models.Index(fields=["case", "task_no", "-signed_at"]),
            models.Index(fields=["project", "-signed_at"]),
        ]

    def __str__(self):
        return f"case={self.case_id} {self.slot} v{self.sign_version}"

    @property
    def slot_label(self) -> str:
        return dict(self.SLOT_CHOICES).get(self.slot, self.slot)


class InstrumentCatalog(models.Model):
    """仪器主数据：维护仪器编号、名称、型号及检定/校准信息。"""

    code = models.CharField(max_length=64, unique=True, db_index=True, verbose_name=_("仪器编号"))
    name = models.CharField(max_length=255, db_index=True, verbose_name=_("仪器设备名称"))
    model = models.CharField(max_length=255, blank=True, default="", verbose_name=_("型号"))
    calibration_org = models.CharField(max_length=255, blank=True, default="", verbose_name=_("检定/校准单位"))
    certificate_no = models.CharField(max_length=128, blank=True, default="", verbose_name=_("证书编号"))
    certificate_valid_until = models.DateField(null=True, blank=True, verbose_name=_("证书有效期"))
    remarks = models.TextField(blank=True, default="", verbose_name=_("备注说明"))
    is_active = models.BooleanField(default=True, db_index=True, verbose_name=_("启用"))
    checkout_project = models.ForeignKey(
        "LibraryProject",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="checked_out_instruments",
        verbose_name=_("最近出库项目"),
        help_text=_("展示用；实际关联以 checkout_project_ids 为准，可多项目/委托共用。"),
    )
    checkout_project_ids = models.JSONField(
        default=list,
        blank=True,
        verbose_name=_("出库关联项目"),
        help_text=_("允许多个委托/项目共用同一台仪器；结束某一项目时仅移除对应 id。"),
    )
    checked_out_at = models.DateTimeField(null=True, blank=True, verbose_name=_("出库时间"))
    checked_out_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="instrument_checkouts_performed",
        verbose_name=_("出库操作人"),
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("创建时间"))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_("更新时间"))

    class Meta:
        verbose_name = _("仪器主数据")
        verbose_name_plural = verbose_name
        ordering = ["code"]

    def __str__(self):
        return f"{self.code} - {self.name}"

    @property
    def is_in_stock(self) -> bool:
        return self.checkout_project_id is None


class InstrumentCheckoutLog(models.Model):
    """仪器出库/入库流水（按唯一编号追溯）。"""

    EVENT_CHECKOUT = "checkout"
    EVENT_CHECKIN = "checkin"
    EVENT_CHOICES = [
        (EVENT_CHECKOUT, _("出库")),
        (EVENT_CHECKIN, _("入库")),
    ]

    instrument = models.ForeignKey(
        InstrumentCatalog,
        on_delete=models.CASCADE,
        related_name="checkout_logs",
        verbose_name=_("仪器"),
    )
    project = models.ForeignKey(
        "LibraryProject",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="instrument_checkout_logs",
        verbose_name=_("关联项目"),
    )
    event_type = models.CharField(max_length=16, choices=EVENT_CHOICES, db_index=True)
    performed_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="instrument_checkout_logs",
        verbose_name=_("操作人"),
    )
    performed_at = models.DateTimeField(auto_now_add=True, db_index=True, verbose_name=_("操作时间"))
    note = models.CharField(max_length=255, blank=True, default="", verbose_name=_("备注"))

    class Meta:
        verbose_name = _("仪器出入库记录")
        verbose_name_plural = verbose_name
        ordering = ["-performed_at", "-id"]

    def __str__(self):
        return f"{self.instrument.code} {self.event_type} @ {self.performed_at:%Y-%m-%d %H:%M}"


def user_signature_image_upload_to(instance, filename: str) -> str:
    ext = ".png"
    if filename and "." in filename:
        ext = filename[filename.rfind(".") :].lower()
        if ext not in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
            ext = ".png"
    return f"user_signatures/{instance.user_id}/signature{ext}"


# 项目/现场模板中的签字槽位（非用户个人签名分类）
PROJECT_SIGNATURE_SLOT_CHOICES = (
    ("inspector", _("检测员签字位")),
    ("checker", _("校核员签字位")),
    ("accompanyingPerson", _("陪同人签字位")),
)

SIGNATURE_USAGE_SLOT_CHOICES = PROJECT_SIGNATURE_SLOT_CHOICES + REPORT_SIGNATURE_SLOT_CHOICES


class UserSignature(models.Model):
    """用户手写签名主档：每人一份，插入项目时按模板签字位选用。"""

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="user_signatures",
        verbose_name=_("用户"),
    )
    image = models.ImageField(
        upload_to=user_signature_image_upload_to,
        verbose_name=_("签名图片"),
    )
    content_sha256 = models.CharField(max_length=64, blank=True, default="", db_index=True, verbose_name=_("内容哈希"))
    original_filename = models.CharField(max_length=255, blank=True, default="", verbose_name=_("原始文件名"))
    version = models.PositiveIntegerField(default=1, verbose_name=_("版本号"))
    is_active = models.BooleanField(default=True, db_index=True, verbose_name=_("启用"))
    uploaded_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="user_signature_uploads",
        verbose_name=_("上传操作人"),
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("创建时间"))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_("更新时间"))

    class Meta:
        verbose_name = _("用户签名")
        verbose_name_plural = verbose_name
        ordering = ["user_id", "-version"]
        constraints = [
            models.UniqueConstraint(
                fields=["user"],
                condition=models.Q(is_active=True),
                name="uniq_user_signature_active",
            ),
        ]
        indexes = [
            models.Index(fields=["user", "-updated_at"]),
        ]

    def __str__(self):
        return f"user={self.user_id} v{self.version}"


class UserSignatureEvent(models.Model):
    """用户签名上传/替换/使用审计。"""

    EVENT_UPLOADED = "uploaded"
    EVENT_REPLACED = "replaced"
    EVENT_USED_SITE_RECORD = "used_site_record"
    EVENT_USED_REPORT = "used_report"
    EVENT_USED_SUBMIT = "used_submit"
    EVENT_USED_REPORT_SIGN = "used_report_sign"
    EVENT_CHOICES = (
        (EVENT_UPLOADED, _("首次上传")),
        (EVENT_REPLACED, _("重新上传/替换")),
        (EVENT_USED_SITE_RECORD, _("用于现场记录")),
        (EVENT_USED_REPORT, _("用于报告")),
        (EVENT_USED_SUBMIT, _("用于检测提交")),
        (EVENT_USED_REPORT_SIGN, _("用于报告环节签字")),
    )

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="signature_events",
        verbose_name=_("签名所属用户"),
    )
    user_signature = models.ForeignKey(
        UserSignature,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="events",
        verbose_name=_("签名版本"),
    )
    role = models.CharField(
        max_length=32,
        choices=SIGNATURE_USAGE_SLOT_CHOICES,
        blank=True,
        default="",
        db_index=True,
        verbose_name=_("项目签字位置"),
        help_text=_("现场/报告模板签字槽；个人签名库不按此分类"),
    )
    event_type = models.CharField(max_length=32, choices=EVENT_CHOICES, db_index=True, verbose_name=_("事件类型"))
    actor = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="signature_event_actions",
        verbose_name=_("操作人"),
    )
    project = models.ForeignKey(
        "LibraryProject",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="signature_events",
        verbose_name=_("项目"),
    )
    case = models.ForeignKey(
        "InspectionCase",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="signature_events",
        verbose_name=_("检测案件"),
    )
    submission = models.ForeignKey(
        "InspectionSubmission",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="signature_events",
        verbose_name=_("检测提交"),
    )
    task_no = models.CharField(max_length=64, blank=True, default="", db_index=True, verbose_name=_("任务编号"))
    task_code = models.CharField(max_length=128, blank=True, default="", verbose_name=_("任务模板代码"))
    output_target = models.CharField(max_length=32, blank=True, default="", verbose_name=_("产出类型"))
    library_file = models.ForeignKey(
        "LibraryFile",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="signature_events",
        verbose_name=_("关联文件"),
    )
    snapshot_path = models.CharField(max_length=512, blank=True, default="", verbose_name=_("使用时的文件路径"))
    content_sha256 = models.CharField(max_length=64, blank=True, default="", verbose_name=_("内容哈希快照"))
    detail = models.JSONField(default=dict, blank=True, verbose_name=_("附加信息"))
    created_at = models.DateTimeField(auto_now_add=True, db_index=True, verbose_name=_("发生时间"))

    class Meta:
        verbose_name = _("用户签名事件")
        verbose_name_plural = verbose_name
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["user", "-created_at"]),
            models.Index(fields=["user", "event_type", "-created_at"]),
        ]

    def __str__(self):
        return f"{self.user_id} {self.event_type} {self.role} @ {self.created_at:%Y-%m-%d %H:%M}"

    @property
    def event_type_label(self) -> str:
        return dict(self.EVENT_CHOICES).get(self.event_type, self.event_type)

    @property
    def role_label(self) -> str:
        if not (self.role or "").strip():
            return "—"
        return dict(SIGNATURE_USAGE_SLOT_CHOICES).get(self.role, self.role)


class BizOperationLog(models.Model):
    """医院信息 / 委托项目 / 产品目录相关写库操作日志（增删改、派工等）。"""

    SCOPE_HOSPITAL = "hospital"
    SCOPE_PROJECT = "project"
    SCOPE_PRODUCT = "product"
    SCOPE_CHOICES = [
        (SCOPE_HOSPITAL, _("医院信息")),
        (SCOPE_PROJECT, _("委托项目")),
        (SCOPE_PRODUCT, _("产品管理")),
    ]

    ACTION_CREATE = "create"
    ACTION_UPDATE = "update"
    ACTION_DELETE = "delete"
    ACTION_DISPATCH = "dispatch"
    ACTION_BIND = "bind"
    ACTION_UNBIND = "unbind"
    ACTION_OTHER = "other"
    ACTION_CHOICES = [
        (ACTION_CREATE, _("添加")),
        (ACTION_UPDATE, _("编辑")),
        (ACTION_DELETE, _("删除")),
        (ACTION_DISPATCH, _("派工")),
        (ACTION_BIND, _("绑定")),
        (ACTION_UNBIND, _("解绑")),
        (ACTION_OTHER, _("其他")),
    ]

    scope = models.CharField(max_length=16, choices=SCOPE_CHOICES, db_index=True, verbose_name=_("范围"))
    action = models.CharField(max_length=16, choices=ACTION_CHOICES, db_index=True, verbose_name=_("操作类型"))
    organization = models.ForeignKey(
        "CommissionOrganization",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="biz_operation_logs",
        verbose_name=_("关联机构"),
    )
    project = models.ForeignKey(
        "LibraryProject",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="biz_operation_logs",
        verbose_name=_("关联项目"),
    )
    entity_type = models.CharField(max_length=64, blank=True, default="", db_index=True, verbose_name=_("对象类型"))
    entity_id = models.PositiveIntegerField(null=True, blank=True, verbose_name=_("对象 ID"))
    summary = models.CharField(max_length=512, verbose_name=_("操作摘要"))
    detail = models.JSONField(default=dict, blank=True, verbose_name=_("详情"))
    actor = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="biz_operation_logs",
        verbose_name=_("操作人"),
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True, verbose_name=_("操作时间"))

    class Meta:
        verbose_name = _("业务操作日志")
        verbose_name_plural = verbose_name
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["scope", "-created_at"]),
            models.Index(fields=["organization", "-created_at"]),
            models.Index(fields=["project", "-created_at"]),
        ]

    def __str__(self):
        return f"{self.scope}:{self.action} {self.summary[:40]}"

    @property
    def action_label(self) -> str:
        return dict(self.ACTION_CHOICES).get(self.action, self.action)

    @property
    def actor_label(self) -> str:
        if self.actor_id is None:
            return "（已删除用户）"
        return self.actor.get_username()


class SalesProductCategory(models.Model):
    """销售产品分类（如放射卫生）。"""

    name = models.CharField(max_length=128, db_index=True, verbose_name=_("分类名称"))
    sort_order = models.PositiveIntegerField(default=0, verbose_name=_("排序"))
    is_active = models.BooleanField(default=True, db_index=True, verbose_name=_("启用"))
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("创建时间"))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_("更新时间"))

    class Meta:
        verbose_name = _("销售产品分类")
        verbose_name_plural = verbose_name
        ordering = ["sort_order", "id"]

    def __str__(self):
        return self.name


class SalesProductLine(models.Model):
    """销售产品线（如普放、核磁、个人剂量监测）。"""

    name = models.CharField(max_length=128, db_index=True, verbose_name=_("产品线名称"))
    sort_order = models.PositiveIntegerField(default=0, verbose_name=_("排序"))
    is_active = models.BooleanField(default=True, db_index=True, verbose_name=_("启用"))
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("创建时间"))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_("更新时间"))

    class Meta:
        verbose_name = _("销售产品线")
        verbose_name_plural = verbose_name
        ordering = ["sort_order", "id"]

    def __str__(self):
        return self.name


class SalesProduct(models.Model):
    """产品目录：用于销售登记；可预设对应设备类型与检测类型。"""

    STATUS_LISTED = "listed"
    STATUS_UNLISTED = "unlisted"
    STATUS_CHOICES = (
        (STATUS_LISTED, _("已上架")),
        (STATUS_UNLISTED, _("已下架")),
    )

    name = models.CharField(max_length=255, db_index=True, verbose_name=_("产品名称"))
    standard_price = models.DecimalField(
        max_digits=12, decimal_places=2, default=0, verbose_name=_("标准价格")
    )
    unit = models.CharField(max_length=32, blank=True, default="", verbose_name=_("单位"))
    status = models.CharField(
        max_length=16,
        choices=STATUS_CHOICES,
        default=STATUS_LISTED,
        db_index=True,
        verbose_name=_("上下架"),
    )
    listed_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        verbose_name=_("上架时间"),
        help_text=_("最近一次设为已上架的时间；新建且上架时写入"),
    )
    category = models.ForeignKey(
        SalesProductCategory,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="products",
        verbose_name=_("分类"),
    )
    product_line = models.ForeignKey(
        SalesProductLine,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="products",
        verbose_name=_("产品线"),
    )
    owner_name = models.CharField(max_length=64, blank=True, default="", verbose_name=_("负责人"))
    notes = models.CharField(max_length=500, blank=True, default="", verbose_name=_("备注"))
    specs = models.TextField(blank=True, default="", verbose_name=_("规格属性"))
    default_device_types = models.JSONField(
        default=list,
        blank=True,
        verbose_name=_("默认设备类型"),
        help_text=_('如 ["CT","DR"]，可多选；与医院设备类型一致'),
    )
    default_device_counts = models.JSONField(
        default=dict,
        blank=True,
        verbose_name=_("各设备类型台数"),
        help_text=_('如 {"CT": 2, "DR": 1}；未写的类型按 1 台计'),
    )
    default_inspection_types = models.JSONField(
        default=list,
        blank=True,
        verbose_name=_("默认检测类型"),
        help_text=_('如 ["验收检测","状态检测"]'),
    )
    is_active = models.BooleanField(default=True, db_index=True, verbose_name=_("启用"))
    created_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="sales_products_created",
        verbose_name=_("创建者"),
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("创建时间"))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_("更新时间"))

    class Meta:
        verbose_name = _("销售产品")
        verbose_name_plural = verbose_name
        ordering = ["-updated_at", "-id"]

    def __str__(self):
        return self.name

    @property
    def status_label(self) -> str:
        return dict(self.STATUS_CHOICES).get(self.status, self.status)

    @property
    def is_listed(self) -> bool:
        return self.status == self.STATUS_LISTED and self.is_active

    def device_count_for(self, device_type: str) -> int:
        counts = self.default_device_counts if isinstance(self.default_device_counts, dict) else {}
        try:
            n = int(counts.get(device_type) or 1)
        except (TypeError, ValueError):
            n = 1
        return max(1, n)

    @property
    def device_bindings_label(self) -> str:
        types = list(self.default_device_types or [])
        if not types:
            return ""
        parts = []
        for dt in types:
            n = self.device_count_for(dt)
            parts.append(f"{dt}×{n}" if n > 1 else dt)
        return "、".join(parts)


class LibraryProjectProduct(models.Model):
    """委托项目上的销售产品记录（与直接挂载设备并行，主要用于销售情况）。"""

    project = models.ForeignKey(
        LibraryProject,
        on_delete=models.CASCADE,
        related_name="project_products",
        verbose_name=_("所属委托"),
    )
    product = models.ForeignKey(
        SalesProduct,
        on_delete=models.PROTECT,
        related_name="project_links",
        verbose_name=_("产品"),
    )
    quantity = models.DecimalField(
        max_digits=12, decimal_places=2, default=1, verbose_name=_("数量")
    )
    unit_price = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
        verbose_name=_("成交单价"),
        help_text=_("挂载时快照标准价格，可按单笔调整"),
    )
    notes = models.CharField(max_length=500, blank=True, default="", verbose_name=_("备注"))
    sort_order = models.PositiveIntegerField(default=0, verbose_name=_("排序"))
    created_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="library_project_products_created",
        verbose_name=_("创建者"),
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("创建时间"))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_("更新时间"))

    class Meta:
        verbose_name = _("委托销售产品")
        verbose_name_plural = verbose_name
        ordering = ["sort_order", "id"]

    def __str__(self):
        return f"{self.project_id} / {self.product_id}"

    @property
    def amount(self):
        try:
            return (self.quantity or 0) * (self.unit_price or 0)
        except Exception:
            return 0


class LibraryProjectProductEquipment(models.Model):
    """销售产品下挂载的具体设备与检测类型。"""

    project_product = models.ForeignKey(
        LibraryProjectProduct,
        on_delete=models.CASCADE,
        related_name="equipment_links",
        verbose_name=_("委托产品"),
    )
    equipment = models.ForeignKey(
        CommissionOrgEquipment,
        on_delete=models.CASCADE,
        related_name="product_links",
        verbose_name=_("受检设备"),
    )
    inspection_type = models.CharField(
        max_length=64,
        blank=True,
        default="",
        verbose_name=_("检测类型"),
    )
    sort_order = models.PositiveIntegerField(default=0, verbose_name=_("排序"))
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("加入时间"))

    class Meta:
        verbose_name = _("委托产品设备")
        verbose_name_plural = verbose_name
        ordering = ["sort_order", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["project_product", "equipment", "inspection_type"],
                name="uniq_project_product_equipment_inspection",
            ),
        ]

    def __str__(self):
        return f"{self.project_product_id} / {self.equipment_id} / {self.inspection_type}"
