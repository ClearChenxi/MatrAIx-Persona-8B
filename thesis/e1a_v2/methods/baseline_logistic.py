# ---------------------------------------------------------------------------
# MOVED FROM: thesis/method_dev/methods/baseline_logistic.py
# DATE:       2026-10-13
# REASON:     Baseline N0/N1/B1/B2/B3 是真实传统方法实现，与 method_dev/ 的
#             LLM 调用上下文无关，可安全复用。本文件已脱离 method_dev/ 调用链。
# ISOLATION:  本文件不再 import method_dev/ 下任何模块；不接受 method_dev/ 的
#             调用方反向引用。审计方法见 ../ISOLATION.md。
# COMPATIBILITY: load_split / E1_RUN 常量已在 io_utils.py 中改为相对 thesis/
#                仓库根解析，不再硬编码 method_dev 路径。import 时如发现 E1_RUN
#                解析异常，请核对 thesis/e1_data/runs/<run-id>/ 是否存在。
# ---------------------------------------------------------------------------

"""B2: L2 Logistic Regression on the same 56-field X/S schema.

Per 手册 step 16, candidates are L2-only Logistic with C in {0.01, 0.1, 1, 10}.
Selection on D activity-macro AP via time-based holdout (later campaigns
for validation). Tie-break: stronger regularization.
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
    to_tabular,
    top_departments_from_d,
)

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler


# Per 手册 step 16: D 内按时间留出做选候选
# D 含活动 27/28/29/1/2/3/4/5/6/7（START_DAY 237–398）
# 时间留出：训练 27/28/29（START 237–281），验证 1/2/3/4/5/6/7（START 346–398）
TRAIN_CAMPAIGNS_D = {"27", "28", "29"}
VALID_CAMPAIGNS_D = {"1", "2", "3", "4", "5", "6", "7"}

C_GRID = [0.01, 0.1, 1.0, 10.0]


def _split_train_valid(feats: list[dict], labels: dict, links: dict):
    X_train_rows, y_train, X_valid_rows, y_valid = [], [], [], []
    for r in feats:
        c = links[r["row_id"]]["campaign"]
        y = labels[r["row_id"]]
        if c in TRAIN_CAMPAIGNS_D:
            X_train_rows.append(r)
            y_train.append(y)
        elif c in VALID_CAMPAIGNS_D:
            X_valid_rows.append(r)
            y_valid.append(y)
    return X_train_rows, y_train, X_valid_rows, y_valid


def _fit_predict(C: float, train_rows, y_train, all_rows, top_deps):
    X_train, _ = to_tabular(train_rows, top_deps)
    X_all, _ = to_tabular(all_rows, top_deps)
    scaler = StandardScaler()
    X_train_s = scaler.fit_transform(X_train)
    X_all_s = scaler.transform(X_all)
    model = LogisticRegression(C=C, max_iter=2000, solver="lbfgs")
    model.fit(X_train_s, y_train)
    return model.predict_proba(X_all_s)[:, 1]


def _score_camp_ap(scores, labels, camps):
    return per_campaign_macro_ap(scores, labels, camps)["macro"]


def run_logistic(splits=("D", "R", "T")) -> dict:
    # Load all D for top-departments + train/valid
    D_feats, D_labels, D_links = load_split("D")
    top_deps = top_departments_from_d(D_feats, n=20)
    Xtr, ytr, Xva, yva = _split_train_valid(D_feats, D_labels, D_links)
    va_camps = [int(D_links[r["row_id"]]["campaign"]) for r in Xva]

    # Candidate selection on D time-holdout
    results = {}
    for C in C_GRID:
        scores = _fit_predict(C, Xtr, ytr, Xva, top_deps)
        ap = _score_camp_ap(scores, yva, va_camps)
        results[C] = ap

    # Tie-break: smaller AP diff <= 0.0001 -> pick stronger reg (smaller C)
    best_ap = max(results.values())
    chosen = None
    for C in C_GRID:  # increasing C, so first (smallest C) wins ties
        if abs(results[C] - best_ap) <= 1e-4:
            chosen = C
            break
    if chosen is None:
        chosen = C_GRID[int(np.argmax([results[c] for c in C_GRID]))]

    # Refit on FULL D with chosen C, evaluate on each split
    out = {
        "method": "B2-Logistic",
        "candidate_AP_on_D_holdout": {str(k): v for k, v in results.items()},
        "chosen_C": chosen,
        "n_train_full_D": len(D_feats),
        "n_features": None,
    }
    # Refit on all D
    full_scores = _fit_predict(chosen, D_feats, [D_labels[r["row_id"]] for r in D_feats], D_feats, top_deps)
    X_d, cols = to_tabular(D_feats, top_deps)
    out["n_features"] = len(cols)

    for sp in splits:
        try:
            feats, labs, links = load_split(sp)
        except FileNotFoundError as e:
            out[sp] = {"error": f"labels not available: {e}"}
            continue
        scores = _fit_predict(chosen, D_feats, [D_labels[r["row_id"]] for r in D_feats], feats, top_deps)
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
    print(json.dumps(run_logistic(), ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
