"""Same-record D/R technical pilot for all E1b conditions, no T access."""

from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from thesis.e1a.methods.baselines import n0_rate
from thesis.e1a.methods.data import ROOT, configuration, load_split, sha256
from thesis.e1b.agent_workflow import P3_HOUSEHOLD_FIELDS, run_persona_batch
from thesis.e1b.direct_controls import run_direct_batch
from thesis.e1b.misalignment import mismatch
from thesis.e1b.pilot import selected


HERE = ROOT / "thesis/e1b"
BRANCH = "thesis-restart-20261014"


def _groups(split_name: str, input_config: dict) -> dict[str, dict[str, str]]:
    run = ROOT / input_config["input_run"]
    path = run / "private" / f"groups_{split_name}.csv"
    checksums = json.loads((run / "checksums.json").read_text(encoding="utf-8"))
    if sha256(path) != checksums[f"private/groups_{split_name}.csv"]:
        raise ValueError("behavior groups differ from verified E1 input bundle")
    with path.open(newline="", encoding="utf-8") as handle:
        return {row["row_id"]: row for row in csv.DictReader(handle)}


def _git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def _gate(condition_config: dict, pilot_runs: tuple[Path, Path]) -> str:
    if condition_config["status"] != "confirmed":
        raise ValueError("E1b condition pilot has not been frozen")
    for path in pilot_runs:
        report = json.loads((path / "report.json").read_text(encoding="utf-8"))
        if report["status"] != condition_config["required_P1_D_R_pilot_status"]:
            raise ValueError("P1 D/R technical gate has not passed")
    if _git("status", "--porcelain", "--untracked-files=all"):
        raise ValueError("E1b code has uncommitted changes")
    if _git("branch", "--show-current") != BRANCH:
        raise ValueError("wrong experiment branch")
    commit = _git("rev-parse", "HEAD")
    if commit != _git("rev-parse", "origin/" + BRANCH):
        raise ValueError("E1b pilot code has not been backed up")
    return commit


def run(split_name: str, method: str, condition_config: dict,
        pilot_config: dict, input_config: dict, pilot_runs: tuple[Path, Path]) -> dict:
    commit = _gate(condition_config, pilot_runs)
    if method not in condition_config["methods"]:
        raise ValueError("unregistered E1b method")
    rows, selection = selected(split_name, pilot_config, input_config)
    source = load_split(split_name, config=input_config)
    by_campaign = defaultdict(list)
    for row in rows:
        by_campaign[int(source.links[row["row_id"]]["campaign"])].append(row)
    complete_groups = _groups(split_name, input_config) if method == "P2" else None
    full_by_campaign = defaultdict(list)
    if method == "P2":
        for row in source.features:
            full_by_campaign[int(source.links[row["row_id"]]["campaign"])].append(row)
    os.umask(0o077)
    destination = HERE / "runs" / datetime.now(timezone.utc).strftime(
        f"condition-{split_name}-{method}-%Y%m%dT%H%M%S.%fZ"
    )
    destination.mkdir(parents=True, exist_ok=False)
    fallback = n0_rate(load_split("D", config=input_config))
    output, donor_log = [], []
    for campaign, campaign_rows in sorted(by_campaign.items()):
        if method == "P2":
            altered, donor_map = mismatch(tuple(full_by_campaign[campaign]), complete_groups,
                                          seed=condition_config["misalignment_seed"])
            altered_by_id = {row["row_id"]: row for row in altered}
            campaign_rows = [altered_by_id[row["row_id"]] for row in campaign_rows]
            selected_ids = {row["row_id"] for row in campaign_rows}
            donor_log.extend(item for item in donor_map if item["recipient_row_id"] in selected_ids)
        target = destination / f"campaign_{campaign}"
        settings = dict(
            model=condition_config["model"], options=condition_config["options"],
            expected_digest=condition_config["model_digest"], fallback_rate=fallback,
            retries=condition_config["maximum_retries_per_record"],
        )
        if method in {"P1", "P2", "P3"}:
            results = run_persona_batch(
                tuple(campaign_rows), target, method_id=method,
                household_field_allowlist=P3_HOUSEHOLD_FIELDS if method == "P3" else None,
                **settings,
            )
        else:
            results = run_direct_batch(method, tuple(campaign_rows), target, **settings)
        output.extend(dict(item, campaign=campaign) for item in results)
    with (destination / "predictions.jsonl").open("w", encoding="utf-8") as handle:
        for item in output:
            handle.write(json.dumps(item, sort_keys=True) + "\n")
    if donor_log:
        with (destination / "P2_donor_mapping_PRIVATE.jsonl").open("w", encoding="utf-8") as handle:
            for item in donor_log:
                handle.write(json.dumps(item, sort_keys=True) + "\n")
    report = {
        "status": "condition_pilot_complete", "split": split_name, "method": method,
        "code_commit": commit, "selection": selection, "rows": len(output),
        "fallback_rows": sum(item["fallback"] for item in output),
        "P2_different_group_fraction": (
            sum(item["different_behavior_group"] for item in donor_log) / len(donor_log)
            if donor_log else None
        ),
        "T_labels_opened": False,
    }
    (destination / "report.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (destination / "checksums.json").write_text(json.dumps({
        path.relative_to(destination).as_posix(): sha256(path)
        for path in sorted(destination.rglob("*")) if path.is_file() and path.name != "checksums.json"
    }, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"run": str(destination), **report}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("split", choices=("D", "R"))
    parser.add_argument("method", choices=("L0", "L1", "L2", "P1", "P2", "P3"))
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--P1-D-run", type=Path)
    parser.add_argument("--P1-R-run", type=Path)
    args = parser.parse_args()
    condition_config = json.loads((HERE / "condition_config.json").read_text(encoding="utf-8"))
    pilot_config = json.loads((HERE / "pilot_config.json").read_text(encoding="utf-8"))
    input_config = configuration()
    rows, selection = selected(args.split, pilot_config, input_config)
    if not args.execute:
        print(json.dumps({"status": condition_config["status"], "split": args.split,
                          "method": args.method, "rows": len(rows),
                          "selection": selection, "model_calls": 0}, indent=2, ensure_ascii=False))
        return
    if not args.P1_D_run or not args.P1_R_run:
        raise ValueError("verified P1 D and R pilot run paths are required")
    print(json.dumps(run(args.split, args.method, condition_config, pilot_config,
                         input_config, (args.P1_D_run, args.P1_R_run)), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
