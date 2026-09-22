from unittest.mock import patch

from django.test import SimpleTestCase

from apps.api.inspection_pdf_service import accumulate_inspection_payloads_ordered_merge
from radiation_detection_report.instrument_table_overlay import collect_results_instrument_rows


class InspectionInstrumentMergeTests(SimpleTestCase):
    def test_ordered_merge_preserves_instruments_from_each_scope(self):
        qc = {
            "instrumentId": "40",
            "name": "X、γ辐射检测仪",
            "instrumentScope": "qualityControl",
            "registrySlot": 1,
            "enabled": True,
        }
        rp = {
            "instrumentId": "1",
            "name": "多功能X射线质量检测仪",
            "instrumentScope": "radiationProtection",
            "registrySlot": 2,
            "enabled": True,
        }

        merged = accumulate_inspection_payloads_ordered_merge(
            [
                {"taskNo": "20", "instruments": [qc]},
                {"taskNo": "21", "instruments": [rp]},
            ]
        )

        self.assertEqual(merged["instruments"], [qc, rp])

    def test_ordered_merge_keeps_same_instrument_in_both_scopes(self):
        shared = {
            "instrumentId": "13",
            "name": "标准水模",
            "enabled": True,
        }
        qc = {**shared, "instrumentScope": "qualityControl", "registrySlot": 1}
        rp = {**shared, "instrumentScope": "radiationProtection", "registrySlot": 2}

        merged = accumulate_inspection_payloads_ordered_merge(
            [{"instruments": [qc]}, {"instruments": [rp]}]
        )

        self.assertEqual(merged["instruments"], [qc, rp])

    @patch(
        "radiation_detection_report.instrument_table_overlay._lookup_instrument_catalog",
        return_value=None,
    )
    def test_results_rows_fall_back_to_displayable_flat_instruments(self, _lookup):
        payload = {
            "instruments": [
                {
                    "instrumentId": "40",
                    "identifier": "JXFS/YQ-064",
                    "name": "X、γ辐射检测仪",
                    "model": "AT1123",
                    "instrumentScope": "qualityControl",
                    "enabled": True,
                }
            ],
            "rawPayload": {
                "instruments": {
                    "qualityControl": [{"instrumentId": "40", "enabled": True}]
                }
            },
        }

        self.assertEqual(
            collect_results_instrument_rows(payload),
            [
                {
                    "code": "JXFS/YQ-064",
                    "model_name": "AT1123 X、γ辐射检测仪",
                    "calib_lines": [],
                    "calib_slash": True,
                }
            ],
        )


class ReportFieldMappingRenderTests(SimpleTestCase):
    """报告栏位映射 valueTemplate 渲染：相邻占位槽不得把数值无分隔粘连。"""

    def test_adjacent_slot_placeholders_get_separator(self):
        from apps.core.htmlpdf_report_mapping_service import (
            render_report_value_template,
        )

        self.assertEqual(
            render_report_value_template("{1}{2}", {"1": "167", "2": "167.00"}),
            "167 167.00",
        )
        self.assertEqual(
            render_report_value_template(
                "{f28}{f29}nGy/min", {"f28": "167", "f29": "167.00"}
            ),
            "167 167.00nGy/min",
        )
        self.assertEqual(
            render_report_value_template("{1}/{2}", {"1": "104", "2": "91"}),
            "104/91",
        )
        self.assertEqual(
            render_report_value_template("{1}，{2}", {"1": "a", "2": "b"}),
            "a，b",
        )

    def test_bare_slot_concat_covers_named_slots(self):
        from apps.core.htmlpdf_report_mapping_service import (
            _compose_report_site_field_final_value,
            _is_bare_slot_concat_template,
        )

        self.assertTrue(_is_bare_slot_concat_template("{f28}{f29}"))
        self.assertTrue(_is_bare_slot_concat_template("{1} {2}"))
        self.assertFalse(_is_bare_slot_concat_template("{1}/{2}"))
        self.assertFalse(_is_bare_slot_concat_template("{1}{2}nGy/min"))

        val, verdict = _compose_report_site_field_final_value(
            ["167", "167.00"],
            {"f28": "167", "f29": "167.00", "1": "167", "2": "167.00"},
            "{f28}{f29}",
            sources=[{"label": "检测值"}, {"label": "报出值"}],
            report_field_label="检测结果",
        )
        self.assertEqual(val, "167 167.00")
        self.assertIsNone(verdict)

    def test_bare_concat_empty_format_does_not_fall_back_to_raw_render(self):
        from apps.core.htmlpdf_report_mapping_service import (
            _compose_report_site_field_final_value,
        )

        # 裸连写模板在来源值全部被过滤时不得退回字面渲染（会把脏值粘回去）。
        val, _ = _compose_report_site_field_final_value(
            ["检测值", "报出值"],
            {"1": "检测值", "2": "报出值"},
            "{1}{2}",
            sources=[{"label": "检测值"}, {"label": "报出值"}],
            report_field_label="检测结果",
        )
        self.assertEqual(val, "")


class SyntheticQcVerdictInjectionTests(SimpleTestCase):
    """报告「单项判定」格：模板已有真实栏位时不再注入合成域，改由真实栏位先擦后写。"""

    def _result_field(self):
        return {
            "id": "透视受检者入射体表空气比释动能率典型值_检测结果",
            "placeholder": "透视受检者入射体表空气比释动能率典型值_检测结果",
            "pdfFieldId": "f83",
            "page": 5,
            "x": 340.0,
            "y": 170.0,
            "w": 80.0,
            "h": 20.0,
            "fieldType": "text",
        }

    def _verdict_field(self):
        return {
            "id": "透视受检者入射体表空气比释动能率典型值_单项判定",
            "placeholder": "透视受检者入射体表空气比释动能率典型值_单项判定",
            "pdfFieldId": "f84",
            "page": 5,
            "x": 489.0,
            "y": 175.0,
            "w": 26.0,
            "h": 15.0,
            "fieldType": "text",
        }

    def test_real_verdict_field_suppresses_synthetic_and_gets_erase_flag(self):
        from apps.api.inspection_report_make import (
            _inject_synthetic_qc_verdict_pdf_fields,
        )

        result_field = self._result_field()
        verdict_field = self._verdict_field()
        fields = [result_field, verdict_field]
        added = _inject_synthetic_qc_verdict_pdf_fields(
            fields, source_pdf_path=None
        )
        self.assertEqual(added, 0)
        self.assertEqual(len(fields), 2)
        self.assertTrue(verdict_field.get("_qcVerdictEraseCell"))

    def test_no_real_verdict_field_still_injects_synthetic(self):
        from apps.api.inspection_report_make import (
            _inject_synthetic_qc_verdict_pdf_fields,
        )

        fields = [self._result_field()]
        added = _inject_synthetic_qc_verdict_pdf_fields(
            fields, source_pdf_path=None
        )
        self.assertEqual(added, 1)
        self.assertTrue(fields[-1].get("_syntheticQcVerdict"))


class ReportFieldOverlapDedupeTests(SimpleTestCase):
    """导出前兜底：同页文本栏位高度重叠时只保留一个，避免同格叠字。"""

    def test_near_identical_rects_keep_real_field_over_synthetic(self):
        from apps.api.inspection_report_make import (
            _dedupe_overlapping_report_text_fields,
        )

        rows = [
            {
                "page": 5,
                "x0": 489.0,
                "y0": 175.0,
                "x1": 515.0,
                "y1": 190.0,
                "fieldType": "text",
                "value": "不合格",
                "pdfFieldId": "f84",
            },
            {
                "page": 5,
                "x0": 491.0,
                "y0": 177.0,
                "x1": 515.0,
                "y1": 189.0,
                "fieldType": "text",
                "value": "不合格",
                "pdfFieldId": "_synVerdict_f83",
                "_syntheticQcVerdict": True,
            },
        ]
        out = _dedupe_overlapping_report_text_fields(rows)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["pdfFieldId"], "f84")

    def test_partial_overlap_is_kept(self):
        from apps.api.inspection_report_make import (
            _dedupe_overlapping_report_text_fields,
        )

        rows = [
            {
                "page": 5,
                "x0": 100.0,
                "y0": 100.0,
                "x1": 160.0,
                "y1": 120.0,
                "fieldType": "text",
                "value": "a",
                "pdfFieldId": "f1",
            },
            {
                "page": 5,
                "x0": 150.0,
                "y0": 100.0,
                "x1": 210.0,
                "y1": 120.0,
                "fieldType": "text",
                "value": "b",
                "pdfFieldId": "f2",
            },
        ]
        self.assertEqual(len(_dedupe_overlapping_report_text_fields(rows)), 2)

    def test_same_rect_different_value_keeps_one(self):
        from apps.api.inspection_report_make import (
            _dedupe_overlapping_report_text_fields,
        )

        rows = [
            {
                "page": 5,
                "x0": 340.0,
                "y0": 170.0,
                "x1": 420.0,
                "y1": 190.0,
                "fieldType": "text",
                "value": "167",
                "pdfFieldId": "f83",
            },
            {
                "page": 5,
                "x0": 340.2,
                "y0": 170.0,
                "x1": 420.0,
                "y1": 190.1,
                "fieldType": "text",
                "value": "167.00 nGy/min",
                "pdfFieldId": "f99",
            },
        ]
        out = _dedupe_overlapping_report_text_fields(rows)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["pdfFieldId"], "f83")
