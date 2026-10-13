"""Evaluate E1a + E1b predictions against decrypted T final_evaluation labels.

Driven by the public-timelock drand round 33019012 unlock at
2026-10-13T03:00:00Z. Run after tle decryption of
T_final_evaluation_labels.csv.

Computes:
- per-method per-campaign AP, Brier
- per-method aggregate (10-campaign macro)
- E1a chain comparisons (层次 1 / 2)
- E1b chain comparisons (层次 3: L1-L0, L2-L1, P1-L2, P1-P2, P1-P3)
"""

from __future__ import annotations

import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from io_utils import brier, load_split  # noqa: E402


ROOT = Path("/Users/jiangchenxi/科研内容/代码仓库/MatrAIx-Persona-8B")
PRIVATE = ROOT / "thesis/method_dev/runs/e1a-e1b-T-20261012/private"
T_LABELS = Path("/Users/jiangchenxi/timelock-decrypted/T_final_evaluation_labels.csv")

E1A_METHODS = [
    ("E1a_N0", "N0"),
    ("E1a_N1", "N1"),
    ("E1a_B1", "B1"),
    ("E1a_B2-Logistic", "B2"),
    ("E1a_B3-CatBoost", "B3"),
    ("E1a_P1-v2", "P1"),
]
E1B_METHODS = [
    ("E1b_L0", "L0"),
    ("E1b_L1", "L1"),
    ("E1b_L2", "L2"),
    ("E1b_P2", "P2"),
    ("E1b_P3", "P3"),
]

CAMPAIGNS = [14, 15, 16, 17, 19, 20, 21, 22, 23, 25]


def read_jsonl_predictions(path: Path) -> dict[str, dict]:
    """Return {row_id: {...}}."""
    out: dict[str, dict] = {}
    with path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            out[row["row_id"]] = row
    return out


def read_decrypted_T_labels(path: Path) -> dict[tuple[int, str], int]:
    """Return {(campaign, household_key): y1_redeemed} for final_evaluation rows."""
    out: dict[tuple[int, str], int] = {}
    with path.open() as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("e2_role") != "final_evaluation":
                continue
            campaign = int(row["campaign"])
            hk = row["household_key"]
            y = int(row["y1_redeemed"])
            out[(campaign, hk)] = y
    return out


def load_final_pool_links() -> dict[str, tuple[int, str]]:
    """Return {row_id: (campaign, household_key)} for T final_evaluation rows."""
    feats_T, _, links_T = load_split("T")
    out: dict[str, tuple[int, str]] = {}
    for r in feats_T:
        link = links_T[r["row_id"]]
        if link.get("e2_role") == "final_evaluation":
            out[r["row_id"]] = (int(link["campaign"]), link["household_key"])
    return out


def average_precision_score(scores: list[float], labels: list[int]) -> float:
    """Average precision (area under PR curve) computed manually."""
    if not labels or sum(labels) == 0:
        return float("nan")
    order = sorted(range(len(scores)), key=lambda i: -scores[i])
    sorted_labels = [labels[i] for i in order]
    tp = fp = 0
    ap_sum = 0.0
    pos_total = sum(labels)
    for k, lab in enumerate(sorted_labels, start=1):
        if lab == 1:
            tp += 1
            ap_sum += tp / k
        else:
            fp += 1
    return ap_sum / pos_total


def per_campaign_ap_brier(scores: dict, labels: dict[tuple[int, str], int],
                          links: dict[str, tuple[int, str]]) -> dict[int, dict[str, float]]:
    """Return {campaign: {"n": int, "pos": int, "ap": float, "brier": float}}."""
    by_camp: dict[int, dict[str, list]] = defaultdict(lambda: {"scores": [], "labels": []})
    for row_id, pred in scores.items():
        if row_id not in links:
            continue
        campaign, hk = links[row_id]
        y = labels.get((campaign, hk))
        if y is None:
            continue
        score = pred.get("p_redeem")
        if score is None or not np.isfinite(score):
            continue
        by_camp[campaign]["scores"].append(score)
        by_camp[campaign]["labels"].append(y)
    out: dict[int, dict[str, float]] = {}
    for campaign, data in by_camp.items():
        s = data["scores"]
        y = data["labels"]
        if not s:
            continue
        b = brier(s, y)
        a = average_precision_score(s, y)
        out[campaign] = {"n": len(s), "pos": sum(y), "ap": a, "brier": b}
    return out


def aggregate_macro(campaign_results: dict[int, dict[str, float]]) -> dict[str, float]:
    """Per-method macro across campaigns (unweighted mean over the 10 campaigns)."""
    cs = [c for c in CAMPAIGNS if c in campaign_results]
    if not cs:
        return {"ap": float("nan"), "brier": float("nan"), "n_camp": 0}
    ap_sum = sum(campaign_results[c]["ap"] for c in cs)
    b_sum = sum(campaign_results[c]["brier"] for c in cs)
    return {"ap": ap_sum / len(cs), "brier": b_sum / len(cs), "n_camp": len(cs)}


def main():
    if not T_LABELS.exists():
        print(f"FATAL: T labels not decrypted yet — {T_LABELS} missing")
        print("Run: tle --decrypt -o T_final_evaluation_labels.csv /Users/jiangchenxi/timelock-package/T_final_evaluation_labels.csv.tle")
        sys.exit(1)
    print(f"Reading decrypted T labels from: {T_LABELS}")
    labels = read_decrypted_T_labels(T_LABELS)
    print(f"  loaded {len(labels)} final_evaluation rows")

    print(f"Loading T links (campaign + household_key from e1_data run)")
    links = load_final_pool_links()
    print(f"  loaded {len(links)} final_evaluation row links")

    rows = []
    by_method: dict[str, dict] = {}
    for artifact_id, label in E1A_METHODS + E1B_METHODS:
        path = PRIVATE / f"{artifact_id}_predictions.jsonl"
        if not path.exists():
            print(f"WARN: missing {path}")
            continue
        scores = read_jsonl_predictions(path)
        per_camp = per_campaign_ap_brier(scores, labels, links)
        macro = aggregate_macro(per_camp)
        by_method[artifact_id] = {"label": label, "per_camp": per_camp, "macro": macro}
        print(f"  {artifact_id:25s}  macro AP={macro['ap']:.4f}  Brier={macro['brier']:.4f}  ({macro['n_camp']} camp)")
        rows.append({
            "artifact_id": artifact_id,
            "label": label,
            "set": artifact_id.split("_")[0],
            "macro_AP": round(macro["ap"], 4),
            "macro_Brier": round(macro["brier"], 4),
            "n_camp": macro["n_camp"],
        })

    print()
    print("=" * 70)
    print("Per-method macro (10-campaign unweighted mean)")
    print("=" * 70)
    for r in rows:
        print(f"  {r['artifact_id']:25s} ({r['set']:4s})  AP={r['macro_AP']:.4f}  Brier={r['macro_Brier']:.4f}")

    print()
    print("=" * 70)
    print("Chain comparisons (层次 1/2/3)")
    print("=" * 70)
    pairs = [
        ("E1a 层次1 (基础效度): P1 - N0 (positive = P1 wins)", "E1a_P1-v2", "E1a_N0"),
        ("E1a 层次1 (基础效度): P1 - N1 (positive = P1 wins)", "E1a_P1-v2", "E1a_N1"),
        ("E1a 层次2 (传统比较): P1 - B1 (positive = P1 wins)", "E1a_P1-v2", "E1a_B1"),
        ("E1a 层次2 (传统比较): P1 - B2-Logistic (positive = P1 wins)", "E1a_P1-v2", "E1a_B2-Logistic"),
        ("E1a 层次2 (传统比较): P1 - B3-CatBoost (positive = P1 wins)", "E1a_P1-v2", "E1a_B3-CatBoost"),
        ("E1b 层次3: L1 - L0 (informational gain from customer features)", "E1b_L1", "E1b_L0"),
        ("E1b 层次3: L2 - L1 (persona text gain)", "E1b_L2", "E1b_L1"),
        ("E1b 层次3: P1 - L2 (agent workflow gain)", "E1a_P1-v2", "E1b_L2"),
        ("E1b 层次3: P1 - P2 (correct customer-persona pairing)", "E1a_P1-v2", "E1b_P2"),
        ("E1b 层次3: P1 - P3 (rich-behavior-field gain)", "E1a_P1-v2", "E1b_P3"),
    ]
    for desc, m1, m2 in pairs:
        if m1 not in by_method or m2 not in by_method:
            continue
        ap_diff = by_method[m1]["macro"]["ap"] - by_method[m2]["macro"]["ap"]
        b_diff = by_method[m1]["macro"]["brier"] - by_method[m2]["macro"]["brier"]
        print(f"  {desc}")
        print(f"    AP   : {by_method[m1]['label']} - {by_method[m2]['label']} = {ap_diff:+.4f}  ({'P1 wins' if ap_diff > 0 else 'm1 loses/ties'})")
        print(f"    Brier: {by_method[m1]['label']} - {by_method[m2]['label']} = {b_diff:+.4f}  ({'P1 wins' if b_diff < 0 else 'm1 loses/ties'})")

    # write summary
    out_dir = ROOT / "thesis/method_dev/runs/e1-evaluation-20261013"
    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / "e1a_e1b_summary.json").open("w") as f:
        json.dump({
            "source_T_labels": str(T_LABELS),
            "source_T_labels_sha256": __import__('hashlib').sha256(T_LABELS.read_bytes()).hexdigest(),
            "methods_evaluated": rows,
            "chain_pairs": [
                {"description": d, "m1": a, "m2": b,
                 "m1_macro_AP": by_method[a]["macro"]["ap"] if a in by_method else None,
                 "m2_macro_AP": by_method[b]["macro"]["ap"] if b in by_method else None,
                 "ap_diff": (by_method[a]["macro"]["ap"] - by_method[b]["macro"]["ap"]) if a in by_method and b in by_method else None,
                 "brier_diff": (by_method[a]["macro"]["brier"] - by_method[b]["macro"]["brier"]) if a in by_method and b in by_method else None}
                for d, a, b in pairs
            ],
            "campaigns": CAMPAIGNS,
            "n_final_rows_evaluated": len(labels),
            "e1b_P1_reference": "E1a_P1-v2_predictions.jsonl",
        }, f, indent=2, ensure_ascii=False)
    print(f"\nWrote summary: {out_dir / 'e1a_e1b_summary.json'}")


if __name__ == "__main__":
    main()
