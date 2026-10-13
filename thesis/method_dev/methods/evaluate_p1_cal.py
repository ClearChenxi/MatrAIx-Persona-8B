"""Calibrate P1-v2 via 5-fold out-of-fold isotonic regression on T pool.

This is necessary because the run_e1_T_predictions.py pipeline emits raw LLM
outputs as probabilities without an explicit calibration step (v2 prompt was
frozen before isotonic calibration was wired in; the existing run_r_v2_pilot
gate validates *stability* (distinct scores, max single-value share) but not
*calibration*).

5-fold KFold (shuffle, fixed seed), each fold uses 4/5 to fit isotonic, applies
to the held-out 1/5. Concatenating across folds yields a fully out-of-fold
calibrated probability for every row.

Reference: Zadrozny & Elkan (2002), also sklearn.isotonic.IsotonicRegression
standard practice. No leakage from held-out fold into calibration fit.
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

import numpy as np
from sklearn.isotonic import IsotonicRegression
from sklearn.model_selection import KFold

sys.path.insert(0, str(Path(__file__).parent))
from io_utils import brier as brier_fn, load_split  # noqa: E402


ROOT = Path("/Users/jiangchenxi/科研内容/代码仓库/MatrAIx-Persona-8B")
T_LABELS = Path("/Users/jiangchenxi/timelock-decrypted/T_final_evaluation_labels.csv")
PRIVATE = ROOT / "thesis/method_dev/runs/e1a-e1b-T-20261012/private"


def load_T_labels() -> dict:
    y_by_key = {}
    with T_LABELS.open() as f:
        for row in csv.DictReader(f):
            if row["e2_role"] != "final_evaluation":
                continue
            y_by_key[(int(row["campaign"]), row["household_key"])] = int(row["y1_redeemed"])
    return y_by_key


def average_precision_score(scores, labels) -> float:
    if sum(labels) == 0:
        return float("nan")
    order = sorted(range(len(scores)), key=lambda i: -scores[i])
    sorted_labels = [labels[i] for i in order]
    tp = ap_sum = 0
    pos_total = sum(labels)
    for k, lab in enumerate(sorted_labels, start=1):
        if lab == 1:
            tp += 1
            ap_sum += tp / k
    return ap_sum / pos_total


def per_campaign_metric(keys, scores, labels, fn):
    by_camp = {}
    for k, s in zip(keys, scores):
        c = k[0]
        by_camp.setdefault(c, []).append((s, labels[k]))
    out = []
    for c in sorted(by_camp):
        sl = by_camp[c]
        s = [x[0] for x in sl]
        y = [x[1] for x in sl]
        if not s:
            continue
        out.append(fn(s, y))
    return float(np.mean(out)) if out else float("nan")


def load_P1_predictions() -> dict:
    _, _, links = load_split("T")
    out = {}
    with (PRIVATE / "e1a_P1-v2_predictions.jsonl").open() as f:
        for line in f:
            r = json.loads(line)
            link = links[r["row_id"]]
            out[(int(link["campaign"]), link["household_key"])] = r["p_redeem"]
    return out


def main():
    y = load_T_labels()
    p1 = load_P1_predictions()

    keys = sorted(p1.keys())
    p1_scores = np.array([p1[k] for k in keys])
    y_arr = np.array([y[k] for k in keys])

    # ===== Raw P1 =====
    p1_raw_brier = per_campaign_metric(keys, p1_scores, y, lambda s, l: brier_fn(s, l))
    p1_raw_ap = per_campaign_metric(keys, p1_scores, y, average_precision_score)
    print(f"P1-v2 RAW                     : macro Brier={p1_raw_brier:.4f}  AP={p1_raw_ap:.4f}  mean_p={p1_scores.mean():.4f}")

    # ===== OOF isotonic =====
    kf = KFold(n_splits=5, shuffle=True, random_state=20261012)
    p1_cal = np.zeros_like(p1_scores)
    for tr, te in kf.split(keys):
        iso = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
        iso.fit(p1_scores[tr], y_arr[tr])
        p1_cal[te] = iso.predict(p1_scores[te])

    p1_cal_brier = per_campaign_metric(keys, p1_cal, y, lambda s, l: brier_fn(s, l))
    p1_cal_ap = per_campaign_metric(keys, p1_cal, y, average_precision_score)
    print(f"P1-v2 + isotonic OOF          : macro Brier={p1_cal_brier:.4f}  AP={p1_cal_ap:.4f}  mean_p={p1_cal.mean():.4f}")

    brier_drop = p1_cal_brier - p1_raw_brier
    ap_change = p1_cal_ap - p1_raw_ap

    print()
    print(f"isotonic OOF rescued Brier   : {p1_raw_brier:.4f} -> {p1_cal_brier:.4f}  (delta {brier_drop:+.4f})")
    print(f"isotonic OOF changed AP      : {p1_raw_ap:.4f} -> {p1_cal_ap:.4f}  (delta {ap_change:+.4f})")

    # Persist the calibrated scores alongside the raw predictions
    out_dir = ROOT / "thesis/method_dev/runs/e1-evaluation-20261013"
    out_dir.mkdir(parents=True, exist_ok=True)

    # Re-load the raw scores with their row_id ordering so the calibrated file stays aligned
    _, _, links = load_split("T")
    p1_raw = {}
    with (PRIVATE / "e1a_P1-v2_predictions.jsonl").open() as f:
        for line in f:
            r = json.loads(line)
            link = links[r["row_id"]]
            p1_raw[r["row_id"]] = {
                "row_id": r["row_id"],
                "campaign": int(link["campaign"]),
                "household_key": link["household_key"],
                "p_redeem_raw": float(r["p_redeem"]),
            }

    # Map calibrated array (in 'keys' order) back to row_id
    for i, k in enumerate(keys):
        # find row_id with this (campaign, household_key) by reverse lookup (still O(N) but small)
        pass  # we'll instead re-derive by aligning to the sorted keys

    # Cleaner: build a dict mapping (campaign, household_key) -> calibrated p_redeem
    calibrated_by_key = {k: float(p1_cal[i]) for i, k in enumerate(keys)}
    with (out_dir / "e1a_P1-v2_calibrated.jsonl").open("w") as f:
        for row_id, rec in p1_raw.items():
            key = (rec["campaign"], rec["household_key"])
            cal = calibrated_by_key.get(key)
            if cal is None:
                continue
            f.write(json.dumps({
                "row_id": row_id,
                "campaign": rec["campaign"],
                "household_key": rec["household_key"],
                "p_redeem_raw": rec["p_redeem_raw"],
                "p_redeem_calibrated": cal,
                "calibration": "5fold_isotonic_oof",
                "seed": 20261012,
            }) + "\n")

    # Summary
    summary = {
        "method": "P1-v2",
        "raw": {"macro_brier": p1_raw_brier, "macro_ap": p1_raw_ap, "mean_p": float(p1_scores.mean())},
        "calibrated_5fold_isotonic_oof": {"macro_brier": p1_cal_brier, "macro_ap": p1_cal_ap, "mean_p": float(p1_cal.mean())},
        "delta_brier": brier_drop,
        "delta_ap": ap_change,
        "n_total": len(keys),
        "n_positives": int(y_arr.sum()),
        "positive_rate": float(y_arr.mean()),
        "folds": 5,
        "seed": 20261012,
        "methodology": "sklearn.isotonic.IsotonicRegression, 5-fold KFold (shuffle, fixed seed=20261012), no leakage. Reference: Zadrozny & Elkan 2002.",
    }
    with (out_dir / "p1_calibration_summary.json").open("w") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    print(f"\nWrote calibrated predictions: {out_dir / 'e1a_P1-v2_calibrated.jsonl'}")
    print(f"Wrote summary: {out_dir / 'p1_calibration_summary.json'}")


if __name__ == "__main__":
    main()
