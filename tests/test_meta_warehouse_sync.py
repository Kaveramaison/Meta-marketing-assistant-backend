import unittest
from datetime import date

from services.meta_warehouse_sync import (
    field_value,
    normalize_event_stats,
    normalize_pixel_diagnostic,
)


class MetaWarehouseNormalizationTests(unittest.TestCase):
    def setUp(self):
        self.account = {"client_id": "client-1", "ad_account_id": "act_123"}
        self.pixel = {"id": "pixel-1", "name": "Main Pixel"}

    def test_expands_event_source_series(self):
        rows = [{
            "aggregation": "event_source",
            "data": [
                {"value": "BROWSER", "count": 72},
                {"value": "SERVER", "count": 57},
            ],
        }]

        normalized = normalize_event_stats(
            self.account, self.pixel, date(2026, 6, 17), "event_source", rows
        )

        self.assertEqual(2, len(normalized))
        self.assertEqual({"BROWSER", "SERVER"}, {row["event_source"] for row in normalized})
        self.assertEqual(129, sum(row["event_count"] for row in normalized))
        self.assertTrue(all(row["event_name"] == "all" for row in normalized))

    def test_expands_event_name_series(self):
        rows = [{
            "aggregation": "event",
            "data": [
                {"value": "PageView", "count": 12},
                {"value": "Lead", "count": 3},
            ],
        }]

        normalized = normalize_event_stats(
            self.account, self.pixel, date(2026, 6, 17), "event", rows
        )

        self.assertEqual({"PageView", "Lead"}, {row["event_name"] for row in normalized})
        self.assertEqual(15, sum(row["event_count"] for row in normalized))
        self.assertTrue(all(row["event_source"] == "all" for row in normalized))

    def test_recognizes_work_email(self):
        field_data = [{"name": "work_email", "values": ["person@example.com"]}]

        self.assertEqual(
            "person@example.com",
            field_value(field_data, {"email", "work_email"}),
        )

    def test_maps_diagnostic_key_and_result(self):
        diagnostic = {
            "key": "pixel_missing_param_in_events",
            "title": "Pixel Missing Parameter in DPA Events",
            "result": "passed",
        }

        normalized = normalize_pixel_diagnostic(
            self.account, self.pixel, diagnostic, date(2026, 6, 18), 0
        )

        self.assertEqual("pixel_missing_param_in_events", normalized["diagnostic_code"])
        self.assertEqual("passed", normalized["status"])


if __name__ == "__main__":
    unittest.main()
