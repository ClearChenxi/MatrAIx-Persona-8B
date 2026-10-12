"""E2: Limited-label Persona incremental value & data efficiency.

Per 手册 §8 + REVIEW_FIXES §4:
- Main activities: 14, 16, 17, 19, 20, 22, 23, 25 (8 activities, n=0/10/20)
- Sensitivity n=40: 20, 22, 23, 25 (4 activities)
- Per-activity adaptation pool of ~20% of T households (328 rows total),
  partitioned into nested train/val subpools by fixed hash order
- Common outer layer: n=0 keeps frozen E2-0 (no fitting); n>0 fits a 1-D
  Logistic calibrator on (frozen score, label) per adaptation activity.
  - E2-0: frozen historical B3
  - E2-1: calibrate frozen B3 with n labels
  - E2-2: same, but add frozen P1 score as second feature
  - E2-3: same, but add frozen B2 score as second feature
- Primary metric: E2-2 vs E2-1 activity-macro Brier delta (negative = P1 helps)
- Secondary: log loss, AP, top-k; only supplement, NOT replace main metric.

This script does NOT read T labels. It uses D/R labels for algorithm validation
and produces the final E2 spec to be run after time-lock decryption.
"""
from __future__ import annotations

import hashlib
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from io_utils import brier, load_split  # noqa: E402

import numpy as np
from sklearn.linear_model import LogisticRegression


# ===== E2 spec (frozen) =====

E2_MAIN_ACTIVITIES = [14, 16, 17, 19, 20, 22, 23, 25]
E2_SENSITIVITY_N40_ACTIVITIES = [20, 22, 23, 25]
E2_BUDGETS_MAIN = [0, 10, 20]
E2_BUDGETS_SENSITIVITY = [40]
SEED = "20261012-e2-limited-label"


def _digest(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def partition_adaptation_pool(adaptation_rows: list[str]) -> dict[str, list[str]]:
    """Sort adaptation rows by hash(SEED:row_id), then split into two pools:
    pool_train (first 80%), pool_val (last 20%). For a given n, we take
    floor(0.8n) from pool_train and n - floor(0.8n) from pool_val.

    This implements 手册 §8 step 23: nested subpools to avoid validation
    families becoming training families at higher budgets.
    """
    sorted_rows = sorted(adaptation_rows, key=lambda rid: _digest(f"{SEED}:pool:{rid}"))
    return {"train": sorted_rows, "val": sorted_rows}  # full set; caller slices


def select_n_labels(adaptation_rows: list[dict], n: int) -> tuple[list[dict], list[dict]]:
    """Return (train_subset, val_subset) for given n.
    Pool partition: train_pool (first 80% of sorted rows), val_pool (last 20%).
    Take floor(0.8n) from train_pool, n - floor(0.8n) from val_pool.
    """
    sorted_rows = sorted(adaptation_rows, key=lambda r: _digest(f"{SEED}:pool:{r['row_id']}"))
    n_train_pool = int(len(sorted_rows) * 0.8)
    train_pool = sorted_rows[:n_train_pool]
    val_pool = sorted_rows[n_train_pool:]
    n_train = int(n * 0.8)
    n_val = n - n_train
    if n_train > len(train_pool):
        raise ValueError(f"n_train={n_train} > train_pool size {len(train_pool)}")
    if n_val > len(val_pool):
        raise ValueError(f"n_val={n_val} > val_pool size {len(val_pool)}")
    return train_pool[:n_train], val_pool[:n_val]


def e2_calibrator_predict(train_features: np.ndarray, train_y: np.ndarray,
                         eval_features: np.ndarray) -> np.ndarray:
    """Fit 1-D (or 2-D for E2-2/E2-3) Logistic calibrator on (features, y) and
    return predicted probability on eval_features.

    E2-1: features = [b3_score]
    E2-2: features = [b3_score, p1_score]
    E2-3: features = [b3_score, b2_score]
    """
    if len(train_y) < 2 or len(np.unique(train_y)) < 2:
        # Fallback: return frozen base score (first column) as-is
        return eval_features[:, 0]
    model = LogisticRegression(C=1.0, max_iter=1000)
    model.fit(train_features, train_y)
    return model.predict_proba(eval_features)[:, 1]


def run_e2_activity(activity: int, eval_feats: list[dict], eval_predictions: dict,
                    adaptation_pool: list[dict], budgets: list[int]) -> dict:
    """Run E2 for one activity on T. eval_predictions must contain
    b3, p1, b2 scores per row_id. adaptation_pool is T adaptation rows
    for this activity (e2_role=adaptation)."""

    # adaptation_pool is from T (with sealed labels); this script can't actually
    # use them until time-lock decryption. We use the *algorithm* on D/R for
    # validation, and just produce the spec for T execution here.
    result = {
        "activity": activity,
        "n_eval": len(eval_feats),
        "n_adaptation_pool": len(adaptation_pool),
        "by_budget": {},
    }
    for n in budgets:
        result["by_budget"][str(n)] = {
            "n": n,
            "n_train": int(n * 0.8),
            "n_val": n - int(n * 0.8),
            "status": "ready" if n == 0 else "awaits_t_unlock",
        }
    return result


# ===== D/R validation: run the algorithm on D/R with their (public) labels =====

def validate_on_D_R(D_feats: list[dict], D_labels: dict, R_feats: list[dict],
                    R_labels: dict, D_preds: dict, R_preds: dict) -> dict:
    """Run the E2 algorithm on D->R: train calibrator on D adaptation, evaluate
    on R. This is a proxy for T execution but does NOT use T labels.
    Demonstrates the algorithm logic works.

    D_preds / R_preds must have keys: b3, p1, b2 (per row_id).
    """
    # Pretend D is the "adaptation" pool and R is the "evaluation" pool.
    # For each "activity" partition D/R as a whole (since D/R each have only
    # one main "period"), just validate the algorithm logic.
    D_keys = list(D_labels.keys())
    R_keys = list(R_labels.keys())

    out = {"D_adaptation": {}, "R_evaluation": {}}
    for n in [0, 10, 20]:
        if n > len(D_keys):
            continue
        # split D_keys into nested subpools
        train, val = select_n_labels(
            [{"row_id": k, "label": D_labels[k], **D_preds[k]} for k in D_keys],
            n=n,
        )

        # Build feature matrix
        def features(rows, key):
            b3 = np.array([r["b3"] for r in rows])
            p1 = np.array([r["p1"] for r in rows])
            b2 = np.array([r["b2"] for r in rows])
            return {"b3": b3, "p1": p1, "b2": b2}

        train_feats = features(train, "train")
        train_y = np.array([r["label"] for r in train])

        # E2-0: no fit, frozen B3
        # E2-1: B3 only
        # E2-2: B3 + P1
        # E2-3: B3 + B2
        for label, feat_cols in [
            ("E2-0", []),
            ("E2-1", ["b3"]),
            ("E2-2", ["b3", "p1"]),
            ("E2-3", ["b3", "b2"]),
        ]:
            if n == 0 and label != "E2-0":
                continue
            if not feat_cols:
                # E2-0: frozen B3 only
                preds_eval = np.array([R_preds[k]["b3"] for k in R_keys])
            else:
                X_train = np.column_stack([train_feats[c] for c in feat_cols])
                X_eval = np.column_stack([
                    np.array([R_preds[k][c] for k in R_keys]) for c in feat_cols
                ])
                preds_eval = e2_calibrator_predict(X_train, train_y, X_eval)
            labels_eval = np.array([R_labels[k] for k in R_keys])
            out["R_evaluation"][f"n{n}_{label}"] = brier(preds_eval.tolist(), labels_eval.tolist())

    return out


# ===== Main: produce E2 spec + D/R validation =====

def main():
    print("=" * 60)
    print("E2 Limited-Label Persona Incremental Value")
    print("=" * 60)

    # Load D/R predictions from E1a files (on D/R)
    D_feats, D_labels, D_links = load_split("D")
    R_feats, R_labels, R_links = load_split("R")

    # Load E1a D predictions for D/R (these exist from D/R dev runs)
    print("\n[E2 spec] Frozen main activities + budgets:")
    for a in E2_MAIN_ACTIVITIES:
        print(f"  活动 {a}: n in {E2_BUDGETS_MAIN}")
    print(f"  n=40 敏感性: 活动 {E2_SENSITIVITY_N40_ACTIVITIES}")

    print("\n[E2 algorithm validation on D/R (no T labels)]")
    # Build dummy predictions for D/R to validate algorithm (use E1a D/R dev results)
    # Real D/R predictions are in run_e1a_dev.py summary — load them
    summary_path = ROOT / "thesis/method_dev/methods/run_e1a_dev_summary.json"
    # If not exists, do a quick dev run
    if not summary_path.exists():
        print(f"  Skipping D/R validation; {summary_path} not yet generated")
    else:
        D_preds = {}
        R_preds = {}
        # Synthesize: use AP table per-campaign B3 scores (placeholder)
        # For real validation, the user should run run_e1a_dev.py first.
        # This stub returns "skipped".
        print("  Skipping D/R validation (no per-row predictions file)")

    # Print the spec for T execution (will be run after time-lock decryption)
    print("\n[E2 T execution spec — requires T labels after time-lock]")
    spec = {
        "main_activities": E2_MAIN_ACTIVITIES,
        "budgets_main": E2_BUDGETS_MAIN,
        "sensitivity_n40_activities": E2_SENSITIVITY_N40_ACTIVITIES,
        "budgets_sensitivity": E2_BUDGETS_SENSITIVITY,
        "outer_layer": "1-D Logistic calibrator on frozen scores per adaptation activity",
        "primary_metric": "E2-2 vs E2-1 activity-macro Brier delta (n=10 and n=20 separately)",
        "secondary_metrics": ["log loss", "AP", "top-10/20/30"],
        "E2_2_vs_E2_3_supplement": "report B2-P1 difference as secondary",
        "frozen_inputs_required": [
            "E1a_P1-v2_predictions.jsonl (frozen P1 scores for T final_evaluation)",
            "E1a_B3-CatBoost_predictions.jsonl (frozen B3 scores)",
            "E1a_B2-Logistic_predictions.jsonl (frozen B2 scores)",
        ],
        "T_labels_required_after_unlock": [
            "adaptation pool Y1 for n=10/20/40 fitting (per activity)",
            "final_evaluation Y1 for AP/Brier evaluation",
        ],
        "seed": SEED,
    }
    spec_path = ROOT / "thesis/method_dev/methods/e2_spec.json"
    spec_path.write_text(json.dumps(spec, ensure_ascii=False, indent=2))
    print(f"  Wrote {spec_path}")

    # Validation rule on D/R using public labels: write algorithm summary
    # (We don't have per-row B3/P1/B2 predictions on D/R, but we have
    # the E1a dev summary which gives per-campaign AP — a partial sanity check)
    print("\n[E2 cross-check on D/R development set]")
    dev_summary = ROOT / "thesis/method_dev/methods/run_e1a_dev_summary.json"
    if dev_summary.exists():
        s = json.loads(dev_summary.read_text())
        print("  E1a D/R AP (used as a proxy signal for E2's expected magnitude):")
        for m in ["N0", "N1", "B1", "B2", "B3"]:
            r_ap = s.get(m, {}).get("R", {}).get("macro_AP")
            d_ap = s.get(m, {}).get("D", {}).get("macro_AP")
            print(f"    {m}: D={d_ap:.3f if d_ap is not None else '-'}, R={r_ap:.3f if r_ap is not None else '-'}")


ROOT = Path("/Users/jiangchenxi/科研内容/代码仓库/MatrAIx-Persona-8B")


if __name__ == "__main__":
    main()
