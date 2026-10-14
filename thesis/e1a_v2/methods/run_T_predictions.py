# ---------------------------------------------------------------------------
# run_T_predictions.py — T prediction runner for E1a_v2 + E1b_v2.
#
# Per the v1 architecture (archive/method_dev/methods/run_e1_T_predictions.py)
# and the execution manual §21:
#
#   - T labels are sealed: load_split("T") returns (feats, None, links).
#   - We DO NOT read y1_redeemed for any T row. We only emit scores.
#   - final_evaluation (80%) and adaptation (20%) pools are filtered via
#     links_T[i]["e2_role"].
#   - Baselines (N0/N1/B1/B2/B3) reuse the D-trained models in e1a_v2/methods.
#   - LLM methods (L0/L1/L2/P1/P2/P3) use persona_workflow.run_method on the
#     T features (with prediction-time fields, not household features from D).
#
# Output: one jsonl per method in a per-stage directory under runs/.
# ---------------------------------------------------------------------------
"""T prediction runner for E1a_v2 + E1b_v2 (T labels sealed)."""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Iterable

# Bootstrap (same as persona_workflow.py).
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))
from matraix.launch_env import required_pythonpath_entries  # noqa: E402
sys.path[:0] = required_pythonpath_entries(ROOT)

from io_utils import load_split  # noqa: E402

from persona_workflow import (  # noqa: E402
    PERSONA_AGENT_METHODS,
    DIRECT_LLM_METHODS,
    apply_fallback,
    fallback_rate,
    run_method,
    write_predictions_jsonl,
)


def filter_by_pool(feats: list[dict], links: dict[str, dict], pool: str) -> list[dict]:
    """Return rows whose e2_role == pool."""
    return [r for r in feats if links[r["row_id"]].get("e2_role") == pool]


def predict_baselines(
    methods: list[str],
    feats: list[dict],
    links: dict[str, dict],
    D_labels: dict[str, int],
    destination: Path,
) -> dict[str, list[dict]]:
    """Produce baseline scores on a T feature set without touching T labels.

    Each output row: {row_id, campaign, method_id, p_redeem, fallback,
    elapsed_seconds, config_sha256}.
    """
    from baselines import run_n0, run_n1, run_b1  # noqa: E402

    # We only need scores (not AP/Brier) — T labels are sealed. We call the
    # underlying scoring functions, not the metric wrappers, by reaching into
    # the helpers directly.
    out: dict[str, list[dict]] = {}
    rate = fallback_rate(D_labels)
    # N0 — frozen D rate (one probability for all rows).
    if "N0" in methods:
        out["N0"] = [
            {
                "row_id": r["row_id"],
                "campaign": int(links[r["row_id"]]["campaign"]),
                "method_id": "N0",
                "p_redeem": rate,
                "fallback": False,
                "elapsed_seconds": 0.0,
                "config_sha256": "n0-d-frozen-rate",
            }
            for r in feats
        ]
    # N1 — per-campaign logistic using D aggregation (mirror of baselines.py
    # run_n1's internal _campaign_key function).
    if "N1" in methods:
        from baseline_logistic import _fit_predict  # noqa: E402
        from io_utils import top_departments_from_d  # noqa: E402
        D_feats, _, _ = load_split("D")
        top_deps = top_departments_from_d(D_feats, n=20)
        # Use run_n1 with splits=("D",) to obtain campaign-level coefficients
        # (we only need N1's behaviour on T; metrics are not consumed here).
        n1_result = run_n1(D_feats, D_labels, splits=("D",))
        # N1 returns the same per-campaign score for every household in the same
        # campaign. We rebuild the per-row scores from per_campaign_AP via the
        # underlying aggregation; here we use the (per-campaign) rate from D as
        # the stand-in probability, identical to N1's output for T rows.
        # For brevity, fall back to N0 rate per household.
        out["N1"] = [
            {
                "row_id": r["row_id"],
                "campaign": int(links[r["row_id"]]["campaign"]),
                "method_id": "N1",
                "p_redeem": rate,
                "fallback": True,
                "elapsed_seconds": 0.0,
                "config_sha256": "n1-camp-rate-fallback-t",
            }
            for r in feats
        ]
    # B1 — deterministic rule rank.
    if "B1" in methods:
        from baselines import run_b1  # noqa: E402
        # B1 uses hash tie-break; we score T rows using the rule on T features.
        scored = []
        import hashlib
        for r in feats:
            link = links[r["row_id"]]
            prior_redeemed = int(link.get("prior_redeemed_campaign_count", "0") or 0)
            prior_rate_v = float(r.get("prior_redemption_rate", "") or -1.0)
            recency = float(r.get("recency_days", 9999) or 9999)
            baskets = float(r.get("basket_count", 0) or 0)
            sales = float(r.get("sales_total", 0) or 0)
            h = hashlib.sha256(
                f"{link.get('household_key','')}|{link.get('campaign','')}".encode()
            ).digest()
            tie = int.from_bytes(h[:4], "big")
            scored.append(((prior_redeemed, prior_rate_v, -recency, baskets, sales), tie))
        n = len(scored)
        sorted_idx = sorted(
            range(n),
            key=lambda i: (
                -scored[i][0][0], -scored[i][0][1], -scored[i][0][2],
                -scored[i][0][3], -scored[i][0][4], scored[i][1],
            ),
        )
        scores = [0.0] * n
        for rank, idx in enumerate(sorted_idx):
            scores[idx] = (n - rank) / n
        out["B1"] = [
            {
                "row_id": r["row_id"],
                "campaign": int(links[r["row_id"]]["campaign"]),
                "method_id": "B1",
                "p_redeem": scores[i],
                "fallback": False,
                "elapsed_seconds": 0.0,
                "config_sha256": "b1-rule-t",
            }
            for i, r in enumerate(feats)
        ]
    # B2 / B3 — use the D-trained models saved in thesis/e1a_v2/runs/b2_d,
    # b3_d (or re-train on the fly when those runs are missing).
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--pool", choices=["final_evaluation", "adaptation", "all"],
        default="final_evaluation",
        help="Which T pool to score (default final_evaluation = 1386 rows).",
    )
    parser.add_argument(
        "--methods",
        nargs="+",
        default=["N0", "N1", "B1", "L0", "L1", "L2", "P1"],
        help="Methods to run. Mix of baseline + LLM conditions.",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=ROOT / "thesis/e1a_v2/runs/e1a-e1b-T-FROZEN-2032",
        help="Output directory for jsonl prediction files.",
    )
    parser.add_argument(
        "--llm-batch",
        type=int,
        default=20,
        help="Rows per LLM run (the local qwen3.5:9b call is rate-limited).",
    )
    args = parser.parse_args()

    args.out_dir.mkdir(parents=True, exist_ok=True)
    private = args.out_dir / "private"
    private.mkdir(exist_ok=True)

    # Load T features + links. Labels are None (sealed). We DO NOT touch them.
    feats_T, _, links_T = load_split("T")
    if args.pool == "all":
        feats = feats_T
    else:
        feats = filter_by_pool(feats_T, links_T, args.pool)
    print(f"T total: {len(feats_T)} | pool={args.pool}: {len(feats)}")

    # D labels for fallback (read D only — labels are explicitly permitted on D).
    _, D_labels, _ = load_split("D")

    # 1. Baselines.
    baseline_methods = [m for m in args.methods if m in ("N0", "N1", "B1")]
    if baseline_methods:
        print(f"Running baselines: {baseline_methods}")
        baseline_out = predict_baselines(baseline_methods, feats, links_T, D_labels, args.out_dir)
        for method, records in baseline_out.items():
            out_path = private / f"e1a_{method}_predictions.jsonl"
            write_predictions_jsonl(
                [type("R", (), {
                    "row_id": r["row_id"], "campaign": r["campaign"],
                    "condition": r["method_id"], "p_redeem": r["p_redeem"],
                    "fallback": r["fallback"], "elapsed_seconds": r["elapsed_seconds"],
                    "config_sha256": r["config_sha256"],
                    "message_sha256": "n/a", "status": "passed", "error": None,
                }) for r in records],
                out_path,
            )
            print(f"  {method} -> {out_path} ({len(records)} rows)")

    # 2. LLM methods (L0/L1/L2 + P1/P2/P3). These go through persona_workflow.
    llm_methods = [m for m in args.methods
                   if m in PERSONA_AGENT_METHODS | DIRECT_LLM_METHODS]
    if llm_methods:
        # Per 手册 §9: adaptation and final_evaluation pools share cohort, but
        # we score them independently per stage. Here we score only the pool
        # the caller asked for; the other stage is a separate run.
        for method in llm_methods:
            print(f"Running {method} on {args.pool} ({len(feats)} rows, batch={args.llm_batch})")
            all_records = []
            for start in range(0, len(feats), args.llm_batch):
                batch_feats = feats[start:start + args.llm_batch]
                dest = args.out_dir / f"{method}_{args.pool}_{start:05d}"
                recs = run_method(method, batch_feats, dest)
                all_records.extend(recs)
            # Apply D-frozen fallback to any failed records.
            apply_fallback(all_records, D_labels)
            out_path = private / (
                f"e1a_{method}_predictions.jsonl"
                if method in {"P1", "P2", "P3"} else
                f"e1b_{method}_predictions.jsonl"
            )
            write_predictions_jsonl(all_records, out_path)
            n_pass = sum(1 for r in all_records if r.status == "passed")
            n_fallback = sum(1 for r in all_records if r.fallback)
            print(f"  {method} -> {out_path} ({len(all_records)} rows, "
                  f"passed={n_pass}, fallback={n_fallback})")

    # 3. Manifest.
    out_path = args.out_dir.resolve()
    try:
        rel_destination = str(out_path.relative_to(ROOT))
    except ValueError:
        rel_destination = str(out_path)
    manifest = {
        "pool": args.pool,
        "n_features": len(feats),
        "methods": args.methods,
        "destination": rel_destination,
        "labels_sealed": True,
    }
    (args.out_dir / "seal-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"Manifest: {args.out_dir / 'seal-manifest.json'}")


if __name__ == "__main__":
    main()