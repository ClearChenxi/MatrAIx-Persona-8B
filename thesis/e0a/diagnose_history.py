"""Read-only participation feasibility after a stopped historical preparation.

Does not create personas, read transactions/outcomes, or invoke a model.
Saves a separate local diagnostic package; never alters failed-run evidence.
"""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import history
import run


def inspect(plan):
    history.validate_plan(plan, require_approval=True)
    directory = Path(plan["data_directory"])
    names = ("campaign_desc.csv", "campaign_table.csv")
    paths = {name: history.source_path(directory, name) for name in names}
    before = {name: history.digest(path) for name, path in paths.items()}
    metadata = {r["CAMPAIGN"]: r for r in history.table(directory, names[0])
                if r["CAMPAIGN"] in history.DEV and r["DESCRIPTION"] in {"TypeB", "TypeC"}}
    counts = {campaign: set() for campaign in metadata}
    row_counts = {campaign: 0 for campaign in metadata}
    for record in history.table(directory, names[1]):
        campaign = record["CAMPAIGN"]
        if campaign not in counts:
            continue
        if record["DESCRIPTION"] != metadata[campaign]["DESCRIPTION"] or not record["household_key"]:
            raise ValueError("invalid historical participation metadata")
        counts[campaign].add(record["household_key"])
        row_counts[campaign] += 1
    if before != {name: history.digest(path) for name, path in paths.items()}:
        raise ValueError("metadata sources changed during diagnosis")
    candidates = []
    for campaign, row in sorted(metadata.items(), key=lambda item: (int(item[1]["START_DAY"]), item[0])):
        cutoff = int(row["START_DAY"])
        candidates.append({"campaign": campaign, "type": row["DESCRIPTION"],
                           "start_day": cutoff, "end_day": int(row["END_DAY"]),
                           "proposed_window": [cutoff - plan["history_window_days"], cutoff - 1],
                           "participation_rows": row_counts[campaign], "unique_participants": len(counts[campaign]),
                           "requested_size_possible_from_participation_only": len(counts[campaign]) >= plan["sample_size"],
                           "positive_purchase_eligibility_checked": False})
    return {"requested_sample_size": plan["sample_size"], "campaign": plan["campaign"],
            "development_metadata": candidates, "source_hashes": before,
            "tables_read": list(names), "labels_read": False, "model_calls": 0,
            "limits": "Participation counts bound eligibility; no transaction or observation completeness audit."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True, help="Stopped preparation package")
    parser.add_argument("--plan", type=Path, default=history.HERE / "history-plan.json", help="Exact original plan matching failed manifest")
    args = parser.parse_args()
    manifest = json.loads((args.run / "agent_manifest.json").read_text())
    expected_hashes = json.loads((args.run / "checksums.json").read_text())
    if run.hashes(args.run) != expected_hashes:
        raise ValueError("failed evidence checksum mismatch")
    if manifest["status"] != "failed" or manifest["actual_generations"] != 0:
        raise ValueError("this diagnostic only applies to zero-call failed preparation")
    if any(args.run.rglob("official_request_*.json")) or any(args.run.rglob("ollama_request.json")):
        raise ValueError("generation requests exist in stopped package")
    plan_path = args.plan
    if history.digest(plan_path) != manifest["plan_sha256"]:
        raise ValueError("plan changed since failed preparation")
    result = inspect(json.loads(plan_path.read_text()))
    result.update(failed_run=str(args.run.resolve()), failed_run_code_commit=manifest["code_commit"],
                  failed_evidence_files_verified=len(expected_hashes), diagnostic_code_commit=run.git("rev-parse", "HEAD"),
                  diagnostic_script_sha256=history.digest(Path(__file__)),
                  diagnostic_worktree_clean=not bool(run.git("status", "--porcelain", "--untracked-files=all")))
    destination = history.HERE / "runs" / ("history-diagnostic-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ"))
    destination.mkdir(parents=True, exist_ok=False)
    run.save(destination / "diagnostic.json", result)
    run.save(destination / "checksums.json", run.hashes(destination))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"Separate local diagnostic evidence: {destination}")


if __name__ == "__main__":
    main()
