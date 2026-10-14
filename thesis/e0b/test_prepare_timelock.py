from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

from prepare_timelock import split_label_bytes


class PrepareTimelockTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "labels.csv"
        self.policy = {
            "logical_label_objects": {
                "T_adaptation_labels": {"e2_role": "adaptation", "expected_rows": 2},
                "T_final_evaluation_labels": {"e2_role": "final_evaluation", "expected_rows": 1},
            }
        }

    def tearDown(self):
        self.temp.cleanup()

    def write_rows(self, rows):
        with self.path.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=["household_key", "campaign", "e2_role", "y1_redeemed"])
            writer.writeheader()
            writer.writerows(rows)

    def test_split_is_deterministic_and_does_not_write_plaintext_files(self):
        self.write_rows([
            {"household_key": "1", "campaign": "14", "e2_role": "adaptation", "y1_redeemed": "0"},
            {"household_key": "2", "campaign": "14", "e2_role": "final_evaluation", "y1_redeemed": "1"},
            {"household_key": "3", "campaign": "15", "e2_role": "adaptation", "y1_redeemed": "1"},
        ])
        first = split_label_bytes(self.path, self.policy)
        second = split_label_bytes(self.path, self.policy)
        self.assertEqual(first, second)
        self.assertEqual(set(first), {"T_adaptation_labels", "T_final_evaluation_labels"})
        self.assertEqual(sorted(path.name for path in self.path.parent.iterdir()), ["labels.csv"])
        self.assertEqual(first["T_adaptation_labels"].count(b"\n"), 3)
        self.assertEqual(first["T_final_evaluation_labels"].count(b"\n"), 2)

    def test_unknown_role_and_wrong_partition_count_fail_closed(self):
        self.write_rows([
            {"household_key": "1", "campaign": "14", "e2_role": "unknown", "y1_redeemed": "0"},
        ])
        with self.assertRaisesRegex(ValueError, "unknown E2 role"):
            split_label_bytes(self.path, self.policy)
        self.write_rows([
            {"household_key": "1", "campaign": "14", "e2_role": "adaptation", "y1_redeemed": "0"},
            {"household_key": "2", "campaign": "14", "e2_role": "final_evaluation", "y1_redeemed": "1"},
        ])
        with self.assertRaisesRegex(ValueError, "row count differs"):
            split_label_bytes(self.path, self.policy)


if __name__ == "__main__":
    unittest.main()
