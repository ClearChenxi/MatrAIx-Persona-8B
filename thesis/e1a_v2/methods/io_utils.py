# ---------------------------------------------------------------------------
# MOVED FROM: thesis/method_dev/methods/io_utils.py
# DATE:       2026-10-13
# REASON:     Baseline N0/N1/B1/B2/B3 是真实传统方法实现，与 method_dev/ 的
#             LLM 调用上下文无关，可安全复用。本文件已脱离 method_dev/ 调用链。
# ISOLATION:  本文件不再 import method_dev/ 下任何模块；不接受 method_dev/ 的
#             调用方反向引用。审计方法见 ../ISOLATION.md。
# COMPATIBILITY: load_split / E1_RUN 常量已在 io_utils.py 中改为相对 thesis/
#                仓库根解析，不再硬编码 method_dev 路径。import 时如发现 E1_RUN
#                解析异常，请核对 thesis/e1_data/runs/<run-id>/ 是否存在。
# ---------------------------------------------------------------------------

"""Shared I/O utilities for E1a methods (B1/B2/B3/N0/N1).

Loads D/R/T features, labels, and links from the frozen e1_data run directory.
Parses JSON share columns into flat numeric features for tabular models.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

# Frozen e1_data run (already E0b-accepted, no method_dev dependency).
# Resolved from the repository root so e1a_v2/ does not import anything from
# thesis/method_dev/. To override (e.g. point to a re-frozen input bundle),
# set THESIS_E1_RUN env var to an absolute path.
import os  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parents[3]  # thesis/e1a_v2/methods/io_utils.py → repo root
_DEFAULT = (
    _REPO_ROOT
    / "thesis/e1_data/runs/e1-data-20261011T042328.304949Z/private"
)
E1_RUN = Path(os.environ.get("THESIS_E1_RUN", str(_DEFAULT)))


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load_split(split: str) -> tuple[list[dict], dict[str, int] | None, dict[str, int]]:
    """Return (features, labels, link_map) for D/R/T.

    features: list of dict per row_id. JSON share columns are parsed into nested dicts.
    labels:   y1_redeemed per row_id. None for T (sealed).
    link_map: row_id -> {household_key, campaign, split, e2_role}.
    """
    feats = _read_csv(E1_RUN / f"features_{split}.csv")
    labels_path = E1_RUN / f"labels_{split}.csv"
    label_map: dict[str, int] | None = None
    if labels_path.exists():
        labs = _read_csv(labels_path)
        label_map = {r["row_id"]: int(r["y1_redeemed"]) for r in labs}
    links = _read_csv(E1_RUN / f"links_{split}.csv")
    link_map = {r["row_id"]: r for r in links}

    # Parse JSON share columns (kept as nested dict in feature row)
    json_cols = [
        "department_spend_shares_json",
        "brand_type_spend_shares_json",
        "campaign_department_weights_json",
        "campaign_brand_type_weights_json",
    ]
    for row in feats:
        for col in json_cols:
            v = row.get(col, "")
            if v:
                try:
                    row[col] = json.loads(v)
                except json.JSONDecodeError:
                    row[col] = {}
            else:
                row[col] = {}
    return feats, label_map, link_map


# ===== Tabular feature extraction =====

# Numeric X/S columns (excluding JSON shares and household/campaign IDs)
NUMERIC_XS = [
    "history_window_days", "recency_days", "basket_count", "shopping_day_count",
    "positive_line_count", "return_line_count", "sales_total", "average_basket_sales",
    "zero_sales_total_flag", "retail_discount_basket_share", "coupon_discount_basket_share",
    "prior_completed_campaign_count", "prior_redeemed_campaign_count", "prior_redemption_rate",
    "no_prior_campaign_flag",
    "top_commodity_1_share", "top_commodity_2_share", "top_commodity_3_share", "other_commodity_share",
    "product_taxonomy_missing_flag",
    "campaign_type", "campaign_valid_days", "campaign_unique_coupon_count",
    "campaign_unique_product_count", "campaign_department_count", "campaign_commodity_count",
    "campaign_brand_type_count",
    "campaign_top_commodity_1_weight", "campaign_top_commodity_2_weight",
    "campaign_top_commodity_3_weight", "campaign_top_commodity_4_weight",
    "campaign_top_commodity_5_weight", "campaign_top_commodity_6_weight",
    "campaign_top_commodity_7_weight", "campaign_top_commodity_8_weight",
    "campaign_other_commodity_weight", "campaign_unknown_taxonomy_flag",
    "target_product_sales_share", "target_commodity_sales_share",
    "target_product_recency_days", "no_target_product_purchase_flag",
]
# Encode empty prior_redemption_rate (no history) as missing numeric
NUMERIC_XS_MAYBE_MISSING = {"prior_redemption_rate"}

CATEGORICAL_XS = ["campaign_type"]  # TypeB/TypeC
TEXT_XS = [
    "top_commodity_1", "top_commodity_2", "top_commodity_3",
    "campaign_top_commodity_1", "campaign_top_commodity_2",
    "campaign_top_commodity_3", "campaign_top_commodity_4",
    "campaign_top_commodity_5", "campaign_top_commodity_6",
    "campaign_top_commodity_7", "campaign_top_commodity_8",
]


def to_tabular(rows: list[dict], top_departments: list[str]) -> tuple["list[list[float]]", list[str]]:
    """Flatten features into a dense numeric matrix for sklearn/catboost.

    Columns: NUMERIC_XS + one-hot(campaign_type) + JSON share top-N departments (each 0..1)
    Missing prior_redemption_rate -> -1 sentinel; "" for numeric strings -> 0.
    """
    import math

    cols: list[str] = list(NUMERIC_XS)
    # campaign_type one-hot (drop one to avoid collinearity)
    for v in ["TypeB", "TypeC"]:
        cols.append(f"campaign_type__{v}")
    for d in top_departments:
        cols.append(f"dep_spend_share__{d}")

    X: list[list[float]] = []
    for r in rows:
        row: list[float] = []
        for c in NUMERIC_XS:
            v = r.get(c, "")
            if v == "":
                row.append(0.0)
            else:
                try:
                    f = float(v)
                    if c in NUMERIC_XS_MAYBE_MISSING and math.isnan(f):
                        row.append(-1.0)
                    else:
                        row.append(f)
                except ValueError:
                    row.append(0.0)
        # campaign_type one-hot
        ct = r.get("campaign_type", "")
        row.append(1.0 if ct == "TypeB" else 0.0)
        row.append(1.0 if ct == "TypeC" else 0.0)
        # department shares
        dep = r.get("department_spend_shares_json", {}) or {}
        for d in top_departments:
            try:
                row.append(float(dep.get(d, 0.0)))
            except (ValueError, TypeError):
                row.append(0.0)
        X.append(row)
    return X, cols


def top_departments_from_d(D_rows: list[dict], n: int = 20) -> list[str]:
    """Pick top-N departments by total spend across D households."""
    sums: dict[str, float] = {}
    for r in D_rows:
        dep = r.get("department_spend_shares_json", {}) or {}
        for k, v in dep.items():
            try:
                sums[k] = sums.get(k, 0.0) + float(v)
            except (ValueError, TypeError):
                pass
    return [k for k, _ in sorted(sums.items(), key=lambda kv: -kv[1])[:n]]


# ===== Metric: Average Precision per campaign, then macro =====

def average_precision(scores: list[float], labels: list[int]) -> float | None:
    """Standard AP. None if no positive label."""
    n_pos = sum(labels)
    if n_pos == 0:
        return None
    order = sorted(range(len(scores)), key=lambda i: -scores[i])
    ap_sum = 0.0
    seen_pos = 0
    for rank, i in enumerate(order, 1):
        if labels[i] == 1:
            seen_pos += 1
            ap_sum += seen_pos / rank
    return ap_sum / n_pos


def per_campaign_macro_ap(scores: list[float], labels: list[int], campaigns: list[int]) -> dict:
    """Return per-campaign AP + macro (unweighted) over campaigns with at least 1 positive."""
    by_camp: dict[int, tuple[list[float], list[int]]] = {}
    for s, y, c in zip(scores, labels, campaigns):
        if c not in by_camp:
            by_camp[c] = ([], [])
        by_camp[c][0].append(s)
        by_camp[c][1].append(y)
    out: dict = {"per_campaign": {}, "macro": None}
    aps = []
    for c, (ss, yy) in sorted(by_camp.items()):
        ap = average_precision(ss, yy)
        out["per_campaign"][str(c)] = None if ap is None else ap
        if ap is not None:
            aps.append(ap)
    out["macro"] = sum(aps) / len(aps) if aps else None
    out["n_campaigns_used"] = len(aps)
    return out


def brier(scores, labels) -> float:
    scores = list(scores)
    labels = list(labels)
    if not scores:
        return float("nan")
    return sum((s - y) ** 2 for s, y in zip(scores, labels)) / len(scores)
