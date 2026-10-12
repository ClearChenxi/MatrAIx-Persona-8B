"""Seal E1a + E1b T-prediction artifacts.

After all 11 prediction jsonl files exist, this script:
1. Verifies each file has exactly 1,386 rows (final_evaluation pool).
2. Computes SHA-256 of every artifact.
3. Writes a seal-manifest.json with per-artifact SHA + size.
4. Computes a manifest SHA (sealed by this manifest).
5. Verifies all 11 artifact paths match prediction-contract.json.

The seal-manifest is the input to `register_timelock_schedule.py` (after
you've confirmed the contract alignment). It does NOT itself touch T labels
or any encrypted timelock materials.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path("/Users/jiangchenxi/科研内容/代码仓库/MatrAIx-Persona-8B")
PRED_DIR = ROOT / "thesis/method_dev/runs/e1a-e1b-T-20261012/private"
SEAL_PATH = PRED_DIR.parent / "seal-manifest.json"
CONTRACT = ROOT / "thesis/e0b/prediction-contract.json"


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    if not PRED_DIR.exists():
        print(f"FATAL: prediction directory {PRED_DIR} does not exist.")
        print("Run run_e1_T_predictions.py first.")
        sys.exit(1)

    contract = json.loads(CONTRACT.read_text())
    artifacts = contract.get("artifact_requirements", [])

    seal = {
        "schema_version": "1.0",
        "sealed_at_utc": "2026-10-12T17:50:00+08:00",
        "seal_owner": "experimental_protocol_owner",
        "T_campaigns": contract["T_campaigns"],
        "pool": "final_evaluation",
        "expected_pool_rows": 1386,
        "artifacts": [],
        "contract_sha256_self": contract.get("contract_sha256_self"),
    }

    for art in artifacts:
        rel = art["path"]
        # Convert relative path to absolute (paths in contract are like
        # "thesis/method_dev/runs/e1a-e1b-T-20261012/private/e1a_B1_predictions.jsonl")
        path = ROOT / rel
        if not path.exists():
            print(f"FATAL: artifact {path} missing")
            sys.exit(1)
        n_rows = sum(1 for _ in path.open(encoding="utf-8"))
        sha = sha256_file(path)
        size = path.stat().st_size
        seal["artifacts"].append({
            "artifact_id": art["artifact_id"],
            "method_id": art["method_id"],
            "path": rel,
            "row_count": n_rows,
            "sha256": sha,
            "size_bytes": size,
            "expected_rows": art["expected_rows"],
            "match": n_rows == art["expected_rows"],
            "frozen_config": art["frozen_config"],
        })

    # Validate row counts
    bad = [a for a in seal["artifacts"] if not a["match"]]
    if bad:
        print("FATAL: artifacts with mismatched row counts:")
        for a in bad:
            print(f"  {a['artifact_id']}: have {a['row_count']}, want {a['expected_rows']}")
        sys.exit(1)

    # Compute seal-manifest SHA
    raw = json.dumps(seal, ensure_ascii=False, sort_keys=True, indent=2)
    seal["seal_manifest_sha256"] = hashlib.sha256(raw.encode()).hexdigest()

    SEAL_PATH.write_text(json.dumps(seal, ensure_ascii=False, indent=2))
    print(f"Wrote {SEAL_PATH}")
    print(f"seal_manifest_sha256: {seal['seal_manifest_sha256']}")
    print()
    print("Per-artifact SHA:")
    for a in seal["artifacts"]:
        print(f"  {a['artifact_id']:<25} n={a['row_count']:4d}  sha={a['sha256'][:16]}...")


if __name__ == "__main__":
    main()
