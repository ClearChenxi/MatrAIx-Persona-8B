from __future__ import annotations

import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from audit import run_audit
from seal_labels import seal
from verify import verify


def write_csv(path, headers, records):
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=headers)
        writer.writeheader()
        writer.writerows(records)


class E0bAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.data = self.root / "data"
        self.data.mkdir()
        self.plan = json.loads((HERE / "e0b-plan.json").read_text(encoding="utf-8"))
        self.plan["data_directory"] = str(self.data)
        archive = self.root / "source.zip"
        archive.write_bytes(b"fixture archive")
        self.plan["dataset_source"]["local_archive"] = str(archive)
        campaign_ids = [campaign for split in ("D", "R", "T") for campaign in self.plan["splits"][split]]
        dates = {}
        desc_rows = []
        table_rows = []
        coupon_rows = []
        for index, campaign in enumerate(campaign_ids):
            if campaign in self.plan["splits"]["T"]:
                start = 531 + self.plan["splits"]["T"].index(campaign) * 5
                end = 600 if campaign == "15" else start + 8
            elif campaign in self.plan["splits"]["R"]:
                start = 435 + self.plan["splits"]["R"].index(campaign) * 10
                end = start + 7
            else:
                start = 237 + self.plan["splits"]["D"].index(campaign) * 10
                end = start + 7
            dates[campaign] = (start, end)
            desc_rows.append({"DESCRIPTION": "TypeB", "CAMPAIGN": campaign, "START_DAY": start, "END_DAY": end})
            for household in ("100", "200", "300", "400", "500"):
                table_rows.append({"DESCRIPTION": "TypeB", "household_key": household, "CAMPAIGN": campaign})
            coupon_rows.append({"COUPON_UPC": f"9{int(campaign):03d}", "PRODUCT_ID": "10", "CAMPAIGN": campaign})
        table_rows.append({"DESCRIPTION": "TypeB", "household_key": "600", "CAMPAIGN": "27"})
        write_csv(self.data / "campaign_desc.csv", self.plan["tables"]["campaign_desc.csv"]["headers"], desc_rows)
        write_csv(self.data / "campaign_table.csv", self.plan["tables"]["campaign_table.csv"]["headers"], table_rows)
        write_csv(self.data / "coupon.csv", self.plan["tables"]["coupon.csv"]["headers"], coupon_rows)
        write_csv(self.data / "product.csv", self.plan["tables"]["product.csv"]["headers"], [{
            "PRODUCT_ID": "10", "MANUFACTURER": "1", "DEPARTMENT": "FOOD", "BRAND": "National",
            "COMMODITY_DESC": "TEST", "SUB_COMMODITY_DESC": "TEST", "CURR_SIZE_OF_PRODUCT": "1",
        }])
        transactions = []
        for campaign, (start, _) in dates.items():
            for household in ("100", "200", "300", "400", "500"):
                transactions.append({
                    "household_key": household, "BASKET_ID": f"{campaign}{household}", "DAY": start - 1,
                    "PRODUCT_ID": "10", "QUANTITY": "1", "SALES_VALUE": "2.00", "STORE_ID": "1",
                    "RETAIL_DISC": "0", "TRANS_TIME": "1200", "WEEK_NO": "1",
                    "COUPON_DISC": "0", "COUPON_MATCH_DISC": "0",
                })
            transactions.append({
                "household_key": "100", "BASKET_ID": f"out{campaign}", "DAY": start,
                "PRODUCT_ID": "10", "QUANTITY": "1", "SALES_VALUE": "2.00", "STORE_ID": "1",
                "RETAIL_DISC": "0", "TRANS_TIME": "1200", "WEEK_NO": "1",
                "COUPON_DISC": "0", "COUPON_MATCH_DISC": "0",
            })
        transactions.append({
            "household_key": "999", "BASKET_ID": "coverage-start", "DAY": "1", "PRODUCT_ID": "10",
            "QUANTITY": "1", "SALES_VALUE": "1.00", "STORE_ID": "1", "RETAIL_DISC": "0",
            "TRANS_TIME": "1200", "WEEK_NO": "1", "COUPON_DISC": "0", "COUPON_MATCH_DISC": "0",
        })
        max_end = max(end for _, end in dates.values())
        transactions.append({
            "household_key": "999", "BASKET_ID": "coverage-end", "DAY": max_end,
            "PRODUCT_ID": "10", "QUANTITY": "1", "SALES_VALUE": "1.00", "STORE_ID": "1",
            "RETAIL_DISC": "0", "TRANS_TIME": "1200", "WEEK_NO": "99",
            "COUPON_DISC": "0", "COUPON_MATCH_DISC": "0",
        })
        write_csv(self.data / "transaction_data.csv", self.plan["tables"]["transaction_data.csv"]["headers"], transactions)
        redemption_rows = []
        for campaign, (start, _) in dates.items():
            redemption_rows.append({
                "household_key": "100", "DAY": start, "COUPON_UPC": f"9{int(campaign):03d}", "CAMPAIGN": campaign,
            })
        redemption_rows.append({"household_key": "100", "DAY": "1", "COUPON_UPC": "9999", "CAMPAIGN": "29"})
        campaign_29_start = dates["29"][0]
        redemption_rows.extend([
            redemption_rows[[row["CAMPAIGN"] for row in redemption_rows].index("29")].copy(),
            {"household_key": "100", "DAY": campaign_29_start, "COUPON_UPC": "9028", "CAMPAIGN": "29"},
            {"household_key": "100", "DAY": campaign_29_start - 1, "COUPON_UPC": "9029", "CAMPAIGN": "29"},
            {"household_key": "600", "DAY": campaign_29_start, "COUPON_UPC": "9029", "CAMPAIGN": "29"},
        ])
        write_csv(self.data / "coupon_redempt.csv", self.plan["tables"]["coupon_redempt.csv"]["headers"], redemption_rows)
        write_csv(self.data / "hh_demographic.csv", self.plan["tables"]["hh_demographic.csv"]["headers"], [{
            "classification_1": "A", "classification_2": "A", "classification_3": "A",
            "HOMEOWNER_DESC": "Unknown", "classification_5": "A", "classification_4": "A",
            "KID_CATEGORY_DESC": "Unknown", "household_key": "100",
        }])
        write_csv(self.data / "causal_data.csv", self.plan["tables"]["causal_data.csv"]["headers"], [{
            "PRODUCT_ID": "10", "STORE_ID": "1", "WEEK_NO": "1", "display": "0", "mailer": "0",
        }])

    def tearDown(self):
        self.temp.cleanup()

    def test_complete_audit_seals_test_results(self):
        run_dir = self.root / "run"
        manifest = run_audit(self.plan, run_dir, require_repository=False)
        self.assertFalse(manifest["label_table_opened"])
        safe = seal(self.plan, run_dir)
        self.assertFalse(safe["test_label_values_exposed"])
        self.assertNotIn("y1_positive", json.dumps(safe))
        anomalies = safe["predeclared_invalid_redemption_counts"]
        self.assertEqual(anomalies["duplicate_exact_redemption_row"], 1)
        self.assertEqual(anomalies["coupon_belongs_to_other_campaign"], 1)
        self.assertEqual(anomalies["redemption_outside_campaign_dates"], 1)
        self.assertEqual(anomalies["household_not_participating_in_declared_campaign"], 1)
        result = verify(run_dir)
        self.assertEqual(result["status"], "verified")
        self.assertEqual(result["activities"], 25)
        permissions = json.loads((run_dir / "public/time_and_label_permissions.json").read_text())
        self.assertTrue(all(permissions["checks"].values()))
        with (run_dir / "private/cohort_permissions.csv").open(newline="") as stream:
            rows = list(csv.DictReader(stream))
        roles = {}
        for row in rows:
            if row["split"] == "T" and row["eligible"] == "1":
                roles.setdefault(row["household_key"], set()).add(row["e2_role"])
        self.assertTrue(roles)
        self.assertTrue(all(len(values) == 1 for values in roles.values()))
        source = json.loads((run_dir / "public/dataset_source.json").read_text())
        self.assertEqual(source["local_archive"], str((self.root / "source.zip").resolve()))
        self.assertEqual(source["archive_size_bytes"], len(b"fixture archive"))
        self.assertEqual(len(source["archive_sha256"]), 64)
        registry = json.loads((run_dir / "public/source_manifest.json").read_text())
        self.assertTrue(all("primary_key" in item and item["time_role"] for item in registry.values()))
        feasibility = json.loads((run_dir / "public/activity_feasibility.json").read_text())
        test_activities = [item for item in feasibility["activities"] if item["split"] == "T"]
        self.assertEqual(len(test_activities), 11)
        self.assertTrue(all(item["e2_support"]["low_support"] for item in test_activities))
        self.assertTrue(all(item["e2_support"]["action"] == "flag_and_retain" for item in test_activities))

    def test_outcome_coverage_excludes_latest_campaign(self):
        transaction_path = self.data / "transaction_data.csv"
        with transaction_path.open("r", encoding="utf-8", newline="") as stream:
            records = list(csv.DictReader(stream))
        records = [row for row in records if row["BASKET_ID"] != "coverage-end"]
        write_csv(transaction_path, self.plan["tables"]["transaction_data.csv"]["headers"], records)
        run_dir = self.root / "incomplete"
        run_audit(self.plan, run_dir, require_repository=False)
        feasibility = json.loads((run_dir / "public/activity_feasibility.json").read_text())
        latest = max(feasibility["activities"], key=lambda item: item["end_day"])
        self.assertFalse(latest["outcome_source_complete"])
        self.assertIn("outcome_source_incomplete", latest["exclusion_reasons"])

    def test_time_permission_overlap_is_rejected(self):
        path = self.data / "campaign_desc.csv"
        with path.open("r", encoding="utf-8", newline="") as stream:
            records = list(csv.DictReader(stream))
        last_d_end = max(int(row["END_DAY"]) for row in records if row["CAMPAIGN"] in self.plan["splits"]["D"])
        for row in records:
            if row["CAMPAIGN"] == "9":
                row["START_DAY"] = str(last_d_end)
                row["END_DAY"] = str(last_d_end + 7)
        write_csv(path, self.plan["tables"]["campaign_desc.csv"]["headers"], records)
        with self.assertRaisesRegex(ValueError, "temporal permission checks failed"):
            run_audit(self.plan, self.root / "overlap", require_repository=False)

    def test_invalid_history_row_is_traced_without_discarding_valid_history(self):
        path = self.data / "transaction_data.csv"
        with path.open("r", encoding="utf-8", newline="") as stream:
            records = list(csv.DictReader(stream))
        invalid = records[0].copy()
        invalid["SALES_VALUE"] = "-1"
        records.append(invalid)
        write_csv(path, self.plan["tables"]["transaction_data.csv"]["headers"], records)
        run_dir = self.root / "invalid-history"
        run_audit(self.plan, run_dir, require_repository=False)
        with (run_dir / "private/cohort_permissions.csv").open(newline="") as stream:
            cohort = list(csv.DictReader(stream))
        unit = next(row for row in cohort if row["campaign"] == "27" and row["household_key"] == "100")
        self.assertEqual(unit["eligible"], "1")
        with (run_dir / "private/history_anomalies.csv").open(newline="") as stream:
            anomalies = list(csv.DictReader(stream))
        traced = next(row for row in anomalies if row["campaign"] == "27" and row["household_key"] == "100")
        self.assertIn("negative_sales_on_positive_quantity", traced["anomaly_categories"])


if __name__ == "__main__":
    unittest.main()
