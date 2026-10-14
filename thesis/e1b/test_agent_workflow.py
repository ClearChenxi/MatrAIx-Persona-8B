"""Check a generated real-task Survey prompt without an LLM call."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from thesis.e1b.agent_workflow import bind_task, build_task, e0a_run, partition_fields


def synthetic_row(campaign_type="TypeC", duration="54"):
    # Mirror the frozen 56-column structure, without any actual household.
    names = (
        "history_window_days recency_days basket_count shopping_day_count positive_line_count "
        "return_line_count sales_total average_basket_sales zero_sales_total_flag "
        "retail_discount_basket_share coupon_discount_basket_share prior_completed_campaign_count "
        "prior_redeemed_campaign_count prior_redemption_rate no_prior_campaign_flag "
        "department_spend_shares_json top_commodity_1 top_commodity_1_share top_commodity_2 "
        "top_commodity_2_share top_commodity_3 top_commodity_3_share other_commodity_share "
        "brand_type_spend_shares_json product_taxonomy_missing_flag campaign_type campaign_valid_days "
        "campaign_unique_coupon_count campaign_unique_product_count campaign_department_count "
        "campaign_commodity_count campaign_brand_type_count campaign_department_weights_json "
        "campaign_brand_type_weights_json campaign_top_commodity_1 campaign_top_commodity_1_weight "
        "campaign_top_commodity_2 campaign_top_commodity_2_weight campaign_top_commodity_3 "
        "campaign_top_commodity_3_weight campaign_top_commodity_4 campaign_top_commodity_4_weight "
        "campaign_top_commodity_5 campaign_top_commodity_5_weight campaign_top_commodity_6 "
        "campaign_top_commodity_6_weight campaign_top_commodity_7 campaign_top_commodity_7_weight "
        "campaign_top_commodity_8 campaign_top_commodity_8_weight campaign_other_commodity_weight "
        "campaign_unknown_taxonomy_flag target_product_sales_share target_commodity_sales_share "
        "target_product_recency_days no_target_product_purchase_flag"
    ).split()
    row = {name: "0" for name in names}
    row["row_id"] = "invented_row"
    row["campaign_type"] = campaign_type
    row["campaign_valid_days"] = duration
    row["campaign_unique_coupon_count"] = "33"
    row["top_commodity_1"] = "FRUIT"
    row["campaign_top_commodity_1"] = "FRUIT"
    return row


class AgentWorkflowTests(unittest.TestCase):
    def test_generated_task_uses_real_activity_card_and_official_rendering(self):
        row = synthetic_row()
        household, campaign = partition_fields(row)
        self.assertEqual(campaign["campaign_valid_days"], "54")
        self.assertNotIn("campaign_valid_days", household)
        with tempfile.TemporaryDirectory() as temporary:
            dest = Path(temporary)
            task = build_task((row,), dest)
            context = (task / "input/context.md").read_text(encoding="utf-8")
            self.assertIn("campaign_valid_days: 54", context)
            self.assertIn("campaign_unique_coupon_count: 33", context)
            self.assertNotIn("10 coupons applicable", context)
            e0a_run.bootstrap()
            with bind_task(task):
                prepared = e0a_run.preflight(
                    {"profiles": [{"id": "invented_profile", "behavior": household}]},
                    dest, task=task,
                )
            messages = prepared[0][2]
            self.assertIn("campaign_valid_days: 54", messages[1]["content"])
            self.assertIn("Top Commodity 1: FRUIT", messages[0]["content"])
            self.assertNotIn("invented_row", str(messages))

    def test_activity_facts_must_match_within_batch(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(ValueError):
                build_task((synthetic_row(), synthetic_row(duration="10")), Path(temporary))


if __name__ == "__main__":
    unittest.main()
