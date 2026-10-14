# ---------------------------------------------------------------------------
# MOVED FROM: thesis/method_dev/methods/baselines.py
# DATE:       2026-10-13
# REASON:     Baseline N0/N1/B1/B2/B3 是真实传统方法实现，与 method_dev/ 的
#             LLM 调用上下文无关，可安全复用。本文件已脱离 method_dev/ 调用链。
# ISOLATION:  本文件不再 import method_dev/ 下任何模块；不接受 method_dev/ 的
#             调用方反向引用。审计方法见 ../ISOLATION.md。
# COMPATIBILITY: load_split / E1_RUN 常量已在 io_utils.py 中改为相对 thesis/
#                仓库根解析，不再硬编码 method_dev 路径。import 时如发现 E1_RUN
#                解析异常，请核对 thesis/e1_data/runs/<run-id>/ 是否存在。
# ---------------------------------------------------------------------------

"""N0 / N1 / B1 baselines for E1a (no model fit needed for these three).

N0: All records share the same probability = historical overall redemption rate
    from the frozen D labels. No per-record, no per-campaign variation.

N1: Activity-level probability model using ONLY S features
    (campaign_type, valid_days, coupon/product counts, top commodity weights,
    brand type weights). Does NOT use household X. All households in the same
    campaign receive the same score — so AP is an "activity rank" indicator only.

B1: Fixed business ranking (per 手册 step 15).
    Priority: prior redeemed > prior rate higher > recency_days smaller >
              basket_count higher > sales_total higher. Ties broken by
              fixed (household_key, campaign) hash.
"""
from __future__ import annotations

import csv
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from io_utils import (  # noqa: E402  # e1a_v2 local — not method_dev/
    E1_RUN,
    average_precision,
    brier,
    load_split,
    per_campaign_macro_ap,
)


# ---------- N0 ----------

def run_n0(D_labels: dict[str, int], splits=("D", "R", "T")) -> dict:
    rate = sum(D_labels.values()) / len(D_labels)
    out = {"method": "N0", "rate": rate}
    for sp in splits:
        try:
            feats, labs, links = load_split(sp)
        except FileNotFoundError as e:
            out[sp] = {"error": f"labels not available: {e}"}
            continue
        scores = [rate] * len(feats)
        labels = [labs[r["row_id"]] for r in feats]
        camps = [int(links[r["row_id"]]["campaign"]) for r in feats]
        ap = per_campaign_macro_ap(scores, labels, camps)
        out[sp] = {
            "n": len(scores),
            "macro_AP": ap["macro"],
            "Brier": brier(scores, labels),
            "per_campaign_AP": ap["per_campaign"],
        }
    return out


# ---------- N1 ----------

def _features_for_n1(row: dict) -> tuple[float, ...]:
    """Use ONLY S features; no household X, no campaign/household IDs."""
    f = []
    f.append(float(row.get("campaign_type", "") == "TypeC"))  # TypeB=0, TypeC=1
    f.append(float(row.get("campaign_valid_days", 0) or 0))
    f.append(float(row.get("campaign_unique_coupon_count", 0) or 0))
    f.append(float(row.get("campaign_unique_product_count", 0) or 0))
    f.append(float(row.get("campaign_department_count", 0) or 0))
    f.append(float(row.get("campaign_commodity_count", 0) or 0))
    f.append(float(row.get("campaign_brand_type_count", 0) or 0))
    f.append(float(row.get("campaign_unknown_taxonomy_flag", 0) or 0))
    for w in [1, 2, 3, 4, 5, 6, 7, 8]:
        col = f"campaign_top_commodity_{w}_weight"
        v = row.get(col, "") or 0
        try:
            f.append(float(v))
        except ValueError:
            f.append(0.0)
    f.append(float(row.get("campaign_other_commodity_weight", 0) or 0))
    return tuple(f)


def _campaign_key_for_n1(row: dict) -> str:
    """N1 collapses a row to its campaign. Use campaign type + dept/coupon/brand
    summary so two campaigns with the same shape are still treated as one.
    """
    return (
        f"{row.get('campaign_type','')}|{row.get('campaign_valid_days','')}|"
        f"{row.get('campaign_unique_coupon_count','')}|"
        f"{row.get('campaign_department_count','')}|"
        f"{row.get('campaign_brand_type_count','')}"
    )


def run_n1(D_feats: list[dict], D_labels: dict[str, int], splits=("D", "R", "T")) -> dict:
    """Logistic regression on S-only aggregated to one row per campaign.
    If logistic fails (e.g. singular), fall back to per-campaign positive rate.
    """
    from sklearn.linear_model import LogisticRegression

    # Aggregate D to per-campaign rows
    by_camp: dict[str, list[int]] = {}
    by_camp_features: dict[str, list[float]] = {}
    for r in D_feats:
        key = _campaign_key_for_n1(r)
        y = D_labels[r["row_id"]]
        if key not in by_camp:
            by_camp[key] = []
            by_camp_features[key] = list(_features_for_n1(r))
        by_camp[key].append(y)

    keys = sorted(by_camp)
    X = [by_camp_features[k] for k in keys]
    y_agg = [1 if sum(by_camp[k]) > 0 else 0 for k in keys]
    # If too few positives for stable fit, fall back to historical rate
    pos_count = sum(y_agg)
    out = {"method": "N1", "n_campaigns_D": len(keys), "n_positive_D": pos_count}

    try:
        if pos_count >= 2 and (len(y_agg) - pos_count) >= 2:
            model = LogisticRegression(C=1.0, max_iter=1000)
            model.fit(X, y_agg)
            predict_fn = lambda feats: float(model.predict_proba([_features_for_n1(r) for r in feats])[:, 1].mean()) \
                if False else None
            # Per-campaign score (same for all households in that campaign)
            camp_scores = {k: float(model.predict_proba([X[i]])[0, 1]) for i, k in enumerate(keys)}
        else:
            raise RuntimeError("too few positives")
    except Exception:
        # Fallback: per-campaign positive rate (or global rate if zero)
        rate = sum(D_labels.values()) / max(len(D_labels), 1)
        camp_scores = {k: (sum(by_camp[k]) / len(by_camp[k])) if by_camp[k] else rate for k in keys}

    out["per_campaign_score_D"] = {k: round(camp_scores[k], 6) for k in keys}

    for sp in splits:
        try:
            feats, labs, links = load_split(sp)
        except FileNotFoundError as e:
            out[sp] = {"error": f"labels not available: {e}"}
            continue
        scores, labels, camps = [], [], []
        for r in feats:
            key = _campaign_key_for_n1(r)
            scores.append(camp_scores.get(key, 0.0))
            labels.append(labs[r["row_id"]])
            camps.append(int(links[r["row_id"]]["campaign"]))
        ap = per_campaign_macro_ap(scores, labels, camps)
        out[sp] = {
            "n": len(scores),
            "macro_AP": ap["macro"],
            "Brier": brier(scores, labels),
            "per_campaign_AP": ap["per_campaign"],
        }
    return out


# ---------- B1 ----------

def _b1_score(row: dict, link: dict) -> tuple[int, ...]:
    """B1 priority tuple. Higher tuple = ranked first. Implementer's note: we
    return a numeric score where higher = more likely to redeem. Ties broken
    by a fixed hash of (household_key, campaign).
    """
    prior_redeemed = link.get("prior_redeemed_campaign_count", "0")
    prior_redeemed = int(prior_redeemed) if prior_redeemed else 0

    prior_rate = row.get("prior_redemption_rate", "")
    if prior_rate == "":
        # No prior history: lowest tier (handled separately below)
        prior_rate_v = -1.0
    else:
        try:
            prior_rate_v = float(prior_rate)
        except ValueError:
            prior_rate_v = -1.0

    recency = float(row.get("recency_days", 9999) or 9999)
    baskets = float(row.get("basket_count", 0) or 0)
    sales = float(row.get("sales_total", 0) or 0)

    # Score = encoded priority (use negative recency/sales so higher tuple = better)
    return (
        prior_redeemed,
        prior_rate_v,
        -recency,
        baskets,
        sales,
    )


def _hash_tiebreak(link: dict) -> int:
    h = hashlib.sha256(f"{link.get('household_key','')}|{link.get('campaign','')}".encode())
    return int.from_bytes(h.digest()[:4], "big")


def run_b1(splits=("D", "R", "T")) -> dict:
    out = {"method": "B1"}
    for sp in splits:
        try:
            feats, labs, links_map = load_split(sp)
        except FileNotFoundError as e:
            out[sp] = {"error": f"labels not available: {e}"}
            continue
        # Build score per row; preserve original order so we can map back
        scored = []
        labels, camps = [], []
        for i, r in enumerate(feats):
            link = links_map[r["row_id"]]
            score = _b1_score(r, link)
            tie = _hash_tiebreak(link)
            scored.append((score, tie, i))
            labels.append(labs[r["row_id"]])
            camps.append(int(link["campaign"]))
        scored.sort(key=lambda x: (-x[0][0], -x[0][1], -x[0][2], -x[0][3], -x[0][4], x[1]))
        # Assign rank-based score (higher rank = higher score)
        n = len(scored)
        rank_scores = [0.0] * n
        for rank, (_, _, orig_i) in enumerate(scored):
            rank_scores[orig_i] = (n - rank) / n  # (n-1)/n ... 1/n
        ap = per_campaign_macro_ap(rank_scores, labels, camps)
        out[sp] = {
            "n": n,
            "macro_AP": ap["macro"],
            "per_campaign_AP": ap["per_campaign"],
        }
    return out


# ---------- Main ----------

def main():
    """Compute N0/N1/B1 metrics on D/R only.

    Per 手册 §21: baselines do NOT run on T (T labels sealed). T prediction
    is the responsibility of e1a_v2/methods/run_T_predictions.py after
    the protocol freeze. We pass splits=("D","R") explicitly so this script
    never accidentally attempts to read sealed T labels via load_split("T").
    """
    D_feats, D_labels, _ = load_split("D")
    result = {
        "N0": run_n0(D_labels, splits=("D", "R")),
        "N1": run_n1(D_feats, D_labels, splits=("D", "R")),
        "B1": run_b1(splits=("D", "R")),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return result


if __name__ == "__main__":
    main()
