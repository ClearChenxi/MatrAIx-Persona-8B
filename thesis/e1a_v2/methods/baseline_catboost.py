# ---------------------------------------------------------------------------
# MOVED FROM: thesis/method_dev/methods/baseline_catboost.py
# DATE:       2026-10-13
# REASON:     Baseline N0/N1/B1/B2/B3 是真实传统方法实现，与 method_dev/ 的
#             LLM 调用上下文无关，可安全复用。本文件已脱离 method_dev/ 调用链。
# ISOLATION:  本文件不再 import method_dev/ 下任何模块；不接受 method_dev/ 的
#             调用方反向引用。审计方法见 ../ISOLATION.md。
# COMPATIBILITY: load_split / E1_RUN 常量已在 io_utils.py 中改为相对 thesis/
#                仓库根解析，不再硬编码 method_dev 路径。import 时如发现 E1_RUN
#                解析异常，请核对 thesis/e1_data/runs/<run-id>/ 是否存在。
# ---------------------------------------------------------------------------

"""B3: CatBoost on the same 56-field X/S schema.

Per 手册 step 16, candidates are depth {3, 5} x learning_rate {0.03, 0.1},
up to 1000 rounds, validation patience 50. Selection on D time-holdout
AP; tie -> shallower model.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from io_utils import (  # noqa: E402  # e1a_v2 local — not method_dev/
    brier,
    load_split,
    per_campaign_macro_ap,
)

import numpy as np
import pandas as pd
from catboost import CatBoostClassifier


TRAIN_CAMPAIGNS_D = {27, 28, 29}
VALID_CAMPAIGNS_D = {1, 2, 3, 4, 5, 6, 7}

# Candidates per 手册 step 16
DEPTHS = [3, 5]
LRS = [0.03, 0.1]
MAX_ROUNDS = 1000
PATIENCE = 50


def _build_pool(rows, top_deps):
    """Build a pandas DataFrame for CatBoost. Numeric cols as float (NaN sentinel
    = -1.0), categorical cols as object/str so CatBoost auto-detects them.
    """
    NUM_MISSING = {"prior_redemption_rate"}
    base_num_cols = [
        "history_window_days", "recency_days", "basket_count", "shopping_day_count",
        "positive_line_count", "return_line_count", "sales_total", "average_basket_sales",
        "zero_sales_total_flag", "retail_discount_basket_share", "coupon_discount_basket_share",
        "prior_completed_campaign_count", "prior_redeemed_campaign_count", "prior_redemption_rate",
        "no_prior_campaign_flag",
        "top_commodity_1_share", "top_commodity_2_share", "top_commodity_3_share", "other_commodity_share",
        "product_taxonomy_missing_flag",
        "campaign_valid_days", "campaign_unique_coupon_count",
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
    dep_cols = [f"dep_spend__{d}" for d in top_deps]
    num_cols = base_num_cols + dep_cols
    cat_cols = ["campaign_type", "top_commodity_1", "top_commodity_2", "top_commodity_3"]

    data: dict[str, list] = {c: [] for c in num_cols + cat_cols}
    for r in rows:
        for c in base_num_cols:
            v = r.get(c, "")
            if v == "":
                data[c].append(np.nan)
            else:
                try:
                    f = float(v)
                    if c in NUM_MISSING and (f != f):
                        data[c].append(np.nan)
                    else:
                        data[c].append(f)
                except ValueError:
                    data[c].append(np.nan)
        # Department shares (one column per top dep)
        dep = r.get("department_spend_shares_json", {}) or {}
        for d in top_deps:
            try:
                data[f"dep_spend__{d}"].append(float(dep.get(d, 0.0)))
            except (ValueError, TypeError):
                data[f"dep_spend__{d}"].append(0.0)
        # Categoricals
        for c in cat_cols:
            data[c].append(r.get(c, "") or "")

    df = pd.DataFrame(data)
    for c in num_cols:
        df[c] = df[c].fillna(-1.0)
    return df, cat_cols


def _score_camp_ap(scores, labels, camps):
    return per_campaign_macro_ap(scores, labels, camps)["macro"]


def run_catboost(splits=("D", "R", "T")) -> dict:
    from io_utils import top_departments_from_d
    D_feats, D_labels, D_links = load_split("D")
    top_deps = top_departments_from_d(D_feats, n=20)
    D_df, cat_cols = _build_pool(D_feats, top_deps)
    D_y = np.array([D_labels[r["row_id"]] for r in D_feats], dtype=np.int32)
    D_camps = np.array([int(D_links[r["row_id"]]["campaign"]) for r in D_feats])

    # Time-holdout split
    train_mask = np.isin(D_camps, list(TRAIN_CAMPAIGNS_D))
    valid_mask = np.isin(D_camps, list(VALID_CAMPAIGNS_D))
    Xtr, ytr = D_df.iloc[train_mask], D_y[train_mask]
    Xva, yva = D_df.iloc[valid_mask], D_y[valid_mask]
    va_camps = D_camps[valid_mask]

    # Candidate selection
    results = {}
    best_params = None
    for d in DEPTHS:
        for lr in LRS:
            model = CatBoostClassifier(
                iterations=MAX_ROUNDS,
                depth=d,
                learning_rate=lr,
                loss_function="Logloss",
                eval_metric="Logloss",
                random_seed=20261012,
                cat_features=cat_cols,
                early_stopping_rounds=PATIENCE,
                verbose=False,
            )
            model.fit(Xtr, ytr, cat_features=cat_cols)
            scores = model.predict_proba(Xva)[:, 1]
            ap = _score_camp_ap(scores, yva.tolist(), va_camps.tolist())
            key = f"depth={d}_lr={lr}"
            results[key] = ap
            if best_params is None or ap > best_params["ap"]:
                best_params = {"key": key, "depth": d, "lr": lr, "ap": ap}

    # Tie-break: shallower model
    best_ap = best_params["ap"]
    chosen = None
    for d in DEPTHS:  # shallow first
        for lr in LRS:
            key = f"depth={d}_lr={lr}"
            if abs(results[key] - best_ap) <= 1e-4:
                chosen = {"depth": d, "lr": lr, "key": key}
                break
        if chosen:
            break

    # Refit on FULL D with chosen params, then evaluate on R / T
    out = {
        "method": "B3-CatBoost",
        "candidate_AP_on_D_holdout": results,
        "chosen": chosen,
        "n_features": D_df.shape[1],
        "n_train_full_D": int(len(D_feats)),
    }

    final_model = CatBoostClassifier(
        iterations=MAX_ROUNDS,
        depth=chosen["depth"],
        learning_rate=chosen["lr"],
        loss_function="Logloss",
        random_seed=20261012,
        cat_features=cat_cols,
        verbose=False,
    )
    final_model.fit(D_df, D_y, cat_features=cat_cols)

    for sp in splits:
        try:
            feats, labs, links = load_split(sp)
        except FileNotFoundError as e:
            out[sp] = {"error": f"labels not available: {e}"}
            continue
        sp_df, _ = _build_pool(feats, top_deps)
        # Ensure same column order (cat features especially)
        sp_df = sp_df[D_df.columns]
        scores = final_model.predict_proba(sp_df)[:, 1]
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


def main():
    print(json.dumps(run_catboost(), ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
