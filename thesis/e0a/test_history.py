"""Meaningful historical-input checks with invented CSV rows; zero real-data/model access."""

import copy
import csv
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import history
import run
from validation import probability


def plan():
    return json.loads((history.HERE / "history-plan.json").read_text())


def transaction(household, day, product="p1", amount="2.00", basket=None, quantity="1"):
    return {"household_key": household, "DAY": str(day), "PRODUCT_ID": product,
            "SALES_VALUE": amount, "QUANTITY": quantity, "BASKET_ID": basket or f"{household}:{day}"}


def fixture():
    return {
        "campaign_desc.csv": [{"CAMPAIGN": "27", "DESCRIPTION": "TypeC", "START_DAY": "237", "END_DAY": "300"}],
        "campaign_table.csv": [{"CAMPAIGN": "27", "DESCRIPTION": "TypeC", "household_key": h}
                               for h in ("invented_a", "invented_b", "invented_c", "invented_without_history")],
        "coupon.csv": [{"CAMPAIGN": "27", "COUPON_UPC": c, "PRODUCT_ID": p}
                       for c, p in (("c1", "p1"), ("c1", "p2"), ("c2", "p1"), ("c2", "p4"), ("c1", "p1"))],
        "product.csv": [{"PRODUCT_ID": p, "COMMODITY_DESC": c}
                        for p, c in (("p1", "Dairy"), ("p2", "Dairy"), ("p3", "Other"), ("p4", "Bread"))],
        "transaction_data.csv": [
            transaction("invented_a", 153, amount="1.00", basket="a1"),
            transaction("invented_a", 153, "p3", "3.00", "a1"),
            transaction("invented_a", 236, "p3", "6.00", "a2"),
            transaction("invented_a", 152, amount="not a number"),
            transaction("invented_a", 237, amount="not a number", product="unjoinable"),
            transaction("invented_a", 600, amount="not a number"),
            transaction("invented_b", 200, "p3", "5.00"),
            transaction("invented_c", 220, "p3", "0.00"),
            transaction("not_participating", 220, amount="not a number"),
            transaction("invented_a", 200, amount="-9.00", quantity="-1"),
            transaction("invented_a", 200, amount="0.00", quantity="0"),
        ],
    }


class HistoricalInputTests(unittest.TestCase):
    def test_git_paths_preserve_chinese_filenames_for_guards_and_hashes(self):
        paths = run.git("ls-files", "thesis/e0a/任务定义卡.md").splitlines()
        self.assertEqual(paths, ["thesis/e0a/任务定义卡.md"])
        self.assertTrue((run.ROOT / paths[0]).is_file())
        changed = run.git("diff", "--name-only", plan()["official_commit"], "HEAD").splitlines()
        self.assertIn(paths[0], changed)
        self.assertFalse(any(name.startswith('"') for name in changed))

    def test_time_boundary_baskets_and_missingness(self):
        profiles, facts, audit = history.assemble(plan(), fixture())
        by_household = {row["household_key"]: next(p["behavior"] for p in profiles if p["id"] == row["profile"])
                        for row in audit["selected"]}
        a = by_household["invented_a"]
        self.assertEqual((audit["lower_inclusive"], audit["cutoff_exclusive"]), (153, 237))
        self.assertEqual((a["shopping_baskets"], a["shopping_days"], a["days_since_last_positive_purchase"]), (2, 2, 1))
        self.assertEqual(a["positive_purchase_sales_value"], "10.00")
        self.assertEqual(a["target_product_sales_share"], "0.1000")
        self.assertEqual(a["target_known_category_sales_share"], "0.1000")
        self.assertEqual(a["days_since_last_target_product_purchase"], 84)
        self.assertEqual(by_household["invented_b"]["days_since_last_target_product_purchase"], "unknown: no target purchase in window")
        self.assertEqual(by_household["invented_c"]["target_product_sales_share"], "unknown: zero total sales")
        self.assertEqual(a["historical_redemption"], "unknown: redemption table not accessed")
        self.assertEqual(audit["eligible_with_positive_purchase"], 3)
        self.assertEqual(audit["label_tables_opened"], [])
        self.assertEqual(facts["valid_days"], 64)

    def test_coupon_join_dedup_and_category_weights(self):
        _, facts, _ = history.assemble(plan(), fixture())
        self.assertEqual((facts["unique_coupons"], facts["unique_products"]), (2, 3))
        self.assertEqual(facts["top_coupon_category_weights"], [
            {"category": "Dairy", "weight": "0.6667"}, {"category": "Bread", "weight": "0.3333"}])
        rows = fixture()
        rows["campaign_table.csv"].append(copy.deepcopy(rows["campaign_table.csv"][0]))
        self.assertEqual(history.assemble(plan(), rows)[2]["duplicate_participations"], 1)

    def test_order_does_not_change_sample_or_aggregates(self):
        rows = fixture()
        rows["campaign_table.csv"].append({"CAMPAIGN": "27", "DESCRIPTION": "TypeC", "household_key": "invented_d"})
        rows["transaction_data.csv"].append(transaction("invented_d", 210))
        expected = history.assemble(plan(), rows)
        for data in rows.values():
            data.reverse()
        self.assertEqual(history.assemble(plan(), rows), expected)
        self.assertEqual(len(expected[2]["selected"]), 3)

    def test_scope_cannot_silently_expand(self):
        for key, value in (("campaign_type", "TypeA"), ("campaign", "14"), ("end_day", 600),
                           ("start_day", 238), ("campaign", "28"), ("sample_size", 4),
                           ("maximum_generations", 7), ("sampling_seed", 0),
                           ("instrument_id", "wrong"), ("labels_accessible", True),
                           ("allowed_tables", list(history.TABLES) + ["coupon_redempt.csv"])):
            candidate = plan()
            candidate[key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                history.validate_plan(candidate)

    def test_bad_dates_joins_values_and_insufficient_history_fail(self):
        for case in ("dates", "target_join", "purchase_join", "negative", "nan", "few", "coverage"):
            rows = fixture()
            if case == "dates":
                rows["campaign_desc.csv"][0]["END_DAY"] = "301"
            elif case == "target_join":
                rows["coupon.csv"][0]["PRODUCT_ID"] = "missing"
            elif case == "purchase_join":
                rows["transaction_data.csv"][0]["PRODUCT_ID"] = "missing"
            elif case == "negative":
                rows["transaction_data.csv"][0]["SALES_VALUE"] = "-1"
            elif case == "nan":
                rows["transaction_data.csv"][0]["SALES_VALUE"] = "NaN"
            elif case == "few":
                rows["campaign_table.csv"] = rows["campaign_table.csv"][:2]
            else:
                rows["transaction_data.csv"] = [r for r in rows["transaction_data.csv"] if int(r["DAY"]) > 153]
            with self.subTest(case=case), self.assertRaises(ValueError):
                history.assemble(plan(), rows)

    def test_approval_guard_precedes_data_read(self):
        with patch.object(history, "source_path", side_effect=AssertionError("must not open data")):
            with self.assertRaisesRegex(ValueError, "confirmation"):
                history.prepare(plan(), Path("unused"))
        approved = plan()
        approved.update(execution_approved=True, status="confirmed", approval_reference="fixture only")
        history.validate_plan(approved, require_approval=True)
        approved["approval_reference"] = " "
        with self.assertRaises(ValueError):
            history.validate_plan(approved, require_approval=True)

    def test_excluded_table_and_symlink_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(ValueError, "not permitted"):
                history.source_path(root, "coupon_redempt.csv")
            (root / "campaign_desc.csv").symlink_to(history.HERE / "history-plan.json")
            with self.assertRaisesRegex(ValueError, "approved directory"):
                history.source_path(root, "campaign_desc.csv")

    def test_historical_instrument_is_strict(self):
        payload = {"instrument": {"id": "thesis_e0a_history_v1"},
                   "answers": [{"questionId": "p_redeem", "value": "0.0"}]}
        self.assertEqual(probability(payload, "thesis_e0a_history_v1"), 0)
        with self.assertRaises(ValueError):
            probability(payload)

    def test_prepare_reads_only_fixture_whitelist_and_saves_provenance(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "invented_sources"
            source.mkdir()
            rows = fixture()
            for name, records in rows.items():
                with (source / name).open("w", newline="", encoding="utf-8") as stream:
                    writer = csv.DictWriter(stream, fieldnames=records[0].keys())
                    writer.writeheader()
                    writer.writerows(records)
            # An invalid excluded table must never be read.
            (source / "coupon_redempt.csv").symlink_to(source / "absent")
            approved = plan()
            approved.update(execution_approved=True, status="confirmed", approval_reference="synthetic fixture test only",
                            data_directory=str(source))
            destination = root / "prepared"
            destination.mkdir()
            config, task = history.prepare(approved, destination)
            hashes = json.loads((destination / "source_hashes.json").read_text())
            self.assertEqual(set(hashes), set(history.TABLES))
            self.assertEqual(len(config["profiles"]), 3)
            self.assertTrue((task / "input/context.md").is_file())
            self.assertEqual(json.loads((destination / "input_audit.json").read_text())["label_tables_opened"], [])

    def test_runner_does_not_execute_unconfirmed_plan(self):
        import run_history
        with patch.object(history, "prepare", side_effect=AssertionError("must not prepare data")), \
             patch.object(run, "native", side_effect=AssertionError("must not call model")):
            with patch("sys.argv", ["run_history.py"]):
                run_history.main()
            for mode in ("--prepare-only", "--execute"):
                with self.subTest(mode=mode), patch("sys.argv", ["run_history.py", mode]), \
                     self.assertRaisesRegex(ValueError, "confirmation"):
                    run_history.main()

    def test_official_task_and_persona_render_without_http(self):
        profiles, facts, _ = history.assemble(plan(), fixture())
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory)
            task = history.create_task(destination, facts)
            run.bootstrap()
            from playground.survey_task_content import SURVEY_TASK_FOLDER_BY_QUESTIONNAIRE_ID
            original = dict(SURVEY_TASK_FOLDER_BY_QUESTIONNAIRE_ID)
            with patch.object(run, "native", side_effect=AssertionError("no HTTP allowed")), \
                 patch("socket.socket.connect", side_effect=AssertionError("no socket allowed")), \
                 history.task_binding(task):
                prepared = run.preflight(dict(plan(), profiles=profiles), destination, task=task)
            self.assertEqual(SURVEY_TASK_FOLDER_BY_QUESTIONNAIRE_ID, original)
            self.assertEqual(len(prepared), 3)
            for _, _, messages in prepared:
                text = json.dumps(messages, ensure_ascii=False)
                self.assertIn("thesis_e0a_history_v1", text)
                self.assertIn("historical redemption", text.lower())
                self.assertIn("unknown: redemption table not accessed", text)
                self.assertIn('valid_days', text)
                for identifier in ("invented_a", "invented_b", "invented_c", "history_1"):
                    self.assertNotIn(identifier, text)


if __name__ == "__main__":
    unittest.main()
