"""Deterministic, label-blind E0b structure and sample audit."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import subprocess
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
DEFAULT_PLAN = HERE / "e0b-plan.json"


def canonical_json(data):
    return json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def write_json(path, data):
    path.write_text(canonical_json(data), encoding="utf-8")


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def inventory_file(path):
    hasher = hashlib.sha256()
    newline_count = 0
    size = 0
    last = b""
    with path.open("rb") as stream:
        while True:
            block = stream.read(8 * 1024 * 1024)
            if not block:
                break
            hasher.update(block)
            newline_count += block.count(b"\n")
            size += len(block)
            last = block[-1:]
    if not size:
        raise ValueError(f"empty source file: {path.name}")
    physical_lines = newline_count if last == b"\n" else newline_count + 1
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        headers = next(csv.reader(stream))
    return {
        "sha256": hasher.hexdigest(),
        "size_bytes": size,
        "rows_excluding_header": physical_lines - 1,
        "headers": headers,
    }


def validate_plan(plan):
    required_splits = {
        "D": ["27", "28", "29", "1", "2", "3", "4", "5", "6", "7"],
        "R": ["9", "10", "11", "12"],
        "T": ["14", "15", "16", "17", "19", "20", "21", "22", "23", "24", "25"],
    }
    if plan.get("schema_version") != "1.0" or plan.get("status") != "confirmed":
        raise ValueError("unconfirmed E0b plan")
    if plan.get("execution_approved") is not True or not plan.get("approval_reference"):
        raise ValueError("E0b execution approval is not recorded")
    if plan.get("history_window_days") != 84 or plan.get("splits") != required_splits:
        raise ValueError("E0b time split differs from the reviewed protocol")
    e2 = plan.get("e2", {})
    if (e2.get("reserved"), e2.get("seed"), e2.get("adaptation_fraction")) != (True, 20261009, 0.2):
        raise ValueError("E2 reservation differs from the approved 20/80 global split")
    if plan.get("target_campaign_types") != ["TypeB", "TypeC"]:
        raise ValueError("only Type B/C target campaigns are allowed")
    evaluation = plan.get("evaluation_policy", {})
    if (evaluation.get("test_campaign_count"),
            evaluation.get("minimum_final_evaluation_households_warning"),
            evaluation.get("low_support_action")) != (11, 20, "flag_and_retain"):
        raise ValueError("activity support policy differs from the reviewed protocol")
    expected_tables = {
        "campaign_desc.csv", "campaign_table.csv", "coupon.csv", "product.csv",
        "transaction_data.csv", "coupon_redempt.csv", "hh_demographic.csv", "causal_data.csv",
    }
    if set(plan.get("tables", {})) != expected_tables:
        raise ValueError("raw table registry must contain exactly eight approved files")
    for name, spec in plan["tables"].items():
        if not isinstance(spec.get("primary_key"), list) or not spec.get("time_role"):
            raise ValueError(f"raw table registry lacks key/time role: {name}")
    if plan.get("label_policy", {}).get("test_mode") != "local_logical_seal":
        raise ValueError("T label policy differs from user-approved local logical sealing")
    source = plan.get("dataset_source", {})
    if source.get("name") != "dunnhumby The Complete Journey" or not source.get("local_archive"):
        raise ValueError("dataset source/archive is not fixed")


def git_output(*args):
    return subprocess.check_output(["git", *args], cwd=REPO, text=True).strip()


def repository_gate(plan):
    if git_output("status", "--porcelain", "--untracked-files=all"):
        raise ValueError("E0b real audit requires a clean committed worktree")
    branch = git_output("branch", "--show-current")
    if branch != plan["branch"]:
        raise ValueError("unexpected experiment branch")
    head = git_output("rev-parse", "HEAD")
    remote = git_output("rev-parse", f"origin/{branch}")
    if head != remote:
        raise ValueError("E0b code must be pushed before real audit")
    official = git_output("rev-parse", "thesis-upstream-3633d8d^{commit}")
    if official != plan["official_commit"]:
        raise ValueError("official baseline tag moved")
    return {"branch": branch, "code_commit": head, "official_commit": official}


def source_path(directory, name, allowed):
    if name not in allowed:
        raise ValueError("source table is not approved")
    path = (directory / name).resolve(strict=True)
    if path.parent != directory.resolve() or path.name != name or path.is_symlink():
        raise ValueError("source must be a regular named file in the approved directory")
    return path


def rows(path, expected_headers):
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames != expected_headers:
            raise ValueError(f"header mismatch in {path.name}")
        yield from reader


def parse_int(value, context):
    try:
        if not value or any(ch in value for ch in ".eE"):
            raise ValueError
        return int(value)
    except ValueError as exc:
        raise ValueError(f"invalid integer in {context}") from exc


def parse_decimal(value, context):
    try:
        result = Decimal(value)
    except (InvalidOperation, TypeError) as exc:
        raise ValueError(f"invalid decimal in {context}") from exc
    if not result.is_finite():
        raise ValueError(f"nonfinite decimal in {context}")
    return result


def connected_components(campaigns):
    ordered = sorted(campaigns, key=lambda item: (item["start_day"], item["end_day"], item["campaign"]))
    components = []
    for item in ordered:
        if not components or item["start_day"] > components[-1]["end_day"] + 1:
            components.append({"start_day": item["start_day"], "end_day": item["end_day"], "campaigns": [item["campaign"]]})
        else:
            components[-1]["end_day"] = max(components[-1]["end_day"], item["end_day"])
            components[-1]["campaigns"].append(item["campaign"])
    return components


def write_csv(path, fieldnames, records):
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(records)


def write_checksums(run_dir):
    entries = {}
    for path in sorted(run_dir.rglob("*")):
        if path.is_file() and path.name != "checksums.json":
            entries[str(path.relative_to(run_dir))] = digest(path)
    write_json(run_dir / "checksums.json", entries)
    return entries


def run_audit(plan, run_dir, require_repository=True):
    validate_plan(plan)
    run_dir.mkdir(parents=True, exist_ok=False)
    public = run_dir / "public"
    private = run_dir / "private"
    public.mkdir()
    private.mkdir()
    os.chmod(private, 0o700)
    repo = repository_gate(plan) if require_repository else {
        "branch": plan["branch"], "code_commit": "test-fixture", "official_commit": plan["official_commit"]
    }
    write_json(run_dir / "plan.json", plan)

    directory = Path(plan["data_directory"]).resolve(strict=True)
    archive = Path(plan["dataset_source"]["local_archive"]).resolve(strict=True)
    if archive.is_symlink() or not archive.is_file():
        raise ValueError("dataset source archive must be a regular file")
    write_json(public / "dataset_source.json", {
        **plan["dataset_source"],
        "local_archive": str(archive),
        "archive_size_bytes": archive.stat().st_size,
        "archive_sha256": digest(archive),
    })
    allowed = set(plan["tables"])
    paths = {name: source_path(directory, name, allowed) for name in sorted(allowed)}
    source_manifest = {}
    for name, path in paths.items():
        observed = inventory_file(path)
        spec = plan["tables"][name]
        if observed["headers"] != spec["headers"]:
            raise ValueError(f"header mismatch in {name}")
        source_manifest[name] = {
            **observed,
            "logical_types": spec["types"],
            "primary_key": spec["primary_key"],
            "time_role": spec["time_role"],
            "local_path": str(path),
            "role": spec["role"],
            "main_input": spec["main_input"],
            "semantic_access": spec["semantic_access"],
            "identifier_policy": "kept as text; no float/scientific conversion",
            "type_evidence": "protocol-declared logical type; fields used by the audit are strictly parsed",
        }
    write_json(public / "source_manifest.json", source_manifest)

    headers = {name: plan["tables"][name]["headers"] for name in allowed}
    desc = {}
    duplicate_desc = 0
    for row in rows(paths["campaign_desc.csv"], headers["campaign_desc.csv"]):
        campaign = row["CAMPAIGN"]
        item = {
            "campaign": campaign,
            "campaign_type": row["DESCRIPTION"],
            "start_day": parse_int(row["START_DAY"], "campaign_desc START_DAY"),
            "end_day": parse_int(row["END_DAY"], "campaign_desc END_DAY"),
        }
        if campaign in desc:
            duplicate_desc += 1
        else:
            desc[campaign] = item

    split_for = {campaign: split for split, campaigns in plan["splits"].items() for campaign in campaigns}
    targets = set(split_for)
    missing_targets = sorted(targets - set(desc))
    if missing_targets:
        raise ValueError(f"missing target campaign metadata: {missing_targets}")
    for campaign in targets:
        item = desc[campaign]
        if item["campaign_type"] not in plan["target_campaign_types"] or item["start_day"] > item["end_day"]:
            raise ValueError(f"invalid target campaign metadata: {campaign}")

    minimum_r_start = min(desc[c]["start_day"] for c in plan["splits"]["R"])
    minimum_t_start = min(desc[c]["start_day"] for c in plan["splits"]["T"])
    maximum_d_end = max(desc[c]["end_day"] for c in plan["splits"]["D"])
    maximum_r_end = max(desc[c]["end_day"] for c in plan["splits"]["R"])
    time_checks = {
        "D_complete_before_R_starts": maximum_d_end < minimum_r_start,
        "D_and_R_complete_before_T_starts": max(maximum_d_end, maximum_r_end) < minimum_t_start,
        "T_starts_at_frozen_day": minimum_t_start == plan["test_freeze_start_day"],
    }
    if not all(time_checks.values()):
        raise ValueError("D/R/T temporal permission checks failed")
    unassigned_target_type = sorted(
        (c for c, item in desc.items() if item["campaign_type"] in plan["target_campaign_types"] and c not in targets),
        key=lambda value: (desc[value]["start_day"], int(value)),
    )
    permission_rows = []
    permissions = {
        "D": "development labels may be used for field, prompt, model, and anomaly-rule development",
        "R": "validation labels may be used only for frozen-candidate selection, calibration, and stability checks",
        "T": "labels and response summaries remain sealed until all E1a/E1b predictions and policies are frozen",
    }
    for split in ("D", "R", "T"):
        for campaign in plan["splits"][split]:
            permission_rows.append({
                "campaign": campaign,
                "split": split,
                "start_day": desc[campaign]["start_day"],
                "end_day": desc[campaign]["end_day"],
                "label_permission": permissions[split],
            })
    write_json(public / "time_and_label_permissions.json", {
        "checks": time_checks,
        "maximum_D_end_day": maximum_d_end,
        "minimum_R_start_day": minimum_r_start,
        "maximum_R_end_day": maximum_r_end,
        "minimum_T_start_day": minimum_t_start,
        "unassigned_TypeB_or_TypeC_campaigns": unassigned_target_type,
        "whole_T_segment_no_rolling_updates": True,
        "campaigns": permission_rows,
    })

    participants = defaultdict(set)
    participation_records = Counter()
    participation_duplicates = Counter()
    participation_type_mismatch = Counter()
    all_participating_households = set()
    for row in rows(paths["campaign_table.csv"], headers["campaign_table.csv"]):
        campaign, household = row["CAMPAIGN"], row["household_key"]
        if not campaign or not household:
            raise ValueError("empty campaign participation identifier")
        all_participating_households.add(household)
        participation_records[campaign] += 1
        if household in participants[campaign]:
            participation_duplicates[campaign] += 1
        participants[campaign].add(household)
        if campaign not in desc or row["DESCRIPTION"] != desc[campaign]["campaign_type"]:
            participation_type_mismatch[campaign] += 1

    products = {}
    product_conflicts = 0
    duplicate_product_rows = 0
    empty_product_ids = 0
    for row in rows(paths["product.csv"], headers["product.csv"]):
        product = row["PRODUCT_ID"]
        signature = tuple(row[name] for name in headers["product.csv"] if name != "PRODUCT_ID")
        if not product:
            empty_product_ids += 1
        elif product in products:
            if products[product] != signature:
                product_conflicts += 1
            else:
                duplicate_product_rows += 1
        else:
            products[product] = signature

    campaign_coupon_pairs = defaultdict(set)
    campaign_coupons = defaultdict(set)
    campaign_products = defaultdict(set)
    coupon_empty = Counter()
    coupon_product_missing = Counter()
    coupon_duplicates = Counter()
    for row in rows(paths["coupon.csv"], headers["coupon.csv"]):
        campaign, coupon, product = row["CAMPAIGN"], row["COUPON_UPC"], row["PRODUCT_ID"]
        if not campaign or not coupon or not product:
            coupon_empty[campaign] += 1
            continue
        if (coupon, product) in campaign_coupon_pairs[campaign]:
            coupon_duplicates[campaign] += 1
        campaign_coupon_pairs[campaign].add((coupon, product))
        campaign_coupons[campaign].add(coupon)
        campaign_products[campaign].add(product)
        if product not in products:
            coupon_product_missing[campaign] += 1

    household_targets = defaultdict(list)
    for campaign in targets:
        for household in participants[campaign]:
            household_targets[household].append(campaign)

    active_history = defaultdict(set)
    invalid_history_units = defaultdict(set)
    transaction_min_day = None
    transaction_max_day = None
    transaction_anomalies = Counter()
    for row in rows(paths["transaction_data.csv"], headers["transaction_data.csv"]):
        try:
            day = parse_int(row["DAY"], "transaction DAY")
        except ValueError:
            transaction_anomalies["invalid_day"] += 1
            continue
        transaction_min_day = day if transaction_min_day is None else min(transaction_min_day, day)
        transaction_max_day = day if transaction_max_day is None else max(transaction_max_day, day)
        household = row["household_key"]
        if not household:
            transaction_anomalies["empty_household"] += 1
            continue
        relevant = household_targets.get(household, ())
        if not relevant:
            continue
        for campaign in relevant:
            item = desc[campaign]
            if not item["start_day"] - plan["history_window_days"] <= day < item["start_day"]:
                continue
            unit = (campaign, household)
            try:
                quantity = parse_decimal(row["QUANTITY"], "transaction QUANTITY")
                sales = parse_decimal(row["SALES_VALUE"], "transaction SALES_VALUE")
            except ValueError:
                invalid_history_units[unit].add("nonfinite_or_invalid_purchase")
                continue
            if quantity <= 0:
                continue
            if sales < 0:
                invalid_history_units[unit].add("negative_sales_on_positive_quantity")
                continue
            if not row["BASKET_ID"]:
                invalid_history_units[unit].add("empty_basket")
                continue
            if row["PRODUCT_ID"] not in products:
                invalid_history_units[unit].add("unknown_product")
                continue
            active_history[campaign].add(household)
    if transaction_min_day is None or transaction_max_day is None:
        raise ValueError("transaction table has no valid DAY values")

    activities = []
    activity_state = {}
    for campaign in sorted(targets, key=lambda value: (desc[value]["start_day"], int(value))):
        item = desc[campaign]
        join_valid = bool(participants[campaign] and campaign_coupon_pairs[campaign])
        join_valid = join_valid and not participation_type_mismatch[campaign]
        join_valid = join_valid and not coupon_empty[campaign] and not coupon_product_missing[campaign]
        join_valid = join_valid and not product_conflicts and not empty_product_ids
        history_complete = transaction_min_day <= item["start_day"] - plan["history_window_days"]
        outcome_complete = transaction_max_day >= item["end_day"]
        valid_active = active_history[campaign]
        feasible = join_valid and history_complete and outcome_complete and bool(valid_active)
        state = {
            "join_valid": join_valid,
            "history_source_complete": history_complete,
            "outcome_source_complete": outcome_complete,
            "feasible": feasible,
        }
        activity_state[campaign] = state
        activities.append({
            **item,
            "split": split_for[campaign],
            "valid_days_inclusive": item["end_day"] - item["start_day"] + 1,
            "participation_records": participation_records[campaign],
            "unique_participants": len(participants[campaign]),
            "duplicate_participations": participation_duplicates[campaign],
            "duplicate_coupon_product_rows": coupon_duplicates[campaign],
            "participation_type_mismatches": participation_type_mismatch[campaign],
            "unique_coupons": len(campaign_coupons[campaign]),
            "unique_target_products": len(campaign_products[campaign]),
            "empty_coupon_identifiers": coupon_empty[campaign],
            "missing_product_joins": coupon_product_missing[campaign],
            "households_with_invalid_history_rows": sum(c == campaign for c, _ in invalid_history_units),
            "history_lower_day": item["start_day"] - plan["history_window_days"],
            "history_source_complete": history_complete,
            "outcome_source_complete": outcome_complete,
            "eligible_positive_history_households_before_outcome_check": len(valid_active),
            "feasible": feasible,
            "exclusion_reasons": [
                reason for reason, failed in [
                    ("invalid_structure_or_join", not join_valid),
                    ("history_source_incomplete", not history_complete),
                    ("outcome_source_incomplete", not outcome_complete),
                    ("no_valid_positive_history_household", not valid_active),
                ] if failed
            ],
        })
    cohort_rows = []
    for campaign in targets:
        state = activity_state[campaign]
        for household in sorted(participants[campaign], key=lambda value: (len(value), value)):
            reasons = []
            if not state["join_valid"]:
                reasons.append("invalid_structure_or_join")
            if not state["history_source_complete"]:
                reasons.append("history_source_incomplete")
            if household not in active_history[campaign]:
                reasons.append("no_legal_positive_purchase_in_history")
            pre_outcome_eligible = not reasons
            if not state["outcome_source_complete"]:
                reasons.append("outcome_source_incomplete")
            cohort_rows.append({
                "household_key": household,
                "campaign": campaign,
                "split": split_for[campaign],
                "eligible": "1" if not reasons else "0",
                "pre_outcome_eligible": "1" if pre_outcome_eligible else "0",
                "exclusion_reasons": ";".join(sorted(set(reasons))),
            })

    history_anomaly_rows = [
        {
            "household_key": household,
            "campaign": campaign,
            "anomaly_categories": ";".join(sorted(categories)),
        }
        for (campaign, household), categories in sorted(
            invalid_history_units.items(), key=lambda item: (int(item[0][0]), len(item[0][1]), item[0][1])
        )
    ]
    write_csv(private / "history_anomalies.csv",
              ["household_key", "campaign", "anomaly_categories"], history_anomaly_rows)
    os.chmod(private / "history_anomalies.csv", 0o600)

    eligible_t_households = sorted({
        row["household_key"] for row in cohort_rows if row["split"] == "T" and row["eligible"] == "1"
    }, key=lambda household: (
        hashlib.sha256(f"{plan['e2']['seed']}:{household}".encode()).hexdigest(), household
    ))
    adaptation_count = math.floor(plan["e2"]["adaptation_fraction"] * len(eligible_t_households))
    adaptation = set(eligible_t_households[:adaptation_count])
    for row in cohort_rows:
        if row["split"] != "T" or row["eligible"] != "1":
            row["e2_role"] = "not_applicable"
        else:
            row["e2_role"] = "adaptation" if row["household_key"] in adaptation else "final_evaluation"

    support_threshold = plan["evaluation_policy"]["minimum_final_evaluation_households_warning"]
    for item in activities:
        if item["split"] != "T":
            item["e2_support"] = "not_applicable"
            continue
        campaign_rows = [row for row in cohort_rows if row["campaign"] == item["campaign"]]
        adaptation_households = sum(row["e2_role"] == "adaptation" for row in campaign_rows)
        final_households = sum(row["e2_role"] == "final_evaluation" for row in campaign_rows)
        item["e2_support"] = {
            "adaptation_households": adaptation_households,
            "final_evaluation_households": final_households,
            "low_support_threshold": support_threshold,
            "low_support": final_households < support_threshold,
            "action": plan["evaluation_policy"]["low_support_action"],
        }
    write_json(public / "activity_feasibility.json", {
        "transaction_day_range": [transaction_min_day, transaction_max_day],
        "duplicate_campaign_descriptions": duplicate_desc,
        "duplicate_product_rows": duplicate_product_rows,
        "product_conflicts": product_conflicts,
        "empty_product_ids": empty_product_ids,
        "transaction_structure_anomalies": dict(transaction_anomalies),
        "history_anomaly_household_campaigns": len(history_anomaly_rows),
        "evaluation_policy": plan["evaluation_policy"],
        "activities": activities,
    })
    write_csv(private / "cohort_permissions.csv",
              ["household_key", "campaign", "split", "eligible", "pre_outcome_eligible", "exclusion_reasons", "e2_role"], cohort_rows)
    os.chmod(private / "cohort_permissions.csv", 0o600)

    eligible = [row for row in cohort_rows if row["eligible"] == "1"]
    stage_counts = {
        "all_raw_participation_records": sum(participation_records.values()),
        "candidate_raw_participation_records": sum(participation_records[c] for c in targets),
        "candidate_deduplicated_household_campaigns": sum(len(participants[c]) for c in targets),
        "candidate_type_b_c_household_campaigns": sum(len(participants[c]) for c in targets if desc[c]["campaign_type"] in plan["target_campaign_types"]),
        "structure_and_join_valid": sum(len(participants[c]) for c in targets if activity_state[c]["join_valid"]),
        "history_source_covered": sum(len(participants[c]) for c in targets if activity_state[c]["join_valid"] and activity_state[c]["history_source_complete"]),
        "legal_positive_history": sum(row["pre_outcome_eligible"] == "1" for row in cohort_rows),
        "outcome_source_covered_and_final_eligible": len(eligible),
    }
    by_split = {}
    for split in ("D", "R", "T"):
        split_rows = [row for row in eligible if row["split"] == split]
        by_split[split] = {
            "household_campaigns": len(split_rows),
            "unique_households": len({row["household_key"] for row in split_rows}),
            "campaigns": len({row["campaign"] for row in split_rows}),
        }
    e2_public = {
        "seed": plan["e2"]["seed"],
        "assignment": plan["e2"]["assignment"],
        "eligible_unique_test_households": len(eligible_t_households),
        "adaptation_households": adaptation_count,
        "final_evaluation_households": len(eligible_t_households) - adaptation_count,
        "adaptation_household_campaigns": sum(row["e2_role"] == "adaptation" for row in eligible),
        "final_evaluation_household_campaigns": sum(row["e2_role"] == "final_evaluation" for row in eligible),
        "labels_used_for_assignment": False,
    }
    write_json(public / "sample_flow.json", {"stages": stage_counts, "eligible_by_split": by_split, "e2": e2_public})

    dependency = {"components_by_split": {}, "eligible_household_repetition": {}}
    for split in ("D", "R", "T"):
        split_activities = [item for item in activities if item["split"] == split]
        dependency["components_by_split"][split] = connected_components(split_activities)
        occurrences = Counter(row["household_key"] for row in eligible if row["split"] == split)
        dependency["eligible_household_repetition"][split] = {
            "unique_households": len(occurrences),
            "households_in_multiple_campaigns": sum(value > 1 for value in occurrences.values()),
            "maximum_campaigns_per_household": max(occurrences.values(), default=0),
        }
    t_campaign_households = {
        c: {row["household_key"] for row in eligible if row["split"] == "T" and row["campaign"] == c}
        for c in plan["splits"]["T"]
    }
    shared_pairs = 0
    pairs_with_overlap = 0
    t_ids = plan["splits"]["T"]
    for i, left in enumerate(t_ids):
        for right in t_ids[i + 1:]:
            shared = len(t_campaign_households[left] & t_campaign_households[right])
            shared_pairs += shared
            pairs_with_overlap += shared > 0
    dependency["test_campaign_pair_overlap"] = {
        "campaign_pairs": len(t_ids) * (len(t_ids) - 1) // 2,
        "pairs_with_shared_eligible_households": pairs_with_overlap,
        "sum_shared_households_over_pairs": shared_pairs,
    }
    dependency["inference_scope"] = "exploratory future-period response prediction; T activities are not independent replicates"
    write_json(public / "activity_dependency.json", dependency)

    current = {name: digest(path) for name, path in paths.items()}
    expected = {name: item["sha256"] for name, item in source_manifest.items()}
    if current != expected:
        raise ValueError("a raw source changed during E0b structural audit")
    manifest = {
        "status": "structure_audit_passed",
        "started_at": datetime.now(timezone.utc).isoformat(),
        **repo,
        "plan_sha256": digest(run_dir / "plan.json"),
        "source_files": len(source_manifest),
        "source_rows": sum(item["rows_excluding_header"] for item in source_manifest.values()),
        "semantic_tables_opened": [
            "campaign_desc.csv", "campaign_table.csv", "coupon.csv", "product.csv", "transaction_data.csv"
        ],
        "label_table_opened": False,
        "demographic_table_opened": False,
        "causal_table_opened": False,
        "test_label_summaries_exposed": False,
        "model_calls": 0,
    }
    write_json(public / "audit_manifest.json", manifest)
    write_checksums(run_dir)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, default=DEFAULT_PLAN)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    validate_plan(plan)
    if not args.execute:
        print(canonical_json({
            "status": plan["status"], "splits": plan["splits"], "e2": plan["e2"],
            "label_policy": plan["label_policy"], "source_data_read": False, "model_calls": 0,
        }), end="")
        return
    run_dir = args.output or HERE / "runs" / f"e0b-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')}"
    result = run_audit(plan, run_dir)
    print(canonical_json({"status": result["status"], "run_directory": str(run_dir), "model_calls": 0}), end="")


if __name__ == "__main__":
    main()
