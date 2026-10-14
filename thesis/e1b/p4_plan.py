"""Freeze the outcome-blind paired P4 cohort before any T model call."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import subprocess
from collections import Counter
from datetime import datetime, timezone

from thesis.e1a.methods.data import ROOT, configuration, load_split, sha256
from thesis.e1b.subsample import select_p4


HERE = ROOT / "thesis/e1b"
BRANCH = "thesis-restart-20261014"


def cohort(config: dict, input_config: dict) -> tuple[tuple[str, ...], dict]:
    if config["status"] != "confirmed_subset_scope" or config["input_split"] != "T":
        raise ValueError("P4 subset scope is not confirmed for T")
    if config["input_role"] != "final_evaluation":
        raise ValueError("P4 must use only final-evaluation households")
    if config["paired_methods"] != ["L1", "L2", "P1", "P2", "P3"]:
        raise ValueError("P4 paired condition inventory differs")
    source = load_split("T", config=input_config)
    input_run = ROOT / input_config["input_run"]
    groups_file = input_run / "private/groups_T.csv"
    checksums = json.loads((input_run / "checksums.json").read_text(encoding="utf-8"))
    if sha256(groups_file) != checksums["private/groups_T.csv"]:
        raise ValueError("frozen T X/S behavior groups differ")
    with groups_file.open(newline="", encoding="utf-8") as handle:
        group_rows = list(csv.DictReader(handle))
    groups = {row["row_id"]: row for row in group_rows}
    if len(groups) != len(group_rows) or set(groups) != set(source.links):
        raise ValueError("T behavior groups and frozen features differ")
    eligible = tuple(row for row in source.features
                     if source.links[row["row_id"]]["e2_role"] == config["input_role"])
    links = {row["row_id"]: source.links[row["row_id"]] for row in eligible}
    selected = select_p4(eligible, links, groups, per_campaign_cap=config["per_campaign_cap"],
                         seed=config["selection_seed"])
    campaigns = sorted({int(link["campaign"]) for link in links.values()})
    if campaigns != config["expected_campaigns"]:
        raise ValueError("eligible T campaign inventory differs")
    counts = Counter(int(links[row_id]["campaign"]) for row_id in selected)
    eligible_counts = Counter(int(link["campaign"]) for link in links.values())
    if any(counts[campaign] != min(config["per_campaign_cap"], eligible_counts[campaign])
           for campaign in campaigns):
        raise ValueError("P4 campaign subset size differs")
    summary = {
        "status": "p4_outcome_blind_cohort_selected",
        "input_run": input_config["input_run"],
        "input_role": config["input_role"],
        "per_campaign_cap": config["per_campaign_cap"],
        "selection_seed": config["selection_seed"],
        "eligible_counts_by_campaign": {str(k): eligible_counts[k] for k in campaigns},
        "selected_counts_by_campaign": {str(k): counts[k] for k in campaigns},
        "selected_rows": len(selected),
        "selected_row_ids_sha256": hashlib.sha256("\n".join(selected).encode()).hexdigest(),
        "group_file_sha256": sha256(groups_file),
        "paired_methods": config["paired_methods"],
        "model_pair": config["model_pair"],
        "T_labels_opened": False,
        "model_calls": 0,
    }
    if summary["selected_row_ids_sha256"] != config["expected_selected_row_ids_sha256"]:
        raise ValueError("P4 X/S-only cohort differs from registered subset hash")
    return selected, summary


def _git(*arguments: str) -> str:
    return subprocess.check_output(["git", *arguments], cwd=ROOT, text=True).strip()


def execute(config: dict, input_config: dict) -> dict:
    if _git("status", "--porcelain", "--untracked-files=all"):
        raise ValueError("P4 planning code or config has uncommitted changes")
    if _git("branch", "--show-current") != BRANCH:
        raise ValueError("P4 planning is on another branch")
    commit = _git("rev-parse", "HEAD")
    if commit != _git("rev-parse", "origin/" + BRANCH):
        raise ValueError("P4 planning version is not backed up on GitHub")
    selected, report = cohort(config, input_config)
    report["code_commit"] = commit
    os.umask(0o077)
    destination = HERE / "runs" / datetime.now(timezone.utc).strftime("p4-plan-%Y%m%dT%H%M%S.%fZ")
    destination.mkdir(parents=True, exist_ok=False)
    source = load_split("T", config=input_config)
    with (destination / "selected_row_ids_PRIVATE.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(("row_id", "campaign"))
        writer.writerows((row_id, source.links[row_id]["campaign"]) for row_id in selected)
    (destination / "report.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (destination / "checksums.json").write_text(json.dumps({
        path.name: sha256(path) for path in destination.iterdir() if path.name != "checksums.json"
    }, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"run": str(destination), **report}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    config = json.loads((HERE / "p4_config.json").read_text(encoding="utf-8"))
    input_config = configuration()
    if args.execute:
        report = execute(config, input_config)
    else:
        _, report = cohort(config, input_config)
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
