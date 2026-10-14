"""Read-only checks of P1 D/R technical pilot outputs and raw-call inventory."""

from __future__ import annotations

import json
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path

from thesis.e1a.methods.data import configuration, sha256
from thesis.e1b.pilot import HERE, selected


def verify(run: Path) -> dict:
    run = run.resolve(strict=True)
    report = json.loads((run / "report.json").read_text(encoding="utf-8"))
    if report["split"] not in {"D", "R"} or report["test_labels_opened"] is not False:
        raise ValueError("pilot report has an invalid data permission")
    checksums = json.loads((run / "checksums.json").read_text(encoding="utf-8"))
    actual = {path.relative_to(run).as_posix() for path in run.rglob("*")
              if path.is_file() and path.name != "checksums.json"}
    if set(checksums) != actual:
        raise ValueError("pilot artifact inventory differs from checksum list")
    for relative, expected in checksums.items():
        if sha256(run / relative) != expected:
            raise ValueError(f"pilot artifact checksum differs: {relative}")
    pilot_config = json.loads((HERE / "pilot_config.json").read_text(encoding="utf-8"))
    selected_rows, selection = selected(report["split"], pilot_config, configuration())
    if selection != report["selection"] or len(selected_rows) != report["requested_rows"]:
        raise ValueError("pilot selection differs from recorded X/S-only cohort")
    predictions = [json.loads(line) for line in (run / "predictions.jsonl").read_text(encoding="utf-8").splitlines()]
    expected_ids = {row["row_id"] for row in selected_rows}
    if len(predictions) != len(expected_ids) or {row["row_id"] for row in predictions} != expected_ids:
        raise ValueError("pilot predictions are incomplete or duplicated")
    legal = []
    by_campaign = defaultdict(set)
    for record in predictions:
        score = record["p_redeem"]
        if (record["method_id"] != "P1" or not isinstance(score, (int, float))
                or not math.isfinite(score) or not 0 <= score <= 1
                or record["model_digest"] != report["model_digest"]
                or not 1 <= len(record["attempts"]) <= pilot_config["maximum_retries_per_record"] + 1):
            raise ValueError("invalid P1 pilot prediction or attempt history")
        if record["fallback"]:
            if record["status"] != "fallback" or any(a["status"] == "passed" for a in record["attempts"]):
                raise ValueError("fallback conflicts with raw attempt status")
        else:
            if record["status"] != "passed" or record["attempts"][-1]["status"] != "passed":
                raise ValueError("passed prediction lacks a passing raw attempt")
            legal.append(score)
            by_campaign[record["campaign"]].add(score)
    counts = Counter(legal)
    legal_rate = len(legal) / len(predictions)
    largest = max(counts.values()) / len(legal) if legal else 1.0
    if (abs(report["legal_rate"] - legal_rate) > 1e-12
            or abs(report["largest_single_score_share"] - largest) > 1e-12
            or report["distinct_legal_scores"] != len(counts)
            or report["distinct_scores_by_campaign"] != {str(k): len(v) for k, v in sorted(by_campaign.items())}):
        raise ValueError("pilot diversity summary differs from predictions")
    adequate_diversity = len(counts) > 1 and largest < 0.9 and all(
        len(by_campaign[int(campaign)]) >= 3
        for campaign, count in selection["campaign_counts"].items() if count >= 20
    )
    passed = legal_rate >= 0.99 and (report["split"] == "D" or adequate_diversity)
    if (report["R_diversity_gate_applicable"] != (report["split"] == "R")
            or report["status"] != ("pilot_passed" if passed else "technical_gate_failed")):
        raise ValueError("pilot technical gate status differs from raw predictions")
    actual_calls = 0
    for campaign, count in selection["campaign_counts"].items():
        folder = run / f"campaign_{campaign}"
        child = json.loads((folder / "run_manifest.json").read_text(encoding="utf-8"))
        if child["method"] != "P1" or child["input_rows"] != count or child["output_rows"] != count:
            raise ValueError("campaign call manifest differs from cohort")
        if child["model_digest"] != report["model_digest"] or child["T_labels_opened"] is not False:
            raise ValueError("campaign model or label permission differs")
        actual_calls += child["actual_model_calls"]
    if actual_calls > len(predictions) * (pilot_config["maximum_retries_per_record"] + 1):
        raise ValueError("P1 pilot exceeded maximum call budget")
    return {
        "status": "verified", "pilot_status": report["status"],
        "code_commit": report["code_commit"], "rows": len(predictions),
        "actual_model_calls": actual_calls, "files_verified": len(checksums),
        "test_labels_opened": False,
    }


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: python -m thesis.e1b.verify_pilot <run-directory>")
    print(json.dumps(verify(Path(sys.argv[1])), indent=2, ensure_ascii=False))
