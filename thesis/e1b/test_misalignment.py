"""Check P2 preserves original S and replaces all of X without self-pairs."""

from __future__ import annotations

import unittest

from thesis.e1b.misalignment import mismatch
from thesis.e1b.test_agent_workflow import synthetic_row


class MisalignmentTests(unittest.TestCase):
    def test_cross_group_same_campaign_whole_record_swap(self):
        rows, groups = [], {}
        for index in range(4):
            row = synthetic_row()
            row["row_id"] = f"invented_{index}"
            row["basket_count"] = str(index + 1)
            row["target_product_sales_share"] = str(index / 10)
            rows.append(row)
            groups[row["row_id"]] = {
                "frequency_group": "low" if index < 2 else "high",
                "historical_redemption_group": "none",
                "target_category_match_group": "low" if index < 2 else "high",
            }
        altered, mapping = mismatch(tuple(rows), groups, seed=20261014)
        self.assertEqual(len(altered), 4)
        self.assertTrue(all(item["different_behavior_group"] for item in mapping))
        for original, changed, pair in zip(rows, altered, mapping):
            donor = next(row for row in rows if row["row_id"] == pair["donor_row_id"])
            self.assertNotEqual(original["row_id"], donor["row_id"])
            self.assertEqual(changed["row_id"], original["row_id"])
            self.assertEqual(changed["campaign_valid_days"], original["campaign_valid_days"])
            self.assertEqual(changed["basket_count"], donor["basket_count"])
            self.assertEqual(changed["target_product_sales_share"], donor["target_product_sales_share"])


if __name__ == "__main__":
    unittest.main()
