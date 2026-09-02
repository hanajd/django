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
