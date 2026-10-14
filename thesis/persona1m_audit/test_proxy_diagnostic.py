"""Synthetic D features verify a label-free, bounded proxy diagnostic."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from thesis.persona1m_audit.proxy_diagnostic import summarize


class ProxyDiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.plan = {"proxy_diagnostic": {
            "status": "approved_D_features_only", "labels_permitted": False,
            "fields": ["coupon_discount_basket_share", "retail_discount_basket_share"],
            "quantiles": ["1/3", "1/2", "2/3", "9/10"],
        }}
        self.config = {"input_run": "invented", "input_hashes": {"features_D.csv": "invented-hash"}}
        self.rows = [
            {"coupon_discount_basket_share": value, "retail_discount_basket_share": value}
            for value in ("0", "0.2", "0.4", "0.6", "1")
        ]

    def test_nearest_rank_and_permission(self):
        with patch("thesis.persona1m_audit.proxy_diagnostic._features", return_value=self.rows) as loaded:
            report = summarize(self.plan, self.config)
        loaded.assert_called_once_with(self.config, "D")
        result = report["fields"]["coupon_discount_basket_share"]
        self.assertEqual(result["unique_values"], 5)
        self.assertEqual(result["nearest_rank_quantiles"],
                         {"1/3": "0.2", "1/2": "0.4", "2/3": "0.6", "9/10": "1"})
        self.assertFalse(report["D_labels_opened"])
        self.assertFalse(report["T_labels_opened"])

    def test_rejects_bad_range(self):
        self.rows[0]["retail_discount_basket_share"] = "1.1"
        with patch("thesis.persona1m_audit.proxy_diagnostic._features", return_value=self.rows):
            with self.assertRaisesRegex(ValueError, "outside"):
                summarize(self.plan, self.config)


if __name__ == "__main__":
    unittest.main()
