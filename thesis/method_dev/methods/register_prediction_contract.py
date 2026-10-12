"""Register the prediction-contract.json after protocol freeze is signed.

Per 手册 §8.5, this updates the contract status to `registered` and populates
the `artifact_requirements` array with every method that will produce predictions.

This script does NOT execute the actual T predictions; it only updates the
contract metadata so that the registration is auditable.

Usage:
    python3 register_prediction_contract.py
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path("/Users/jiangchenxi/科研内容/代码仓库/MatrAIx-Persona-8B")
CONTRACT = ROOT / "thesis/e0b/prediction-contract.json"
FEATURES_T = ROOT / "thesis/e1_data/runs/e1-data-20261011T042328.304949Z/private/features_T.csv"
LINKS_T = ROOT / "thesis/e1_data/runs/e1-data-20261011T042328.304949Z/private/links_T.csv"

# Methods frozen in e1a_freeze_manifest.md
E1A_METHODS = [
    {"id": "N0", "kind": "constant"},
    {"id": "N1", "kind": "campaign_only"},
    {"id": "B1", "kind": "rfm_rule"},
    {"id": "B2-Logistic", "kind": "tabular_supervised", "C": 0.1},
    {"id": "B3-CatBoost", "kind": "tabular_supervised", "depth": 5, "lr": 0.1},
    {"id": "P1-v2", "kind": "persona_agent", "prompt": "v2"},
]

E1B_METHODS = [
    {"id": "L0", "kind": "direct_llm", "input": "S_only", "prompt": "campaign_only"},
    {"id": "L1", "kind": "direct_llm", "input": "X/S_structured", "prompt": "structured"},
    {"id": "L2", "kind": "direct_llm", "input": "X/S_persona_text", "prompt": "persona_text"},
    {"id": "P2", "kind": "persona_agent", "input": "misaligned_persona", "prompt": "v2"},
    {"id": "P3", "kind": "persona_agent", "input": "minimal_RFM_persona", "prompt": "v2"},
]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    contract = json.loads(CONTRACT.read_text())

    # Build artifact_requirements
    artifacts = []
    base_path = "thesis/method_dev/runs/e1a-e1b-T-20261012/private"

    # E1a artifacts (6 methods on final_evaluation pool = 1386 rows)
    for m in E1A_METHODS:
        mid = m["id"]
        artifacts.append({
            "artifact_id": f"E1a_{mid}",
            "prediction_set": "E1a",
            "method_id": mid,
            "variant_id": mid,
            "pool": "final_evaluation",
            "campaigns": contract["T_campaigns"],
            "repetitions": 1,
            "expected_rows": 1386,
            "prediction_column": "p_redeem",
            "path": f"{base_path}/e1a_{mid}_predictions.jsonl",
            "frozen_config": m,
        })

    # E1b artifacts (5 conditions on final_evaluation pool)
    for m in E1B_METHODS:
        mid = m["id"]
        artifacts.append({
            "artifact_id": f"E1b_{mid}",
            "prediction_set": "E1b",
            "method_id": mid,
            "variant_id": mid,
            "pool": "final_evaluation",
            "campaigns": contract["T_campaigns"],
            "repetitions": 1,
            "expected_rows": 1386,
            "prediction_column": "p_redeem",
            "path": f"{base_path}/e1b_{mid}_predictions.jsonl",
            "frozen_config": m,
        })

    contract["status"] = "registered"
    contract["registration_at"] = "2026-10-12T17:50:00+08:00"
    contract["registrar"] = "experimental_protocol_owner"
    contract["reason"] = (
        "Protocol v1.2 E1a freeze manifest signed 2026-10-12. "
        "Methods N0/N1/B1/B2-Logistic(C=0.1)/B3-CatBoost(d5,l0.1)/P1-v2 frozen. "
        "P1 R stability pass (100 records, 100% valid rate, 66 distinct scores, "
        "max single-value share 12%). All required_prediction_sets registered."
    )
    contract["artifact_requirements"] = artifacts

    # Update sha256 of contract itself (re-canonicalize after edit)
    raw = json.dumps(contract, ensure_ascii=False, sort_keys=True, indent=2)
    new_sha = hashlib.sha256(raw.encode()).hexdigest()
    contract["contract_sha256_self"] = new_sha

    CONTRACT.write_text(json.dumps(contract, ensure_ascii=False, indent=2))
    print(f"Wrote {CONTRACT}")
    print(f"status: {contract['status']}")
    print(f"artifact_requirements: {len(artifacts)} items ({len(E1A_METHODS)} E1a + {len(E1B_METHODS)} E1b)")
    print(f"contract_sha256_self: {new_sha}")
    print()
    print("Note: timelock-policy.json status remains 'pending_schedule_registration'.")
    print("T predictions can now be generated; final results require timelock schedule")


if __name__ == "__main__":
    main()
