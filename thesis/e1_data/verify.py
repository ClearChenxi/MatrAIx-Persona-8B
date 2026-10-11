"""Verify prepared D/R/T inputs without reading any T label file."""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from build import BASE_FEATURE_FIELDS, canonical_json, digest, validate_plan


def csv_rows(path):
    with path.open("r", encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        return reader.fieldnames, list(reader)


def verify(run_dir):
    plan = json.loads((run_dir / "plan.json").read_text(encoding="utf-8"))
    validate_plan(plan)
    checksums = json.loads((run_dir / "checksums.json").read_text(encoding="utf-8"))
    actual = {}
    for path in sorted(run_dir.rglob("*")):
        if path.is_file() and path.name != "checksums.json":
            actual[str(path.relative_to(run_dir))] = digest(path)
    if actual != checksums:
        raise ValueError("prepared data inventory or checksum mismatch")
    forbidden_names = {"labels_T.csv", "labels_T_SEALED.csv", "label_summary_PRIVATE.json"}
    if forbidden_names & {Path(name).name for name in actual}:
        raise ValueError("prepared dataset contains a forbidden T label artifact")
    manifest = json.loads((run_dir / "public/manifest.json").read_text(encoding="utf-8"))
    dictionary = json.loads((run_dir / "public/field_dictionary.json").read_text(encoding="utf-8"))
    if manifest["status"] != "e1_data_prepared" or manifest["test_labels_opened"] or manifest["test_labels_written"]:
        raise ValueError("prepared data manifest is not safely complete")
    if manifest["excluded_sources_opened"] or manifest["labels_written"] != ["D", "R"]:
        raise ValueError("source or label permissions were violated")
    if set(dictionary["forbidden_model_fields"]) & set(BASE_FEATURE_FIELDS):
        raise ValueError("forbidden field entered the model feature table")

    rows = {}
    links = {}
    for split in ("D", "R", "T"):
        fields, rows[split] = csv_rows(run_dir / f"private/features_{split}.csv")
        if fields != BASE_FEATURE_FIELDS:
            raise ValueError(f"feature schema mismatch for {split}")
        link_fields, links[split] = csv_rows(run_dir / f"private/links_{split}.csv")
        if not {"row_id", "household_key", "campaign", "split", "e2_role"} <= set(link_fields):
            raise ValueError(f"link schema mismatch for {split}")
        feature_ids = [row["row_id"] for row in rows[split]]
        link_ids = [row["row_id"] for row in links[split]]
        if feature_ids != sorted(feature_ids) or feature_ids != link_ids or len(feature_ids) != len(set(feature_ids)):
            raise ValueError(f"row identity mismatch for {split}")
        if len(rows[split]) != manifest["rows"][split]:
            raise ValueError(f"row count mismatch for {split}")
        with (run_dir / f"private/model_inputs_{split}.jsonl").open("r", encoding="utf-8") as stream:
            inputs = [line for line in stream if line.strip()]
        if len(inputs) != len(rows[split]):
            raise ValueError(f"model input count mismatch for {split}")
        if any('"household_key"' in line or '"campaign_id"' in line for line in inputs):
            raise ValueError(f"identifier exposed in {split} model input")
        for row in rows[split]:
            for field in ("retail_discount_basket_share", "coupon_discount_basket_share",
                          "target_product_sales_share", "target_commodity_sales_share"):
                if row[field] == "":
                    if field not in {"target_product_sales_share", "target_commodity_sales_share"} or row["zero_sales_total_flag"] != "1":
                        raise ValueError(f"unexpected missing share in {split}: {field}")
                    continue
                value = float(row[field])
                if not 0 <= value <= 1:
                    raise ValueError(f"share outside [0,1] in {split}: {field}")
    for split in ("D", "R"):
        _, label_rows = csv_rows(run_dir / f"private/labels_{split}.csv")
        if [row["row_id"] for row in label_rows] != [row["row_id"] for row in rows[split]]:
            raise ValueError(f"label-feature identity mismatch for {split}")
    if any(row["campaign"] == "24" for row in links["T"]):
        raise ValueError("outcome-incomplete campaign 24 entered T features")
    role_counts = {}
    for role in ("adaptation", "final_evaluation"):
        role_counts[role] = sum(row["e2_role"] == role for row in links["T"])
    if role_counts != manifest["test_permissions"]:
        raise ValueError("T role counts differ from manifest")
    if any((run_dir / "private" / name).stat().st_mode & 0o077 for name in os.listdir(run_dir / "private")):
        raise ValueError("private prepared data file is group/world accessible")
    return {
        "status": "verified",
        "rows": manifest["rows"],
        "features": manifest["unique_features"],
        "test_permissions": role_counts,
        "test_labels_opened": False,
        "model_calls": 0,
        "verified_files": len(actual),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_directory", type=Path)
    args = parser.parse_args()
    print(canonical_json(verify(args.run_directory.resolve(strict=True))), end="")


if __name__ == "__main__":
    main()
