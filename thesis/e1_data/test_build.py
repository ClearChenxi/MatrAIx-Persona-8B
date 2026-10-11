from __future__ import annotations

import json
import sys
import unittest
from collections import Counter
from decimal import Decimal
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from build import (
    BASE_FEATURE_FIELDS,
    full_share_json,
    nearest_rank,
    persona_input,
    permitted_history,
    row_identifier,
    tercile_group,
    validate_plan,
)


class E1DataUnitTests(unittest.TestCase):
    def setUp(self):
        self.plan = json.loads((HERE / "plan.json").read_text(encoding="utf-8"))

    def test_plan_and_history_label_permissions_are_frozen(self):
        validate_plan(self.plan)
        self.assertTrue(permitted_history("D", "D", self.plan))
        self.assertFalse(permitted_history("R", "R", self.plan))
        self.assertTrue(permitted_history("T", "D", self.plan))
        self.assertTrue(permitted_history("T", "R", self.plan))
        self.assertFalse(permitted_history("T", "T", self.plan))
        self.assertNotIn("household_key", BASE_FEATURE_FIELDS)
        self.assertNotIn("campaign", BASE_FEATURE_FIELDS)

    def test_nearest_rank_groups_merge_repeated_thresholds(self):
        values = [1, 1, 1, 2, 3, 9]
        lower = nearest_rank(values, 1 / 3)
        upper = nearest_rank(values, 2 / 3)
        self.assertEqual((lower, upper), (1, 2))
        self.assertEqual(tercile_group(1, lower, upper), "low")
        self.assertEqual(tercile_group(2, lower, upper), "medium")
        self.assertEqual(tercile_group(3, lower, upper), "high")
        self.assertEqual(tercile_group(0, 0, 0), "low")
        self.assertEqual(tercile_group(1, 0, 0), "high")

    def test_vocabulary_mapping_is_deterministic(self):
        raw = Counter({"FOOD": Decimal("3"), "UNSEEN": Decimal("1")})
        encoded = json.loads(full_share_json(raw, ["FOOD"], "<OTHER>", 6))
        self.assertEqual(encoded, {"<OTHER>": "0.250000", "FOOD": "0.750000"})
        self.assertEqual(row_identifier("v1", "001", "29"), row_identifier("v1", "001", "29"))
        self.assertNotEqual(row_identifier("v1", "001", "29"), row_identifier("v1", "001", "28"))

    def test_persona_render_omits_identifiers_and_outcomes(self):
        row = {field: "" for field in BASE_FEATURE_FIELDS}
        row.update({
            "row_id": "opaque", "history_window_days": "84", "recency_days": "1", "basket_count": "2",
            "shopping_day_count": "2", "positive_line_count": "3", "return_line_count": "0",
            "sales_total": "10.00", "average_basket_sales": "5.00",
            "zero_sales_total_flag": "0",
            "retail_discount_basket_share": "0.500000", "coupon_discount_basket_share": "0.000000",
            "prior_completed_campaign_count": "0", "prior_redeemed_campaign_count": "0",
            "department_spend_shares_json": '{"FOOD":"1.000000","<OTHER>":"0.000000"}',
            "brand_type_spend_shares_json": '{"National":"1.000000","<OTHER>":"0.000000"}',
            "target_product_sales_share": "0.100000", "target_commodity_sales_share": "0.200000",
            "campaign_type": "TypeB", "campaign_valid_days": "33", "campaign_unique_coupon_count": "4",
            "campaign_unique_product_count": "10", "campaign_department_count": "1",
            "campaign_commodity_count": "2", "campaign_brand_type_count": "1",
            "campaign_department_weights_json": '{"FOOD":"1.000000","<OTHER>":"0.000000"}',
            "campaign_brand_type_weights_json": '{"National":"1.000000","<OTHER>":"0.000000"}',
            "campaign_other_commodity_weight": "0.000000",
        })
        rendered = json.dumps(persona_input(row), ensure_ascii=False)
        self.assertNotIn("opaque", rendered)
        self.assertNotIn("row_id", rendered)
        self.assertNotIn("household_key", rendered)
        self.assertNotIn("campaign_id", rendered)
        self.assertNotIn("y1_redeemed", rendered)
        self.assertIn("future outcomes", rendered)


if __name__ == "__main__":
    unittest.main()
