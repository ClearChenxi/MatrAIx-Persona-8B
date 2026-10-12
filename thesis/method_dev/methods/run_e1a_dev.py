"""Aggregate all E1a methods (N0/N1/B1/B2/B3) on D and R for dev comparison.

This is for the **historical development** stage (手册 step 15-16), so we
evaluate on D (train set, may be optimistic) and R (held-out validation).
T labels are sealed, so T will report predictions only.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from run_baselines import run_n0, run_n1, run_b1
from run_logistic import run_logistic
from run_catboost import run_catboost


def main():
    print("=" * 60)
    print("E1a Method Development — historical D / R")
    print("=" * 60)

    # Need D feats for N1
    from io_utils import load_split
    D_feats, D_labels, _ = load_split("D")

    all_results = {}
    for name, fn, args in [
        ("N0", run_n0, (D_labels,)),
        ("N1", run_n1, (D_feats, D_labels)),
        ("B1", run_b1, ()),
        ("B2", run_logistic, ()),
        ("B3", run_catboost, ()),
    ]:
        print(f"\n--- {name} ---")
        try:
            r = fn(*args)
        except Exception as e:
            print(f"FAILED: {e}")
            r = {"error": str(e)}
        all_results[name] = r
        # Print summary line
        for sp in ("D", "R"):
            if sp in r and isinstance(r[sp], dict) and "macro_AP" in r[sp]:
                ap = r[sp]["macro_AP"]
                n = r[sp]["n"]
                brier = r[sp].get("Brier", float("nan"))
                print(f"  {sp}: n={n:4d}, macro_AP={ap:.4f}, Brier={brier:.4f}")
        if "T" in r and isinstance(r["T"], dict) and "error" in r["T"]:
            print(f"  T: predictions OK (labels sealed)")

    print("\n" + "=" * 60)
    print("Summary (macro_AP)")
    print("=" * 60)
    print(f"{'method':<8} {'D':>8} {'R':>8}")
    for name, r in all_results.items():
        d = r.get("D", {}).get("macro_AP", float("nan"))
        rr = r.get("R", {}).get("macro_AP", float("nan"))
        d_str = f"{d:.4f}" if isinstance(d, float) and not (d != d) else "—"
        r_str = f"{rr:.4f}" if isinstance(rr, float) and not (rr != rr) else "—"
        print(f"{name:<8} {d_str:>8} {r_str:>8}")


if __name__ == "__main__":
    main()
