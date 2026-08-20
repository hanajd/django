from pathlib import Path
from tempfile import TemporaryDirectory

from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from apps.core.models import CommissionOrganization, LibraryFile
from converter.project_vars import extract_project_values, load_keywords

from .models import (
    EvaluationLatexTemplate,
    EvaluationReport,
    EvaluationReportUpload,
    EvaluationReportUploadFile,
)
from .services import generate_base_report, report_has_base_content


class EvaluationFormExtractionTests(SimpleTestCase):
    def test_commission_form_fields_are_mapped_without_schema_defaults(self):
        markdown = """
| 评价类型 | ☑放射性职业病危害预评价 ☐放射性职业病危害控制效果评价 |  |  |
| --- | --- | --- | --- |
| 单位名称 | 示例医院 | 法定代表人 | 张三 |
| 联系人 | 李四 | 电话 | 13800000000 |
| 项目名称 | 示例医院后装机房改建项目 | 建设地址 | 示例路1号 |
| 项目性质 | 新建☐ 改建☑ 扩建☐ |  |  |
| 项目总投资 | 1200万元 | 放射卫生防护投资 | 30万元 |
| 单位简介 | 单位简介正文 | 项目简介 | 项目简介正文 |
"""
        keywords = load_keywords(Path("converter/data/project_keywords.csv"))

        values = extract_project_values(markdown, keywords, include_defaults=False)

        self.assertEqual(values["buildunit"], "示例医院")
        self.assertEqual(values["project"], "后装机房改建项目")
        self.assertEqual(values["projectnature"], "改建")
        self.assertEqual(values["buildunitlegalrep"], "张三")
        self.assertEqual(values["buildunitcontact"], "李四")
        self.assertEqual(values["buildunitphone"], "13800000000")
        self.assertEqual(values["reportkind"], "预评价报告书")
        self.assertIn("1200万元", values["totalinvestment"])
        self.assertNotIn("legalrep", values)
        self.assertNotIn("projectleader", values)

    def test_html_commission_form_tables_are_parsed(self):
        markdown = """
**江西辐射剂量检测院有限公司：**

**委托编号：250420 **

<table>
<tr><td>评价类型</td><td colspan="6">☑放射性职业病危害预评价☐放射性职业病危害控制效果评价</td></tr>
<tr><td rowspan="2">单位名称</td><td colspan="4" rowspan="2">萍乡市人民医院</td><td>法定代表人</td><td>刘绍华</td></tr>
<tr><td>负 责 人</td><td>郑志刚</td></tr>
<tr><td>联 系 人</td><td>陈佳龙</td><td>电话</td><td colspan="2">13707991201</td></tr>
<tr><td>项目名称</td><td colspan="4">萍乡市人民医院后装治疗机机房改建项目</td><td>建设地址</td><td>江西省萍乡市开发区武功山中大道8号</td></tr>
<tr><td>项目性质</td><td colspan="6">新建□  改建☑  扩建□</td></tr>
<tr><td>项目 总投资</td><td colspan="3">1200万元</td><td colspan="2">放射卫生防护 投资</td><td>30万元</td></tr>
<tr><td>单位简介</td><td colspan="6">单位简介正文</td></tr>
<tr><td>项目简介</td><td colspan="6">项目简介正文</td></tr>
</table>
"""
        keywords = load_keywords(Path("converter/data/project_keywords.csv"))
        values = extract_project_values(markdown, keywords, include_defaults=False)

        self.assertEqual(values["buildunit"], "萍乡市人民医院")
        self.assertEqual(values["project"], "后装治疗机机房改建项目")
        self.assertEqual(values["projectnature"], "改建")
        self.assertEqual(values["buildunitcontact"], "陈佳龙")
        self.assertEqual(values["company"], "江西辐射剂量检测院有限公司")
        self.assertEqual(values["reportno"], "赣检测院250420")
        self.assertIn("1200万元", values["totalinvestment"])


class StructuredEditWithoutEvaluationFormTests(TestCase):
    def setUp(self):
        self.temp_dir = TemporaryDirectory(prefix="eval_edit_test_")
        self.addCleanup(self.temp_dir.cleanup)
        root = Path(self.temp_dir.name)
        self.root = root
        settings_context = self.settings(
            EVALUATION_REPORT_WORK_ROOT=root / "work",
            EVALUATION_LATEX_TEMPLATE_ROOT=root / "templates",
            FILE_LIBRARY_ROOT=root / "library",
        )
        settings_context.enable()
        self.addCleanup(settings_context.disable)

        template_root = root / "templates" / "blank_test"
        (template_root / "chapters").mkdir(parents=True)
        (template_root / "main.tex").write_text(
            "% <<PROJECT_VARS>>\n% <<END_PROJECT_VARS>>\n"
            "\\begin{document}\n\\input{chapters/00-cover}\n\\end{document}\n",
            encoding="utf-8",
        )
        (template_root / "chapters" / "00-cover.tex").write_text(
            "\\begin{center}空白报告封面\\end{center}\n",
            encoding="utf-8",
        )

        self.user = User.objects.create_user("editor", password="test-password")
        hospital = CommissionOrganization.objects.create(
            level=CommissionOrganization.LEVEL_HOSPITAL,
            name="示例医院",
        )
        EvaluationLatexTemplate.objects.create(
            key="blank_test",
            name="空白测试模板",
            source=EvaluationLatexTemplate.Source.UPLOADED,
        )
        self.report = EvaluationReport.objects.create(
            title="无评价信息表报告",
            hospital=hospital,
            latex_template_key="blank_test",
            created_by=self.user,
        )

    def test_editor_opens_without_evaluation_form_or_base_report(self):
        self.client.force_login(self.user)

        response = self.client.get(
            reverse("evaluation_report_structured_edit", kwargs={"pk": self.report.pk})
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "fe-page")
        self.assertFalse(self.report.uploads.filter(slot_key="evaluation_form").exists())

    def test_generate_base_report_fills_template_fields_in_place(self):
        source_path = self.root / "library" / "evaluation_form.md"
        source_path.parent.mkdir(parents=True)
        source_path.write_text(
            "| 评价类型 | ☑放射性职业病危害预评价 |  |  |\n"
            "| --- | --- | --- | --- |\n"
            "| 单位名称 | 示例医院 | 法定代表人 | 张三 |\n"
            "| 项目名称 | 示例医院后装机房改建项目 | 建设地址 | 示例路1号 |\n"
            "| 单位简介 | 示例医院的个性化简介 | 项目性质 | 改建☑ |\n",
            encoding="utf-8",
        )
        library_file = LibraryFile.objects.create(
            original_name="评价信息表.md",
            relative_path="evaluation_form.md",
            category=LibraryFile.CATEGORY_EVALUATION_FORM,
            created_by=self.user,
        )
        slot = EvaluationReportUpload.objects.create(
            report=self.report,
            slot_key="evaluation_form",
            label="评价信息表",
            library_category=LibraryFile.CATEGORY_EVALUATION_FORM,
        )
        EvaluationReportUploadFile.objects.create(upload=slot, library_file=library_file)

        generate_base_report(self.report)

        main_tex = (self.report.work_dir / "latex" / "main.tex").read_text(encoding="utf-8")
        unit_profile = (
            self.report.work_dir / "latex" / "files" / "unitProfile.tex"
        ).read_text(encoding="utf-8")
        self.assertIn(r"\newcommand{\buildunit}{示例医院}", main_tex)
        self.assertIn(r"\newcommand{\project}{后装机房改建项目}", main_tex)
        self.assertIn("示例医院的个性化简介", unit_profile)
        self.assertNotIn(r"\input{chapters/99-eval-info}", main_tex)
        self.assertTrue(report_has_base_content(self.report))
