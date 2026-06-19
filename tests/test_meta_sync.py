import unittest
from unittest.mock import patch

from services.meta_sync import normalize_action_rows, upsert_rows


class _Response:
    def __init__(self, data):
        self.data = data


class _Table:
    def __init__(self):
        self.rows = []

    def upsert(self, rows, on_conflict):
        self.rows.extend(rows)
        return self

    def execute(self):
        return _Response(self.rows)


class _Supabase:
    def __init__(self):
        self.target = _Table()

    def table(self, _table_name):
        return self.target


class MetaSyncActionTests(unittest.TestCase):
    def test_action_breakdown_fields_are_preserved(self):
        rows = normalize_action_rows(
            {"client_id": "client-1", "ad_account_id": "act_123"},
            [{
                "date_start": "2026-06-18",
                "ad_id": "ad-1",
                "actions": [{
                    "action_type": "lead",
                    "value": "2",
                    "action_device": "mobile",
                    "action_destination": "messenger",
                }],
            }],
        )

        self.assertEqual("mobile", rows[0]["action_device"])
        self.assertEqual("messenger", rows[0]["action_destination"])
        self.assertEqual("all", rows[0]["action_reaction"])

    def test_upsert_removes_duplicate_conflict_keys(self):
        client = _Supabase()
        rows = [
            {"client_id": "client-1", "ad_id": "ad-1", "value": 1},
            {"client_id": "client-1", "ad_id": "ad-1", "value": 2},
            {"client_id": "client-1", "ad_id": "ad-2", "value": 3},
        ]

        with patch("services.meta_sync.supabase", return_value=client):
            saved = upsert_rows("meta_action_daily", rows, "client_id,ad_id")

        self.assertEqual(2, saved)
        self.assertEqual(
            {("ad-1", 2), ("ad-2", 3)},
            {(row["ad_id"], row["value"]) for row in client.target.rows},
        )


if __name__ == "__main__":
    unittest.main()
