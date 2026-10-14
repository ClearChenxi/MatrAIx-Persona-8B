"""Independently audit the frozen 20-row D persona sample against raw inputs."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from build import (
    RAW_HEADERS,
    canonical_json,
    decimal_text,
    parse_decimal,
    parse_int,
    persona_input,
    ratio_text,
    table,
)
from verify import verify


def records(path):
    with path.open("r", encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def audit(run_dir):
    verification = verify(run_dir)
    plan = json.loads((run_dir / "plan.json").read_text(encoding="utf-8"))
    e0b_run = Path(plan["e0b_run"]).resolve(strict=True)
    e0b_plan = json.loads((e0b_run / "plan.json").read_text(encoding="utf-8"))
    data_dir = Path(e0b_plan["data_directory"]).resolve(strict=True)
    private = run_dir / "private"

    sample = records(private / "persona_audit_sample_D.csv")
    if len(sample) != plan["manual_audit_sample_size"] or any(row["manual_review_status"] != "pending" for row in sample):
        raise ValueError("unexpected persona audit sample state")
    sample_ids = {row["row_id"] for row in sample}
    features = {row["row_id"]: row for row in records(private / "features_D.csv") if row["row_id"] in sample_ids}
    links = {row["row_id"]: row for row in records(private / "links_D.csv") if row["row_id"] in sample_ids}
    if set(features) != sample_ids or set(links) != sample_ids:
        raise ValueError("audit sample does not resolve to D feature/link rows")

    product = {}
    for row in table(data_dir / "product.csv", RAW_HEADERS["product.csv"]):
        product[row["PRODUCT_ID"]] = {
            "department": row["DEPARTMENT"].strip() or plan["unknown_category"],
            "commodity": row["COMMODITY_DESC"].strip() or plan["unknown_category"],
            "brand": row["BRAND"].strip() or plan["unknown_category"],
        }
    selected_campaigns = {row["campaign"] for row in links.values()}
    targets = defaultdict(set)
    for row in table(data_dir / "coupon.csv", RAW_HEADERS["coupon.csv"]):
        if row["CAMPAIGN"] in selected_campaigns:
            targets[row["CAMPAIGN"]].add(row["PRODUCT_ID"])

    state = {}
    by_household = defaultdict(list)
    for row_id, link in links.items():
        item = {
            "row_id": row_id,
            "campaign": link["campaign"],
            "lower": int(link["history_lower_day"]),
            "upper": int(link["history_upper_day"]),
            "days": set(),
            "baskets": {},
            "positive_lines": 0,
            "return_lines": 0,
            "sales": Decimal(0),
            "target_sales": Decimal(0),
            "target_commodity_sales": Decimal(0),
            "target_days": set(),
        }
        state[row_id] = item
        by_household[link["household_key"]].append(item)

    for row in table(data_dir / "transaction_data.csv", RAW_HEADERS["transaction_data.csv"]):
        candidates = by_household.get(row["household_key"], ())
        if not candidates:
            continue
        day = parse_int(row["DAY"], "sample transaction DAY")
        for item in candidates:
            if not item["lower"] <= day <= item["upper"]:
                continue
            quantity = parse_decimal(row["QUANTITY"], "sample transaction QUANTITY")
            if quantity <= 0:
                item["return_lines"] += 1
                continue
            sales = parse_decimal(row["SALES_VALUE"], "sample transaction SALES_VALUE")
            if sales < 0 or not row["BASKET_ID"] or row["PRODUCT_ID"] not in product:
                raise ValueError("invalid positive purchase in audit sample")
            retail = parse_decimal(row["RETAIL_DISC"], "sample RETAIL_DISC") != 0
            coupon = (parse_decimal(row["COUPON_DISC"], "sample COUPON_DISC") != 0
                      or parse_decimal(row["COUPON_MATCH_DISC"], "sample COUPON_MATCH_DISC") != 0)
            basket = item["baskets"].setdefault(row["BASKET_ID"], {"retail": False, "coupon": False})
            basket["retail"] = basket["retail"] or retail
            basket["coupon"] = basket["coupon"] or coupon
            item["days"].add(day)
            item["positive_lines"] += 1
            item["sales"] += sales
            target_products = targets[item["campaign"]]
            target_commodities = {product[key]["commodity"] for key in target_products}
            if row["PRODUCT_ID"] in target_products:
                item["target_sales"] += sales
                item["target_days"].add(day)
            if product[row["PRODUCT_ID"]]["commodity"] in target_commodities:
                item["target_commodity_sales"] += sales

    places = plan["numeric_decimal_places"]
    money_places = plan["money_decimal_places"]
    for row_id, item in state.items():
        feature = features[row_id]
        baskets = item["baskets"]
        expected = {
            "recency_days": str(item["upper"] + 1 - max(item["days"])),
            "basket_count": str(len(baskets)),
            "shopping_day_count": str(len(item["days"])),
            "positive_line_count": str(item["positive_lines"]),
            "return_line_count": str(item["return_lines"]),
            "sales_total": decimal_text(item["sales"], money_places),
            "average_basket_sales": decimal_text(item["sales"] / len(baskets), money_places),
            "retail_discount_basket_share": ratio_text(Decimal(sum(value["retail"] for value in baskets.values())), Decimal(len(baskets)), places),
            "coupon_discount_basket_share": ratio_text(Decimal(sum(value["coupon"] for value in baskets.values())), Decimal(len(baskets)), places),
            "target_product_sales_share": ratio_text(item["target_sales"], item["sales"], places),
            "target_commodity_sales_share": ratio_text(item["target_commodity_sales"], item["sales"], places),
            "target_product_recency_days": str(item["upper"] + 1 - max(item["target_days"])) if item["target_days"] else "",
        }
        mismatches = {name: (feature[name], value) for name, value in expected.items() if feature[name] != value}
        if mismatches:
            raise ValueError(f"independent raw aggregate mismatch for sample row {row_id}: {mismatches}")
        if feature["campaign_unique_product_count"] != str(len(targets[item["campaign"]])):
            raise ValueError("campaign target product count mismatch in audit sample")

    index = records(private / "model_payload_index_D.csv")
    with (private / "model_payloads_D.jsonl").open("r", encoding="utf-8") as stream:
        payloads = [json.loads(line) for line in stream]
    payload_by_id = {row["row_id"]: payloads[int(row["line_number"]) - 1] for row in index if row["row_id"] in sample_ids}
    for row_id in sample_ids:
        if payload_by_id.get(row_id) != persona_input(features[row_id]):
            raise ValueError("persona payload differs from the deterministic feature rendering")
        serialized = json.dumps(payload_by_id[row_id], ensure_ascii=False).lower()
        for forbidden in ("household_key", "campaign_id", "row_id", "income", "personality", "motivation"):
            if forbidden in serialized:
                raise ValueError(f"forbidden inferred/identifier term in persona sample: {forbidden}")

    return {
        "status": "manual_sample_audit_passed",
        "sample_rows": len(sample_ids),
        "raw_aggregate_fields_recomputed_per_row": 12,
        "persona_payloads_matched": len(sample_ids),
        "history_windows_checked": len(sample_ids),
        "test_labels_opened": False,
        "model_calls": 0,
        "prepared_data_verification": verification["status"],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_directory", type=Path)
    args = parser.parse_args()
    print(canonical_json(audit(args.run_directory.resolve(strict=True))), end="")


if __name__ == "__main__":
    main()
