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
        ('template_tester', _('模板与 HTMLPDF 测试')),
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
    perm_process_pipeline = models.BooleanField(default=False, verbose_name=_('流程处理'))
    perm_htmlpdf = models.BooleanField(
        default=False,
        verbose_name=_('HTMLPDF 模板编辑器'),
        help_text=_('进入 HTMLPDF 编辑器及相关 API；不含 MinerU/Ollama 流程处理'),
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
    position = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        verbose_name=_('职位')
    )
    role = models.ForeignKey(
        Role,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='user_profiles',
        verbose_name=_('角色')
    )
    perm_overrides = models.JSONField(
        default=dict,
        blank=True,
        verbose_name=_('权限个性化覆盖'),
        help_text=_('按权限键存储 true/false；未出现的键继承角色。空对象表示完全跟随角色'),
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
        default=list,
        blank=True,
        verbose_name=_("模板默认检测仪器"),
        help_text=_(
            "有序的主数据仪器 id 列表（对应 InstrumentCatalog）。"
            "合并到提交的 instruments 中用于下拉与 PDF 展示；与提交去重后追加。"
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


class LibraryProject(models.Model):
    """文件库项目：用于隔离与筛选跨分类文件。"""

    code = models.CharField(max_length=64, unique=True, db_index=True, verbose_name=_("项目编码"))
    name = models.CharField(max_length=128, db_index=True, verbose_name=_("项目名称"))
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
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("创建时间"))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_("更新时间"))
    library_tasks = models.ManyToManyField(
        LibraryTask,
        blank=True,
        related_name="projects",
        verbose_name=_("关联任务"),
        help_text=_("项目启用哪些任务；分配任务时仅能选择已关联的任务"),
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
    """项目内流程岗位：同一用户在同一项目仅允许一个流程岗位。"""

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
        项目任务分配给某用户后：按该用户全局角色写入对应「流程岗位」槽位（每项目每岗位至多一人）。
        若该岗位已有记录则更新为当前用户（与测试账号一人兼任多岗的模型一致）。
        """
        if project is None or user is None:
            return None
        wf = cls.default_workflow_role_for_user(user)
        obj, _created = cls.objects.update_or_create(
            project=project,
            workflow_role=wf,
            defaults={"user": user},
        )
        return obj

    class Meta:
        verbose_name = _("项目流程成员")
        verbose_name_plural = verbose_name
        ordering = ["project_id", "workflow_role", "user_id"]
        constraints = [
            models.UniqueConstraint(
                fields=["project", "workflow_role"],
                name="uniq_project_workflow_role_slot",
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
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("创建时间"))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_("更新时间"))

    class Meta:
        verbose_name = _("仪器主数据")
        verbose_name_plural = verbose_name
        ordering = ["code"]

    def __str__(self):
        return f"{self.code} - {self.name}"
