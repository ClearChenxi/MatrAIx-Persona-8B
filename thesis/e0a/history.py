"""Label-free, deterministic E0a inputs; real preparation requires confirmed plan."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter
from contextlib import contextmanager
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

HERE = Path(__file__).resolve().parent
TABLES = ("campaign_desc.csv", "campaign_table.csv", "coupon.csv", "product.csv", "transaction_data.csv")
DEV = {"27", "28", "29", "1", "2", "3", "4", "5", "6", "7"}
HEADERS = {
    "campaign_desc.csv": {"DESCRIPTION", "CAMPAIGN", "START_DAY", "END_DAY"},
    "campaign_table.csv": {"DESCRIPTION", "CAMPAIGN", "household_key"},
    "coupon.csv": {"CAMPAIGN", "COUPON_UPC", "PRODUCT_ID"},
    "product.csv": {"PRODUCT_ID", "COMMODITY_DESC"},
    "transaction_data.csv": {"household_key", "BASKET_ID", "DAY", "PRODUCT_ID", "QUANTITY", "SALES_VALUE"},
}


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def validate_plan(plan, require_approval=False):
    if plan["campaign"] not in DEV or plan["campaign_type"] not in {"TypeB", "TypeC"}:
        raise ValueError("only proposed historical development Type B/C campaigns allowed")
    if not (plan["end_day"] < 435 and plan["start_day"] < plan["end_day"]):
        raise ValueError("campaign must finish before historical validation starts")
    if (plan["campaign"], plan["campaign_type"], plan["start_day"], plan["end_day"]) != ("27", "TypeC", 237, 300):
        raise ValueError("only campaign 27 with reviewed dates/type is implemented")
    if (plan["sampling_seed"], plan["instrument_id"], plan["environment_name"]) != (20261010, "thesis_e0a_history_v1", "e0a-history"):
        raise ValueError("sampling/task identity differs from reviewed scope")
    if plan["sampling"] != "sha256(seed:campaign:household_key), ascending, eligible households only":
        raise ValueError("sampling declaration differs from implementation")
    if (plan["history_window_days"], plan["sample_size"], plan["repetitions"], plan["maximum_generations"]) != (84, 3, 2, 6):
        raise ValueError("scope changes require a new reviewed implementation")
    if plan["allowed_tables"] != list(TABLES) or plan["labels_accessible"] is not False:
        raise ValueError("table whitelist or label permissions changed")
    if plan["failure_policy"] != "stop_on_first_failure_no_retry_no_imputation":
        raise ValueError("failure policy changed")
    if require_approval and (
        plan["execution_approved"] is not True
        or plan["status"] != "confirmed"
        or not isinstance(plan["approval_reference"], str)
        or not plan["approval_reference"].strip()
    ):
        raise ValueError("user confirmation of historical scope must be recorded before real preparation/execution")


def source_path(directory, name):
    if name not in TABLES:
        raise ValueError("table is not permitted")
    # Resolve both sides to prevent accidental symlinks to an excluded table.
    path = (directory / name).resolve(strict=True)
    if path.parent != directory.resolve() or path.name != name:
        raise ValueError("source must be a named file inside the approved directory")
    return path


def table(directory, name):
    path = source_path(directory, name)
    with path.open(newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        if not HEADERS[name] <= set(reader.fieldnames or []):
            raise ValueError(f"missing required columns in {name}")
        yield from reader


def money(value):
    return str(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def ratio(value, total):
    return str((value / total).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))


def assemble(plan, rows):
    """Pure extraction, accepting only five permitted table iterables (fixtures in tests)."""
    validate_plan(plan)
    campaign = plan["campaign"]
    desc = [r for r in rows["campaign_desc.csv"] if r["CAMPAIGN"] == campaign]
    if len(desc) != 1:
        raise ValueError("campaign metadata missing or duplicated")
    desc = desc[0]
    if (desc["DESCRIPTION"], int(desc["START_DAY"]), int(desc["END_DAY"])) != (plan["campaign_type"], plan["start_day"], plan["end_day"]):
        raise ValueError("campaign dates/type differ from reviewed plan")
    participants = set()
    duplicate_participations = 0
    for row in rows["campaign_table.csv"]:
        if row["CAMPAIGN"] != campaign:
            continue
        if row["DESCRIPTION"] != plan["campaign_type"] or not row["household_key"]:
            raise ValueError("invalid participation relation")
        duplicate_participations += row["household_key"] in participants
        participants.add(row["household_key"])
    coupon_pairs = {
        (r["COUPON_UPC"], r["PRODUCT_ID"])
        for r in rows["coupon.csv"] if r["CAMPAIGN"] == campaign
    }
    if not coupon_pairs or any(not c or not p for c, p in coupon_pairs):
        raise ValueError("valid coupon-product facts unavailable")
    products = {}
    for row in rows["product.csv"]:
        key = row["PRODUCT_ID"]
        category = row["COMMODITY_DESC"].strip() or "<unknown>"
        if not key or (key in products and products[key] != category):
            raise ValueError("conflicting or empty product identifiers")
        products[key] = category
    targets = {p for _, p in coupon_pairs}
    if not targets <= products.keys():
        raise ValueError("target product join incomplete")
    coupon_categories = {(c, products[p]) for c, p in coupon_pairs}
    counts = Counter(cat for _, cat in coupon_categories)
    top = sorted(counts, key=lambda cat: (-counts[cat], cat))[:8]
    known_categories = set(counts) - {"<unknown>"}
    cutoff = plan["start_day"]
    lower = cutoff - plan["history_window_days"]
    state = {}
    minimum_day = None
    for row in rows["transaction_data.csv"]:
        day = int(row["DAY"])
        minimum_day = day if minimum_day is None else min(minimum_day, day)
        # Reject future/same-day data before inspecting purchase amount or product.
        if not lower <= day < cutoff or row["household_key"] not in participants:
            continue
        quantity, amount = Decimal(row["QUANTITY"]), Decimal(row["SALES_VALUE"])
        if not quantity.is_finite() or not amount.is_finite():
            raise ValueError("nonfinite historical purchase")
        if quantity <= 0:
            continue  # Returns/non-purchases excluded, not reinterpreted as positive purchases.
        if amount < 0 or not row["BASKET_ID"]:
            raise ValueError("invalid positive-purchase row")
        product = row["PRODUCT_ID"]
        if product not in products:
            raise ValueError("historical product join incomplete")
        s = state.setdefault(row["household_key"], {
            "baskets": set(), "days": set(), "sales": Decimal(0), "target_sales": Decimal(0),
            "category_sales": Decimal(0), "target_days": set(), "positive_rows": 0,
        })
        s["baskets"].add(row["BASKET_ID"])
        s["days"].add(day)
        s["sales"] += amount
        s["positive_rows"] += 1
        if product in targets:
            s["target_sales"] += amount
            s["target_days"].add(day)
        if products[product] in known_categories:
            s["category_sales"] += amount
    if minimum_day is None or minimum_day > lower:
        raise ValueError("source does not cover the proposed history window start")
    eligible = sorted(state, key=lambda h: (
        hashlib.sha256(f"{plan['sampling_seed']}:{campaign}:{h}".encode()).hexdigest(), h
    ))
    if len(eligible) < plan["sample_size"]:
        raise ValueError("insufficient eligible households; do not expand/substitute automatically")
    profiles, links = [], []
    for index, household in enumerate(eligible[:plan["sample_size"]], 1):
        s = state[household]
        behavior = {
            "history_window_days": plan["history_window_days"],
            "shopping_baskets": len(s["baskets"]), "shopping_days": len(s["days"]),
            "days_since_last_positive_purchase": cutoff - max(s["days"]),
            "positive_purchase_sales_value": money(s["sales"]),
            "target_product_sales_share": ratio(s["target_sales"], s["sales"]) if s["sales"] else "unknown: zero total sales",
            "target_known_category_sales_share": ratio(s["category_sales"], s["sales"]) if s["sales"] else "unknown: zero total sales",
            "days_since_last_target_product_purchase": cutoff - max(s["target_days"]) if s["target_days"] else "unknown: no target purchase in window",
            "historical_redemption": "unknown: redemption table not accessed",
        }
        alias = f"history_{index}"
        profiles.append({"id": alias, "behavior": behavior})
        links.append({"profile": alias, "household_key": household, "campaign": campaign,
                      "positive_rows": s["positive_rows"],
                      "sampling_hash": hashlib.sha256(f"{plan['sampling_seed']}:{campaign}:{household}".encode()).hexdigest()})
    facts = {
        "campaign_type": plan["campaign_type"], "valid_days": plan["end_day"] - cutoff + 1,
        "confirmed_entire_bundle_recipient": True,
        "unique_coupons": len({c for c, _ in coupon_pairs}), "unique_products": len(targets),
        "category_count_including_unknown": len(counts),
        "top_coupon_category_weights": [{"category": cat, "weight": ratio(Decimal(counts[cat]), Decimal(len(coupon_categories)))} for cat in top],
        "other_category_weight": ratio(Decimal(sum(counts[c] for c in counts if c not in top)), Decimal(len(coupon_categories))),
        "unknowns": "Coupon face value, creative, images, minimum spend, demographics, psychology, and outcomes are not provided.",
    }
    audit = {"lower_inclusive": lower, "cutoff_exclusive": cutoff,
             "participants": len(participants), "duplicate_participations": duplicate_participations,
             "eligible_with_positive_purchase": len(eligible), "selected": links,
             "label_tables_opened": [], "accuracy_evaluated": False}
    return profiles, facts, audit


def create_task(destination, facts):
    import shutil
    task = destination / "task"
    shutil.copytree(HERE / "historical_task", task)
    context = (
        "# Pre-campaign facts\n\n"
        "Predict before the campaign starts. All household observations are from the preceding 84 days.\n"
        "The household receives the entire bundle. Target product/category means the known coupon-covered sets.\n"
        "Sales value is the recorded amount; no currency name is inferred. Shares use positive purchase sales value.\n"
        "Recency is days from cutoff; unknown recency does not mean zero days. Category weights use unique coupon-category pairs.\n"
        "This is an interface check, not a probability calibration or marketing accuracy evaluation.\n\n"
        + json.dumps(facts, ensure_ascii=False, indent=2) + "\n"
    )
    (task / "input/context.md").write_text(context, encoding="utf-8")
    return task


@contextmanager
def task_binding(task):
    """Bind the official questionnaire lookup to this run's immutable input task.

    Official prompt building looks up a questionnaire ID a second time. Its
    built-in discovery scans application/tasks only, not ignored run packages.
    Register a relative folder in memory and restore it; never edit upstream.
    """
    import os
    import run
    from playground.survey_task_content import SURVEY_TASK_FOLDER_BY_QUESTIONNAIRE_ID

    instrument = "thesis_e0a_history_v1"
    registry = SURVEY_TASK_FOLDER_BY_QUESTIONNAIRE_ID
    previous = registry.get(instrument)
    registry[instrument] = os.path.relpath(task.resolve(), run.ROOT / "application/tasks")
    try:
        yield
    finally:
        if previous is None:
            registry.pop(instrument, None)
        else:
            registry[instrument] = previous


def prepare(plan, destination):
    validate_plan(plan, require_approval=True)
    directory = Path(plan["data_directory"]).resolve(strict=True)
    paths = {name: source_path(directory, name) for name in TABLES}
    before = {name: digest(path) for name, path in paths.items()}
    profiles, facts, audit = assemble(plan, {name: table(directory, name) for name in TABLES})
    after = {name: digest(path) for name, path in paths.items()}
    if before != after:
        raise ValueError("source files changed while preparing")
    config = dict(plan, profiles=profiles)
    task = create_task(destination, facts)
    for name, data in [("config.json", config), ("campaign_facts.json", facts),
                       ("input_audit.json", audit), ("source_hashes.json", before)]:
        (destination / name).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return config, task


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, default=HERE / "history-plan.json")
    args = parser.parse_args()
    plan = json.loads(args.plan.read_text())
    validate_plan(plan)
    print(json.dumps({"status": plan["status"], "campaign": plan["campaign"],
                      "window": [plan["start_day"] - 84, plan["start_day"] - 1],
                      "maximum_generations": plan["maximum_generations"],
                      "execution_approved": plan["execution_approved"],
                      "source_data_read": False, "model_calls": 0}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
