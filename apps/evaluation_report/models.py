from django.conf import settings
from django.contrib.auth.models import User
from django.db import models

from apps.core.models import CommissionOrganization, LibraryFile

from .constants import (
    DEVICE_CATEGORY_CHOICES,
    REPORT_TYPE_CHOICES,
)


class EvaluationLatexTemplate(models.Model):
    """可维护的 LaTeX 报告模板（内置或用户上传）。"""

    class Source(models.TextChoices):
        BUNDLED = "bundled", "内置"
        UPLOADED = "uploaded", "上传"

    key = models.SlugField(max_length=64, unique=True, verbose_name="模板键")
    name = models.CharField(max_length=255, verbose_name="名称")
    description = models.TextField(blank=True, verbose_name="说明")
    source = models.CharField(
        max_length=16,
        choices=Source.choices,
        default=Source.UPLOADED,
        verbose_name="来源",
    )
    bundled_dir = models.CharField(
        max_length=128,
        blank=True,
        verbose_name="内置目录名",
        help_text="source=bundled 时相对 LATEX_TEMPLATE_ROOT 的子目录",
    )
    device_category = models.CharField(
        max_length=32,
        choices=DEVICE_CATEGORY_CHOICES,
        blank=True,
        verbose_name="设备大类（已弃用）",
    )
    report_subtype = models.CharField(
        max_length=32,
        choices=REPORT_TYPE_CHOICES,
        blank=True,
        verbose_name="报告类型",
    )
    is_system = models.BooleanField(
        default=False,
        verbose_name="系统内置",
        help_text="不可删除；内置模板在库中只读预览",
    )
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="evaluation_latex_templates_created",
        verbose_name="创建人",
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="更新时间")

    class Meta:
        ordering = ["name", "key"]
        verbose_name = "评价报告 LaTeX 模板"
        verbose_name_plural = verbose_name

    def __str__(self) -> str:
        return f"{self.name} ({self.key})"

    @property
    def storage_root(self):
        if self.source == self.Source.BUNDLED:
            return settings.LATEX_TEMPLATE_ROOT / self.bundled_dir
        return settings.EVALUATION_LATEX_TEMPLATE_ROOT / self.key

    @property
    def is_editable(self) -> bool:
        return self.source == self.Source.UPLOADED

    @property
    def device_category_label(self) -> str:
        if not self.device_category:
            return "—"
        return self.get_device_category_display()

    @property
    def report_subtype_label(self) -> str:
        if not self.report_subtype:
            return "—"
        return self.get_report_subtype_display()

    @property
    def has_main_tex(self) -> bool:
        return (self.storage_root / "main.tex").is_file()


class EvaluationReport(models.Model):
    """评价报告实例：绑定医院 + 报告类型 + LaTeX 模板，按清单上传材料后编译 PDF。"""

    class Status(models.TextChoices):
        DRAFT = "draft", "草稿"
        UPLOADING = "uploading", "材料上传中"
        CONVERTING = "converting", "转换中"
        COMPILING = "compiling", "编译中"
        COMPILED = "compiled", "已生成 PDF"
        FAILED = "failed", "失败"

    title = models.CharField(max_length=255, verbose_name="标题")
    hospital = models.ForeignKey(
        CommissionOrganization,
        on_delete=models.PROTECT,
        related_name="evaluation_reports",
        verbose_name="医院",
        limit_choices_to={"level": CommissionOrganization.LEVEL_HOSPITAL, "is_active": True},
    )
    device_category = models.CharField(
        max_length=32,
        choices=DEVICE_CATEGORY_CHOICES,
        blank=True,
        default="",
        verbose_name="设备大类（已弃用）",
    )
    report_subtype = models.CharField(
        max_length=32,
        choices=REPORT_TYPE_CHOICES,
        blank=True,
        default="",
        verbose_name="评价报告类型",
    )
    latex_template_key = models.CharField(
        max_length=64,
        default="yp250420",
        verbose_name="LaTeX 模板",
    )
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.DRAFT,
        verbose_name="状态",
    )
    output_pdf = models.FileField(
        upload_to="evaluation_reports/output/",
        blank=True,
        verbose_name="输出 PDF",
    )
    compile_log = models.TextField(blank=True, verbose_name="编译日志")
    convert_log = models.TextField(blank=True, verbose_name="转换日志")
    error_message = models.TextField(blank=True, verbose_name="错误信息")
    work_subdir = models.CharField(
        max_length=64,
        blank=True,
        verbose_name="工作目录",
    )
    allow_incomplete_build = models.BooleanField(
        default=True,
        verbose_name="测试模式（允许缺项编译）",
        help_text="测试阶段可不上传全部材料即生成 PDF",
    )
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="evaluation_reports_created",
        verbose_name="创建人",
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="更新时间")

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "评价报告"
        verbose_name_plural = verbose_name

    def __str__(self) -> str:
        return f"{self.title} ({self.get_status_display()})"

    @property
    def work_dir(self):
        if not self.work_subdir:
            return None
        return settings.EVALUATION_REPORT_WORK_ROOT / self.work_subdir

    @property
    def template_def(self):
        from .latex_template_service import get_template_or_none

        return get_template_or_none(self.latex_template_key)

    @property
    def template_label(self) -> str:
        tpl = self.template_def
        return tpl.name if tpl else self.latex_template_key

    @property
    def device_category_label(self) -> str:
        if not self.device_category:
            return "—"
        return self.get_device_category_display()

    @property
    def report_subtype_label(self) -> str:
        if not self.report_subtype:
            return "—"
        return self.get_report_subtype_display()

    def ensure_work_dir(self):
        subdir = self.work_subdir or f"report_{self.pk}"
        path = settings.EVALUATION_REPORT_WORK_ROOT / subdir
        path.mkdir(parents=True, exist_ok=True)
        (path / "latex").mkdir(exist_ok=True)
        (path / "images").mkdir(exist_ok=True)
        if self.work_subdir != subdir:
            self.work_subdir = subdir
            self.save(update_fields=["work_subdir", "updated_at"])
        return path

    def upload_progress(self) -> dict[str, int]:
        qs = self.uploads.all()
        total = qs.count()
        done = qs.filter(files__isnull=False).distinct().count()
        return {"total": total, "done": done, "pending": total - done}

    def resolve_template_key(self) -> str:
        from .latex_template_service import template_for_type

        tpl = template_for_type(self.report_subtype)
        if tpl:
            return tpl.key
        return self.latex_template_key or "yp250420"


class EvaluationKeywordSchema(models.Model):
    """通用关键词：LaTeX \\command 与中文标签对应（全项目共享）。"""

    command = models.CharField(max_length=64, unique=True, verbose_name="LaTeX 命令")
    label_cn = models.CharField(max_length=128, verbose_name="中文标签")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="更新时间")

    class Meta:
        ordering = ["command"]
        verbose_name = "评价报告关键词（通用）"
        verbose_name_plural = verbose_name

    def __str__(self) -> str:
        return f"\\{self.command} → {self.label_cn}"


class EvaluationReportKeyword(models.Model):
    """项目具体关键词取值（每个评价报告一份）。"""

    report = models.ForeignKey(
        EvaluationReport,
        on_delete=models.CASCADE,
        related_name="project_keywords",
        verbose_name="评价报告",
    )
    command = models.CharField(max_length=64, verbose_name="LaTeX 命令")
    value = models.TextField(blank=True, verbose_name="取值")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="更新时间")

    class Meta:
        ordering = ["command"]
        verbose_name = "评价报告关键词（项目）"
        verbose_name_plural = verbose_name
        constraints = [
            models.UniqueConstraint(
                fields=["report", "command"],
                name="uniq_eval_report_keyword",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.report_id}:{self.command}"

    @property
    def label_cn(self) -> str:
        schema = EvaluationKeywordSchema.objects.filter(command=self.command).first()
        return schema.label_cn if schema else self.command


class EvaluationReportUpload(models.Model):
    """评价报告材料上传槽位（评价信息表 / 附件 1–12 等）。"""

    report = models.ForeignKey(
        EvaluationReport,
        on_delete=models.CASCADE,
        related_name="uploads",
        verbose_name="评价报告",
    )
    slot_key = models.CharField(max_length=64, verbose_name="槽位键")
    label = models.CharField(max_length=255, verbose_name="显示名称")
    library_category = models.CharField(max_length=32, verbose_name="文件库分类")
    sort_order = models.PositiveIntegerField(default=0, verbose_name="排序")
    required = models.BooleanField(default=False, verbose_name="必填")
    appendix_section = models.CharField(
        max_length=255,
        blank=True,
        verbose_name="附录 section 标题",
        help_text="来自模板 13-appendix.tex 的 \\section 标题",
    )
    appendix_anchor = models.CharField(
        max_length=16,
        default="section",
        verbose_name="附录锚点类型",
    )
    preview_md = models.TextField(blank=True, verbose_name="预览 Markdown")
    preview_tex = models.TextField(blank=True, verbose_name="预览 LaTeX")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="更新时间")

    class Meta:
        ordering = ["sort_order", "pk"]
        verbose_name = "评价报告上传项"
        verbose_name_plural = verbose_name
        constraints = [
            models.UniqueConstraint(
                fields=["report", "slot_key"],
                name="uniq_eval_report_upload_slot",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.report_id}:{self.slot_key}"

    @property
    def is_uploaded(self) -> bool:
        return self.files.exists()

    def ordered_files(self):
        return self.files.select_related("library_file").order_by("sort_order", "created_at", "pk")

    @property
    def primary_file(self) -> LibraryFile | None:
        row = self.ordered_files().first()
        return row.library_file if row else None


class EvaluationReportUploadFile(models.Model):
    """槽位下的单个上传文件（附件可多个，按 sort_order 排序）。"""

    class PageMode(models.TextChoices):
        NORMAL = "", "随正文纸张"
        A3_LANDSCAPE = "a3landscape", "A3 横置整页"

    upload = models.ForeignKey(
        EvaluationReportUpload,
        on_delete=models.CASCADE,
        related_name="files",
        verbose_name="上传槽位",
    )
    library_file = models.ForeignKey(
        LibraryFile,
        on_delete=models.CASCADE,
        related_name="evaluation_upload_files",
        verbose_name="文件库记录",
    )
    sort_order = models.PositiveIntegerField(default=0, verbose_name="排序")
    page_mode = models.CharField(
        max_length=32,
        blank=True,
        default="",
        choices=PageMode.choices,
        verbose_name="页面模式",
        help_text="空=A4 正文宽度；a3landscape=单独 A3 横置页",
    )
    rotate = models.PositiveSmallIntegerField(
        default=0,
        verbose_name="旋转角度",
        help_text="0 / 90 / 180 / 270",
    )
    width_percent = models.PositiveSmallIntegerField(
        default=100,
        verbose_name="图片宽度百分比",
        help_text="相对版心宽度，30–100；A3 横置时忽略",
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="上传时间")

    class Meta:
        ordering = ["sort_order", "created_at", "pk"]
        verbose_name = "评价报告上传文件"
        verbose_name_plural = verbose_name

    def __str__(self) -> str:
        return f"{self.upload_id}:{self.library_file_id}"

    def normalized_display(self) -> dict:
        """规范化显示选项，供 LaTeX / 前端使用。"""
        rotate = int(self.rotate or 0) % 360
        if rotate not in (0, 90, 180, 270):
            rotate = 0
        width = int(self.width_percent or 100)
        width = max(30, min(100, width))
        page_mode = (self.page_mode or "").strip()
        if page_mode not in {self.PageMode.NORMAL, self.PageMode.A3_LANDSCAPE}:
            page_mode = self.PageMode.NORMAL
        return {
            "page_mode": page_mode,
            "rotate": rotate,
            "width_percent": width,
        }
