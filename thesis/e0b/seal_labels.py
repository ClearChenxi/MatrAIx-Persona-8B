"""Construct E0b labels while keeping T outcomes in a local logical seal."""

from __future__ import annotations

import argparse
import csv
import json
import os
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

from audit import canonical_json, digest, rows, validate_plan, write_checksums, write_csv, write_json


def load_cohort(path):
    with path.open("r", encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def parse_day(value):
    if not value or any(ch in value for ch in ".eE"):
        raise ValueError
    return int(value)


def parse_decimal(value):
    result = Decimal(value)
    if not result.is_finite():
        raise InvalidOperation
    return result


def seal(plan, run_dir):
    validate_plan(plan)
    public = run_dir / "public"
    private = run_dir / "private"
    manifest = json.loads((public / "audit_manifest.json").read_text(encoding="utf-8"))
    if manifest["status"] != "structure_audit_passed" or manifest["label_table_opened"]:
        raise ValueError("label sealing requires an unsealed passed structure audit")
    if digest(run_dir / "plan.json") != manifest["plan_sha256"]:
        raise ValueError("run plan changed after structure audit")
    source_manifest = json.loads((public / "source_manifest.json").read_text(encoding="utf-8"))
    directory = Path(plan["data_directory"]).resolve(strict=True)
    paths = {name: (directory / name).resolve(strict=True) for name in plan["tables"]}
    for name, path in paths.items():
        if digest(path) != source_manifest[name]["sha256"]:
            raise ValueError(f"source changed before label sealing: {name}")
    headers = {name: plan["tables"][name]["headers"] for name in plan["tables"]}

    desc = {}
    for row in rows(paths["campaign_desc.csv"], headers["campaign_desc.csv"]):
        desc[row["CAMPAIGN"]] = (row["DESCRIPTION"], parse_day(row["START_DAY"]), parse_day(row["END_DAY"]))
    participants = defaultdict(set)
    all_households = set()
    for row in rows(paths["campaign_table.csv"], headers["campaign_table.csv"]):
        participants[row["CAMPAIGN"]].add(row["household_key"])
        all_households.add(row["household_key"])
    coupons = defaultdict(set)
    coupon_campaigns = defaultdict(set)
    target_products = defaultdict(set)
    for row in rows(paths["coupon.csv"], headers["coupon.csv"]):
        coupons[row["CAMPAIGN"]].add(row["COUPON_UPC"])
        coupon_campaigns[row["COUPON_UPC"]].add(row["CAMPAIGN"])
        target_products[row["CAMPAIGN"]].add(row["PRODUCT_ID"])

    cohort = [row for row in load_cohort(private / "cohort_permissions.csv") if row["eligible"] == "1"]
    eligible_pairs = {(row["campaign"], row["household_key"]) for row in cohort}
    positive_y1 = set()
    anomaly = Counter()
    seen_rows = set()
    redemption_path = paths["coupon_redempt.csv"]
    for row in rows(redemption_path, headers["coupon_redempt.csv"]):
        key = (row["household_key"], row["DAY"], row["COUPON_UPC"], row["CAMPAIGN"])
        if key in seen_rows:
            anomaly["duplicate_exact_redemption_row"] += 1
            continue
        seen_rows.add(key)
        household, campaign, coupon = row["household_key"], row["CAMPAIGN"], row["COUPON_UPC"]
        try:
            day = parse_day(row["DAY"])
        except (ValueError, TypeError):
            anomaly["invalid_redemption_day"] += 1
            continue
        if campaign not in desc:
            anomaly["unknown_campaign"] += 1
            continue
        if not household or household not in all_households:
            anomaly["unknown_household"] += 1
            continue
        if household not in participants[campaign]:
            anomaly["household_not_participating_in_declared_campaign"] += 1
            continue
        if coupon not in coupons[campaign]:
            if coupon in coupon_campaigns:
                anomaly["coupon_belongs_to_other_campaign"] += 1
            else:
                anomaly["unknown_coupon"] += 1
            continue
        start, end = desc[campaign][1], desc[campaign][2]
        if not start <= day <= end:
            anomaly["redemption_outside_campaign_dates"] += 1
            continue
        if (campaign, household) in eligible_pairs:
            positive_y1.add((campaign, household))

    positive_y2 = set()
    eligible_by_household = defaultdict(list)
    for campaign, household in eligible_pairs:
        eligible_by_household[household].append(campaign)
    for row in rows(paths["transaction_data.csv"], headers["transaction_data.csv"]):
        household = row["household_key"]
        relevant = eligible_by_household.get(household, ())
        if not relevant:
            continue
        try:
            day = parse_day(row["DAY"])
            quantity = parse_decimal(row["QUANTITY"])
            sales = parse_decimal(row["SALES_VALUE"])
        except (ValueError, InvalidOperation, TypeError):
            continue
        if quantity <= 0 or sales < 0:
            continue
        product = row["PRODUCT_ID"]
        for campaign in relevant:
            start, end = desc[campaign][1], desc[campaign][2]
            if start <= day <= end and product in target_products[campaign]:
                positive_y2.add((campaign, household))

    labels = {"D": [], "R": [], "T": []}
    for row in cohort:
        key = (row["campaign"], row["household_key"])
        labels[row["split"]].append({
            "household_key": row["household_key"],
            "campaign": row["campaign"],
            "e2_role": row["e2_role"],
            "y1_redeemed": "1" if key in positive_y1 else "0",
            "y2_target_product_positive_purchase": "1" if key in positive_y2 else "0",
            "y3_target_product_net_sales": "DEFERRED_UNVERIFIED_SEMANTICS",
        })
    fields = ["household_key", "campaign", "e2_role", "y1_redeemed",
              "y2_target_product_positive_purchase", "y3_target_product_net_sales"]
    filenames = {"D": "labels_D_development.csv", "R": "labels_R_validation.csv", "T": "labels_T_SEALED.csv"}
    for split, filename in filenames.items():
        labels[split].sort(key=lambda row: (int(row["campaign"]), len(row["household_key"]), row["household_key"]))
        write_csv(private / filename, fields, labels[split])
        os.chmod(private / filename, 0o600)
    private_summary = {
        "warning": "PRIVATE LABEL SUMMARY. Do not inspect before the protocol permits the corresponding split.",
        "rows": {split: len(data) for split, data in labels.items()},
        "y1_positive": {split: sum(row["y1_redeemed"] == "1" for row in data) for split, data in labels.items()},
        "y2_positive": {split: sum(row["y2_target_product_positive_purchase"] == "1" for row in data) for split, data in labels.items()},
    }
    write_json(private / "label_summary_PRIVATE.json", private_summary)
    os.chmod(private / "label_summary_PRIVATE.json", 0o600)

    for name in ("coupon_redempt.csv", "transaction_data.csv"):
        if digest(paths[name]) != source_manifest[name]["sha256"]:
            raise ValueError(f"source changed during label sealing: {name}")

    historical_summary = {}
    for split in ("D", "R"):
        count = private_summary["rows"][split]
        y1_positive = private_summary["y1_positive"][split]
        y2_positive = private_summary["y2_positive"][split]
        historical_summary[split] = {
            "rows": count,
            "y1_positive": y1_positive,
            "y1_rate": round(y1_positive / count, 12) if count else None,
            "y2_positive": y2_positive,
            "y2_rate": round(y2_positive / count, 12) if count else None,
        }

    safe = {
        "status": "labels_constructed_and_T_logically_sealed",
        "sealed_at": datetime.now(timezone.utc).isoformat(),
        "label_schema": {
            "Y1": plan["label_policy"]["y1"],
            "Y2": plan["label_policy"]["y2"],
            "Y3": plan["label_policy"]["y3"],
        },
        "eligible_rows_by_split": {split: len(data) for split, data in labels.items()},
        "historical_label_summary": historical_summary,
        "redemption_rows_scanned": source_manifest["coupon_redempt.csv"]["rows_excluding_header"],
        "predeclared_invalid_redemption_counts": dict(sorted(anomaly.items())),
        "sealed_files": {filename: digest(private / filename) for filename in filenames.values()},
        "private_summary_sha256": digest(private / "label_summary_PRIVATE.json"),
        "test_file_mode": oct((private / filenames["T"]).stat().st_mode & 0o777),
        "test_label_values_exposed": False,
        "test_positive_count_exposed": False,
        "test_response_rate_exposed": False,
        "model_calls": 0,
    }
    write_json(public / "label_seal_manifest.json", safe)
    manifest.update({
        "status": "e0b_run_complete",
        "label_table_opened": True,
        "label_access_process": "seal_labels.py only",
        "test_label_summaries_exposed": False,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    })
    write_json(public / "audit_manifest.json", manifest)
    write_checksums(run_dir)
    print(canonical_json({
        "status": safe["status"],
        "eligible_rows_by_split": safe["eligible_rows_by_split"],
        "invalid_redemption_categories": len(safe["predeclared_invalid_redemption_counts"]),
        "test_label_values_exposed": False,
        "model_calls": 0,
    }), end="")
    return safe


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_directory", type=Path)
    args = parser.parse_args()
    plan = json.loads((args.run_directory / "plan.json").read_text(encoding="utf-8"))
    seal(plan, args.run_directory.resolve(strict=True))


if __name__ == "__main__":
    main()
