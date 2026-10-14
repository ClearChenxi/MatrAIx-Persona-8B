"""Synthetic checks for baseline information boundaries and scoring."""

from __future__ import annotations

import unittest

from thesis.e1a.methods.baselines import (
    S_NUMERIC, campaign_macro_ap, n0_rate, n1_features, predict_b1,
)
from thesis.e1a.methods.data import Split


def feature(row_id, *, rate="", recency="10", baskets="5", sales="100", type_="TypeB"):
    row = {
        "row_id": row_id,
        "prior_redeemed_campaign_count": "0",
        "prior_redemption_rate": rate,
        "recency_days": recency,
        "basket_count": baskets,
        "sales_total": sales,
        "campaign_type": type_,
    }
    row.update({field: "0" for field in S_NUMERIC})
    return row


class BaselineTests(unittest.TestCase):
    def test_n1_sees_only_activity_facts(self):
        a = feature("a", rate="1", recency="1", baskets="99")
        b = feature("b", rate="0", recency="80", baskets="1")
        self.assertEqual(n1_features(a), n1_features(b))

    def test_b1_orders_by_frozen_priority_within_campaign(self):
        rows = [feature("a", recency="40"), feature("b", recency="2"), feature("c", rate="0.4")]
        rows[2]["prior_redeemed_campaign_count"] = "1"
        links = {
            row["row_id"]: {"household_key": row["row_id"], "campaign": "14"}
            for row in rows
        }
        split = Split(tuple(rows), links, {"a": 0, "b": 0, "c": 1})
        scores = predict_b1(split)
        self.assertGreater(scores[2], scores[1])
        self.assertGreater(scores[1], scores[0])
        self.assertAlmostEqual(campaign_macro_ap(split, scores)["macro_ap"], 1.0)

    def test_n0_uses_only_development_labels(self):
        split = Split(tuple(), {}, {"a": 0, "b": 1, "c": 0})
        self.assertAlmostEqual(n0_rate(split), 1 / 3)

    def test_no_positive_activity_retained_but_ap_na(self):
        rows = [feature("a"), feature("b"), feature("c")]
        links = {"a": {"campaign": "14"}, "b": {"campaign": "14"}, "c": {"campaign": "16"}}
        split = Split(tuple(rows), links, {"a": 0, "b": 0, "c": 1})
        result = campaign_macro_ap(split, [0.1, 0.2, 0.3])
        self.assertIsNone(result["per_campaign"]["14"])
        self.assertEqual(result["n_campaigns_used"], 1)


if __name__ == "__main__":
    unittest.main()
