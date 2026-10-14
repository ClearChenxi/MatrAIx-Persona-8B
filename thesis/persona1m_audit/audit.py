"""Audit possible Persona 1M-to-household bridges without persona rows or outcomes."""

from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from thesis.e1a.methods.data import ROOT, configuration, sha256


HERE = Path(__file__).resolve().parent
BRANCH = "thesis-restart-20261014"
SOURCE_FILES = ("README.md", "persona_codes.schema.json", "audit.json")
ALLOWED_RELATIONS = {"unvalidated_proxy", "not_observed_in_main_input", "direct_equivalent"}


def _git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def _source(metadata_dir: Path, name: str, expected_hash: str) -> Path:
    path = metadata_dir / name
    if not path.is_file() or path.is_symlink() or sha256(path) != expected_hash:
        raise ValueError(f"Persona 1M metadata missing or differs: {name}")
    return path


def _features(input_config: dict, name: str) -> list[dict[str, str]]:
    if name not in {"D", "R"}:
        raise ValueError("matchability audit may read only D/R features")
    path = ROOT / input_config["input_run"] / "private" / f"features_{name}.csv"
    if not path.is_file() or path.is_symlink() or sha256(path) != input_config["input_hashes"][path.name]:
        raise ValueError("frozen D/R feature hash differs")
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    if not rows or len({row["row_id"] for row in rows}) != len(rows):
        raise ValueError("D/R features are empty or have repeated IDs")
    if {"y1_redeemed", "household_key", "e2_role"} & rows[0].keys():
        raise ValueError("outcome, identity or test permission entered X/S")
    return rows


def audit(plan: dict, metadata_dir: Path, input_config: dict) -> dict:
    if plan["status"] != "approved_metadata_only_matchability_audit":
        raise ValueError("matchability audit scope is not approved")
    if plan["dunnhumby_splits"] != ["D", "R"]:
        raise ValueError("this audit is limited to D/R")
    metadata = {name: _source(metadata_dir, name, plan["metadata_sha256"][name])
                for name in SOURCE_FILES}
    codebook = json.loads(metadata["persona_codes.schema.json"].read_text(encoding="utf-8"))
    columns = codebook["columns"]
    if len(columns) != plan["expected_persona_fields"]:
        raise ValueError("Persona 1M field count differs")
    by_id = {column["id"]: column for column in columns}
    if len(by_id) != len(columns):
        raise ValueError("Persona 1M codebook repeats a field ID")
    source_stats = json.loads(metadata["audit.json"].read_text(encoding="utf-8"))["source_stats"]
    source_counts = {name: stats["rows"] for name, stats in sorted(source_stats.items())}
    if sum(source_counts.values()) != plan["expected_persona_rows"]:
        raise ValueError("Persona 1M audited row count differs")
    card = metadata["README.md"].read_text(encoding="utf-8")
    if "license_name: matraix-research-only" not in card or "Not a representative sample" not in card:
        raise ValueError("dataset card no longer supports the planned license/representativeness caveat")

    rows_by_split = {name: _features(input_config, name) for name in plan["dunnhumby_splits"]}
    feature_names = set(rows_by_split["D"][0]) - {"row_id"}
    if any(set(rows[0]) - {"row_id"} != feature_names for rows in rows_by_split.values()):
        raise ValueError("D/R X/S schemas differ")
    seen_persona_fields = set()
    bridges = []
    for item in plan["bridges"]:
        persona_field = item["persona_field"]
        observed = item["dunnhumby_fields"]
        relation = item["relation"]
        if (persona_field not in by_id or persona_field in seen_persona_fields
                or set(observed) - feature_names or relation not in ALLOWED_RELATIONS
                or (relation == "not_observed_in_main_input") != (not observed)
                or not item["reason"]):
            raise ValueError("invalid predeclared bridge")
        seen_persona_fields.add(persona_field)
        present = {
            split: {field: sum(row[field] != "" for row in rows)
                    for field in observed}
            for split, rows in rows_by_split.items()
        }
        bridges.append({
            "persona_field": persona_field,
            "persona_label": by_id[persona_field]["label"],
            "persona_values": by_id[persona_field]["values"],
            "dunnhumby_fields": observed,
            "relation": relation,
            "reason": item["reason"],
            "dunnhumby_nonmissing_rows": present,
            "persona_field_population_coverage": "not_measured_without_full_release",
        })
    if not bridges:
        raise ValueError("no bridge hypotheses were predeclared")
    relations = Counter(item["relation"] for item in bridges)
    return {
        "status": "schema_matchability_audited",
        "dataset_repo": plan["dataset_repo"],
        "dataset_revision": plan["dataset_revision"],
        "dataset_metadata_sha256": plan["metadata_sha256"],
        "persona_rows_from_release_audit": sum(source_counts.values()),
        "persona_fields_in_codebook": len(columns),
        "persona_source_rows": source_counts,
        "license": "non-commercial research only; responsible-use restrictions apply",
        "dunnhumby_input_run": input_config["input_run"],
        "dunnhumby_rows": {name: len(rows) for name, rows in rows_by_split.items()},
        "dunnhumby_feature_fields": len(feature_names),
        "bridges": bridges,
        "bridge_relation_counts": dict(sorted(relations.items())),
        "identity_matching_authorized": False,
        "formal_P1_change_authorized": False,
        "persona_records_opened": False,
        "T_features_opened": False,
        "T_labels_opened": False,
        "model_calls": 0,
    }


def execute(plan: dict, metadata_dir: Path, input_config: dict) -> dict:
    if _git("status", "--porcelain", "--untracked-files=all"):
        raise ValueError("audit code or plan has uncommitted changes")
    if _git("branch", "--show-current") != BRANCH:
        raise ValueError("audit is on another branch")
    commit = _git("rev-parse", "HEAD")
    if commit != _git("rev-parse", "origin/" + BRANCH):
        raise ValueError("audit code has not been backed up on GitHub")
    report = audit(plan, metadata_dir, input_config)
    report["code_commit"] = commit
    os.umask(0o077)
    destination = HERE / "runs" / datetime.now(timezone.utc).strftime("matchability-%Y%m%dT%H%M%S.%fZ")
    destination.mkdir(parents=True, exist_ok=False)
    path = destination / "report.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (destination / "checksums.json").write_text(
        json.dumps({path.name: sha256(path)}, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return {"run": str(destination), **report}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--metadata-dir", required=True, type=Path)
    args = parser.parse_args()
    plan = json.loads((HERE / "plan.json").read_text(encoding="utf-8"))
    print(json.dumps(execute(plan, args.metadata_dir, configuration()), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
