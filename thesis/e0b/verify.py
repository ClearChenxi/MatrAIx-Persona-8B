"""Read-only E0b evidence verifier that never reports T label values."""

from __future__ import annotations

import argparse
import csv
import json
import stat
from pathlib import Path

from audit import canonical_json, digest, validate_plan


def permission_mode(path):
    return stat.S_IMODE(path.stat(follow_symlinks=False).st_mode)


def verify_private_permissions(run_dir):
    private = run_dir / "private"
    if private.is_symlink() or not private.is_dir():
        raise ValueError("E0b private evidence directory is missing or is a symlink")
    if permission_mode(private) != 0o700:
        raise ValueError("E0b private evidence directory current mode is not 0o700")
    private_files = sorted(private.rglob("*"))
    if not private_files:
        raise ValueError("E0b private evidence directory is empty")
    for path in private_files:
        if path.is_symlink():
            raise ValueError("E0b private evidence contains a symlink")
        if path.is_dir():
            if permission_mode(path) != 0o700:
                raise ValueError("E0b nested private directory current mode is not 0o700")
        elif not path.is_file() or permission_mode(path) != 0o600:
            raise ValueError("E0b private file current mode is not 0o600")
    return sum(path.is_file() for path in private_files)


def verify(run_dir):
    private_file_count = verify_private_permissions(run_dir)
    plan = json.loads((run_dir / "plan.json").read_text(encoding="utf-8"))
    validate_plan(plan)
    checksums = json.loads((run_dir / "checksums.json").read_text(encoding="utf-8"))
    actual = {}
    for path in sorted(run_dir.rglob("*")):
        if path.is_file() and path.name != "checksums.json":
            actual[str(path.relative_to(run_dir))] = digest(path)
    if actual != checksums:
        raise ValueError("E0b evidence file inventory or digest mismatch")
    manifest = json.loads((run_dir / "public/audit_manifest.json").read_text(encoding="utf-8"))
    seal = json.loads((run_dir / "public/label_seal_manifest.json").read_text(encoding="utf-8"))
    flow = json.loads((run_dir / "public/sample_flow.json").read_text(encoding="utf-8"))
    activities = json.loads((run_dir / "public/activity_feasibility.json").read_text(encoding="utf-8"))
    dependency = json.loads((run_dir / "public/activity_dependency.json").read_text(encoding="utf-8"))
    permissions = json.loads((run_dir / "public/time_and_label_permissions.json").read_text(encoding="utf-8"))
    if manifest["status"] != "e0b_run_complete" or manifest["test_label_summaries_exposed"]:
        raise ValueError("E0b manifest is not safely complete")
    if seal["test_label_values_exposed"] or seal["test_positive_count_exposed"] or seal["test_response_rate_exposed"]:
        raise ValueError("T label seal reports forbidden result information")
    if set(seal["historical_label_summary"]) != {"D", "R"}:
        raise ValueError("public historical label summary must contain D/R and exclude T")
    for split in ("D", "R"):
        if seal["historical_label_summary"][split]["rows"] != seal["eligible_rows_by_split"][split]:
            raise ValueError(f"public {split} label summary row count is inconsistent")
    if seal["test_file_mode"] != "0o600":
        raise ValueError("recorded T label file mode is not owner-only")
    if flow["e2"]["labels_used_for_assignment"]:
        raise ValueError("E2 household assignment used labels")
    if len(activities["activities"]) != sum(len(value) for value in plan["splits"].values()):
        raise ValueError("activity feasibility list is incomplete")
    test_activities = [item for item in activities["activities"] if item["split"] == "T"]
    if len(test_activities) != plan["evaluation_policy"]["test_campaign_count"]:
        raise ValueError("test activity count differs from the frozen policy")
    threshold = plan["evaluation_policy"]["minimum_final_evaluation_households_warning"]
    for item in test_activities:
        support = item["e2_support"]
        if support["low_support"] != (support["final_evaluation_households"] < threshold):
            raise ValueError("test activity low-support status is inconsistent")
        if support["action"] != "flag_and_retain":
            raise ValueError("low-support test activity policy changed")
    if dependency["inference_scope"] != "exploratory future-period response prediction; T activities are not independent replicates":
        raise ValueError("inference scope differs from the protocol")
    if not all(permissions["checks"].values()) or not permissions["whole_T_segment_no_rolling_updates"]:
        raise ValueError("time split or T permission checks failed")
    if len(permissions["campaigns"]) != len(activities["activities"]):
        raise ValueError("time/label permission table is incomplete")
    cohort_path = run_dir / "private/cohort_permissions.csv"
    with cohort_path.open("r", encoding="utf-8", newline="") as stream:
        cohort = list(csv.DictReader(stream))
    role_by_household = {}
    for row in cohort:
        if row["split"] != "T" or row["eligible"] != "1":
            continue
        prior = role_by_household.setdefault(row["household_key"], row["e2_role"])
        if prior != row["e2_role"] or row["e2_role"] not in {"adaptation", "final_evaluation"}:
            raise ValueError("E2 permission is not globally consistent by household")
    expected_t = seal["eligible_rows_by_split"]["T"]
    actual_t = sum(row["split"] == "T" and row["eligible"] == "1" for row in cohort)
    if actual_t != expected_t:
        raise ValueError("sealed T label row count differs from eligible cohort")
    return {
        "status": "verified",
        "verified_files": len(actual),
        "private_files_with_current_mode_verified": private_file_count,
        "source_files": manifest["source_files"],
        "source_rows": manifest["source_rows"],
        "activities": len(activities["activities"]),
        "eligible_rows_by_split": seal["eligible_rows_by_split"],
        "historical_label_summary": seal["historical_label_summary"],
        "e2_unique_test_households": len(role_by_household),
        "test_label_values_exposed": False,
        "model_calls": 0,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_directory", type=Path)
    args = parser.parse_args()
    print(canonical_json(verify(args.run_directory.resolve(strict=True))), end="")


if __name__ == "__main__":
    main()
