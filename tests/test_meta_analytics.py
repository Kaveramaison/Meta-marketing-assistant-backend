import unittest
from datetime import date

from services.meta_analytics import comparison_window, metric_delta, metric_summary


class MetaAnalyticsMetricTests(unittest.TestCase):
    def test_summary_uses_shared_metric_definitions(self):
        summary = metric_summary([
            {"spend": 100, "impressions": 1000, "clicks": 20, "reach": 800, "results": 5, "revenue": 300},
            {"spend": 50, "impressions": 500, "clicks": 5, "reach": 400, "results": 5, "revenue": 100},
        ])
        self.assertEqual(summary["spend"], 150)
        self.assertEqual(summary["ctr"], 1.67)
        self.assertEqual(summary["cpc"], 6)
        self.assertEqual(summary["cpm"], 100)
        self.assertEqual(summary["cpl"], 15)
        self.assertEqual(summary["roas"], 2.67)
        self.assertEqual(summary["frequency"], 1.25)

    def test_previous_period_matches_selected_duration(self):
        self.assertEqual(
            comparison_window(date(2026, 6, 10), date(2026, 6, 16), "previous_period"),
            (date(2026, 6, 3), date(2026, 6, 9)),
        )

    def test_delta_is_null_without_comparison_denominator(self):
        delta = metric_delta({"spend": 100, "results": 4}, {"spend": 0, "results": 2})
        self.assertIsNone(delta["spend"])
        self.assertEqual(delta["results"], 100)


if __name__ == "__main__":
    unittest.main()
