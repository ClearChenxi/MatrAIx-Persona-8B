"""P4 uses one fixed label-free subset for every paired condition."""

from __future__ import annotations

import unittest

from thesis.e1b.subsample import select_p4


class SubsampleTests(unittest.TestCase):
    def test_per_campaign_cap_and_determinism(self):
        rows, links, groups = [], {}, {}
        for campaign in (14, 15):
            for index in range(7):
                row_id = f"invented-{campaign}-{index}"
                rows.append({"row_id": row_id})
                links[row_id] = {"campaign": str(campaign)}
                groups[row_id] = {
                    "frequency_group": "low" if index < 3 else "high",
                    "historical_redemption_group": "none",
                    "target_category_match_group": "low" if index % 2 else "high",
                }
        first = select_p4(tuple(rows), links, groups, per_campaign_cap=4, seed=20261014)
        self.assertEqual(first, select_p4(tuple(rows), links, groups, per_campaign_cap=4, seed=20261014))
        self.assertEqual(len(first), 8)
        self.assertEqual(sum(links[row_id]["campaign"] == "14" for row_id in first), 4)
        self.assertEqual(sum(links[row_id]["campaign"] == "15" for row_id in first), 4)


if __name__ == "__main__":
    unittest.main()
