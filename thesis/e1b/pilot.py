"""Deterministic D/R P1 technical pilot, with no T-label access."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import subprocess
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from thesis.e1a.methods.baselines import n0_rate
from thesis.e1a.methods.data import ROOT, configuration, load_split, sha256
from thesis.e1b.agent_workflow import run_persona_batch


HERE = ROOT / "thesis/e1b"
BRANCH = "thesis-restart-20261014"


def _csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _order(seed: int, row_id: str) -> str:
    return hashlib.sha256(f"{seed}:{row_id}".encode()).hexdigest()


def selected(split_name: str, pilot_config: dict, input_config: dict) -> tuple[tuple[dict, ...], dict]:
    if split_name not in {"D", "R"}:
        raise ValueError("technical pilot only permits D/R")
    split = load_split(split_name, config=input_config)
    by_id = {row["row_id"]: row for row in split.features}
    private = ROOT / input_config["input_run"] / "private"
    if split_name == "D":
        path = private / "persona_audit_sample_D.csv"
        if sha256(path) != pilot_config["D_sample_sha256"]:
            raise ValueError("D boundary sample hash differs")
        ids = [row["row_id"] for row in _csv(path)]
        if len(ids) != pilot_config["D_sample_rows"] or len(set(ids)) != len(ids):
            raise ValueError("D boundary sample is incomplete")
    else:
        path = private / "groups_R.csv"
        if sha256(path) != pilot_config["R_groups_sha256"]:
            raise ValueError("R analysis-group hash differs")
        groups = {row["row_id"]: row for row in _csv(path)}
        if set(groups) != set(by_id):
            raise ValueError("R group table differs from features")
        campaigns = sorted({int(link["campaign"]) for link in split.links.values()})
        target = pilot_config["R_sample_rows"]
        if target % len(campaigns):
            raise ValueError("R pilot size must divide evenly across campaigns")
        per_campaign = target // len(campaigns)
        ids = []
        for campaign in campaigns:
            buckets = defaultdict(list)
            for row in split.features:
                row_id = row["row_id"]
                if int(split.links[row_id]["campaign"]) != campaign:
                    continue
                group = groups[row_id]
                key = (group["frequency_group"], group["historical_redemption_group"],
                       group["target_category_match_group"])
                buckets[key].append(row_id)
            for key in buckets:
                buckets[key].sort(key=lambda row_id: _order(input_config["seed"], row_id))
            strata = sorted(buckets, key=lambda key: _order(input_config["seed"], f"{campaign}:{key}"))
            chosen = []
            while len(chosen) < per_campaign and any(buckets.values()):
                for key in strata:
                    if buckets[key] and len(chosen) < per_campaign:
                        chosen.append(buckets[key].pop(0))
            if len(chosen) != per_campaign:
                raise ValueError("R campaign has too few eligible records")
            ids.extend(chosen)
    if any(row_id not in by_id for row_id in ids):
        raise ValueError("selected row is absent from frozen features")
    selected_rows = tuple(by_id[row_id] for row_id in ids)
    counts = Counter(int(split.links[row_id]["campaign"]) for row_id in ids)
    return selected_rows, {"campaign_counts": {str(k): v for k, v in sorted(counts.items())},
                           "selection_sha256": hashlib.sha256("\n".join(ids).encode()).hexdigest()}


def _git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def _execution_gate(pilot_config: dict) -> str:
    if pilot_config["status"] != "confirmed":
        raise ValueError("pilot scope has not been confirmed")
    if _git("status", "--porcelain", "--untracked-files=all"):
        raise ValueError("pilot code or plan has uncommitted changes")
    if _git("branch", "--show-current") != BRANCH:
        raise ValueError("pilot is on another branch")
    commit = _git("rev-parse", "HEAD")
    if commit != _git("rev-parse", "origin/" + BRANCH):
        raise ValueError("pilot code has not been pushed")
    return commit


def run(split_name: str, pilot_config: dict, input_config: dict) -> dict:
    commit = _execution_gate(pilot_config)
    rows, selection = selected(split_name, pilot_config, input_config)
    D = load_split("D", config=input_config)
    fallback = n0_rate(D)
    split = load_split(split_name, config=input_config)
    by_campaign = defaultdict(list)
    for row in rows:
        by_campaign[int(split.links[row["row_id"]]["campaign"])].append(row)
    os.umask(0o077)
    destination = HERE / "runs" / datetime.now(timezone.utc).strftime(f"pilot-{split_name}-%Y%m%dT%H%M%S.%fZ")
    destination.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    outputs = []
    for campaign, campaign_rows in sorted(by_campaign.items()):
        result = run_persona_batch(
            tuple(campaign_rows), destination / f"campaign_{campaign}",
            model=pilot_config["model"], options=pilot_config["options"],
            expected_digest=pilot_config["model_digest"], fallback_rate=fallback,
            retries=pilot_config["maximum_retries_per_record"],
        )
        outputs.extend(dict(row, campaign=campaign, method_id="P1") for row in result)
    if len(outputs) != len(rows):
        raise RuntimeError("P1 pilot is incomplete")
    with (destination / "predictions.jsonl").open("w", encoding="utf-8") as handle:
        for record in outputs:
            handle.write(json.dumps(record, sort_keys=True) + "\n")
    valid = [row for row in outputs if not row["fallback"]]
    by_camp_scores = defaultdict(set)
    for row in valid:
        by_camp_scores[row["campaign"]].add(row["p_redeem"])
    score_counts = Counter(row["p_redeem"] for row in valid)
    largest_share = max(score_counts.values()) / len(valid) if valid else 1.0
    adequate_diversity = all(
        len(by_camp_scores[int(campaign)]) >= 3 for campaign, count in selection["campaign_counts"].items()
        if count >= 20
    ) and len(score_counts) > 1 and largest_share < 0.9
    legal_rate = len(valid) / len(outputs)
    report = {
        "status": "pilot_passed" if legal_rate >= 0.99 and (
            split_name == "D" or adequate_diversity
        ) else "technical_gate_failed",
        "split": split_name, "code_commit": commit, "input_run": input_config["input_run"],
        "model": pilot_config["model"], "model_digest": pilot_config["model_digest"],
        "selection": selection, "requested_rows": len(rows), "output_rows": len(outputs),
        "legal_rate": legal_rate, "distinct_legal_scores": len(score_counts),
        "largest_single_score_share": largest_share,
        "R_diversity_gate_applicable": split_name == "R",
        "distinct_scores_by_campaign": {str(k): len(v) for k, v in sorted(by_camp_scores.items())},
        "elapsed_seconds": time.monotonic() - started,
        "test_labels_opened": False,
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
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    pilot_config = json.loads((HERE / "pilot_config.json").read_text(encoding="utf-8"))
    input_config = configuration()
    rows, selection = selected(args.split, pilot_config, input_config)
    if not args.execute:
        print(json.dumps({"status": pilot_config["status"], "split": args.split,
                          "selected_rows": len(rows), "selection": selection,
                          "model_calls": 0}, indent=2, ensure_ascii=False))
        return
    print(json.dumps(run(args.split, pilot_config, input_config), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
