"""Build deterministic, same-information D/R/T inputs without opening T labels."""

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
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
DEFAULT_PLAN = HERE / "plan.json"
RAW_TABLES = ("campaign_desc.csv", "campaign_table.csv", "coupon.csv", "product.csv", "transaction_data.csv")
RAW_HEADERS = {
    "campaign_desc.csv": ["DESCRIPTION", "CAMPAIGN", "START_DAY", "END_DAY"],
    "campaign_table.csv": ["DESCRIPTION", "household_key", "CAMPAIGN"],
    "coupon.csv": ["COUPON_UPC", "PRODUCT_ID", "CAMPAIGN"],
    "product.csv": ["PRODUCT_ID", "MANUFACTURER", "DEPARTMENT", "BRAND", "COMMODITY_DESC", "SUB_COMMODITY_DESC", "CURR_SIZE_OF_PRODUCT"],
    "transaction_data.csv": ["household_key", "BASKET_ID", "DAY", "PRODUCT_ID", "QUANTITY", "SALES_VALUE", "STORE_ID", "RETAIL_DISC", "TRANS_TIME", "WEEK_NO", "COUPON_DISC", "COUPON_MATCH_DISC"],
}
BASE_FEATURE_FIELDS = [
    "row_id", "history_window_days", "recency_days", "basket_count", "shopping_day_count",
    "positive_line_count", "return_line_count", "sales_total", "average_basket_sales", "zero_sales_total_flag",
    "retail_discount_basket_share", "coupon_discount_basket_share",
    "prior_completed_campaign_count", "prior_redeemed_campaign_count", "prior_redemption_rate",
    "no_prior_campaign_flag", "department_spend_shares_json",
    "top_commodity_1", "top_commodity_1_share", "top_commodity_2", "top_commodity_2_share",
    "top_commodity_3", "top_commodity_3_share", "other_commodity_share",
    "brand_type_spend_shares_json", "product_taxonomy_missing_flag",
    "campaign_type", "campaign_valid_days", "campaign_unique_coupon_count",
    "campaign_unique_product_count", "campaign_department_count", "campaign_commodity_count",
    "campaign_brand_type_count", "campaign_department_weights_json", "campaign_brand_type_weights_json",
]
for index in range(1, 9):
    BASE_FEATURE_FIELDS.extend([f"campaign_top_commodity_{index}", f"campaign_top_commodity_{index}_weight"])
BASE_FEATURE_FIELDS.extend([
    "campaign_other_commodity_weight", "campaign_unknown_taxonomy_flag",
    "target_product_sales_share", "target_commodity_sales_share", "target_product_recency_days",
    "no_target_product_purchase_flag", "frequency_group", "historical_redemption_group",
    "target_category_match_group",
])


def canonical_json(data):
    return json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def write_json(path, data):
    path.write_text(canonical_json(data), encoding="utf-8")


def write_csv(path, fieldnames, records):
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(records)


def read_csv(path):
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def table(path, headers):
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames != headers:
            raise ValueError(f"header mismatch in {path.name}")
        yield from reader


def parse_int(value, context):
    if not value or any(ch in value for ch in ".eE"):
        raise ValueError(f"invalid integer in {context}")
    return int(value)


def parse_decimal(value, context):
    try:
        result = Decimal(value)
    except (InvalidOperation, TypeError) as exc:
        raise ValueError(f"invalid decimal in {context}") from exc
    if not result.is_finite():
        raise ValueError(f"nonfinite decimal in {context}")
    return result


def decimal_text(value, places=6):
    quantum = Decimal(1).scaleb(-places)
    return format(value.quantize(quantum, rounding=ROUND_HALF_UP), f".{places}f")


def ratio_text(numerator, denominator, places=6):
    return "" if not denominator else decimal_text(numerator / denominator, places)


def normalize(value, unknown):
    value = value.strip()
    return value if value else unknown


def git_output(*args):
    return subprocess.check_output(["git", *args], cwd=REPO, text=True).strip()


def repository_gate(plan):
    if git_output("status", "--porcelain", "--untracked-files=all"):
        raise ValueError("real D/R/T preparation requires a clean committed worktree")
    branch = git_output("branch", "--show-current")
    if branch != plan["branch"]:
        raise ValueError("unexpected experiment branch")
    head = git_output("rev-parse", "HEAD")
    if head != git_output("rev-parse", f"origin/{branch}"):
        raise ValueError("data preparation code must be pushed before real execution")
    if git_output("rev-parse", "thesis-upstream-3633d8d^{commit}") != plan["official_commit"]:
        raise ValueError("official baseline tag moved")
    return {"branch": branch, "code_commit": head, "official_commit": plan["official_commit"]}


def validate_plan(plan):
    expected_permissions = {"D": ["D"], "R": ["D"], "T": ["D", "R"]}
    if plan.get("schema_version") != "1.0" or plan.get("status") != "confirmed":
        raise ValueError("unconfirmed D/R/T preparation plan")
    if plan.get("execution_approved") is not True or not plan.get("approval_reference"):
        raise ValueError("D/R/T preparation approval is not recorded")
    if plan.get("history_window_days") != 84 or plan.get("vocabulary_fit_split") != "D":
        raise ValueError("history window or vocabulary fitting split changed")
    if plan.get("history_label_permissions") != expected_permissions:
        raise ValueError("historical label permissions changed")
    required_exclusions = {
        "coupon_redempt.csv", "hh_demographic.csv", "causal_data.csv",
        "labels_T_SEALED.csv", "label_summary_PRIVATE.json",
    }
    if set(plan.get("excluded_sources", ())) != required_exclusions:
        raise ValueError("excluded source policy changed")
    if plan.get("manual_audit_sample_size") != 20:
        raise ValueError("manual audit sample size changed")


def load_e0b(plan):
    run = Path(plan["e0b_run"]).resolve(strict=True)
    public = run / "public"
    private = run / "private"
    audit = json.loads((public / "audit_manifest.json").read_text(encoding="utf-8"))
    seal = json.loads((public / "label_seal_manifest.json").read_text(encoding="utf-8"))
    source_manifest = json.loads((public / "source_manifest.json").read_text(encoding="utf-8"))
    checksums = json.loads((run / "checksums.json").read_text(encoding="utf-8"))
    if audit["status"] != "e0b_run_complete" or audit["plan_sha256"] != plan["e0b_plan_sha256"]:
        raise ValueError("E0b run identity or status mismatch")
    if seal["test_label_values_exposed"] or seal["test_positive_count_exposed"] or seal["test_response_rate_exposed"]:
        raise ValueError("E0b T seal is unsafe")
    if set(seal["historical_label_summary"]) != {"D", "R"}:
        raise ValueError("E0b public label summary has unexpected splits")
    allowed_private = {
        "cohort_permissions.csv": private / "cohort_permissions.csv",
        "labels_D_development.csv": private / "labels_D_development.csv",
        "labels_R_validation.csv": private / "labels_R_validation.csv",
    }
    for name, path in allowed_private.items():
        if digest(path) != checksums[f"private/{name}"]:
            raise ValueError(f"E0b permitted input changed: {name}")
    sealed_t = private / "labels_T_SEALED.csv"
    if not sealed_t.is_file() or oct(sealed_t.stat().st_mode & 0o777) != "0o600":
        raise ValueError("sealed T label file is missing or has unsafe permissions")
    cohort = [row for row in read_csv(allowed_private["cohort_permissions.csv"]) if row["eligible"] == "1"]
    labels = {
        "D": read_csv(allowed_private["labels_D_development.csv"]),
        "R": read_csv(allowed_private["labels_R_validation.csv"]),
    }
    return run, audit, seal, source_manifest, cohort, labels


def nearest_rank(values, fraction):
    ordered = sorted(values)
    if not ordered:
        return None
    return ordered[max(0, math.ceil(len(ordered) * fraction) - 1)]


def tercile_group(value, lower, upper):
    if lower == upper:
        return "low" if value <= lower else "high"
    if value <= lower:
        return "low"
    if value <= upper:
        return "medium"
    return "high"


def mapped_counter(raw, vocabulary, other):
    allowed = set(vocabulary)
    result = Counter()
    for key, value in raw.items():
        result[key if key in allowed else other] += value
    return result


def full_share_json(raw, vocabulary, other, places):
    mapped = mapped_counter(raw, vocabulary, other)
    keys = list(vocabulary) + [other]
    total = sum(mapped.values(), Decimal(0))
    values = {key: ratio_text(mapped[key], total, places) if total else "" for key in keys}
    return json.dumps(values, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def top_shares(raw, vocabulary, other, count, places):
    mapped = mapped_counter(raw, vocabulary, other)
    total = sum(mapped.values(), Decimal(0))
    ordered = sorted((key for key, value in mapped.items() if value), key=lambda key: (-mapped[key], key))
    top = ordered[:count]
    slots = []
    for index in range(count):
        if index < len(top):
            key = top[index]
            slots.append((key, ratio_text(mapped[key], total, places)))
        else:
            slots.append(("", ""))
    other_value = sum((mapped[key] for key in ordered[count:]), Decimal(0))
    return slots, ratio_text(other_value, total, places) if total else ""


def activity_weight_pairs(pairs, products, field, vocabulary, other):
    allowed = set(vocabulary)
    mapped = set()
    for coupon, product in pairs:
        value = products[product][field]
        mapped.add((coupon, value if value in allowed else other))
    counts = Counter(value for _, value in mapped)
    return counts


def activity_features(campaign, metadata, coupon_pairs, products, vocabulary, plan):
    unknown, other = plan["unknown_category"], plan["other_category"]
    pairs = coupon_pairs[campaign]
    target_products = {product for _, product in pairs}
    departments = {products[p]["department"] for p in target_products}
    commodities = {products[p]["commodity"] for p in target_products}
    brands = {products[p]["brand"] for p in target_products}
    dept_pairs = activity_weight_pairs(pairs, products, "department", vocabulary["department"], other)
    brand_pairs = activity_weight_pairs(pairs, products, "brand", vocabulary["brand"], other)
    commodity_pairs = activity_weight_pairs(pairs, products, "commodity", vocabulary["commodity"], other)
    places = plan["numeric_decimal_places"]
    commodity_slots, commodity_other = top_shares(
        commodity_pairs, list(commodity_pairs), other, 8, places
    )
    result = {
        "campaign_type": metadata[campaign]["type"],
        "campaign_valid_days": metadata[campaign]["end"] - metadata[campaign]["start"] + 1,
        "campaign_unique_coupon_count": len({coupon for coupon, _ in pairs}),
        "campaign_unique_product_count": len(target_products),
        "campaign_department_count": len(departments),
        "campaign_commodity_count": len(commodities),
        "campaign_brand_type_count": len(brands),
        "campaign_department_weights_json": full_share_json(dept_pairs, vocabulary["department"], other, places),
        "campaign_brand_type_weights_json": full_share_json(brand_pairs, vocabulary["brand"], other, places),
        "campaign_other_commodity_weight": commodity_other,
        "campaign_unknown_taxonomy_flag": int(any(
            unknown in (products[p]["department"], products[p]["commodity"], products[p]["brand"])
            for p in target_products
        )),
    }
    for index, (category, weight) in enumerate(commodity_slots, 1):
        result[f"campaign_top_commodity_{index}"] = category
        result[f"campaign_top_commodity_{index}_weight"] = weight
    return result


def permitted_history(split, entry_split, plan):
    return entry_split in plan["history_label_permissions"][split]


def row_identifier(namespace, household, campaign):
    return hashlib.sha256(f"{namespace}:{household}:{campaign}".encode()).hexdigest()


def persona_input(row):
    department = json.loads(row["department_spend_shares_json"])
    brand = json.loads(row["brand_type_spend_shares_json"])
    activity_department = json.loads(row["campaign_department_weights_json"])
    activity_brand = json.loads(row["campaign_brand_type_weights_json"])
    commodities = [
        {"category": row[f"top_commodity_{i}"], "sales_share": row[f"top_commodity_{i}_share"]}
        for i in range(1, 4) if row[f"top_commodity_{i}"]
    ]
    activity_commodities = [
        {"category": row[f"campaign_top_commodity_{i}"], "coupon_category_weight": row[f"campaign_top_commodity_{i}_weight"]}
        for i in range(1, 9) if row[f"campaign_top_commodity_{i}"]
    ]
    return {
        "row_id": row["row_id"],
        "observed_household": {
            "observation_window_days": int(row["history_window_days"]),
            "days_since_last_positive_purchase": int(row["recency_days"]),
            "shopping_baskets": int(row["basket_count"]),
            "shopping_days": int(row["shopping_day_count"]),
            "positive_purchase_lines": int(row["positive_line_count"]),
            "return_or_nonpositive_quantity_lines": int(row["return_line_count"]),
            "positive_purchase_sales_value": row["sales_total"],
            "average_basket_sales_value": row["average_basket_sales"],
            "retail_discount_basket_share": row["retail_discount_basket_share"],
            "coupon_discount_basket_share": row["coupon_discount_basket_share"],
            "permitted_prior_completed_campaigns": int(row["prior_completed_campaign_count"]),
            "permitted_prior_redeemed_campaigns": int(row["prior_redeemed_campaign_count"]),
            "permitted_prior_redemption_rate": row["prior_redemption_rate"] or "unknown: no permitted prior campaign",
            "department_sales_shares": department,
            "top_commodity_sales_shares": commodities,
            "other_commodity_sales_share": row["other_commodity_share"],
            "brand_type_sales_shares": brand,
            "target_product_sales_share": row["target_product_sales_share"] or "unknown: zero total sales",
            "target_commodity_sales_share": row["target_commodity_sales_share"] or "unknown: zero total sales",
            "days_since_last_target_product_purchase": row["target_product_recency_days"] or "unknown: no target product purchase",
        },
        "campaign_facts": {
            "campaign_type": row["campaign_type"],
            "valid_days": int(row["campaign_valid_days"]),
            "confirmed_entire_bundle_recipient": True,
            "unique_coupons": int(row["campaign_unique_coupon_count"]),
            "unique_products": int(row["campaign_unique_product_count"]),
            "department_count": int(row["campaign_department_count"]),
            "commodity_count": int(row["campaign_commodity_count"]),
            "brand_type_count": int(row["campaign_brand_type_count"]),
            "department_coupon_weights": activity_department,
            "brand_type_coupon_weights": activity_brand,
            "top_coupon_commodity_weights": activity_commodities,
            "other_commodity_weight": row["campaign_other_commodity_weight"],
        },
        "unknown_information": [
            "coupon face value", "creative text", "images", "minimum spend",
            "demographics", "psychology", "motives", "future outcomes",
        ],
    }


def field_spec(name):
    if name == "row_id":
        return {
            "name": name, "source": "sha256(namespace:household_key:campaign)", "window": "not applicable",
            "unit": "opaque join identifier", "missing": "forbidden", "traditional_encoding": "exclude from model",
            "persona_presentation": "logging only; not shown as a household or campaign identifier",
        }
    campaign_field = name.startswith("campaign_")
    group_field = name.endswith("_group")
    prior_field = name.startswith("prior_") or name == "no_prior_campaign_flag"
    if campaign_field:
        source = "campaign_desc/coupon/product known before campaign start"
        window = "campaign design, independent of campaign outcomes"
    elif group_field:
        source = "derived from the corresponding feature using D-only frozen thresholds"
        window = "same as source feature; thresholds fit on D"
    elif prior_field:
        source = "permitted completed D/R labels from the E0b seal"
        window = "END_DAY < current START_DAY; permission is D<-D, R<-D, T<-D/R"
    elif name.startswith("target_") or name == "no_target_product_purchase_flag":
        source = "transaction_data matched to prediction-time coupon/product target sets"
        window = "START_DAY-84 <= DAY < START_DAY"
    else:
        source = "transaction_data joined to product taxonomy"
        window = "START_DAY-84 <= DAY < START_DAY"
    categorical = (
        name.endswith("_group") or name in {"campaign_type"}
        or "commodity_" in name and not name.endswith(("_share", "_weight", "_count"))
    )
    json_map = name.endswith("_json")
    flag = name.endswith("_flag")
    if json_map:
        encoding = "expand by the frozen D vocabulary; parse as structured JSON, never as free text"
    elif categorical:
        encoding = "categorical with frozen D vocabulary and <OTHER>/<UNKNOWN> handling"
    elif flag:
        encoding = "binary 0/1"
    else:
        encoding = "numeric; scaling/imputation fit on D only"
    missing = "empty string means structurally unavailable; preserve a paired missing flag where defined"
    if flag or name.endswith("_count") or name in {"history_window_days", "recency_days", "basket_count", "shopping_day_count", "positive_line_count", "return_line_count", "campaign_valid_days"}:
        missing = "not permitted"
    return {
        "name": name, "source": source, "window": window, "unit": "see field name and global rules",
        "missing": missing, "traditional_encoding": encoding,
        "persona_presentation": "deterministic factual value with the same precision; identifiers and group labels are not narrated",
    }


def write_jsonl(path, records):
    with path.open("w", encoding="utf-8") as stream:
        for record in records:
            stream.write(json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")


def write_checksums(run_dir):
    files = {}
    for path in sorted(run_dir.rglob("*")):
        if path.is_file() and path.name != "checksums.json":
            files[str(path.relative_to(run_dir))] = digest(path)
    write_json(run_dir / "checksums.json", files)
    return files


def build(plan, output, require_repository=True):
    validate_plan(plan)
    output.mkdir(parents=True, exist_ok=False)
    public, private = output / "public", output / "private"
    public.mkdir()
    private.mkdir()
    os.chmod(private, 0o700)
    repo = repository_gate(plan) if require_repository else {
        "branch": plan["branch"], "code_commit": "test-fixture", "official_commit": plan["official_commit"]
    }
    write_json(output / "plan.json", plan)
    e0b_run, e0b_audit, e0b_seal, source_manifest, cohort, labels = load_e0b(plan)
    e0b_plan = json.loads((e0b_run / "plan.json").read_text(encoding="utf-8"))
    data_dir = Path(e0b_plan["data_directory"]).resolve(strict=True)
    paths = {name: (data_dir / name).resolve(strict=True) for name in RAW_TABLES}
    for name, path in paths.items():
        if path.parent != data_dir or path.is_symlink() or digest(path) != source_manifest[name]["sha256"]:
            raise ValueError(f"raw source identity mismatch: {name}")

    metadata = {}
    for row in table(paths["campaign_desc.csv"], RAW_HEADERS["campaign_desc.csv"]):
        metadata[row["CAMPAIGN"]] = {
            "type": row["DESCRIPTION"], "start": parse_int(row["START_DAY"], "START_DAY"),
            "end": parse_int(row["END_DAY"], "END_DAY"),
        }
    products = {}
    unknown = plan["unknown_category"]
    for row in table(paths["product.csv"], RAW_HEADERS["product.csv"]):
        product = row["PRODUCT_ID"]
        value = {
            "department": normalize(row["DEPARTMENT"], unknown),
            "commodity": normalize(row["COMMODITY_DESC"], unknown),
            "brand": normalize(row["BRAND"], unknown),
        }
        if not product or (product in products and products[product] != value):
            raise ValueError("invalid or conflicting product record")
        products[product] = value

    campaigns = {row["campaign"] for row in cohort}
    coupon_pairs = defaultdict(set)
    for row in table(paths["coupon.csv"], RAW_HEADERS["coupon.csv"]):
        if row["CAMPAIGN"] not in campaigns:
            continue
        if not row["COUPON_UPC"] or row["PRODUCT_ID"] not in products:
            raise ValueError("invalid target coupon-product relation")
        coupon_pairs[row["CAMPAIGN"]].add((row["COUPON_UPC"], row["PRODUCT_ID"]))
    if any(not coupon_pairs[campaign] for campaign in campaigns):
        raise ValueError("campaign target set is empty")
    target_products_by_campaign = {
        campaign: {product for _, product in pairs} for campaign, pairs in coupon_pairs.items()
    }
    target_commodities_by_campaign = {
        campaign: {products[product]["commodity"] for product in target_products}
        for campaign, target_products in target_products_by_campaign.items()
    }

    units = {}
    units_by_household = defaultdict(list)
    for row in cohort:
        key = (row["campaign"], row["household_key"])
        unit = {
            "campaign": row["campaign"], "household": row["household_key"], "split": row["split"],
            "e2_role": row["e2_role"], "baskets": {}, "days": set(), "sales": Decimal(0),
            "positive_lines": 0, "return_lines": 0, "department_sales": Counter(),
            "commodity_sales": Counter(), "brand_sales": Counter(), "taxonomy_missing": False,
            "target_product_sales": Decimal(0), "target_commodity_sales": Decimal(0), "target_days": set(),
        }
        units[key] = unit
        units_by_household[row["household_key"]].append(unit)

    for row in table(paths["transaction_data.csv"], RAW_HEADERS["transaction_data.csv"]):
        household_units = units_by_household.get(row["household_key"])
        if not household_units:
            continue
        day = parse_int(row["DAY"], "transaction DAY")
        active_units = [unit for unit in household_units if
                        metadata[unit["campaign"]]["start"] - plan["history_window_days"] <= day < metadata[unit["campaign"]]["start"]]
        if not active_units:
            continue
        quantity = parse_decimal(row["QUANTITY"], "transaction QUANTITY")
        if quantity <= 0:
            for unit in active_units:
                unit["return_lines"] += 1
            continue
        sales = parse_decimal(row["SALES_VALUE"], "transaction SALES_VALUE")
        if sales < 0 or not row["BASKET_ID"] or row["PRODUCT_ID"] not in products:
            raise ValueError("invalid positive purchase in an eligible history window")
        discounts = [parse_decimal(row[name], f"transaction {name}")
                     for name in ("RETAIL_DISC", "COUPON_DISC", "COUPON_MATCH_DISC")]
        product = products[row["PRODUCT_ID"]]
        for unit in active_units:
            basket = unit["baskets"].setdefault(row["BASKET_ID"], {"sales": Decimal(0), "retail": False, "coupon": False})
            basket["sales"] += sales
            basket["retail"] = basket["retail"] or discounts[0] != 0
            basket["coupon"] = basket["coupon"] or discounts[1] != 0 or discounts[2] != 0
            unit["days"].add(day)
            unit["sales"] += sales
            unit["positive_lines"] += 1
            unit["department_sales"][product["department"]] += sales
            unit["commodity_sales"][product["commodity"]] += sales
            unit["brand_sales"][product["brand"]] += sales
            unit["taxonomy_missing"] = unit["taxonomy_missing"] or unknown in product.values()
            target_products = target_products_by_campaign[unit["campaign"]]
            target_commodities = target_commodities_by_campaign[unit["campaign"]]
            if row["PRODUCT_ID"] in target_products:
                unit["target_product_sales"] += sales
                unit["target_days"].add(day)
            if product["commodity"] in target_commodities:
                unit["target_commodity_sales"] += sales

    if any(not unit["positive_lines"] for unit in units.values()):
        raise ValueError("E0b-eligible unit has no legal positive history")

    d_units = [unit for unit in units.values() if unit["split"] == "D"]
    vocabulary = {}
    for field, state_key in (("department", "department_sales"), ("commodity", "commodity_sales"), ("brand", "brand_sales")):
        values = {value for unit in d_units for value in unit[state_key]}
        for campaign in {unit["campaign"] for unit in d_units}:
            values.update(products[p][field] for _, p in coupon_pairs[campaign])
        values.discard(plan["other_category"])
        vocabulary[field] = sorted(values)
    write_json(public / "vocabulary.json", {**vocabulary, "other": plan["other_category"], "fit_split": "D"})

    label_by_pair = {}
    history_by_household = defaultdict(list)
    for split, records in labels.items():
        for row in records:
            pair = (row["campaign"], row["household_key"])
            if pair in label_by_pair:
                raise ValueError("duplicate permitted historical label")
            label_by_pair[pair] = row
            history_by_household[row["household_key"]].append({
                "campaign": row["campaign"], "split": split, "end": metadata[row["campaign"]]["end"],
                "redeemed": int(row["y1_redeemed"]),
            })

    activity_by_campaign = {
        campaign: activity_features(campaign, metadata, coupon_pairs, products, vocabulary, plan)
        for campaign in campaigns
    }
    rows_by_split = {split: [] for split in ("D", "R", "T")}
    links_by_split = {split: [] for split in ("D", "R", "T")}
    labels_by_split = {split: [] for split in ("D", "R")}
    raw_for_groups = []
    places = plan["numeric_decimal_places"]
    for (campaign, household), unit in units.items():
        split = unit["split"]
        start = metadata[campaign]["start"]
        baskets = unit["baskets"]
        prior = [entry for entry in history_by_household[household]
                 if entry["end"] < start and permitted_history(split, entry["split"], plan)]
        prior_count = len(prior)
        prior_redeemed = sum(entry["redeemed"] for entry in prior)
        top_commodities, other_commodity = top_shares(
            unit["commodity_sales"], vocabulary["commodity"], plan["other_category"], 3, places
        )
        row = {
            "row_id": row_identifier(plan["row_id_namespace"], household, campaign),
            "history_window_days": plan["history_window_days"],
            "recency_days": start - max(unit["days"]),
            "basket_count": len(baskets),
            "shopping_day_count": len(unit["days"]),
            "positive_line_count": unit["positive_lines"],
            "return_line_count": unit["return_lines"],
            "sales_total": decimal_text(unit["sales"], plan["money_decimal_places"]),
            "average_basket_sales": decimal_text(unit["sales"] / len(baskets), plan["money_decimal_places"]),
            "zero_sales_total_flag": int(not unit["sales"]),
            "retail_discount_basket_share": ratio_text(Decimal(sum(b["retail"] for b in baskets.values())), Decimal(len(baskets)), places),
            "coupon_discount_basket_share": ratio_text(Decimal(sum(b["coupon"] for b in baskets.values())), Decimal(len(baskets)), places),
            "prior_completed_campaign_count": prior_count,
            "prior_redeemed_campaign_count": prior_redeemed,
            "prior_redemption_rate": ratio_text(Decimal(prior_redeemed), Decimal(prior_count), places),
            "no_prior_campaign_flag": int(not prior_count),
            "department_spend_shares_json": full_share_json(unit["department_sales"], vocabulary["department"], plan["other_category"], places),
            "other_commodity_share": other_commodity,
            "brand_type_spend_shares_json": full_share_json(unit["brand_sales"], vocabulary["brand"], plan["other_category"], places),
            "product_taxonomy_missing_flag": int(unit["taxonomy_missing"]),
            "target_product_sales_share": ratio_text(unit["target_product_sales"], unit["sales"], places),
            "target_commodity_sales_share": ratio_text(unit["target_commodity_sales"], unit["sales"], places),
            "target_product_recency_days": start - max(unit["target_days"]) if unit["target_days"] else "",
            "no_target_product_purchase_flag": int(not unit["target_days"]),
            **activity_by_campaign[campaign],
            "frequency_group": "",
            "historical_redemption_group": "",
            "target_category_match_group": "",
        }
        for index, (category, share) in enumerate(top_commodities, 1):
            row[f"top_commodity_{index}"] = category
            row[f"top_commodity_{index}_share"] = share
        raw_for_groups.append(row)
        rows_by_split[split].append(row)
        links_by_split[split].append({
            "row_id": row["row_id"], "household_key": household, "campaign": campaign,
            "split": split, "e2_role": unit["e2_role"],
            "history_lower_day": start - plan["history_window_days"], "history_upper_day": start - 1,
        })
        if split in labels_by_split:
            source_label = label_by_pair.get((campaign, household))
            if not source_label:
                raise ValueError("missing permitted D/R label")
            labels_by_split[split].append({
                "row_id": row["row_id"], "y1_redeemed": source_label["y1_redeemed"],
                "y2_target_product_positive_purchase": source_label["y2_target_product_positive_purchase"],
            })

    d_rows = rows_by_split["D"]
    frequency_values = [int(row["basket_count"]) for row in d_rows]
    target_values = [Decimal(row["target_commodity_sales_share"]) for row in d_rows if row["target_commodity_sales_share"]]
    redemption_values = [Decimal(row["prior_redemption_rate"]) for row in d_rows if row["prior_redemption_rate"]]
    thresholds = {
        "frequency": [nearest_rank(frequency_values, 1 / 3), nearest_rank(frequency_values, 2 / 3)],
        "historical_redemption_median": nearest_rank(redemption_values, 1 / 2),
        "target_category_match": [nearest_rank(target_values, 1 / 3), nearest_rank(target_values, 2 / 3)],
    }
    if (None in thresholds["frequency"] or thresholds["historical_redemption_median"] is None
            or None in thresholds["target_category_match"]):
        raise ValueError("D does not support the predeclared behavior group thresholds")
    for row in raw_for_groups:
        row["frequency_group"] = tercile_group(int(row["basket_count"]), *thresholds["frequency"])
        row["historical_redemption_group"] = (
            "no_history" if not row["prior_redemption_rate"]
            else "low" if Decimal(row["prior_redemption_rate"]) <= thresholds["historical_redemption_median"] else "high"
        )
        row["target_category_match_group"] = (
            "missing" if not row["target_commodity_sales_share"] else tercile_group(
                Decimal(row["target_commodity_sales_share"]), *thresholds["target_category_match"]
            )
        )
    threshold_json = {
        "frequency": thresholds["frequency"],
        "historical_redemption_median": decimal_text(thresholds["historical_redemption_median"], places),
        "target_category_match": [decimal_text(value, places) for value in thresholds["target_category_match"]],
        "fit_split": "D", "boundary_policy": "boundary_to_lower; repeated thresholds merge empty groups",
    }
    write_json(public / "group_thresholds.json", threshold_json)

    for split in ("D", "R", "T"):
        rows_by_split[split].sort(key=lambda row: row["row_id"])
        links_by_split[split].sort(key=lambda row: row["row_id"])
        write_csv(private / f"features_{split}.csv", BASE_FEATURE_FIELDS, rows_by_split[split])
        write_csv(private / f"links_{split}.csv",
                  ["row_id", "household_key", "campaign", "split", "e2_role", "history_lower_day", "history_upper_day"],
                  links_by_split[split])
        write_jsonl(private / f"model_inputs_{split}.jsonl", [persona_input(row) for row in rows_by_split[split]])
        if split in labels_by_split:
            labels_by_split[split].sort(key=lambda row: row["row_id"])
            write_csv(private / f"labels_{split}.csv",
                      ["row_id", "y1_redeemed", "y2_target_product_positive_purchase"], labels_by_split[split])
    activity_rows = []
    for campaign in sorted(campaigns, key=lambda c: (metadata[c]["start"], int(c))):
        activity_rows.append({
            "campaign": campaign, "start_day": metadata[campaign]["start"], "end_day": metadata[campaign]["end"],
            "facts_json": json.dumps(activity_by_campaign[campaign], ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        })
    write_csv(private / "activity_facts.csv", ["campaign", "start_day", "end_day", "facts_json"], activity_rows)

    strata = defaultdict(list)
    for row in d_rows:
        key = (row["frequency_group"], row["historical_redemption_group"],
               "target" if not int(row["no_target_product_purchase_flag"]) else "no_target",
               "missing_taxonomy" if int(row["product_taxonomy_missing_flag"]) else "complete_taxonomy")
        strata[key].append(row)
    for values in strata.values():
        values.sort(key=lambda row: hashlib.sha256(f"20261011:{row['row_id']}".encode()).hexdigest())
    sample = []
    while len(sample) < plan["manual_audit_sample_size"] and any(strata.values()):
        for key in sorted(strata):
            if strata[key] and len(sample) < plan["manual_audit_sample_size"]:
                row = strata[key].pop(0)
                sample.append({
                    "row_id": row["row_id"], "frequency_group": row["frequency_group"],
                    "historical_redemption_group": row["historical_redemption_group"],
                    "target_category_match_group": row["target_category_match_group"],
                    "target_product_history": "present" if not int(row["no_target_product_purchase_flag"]) else "absent",
                    "taxonomy_status": "missing" if int(row["product_taxonomy_missing_flag"]) else "complete",
                    "automated_boundary_checks": "passed", "manual_review_status": "pending",
                })
    write_csv(private / "persona_audit_sample_D.csv",
              ["row_id", "frequency_group", "historical_redemption_group", "target_category_match_group",
               "target_product_history", "taxonomy_status", "automated_boundary_checks", "manual_review_status"], sample)

    field_dictionary = {
        "schema_version": "1.0",
        "identifier_fields": ["row_id"],
        "forbidden_model_fields": ["household_key", "campaign", "split", "e2_role", "y1_redeemed", "y2_target_product_positive_purchase"],
        "feature_fields": BASE_FEATURE_FIELDS[1:],
        "fields": [field_spec(name) for name in BASE_FEATURE_FIELDS],
        "rules": {
            "history_window": "START_DAY-84 <= DAY < START_DAY",
            "legal_positive_purchase": "QUANTITY > 0, SALES_VALUE >= 0, nonempty BASKET_ID, known PRODUCT_ID",
            "returns": "history-window rows with QUANTITY <= 0 are counted separately and excluded from positive-purchase aggregates",
            "discount_share": "share of legal positive-purchase baskets with nonzero RETAIL_DISC or nonzero COUPON_DISC/COUPON_MATCH_DISC",
            "prior_campaigns": plan["history_label_permissions"],
            "category_vocabulary": "fit on D household histories and D campaign facts; R/T unseen values map to <OTHER>",
            "campaign_category_weight": "deduplicated coupon-category pairs; no redemption weighting",
            "persona": plan["persona_policy"],
        },
    }
    write_json(public / "field_dictionary.json", field_dictionary)

    for path in private.iterdir():
        if path.is_file():
            os.chmod(path, 0o600)
    t_roles = Counter(row["e2_role"] for row in links_by_split["T"])
    manifest = {
        "status": "e1_data_prepared",
        "completed_at": datetime.now(timezone.utc).isoformat(),
        **repo,
        "e0b_run": e0b_run.name,
        "e0b_code_commit": e0b_audit["code_commit"],
        "e0b_plan_sha256": e0b_audit["plan_sha256"],
        "rows": {split: len(rows_by_split[split]) for split in ("D", "R", "T")},
        "unique_features": len(BASE_FEATURE_FIELDS) - 1,
        "vocabulary_sizes": {name: len(values) for name, values in vocabulary.items()},
        "test_permissions": dict(t_roles),
        "labels_written": ["D", "R"],
        "test_labels_opened": False,
        "test_labels_written": False,
        "excluded_sources_opened": [],
        "source_tables_opened": list(RAW_TABLES),
        "permitted_e0b_private_inputs": ["cohort_permissions.csv", "labels_D_development.csv", "labels_R_validation.csv"],
        "model_calls": 0,
        "manual_audit_sample_rows": len(sample),
    }
    write_json(public / "manifest.json", manifest)
    current = {name: digest(path) for name, path in paths.items()}
    expected = {name: source_manifest[name]["sha256"] for name in RAW_TABLES}
    if current != expected:
        raise ValueError("raw source changed during D/R/T preparation")
    write_checksums(output)
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
            "status": plan["status"], "history_window_days": plan["history_window_days"],
            "history_label_permissions": plan["history_label_permissions"],
            "excluded_sources": plan["excluded_sources"], "source_data_read": False, "model_calls": 0,
        }), end="")
        return
    output = args.output or HERE / "runs" / f"e1-data-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')}"
    manifest = build(plan, output)
    print(canonical_json({
        "status": manifest["status"], "output": str(output), "rows": manifest["rows"],
        "test_labels_opened": False, "model_calls": 0,
    }), end="")


if __name__ == "__main__":
    main()
