"""Read-only replay and artifact-integrity check of D/R-only aggregates."""

from __future__ import annotations

import json
import sys
from decimal import Decimal
from pathlib import Path

from thesis.e1a.methods.data import configuration, sha256
from thesis.persona1m_audit.aggregate import HERE, prepare
from thesis.persona1m_audit.audit import _features


def verify(run: Path) -> dict:
    run = run.resolve(strict=True)
    config = json.loads((HERE / "aggregate_config.json").read_text(encoding="utf-8"))
    plan = json.loads((HERE / "plan.json").read_text(encoding="utf-8"))
    input_config = configuration()
    report = json.loads((run / "report.json").read_text(encoding="utf-8"))
    hashes = json.loads((run / "checksums.json").read_text(encoding="utf-8"))
    actual = {path.name for path in run.iterdir() if path.is_file() and path.name != "checksums.json"}
    expected_files = {"report.json", "summary_D_PRIVATE.jsonl", "summary_R_PRIVATE.jsonl"}
    if actual != expected_files or set(hashes) != expected_files:
        raise ValueError("aggregate artifact inventory differs")
    for name, expected in hashes.items():
        if sha256(run / name) != expected:
            raise ValueError(f"aggregate artifact differs from checksum: {name}")
    if (report["status"] != "D_R_aggregate_prepared_no_model"
            or report["dataset_revision"] != plan["dataset_revision"]
            or report["source_coverage_sha256"] != config["source_coverage_report_sha256"]
            or report["input_run"] != input_config["input_run"]
            or report["output_decimal_places"] != config["output_decimal_places"]
            or report["source_mixture"] != config["source_mixture"]
            or not all(report[key] is False for key in (
                "D_labels_opened", "R_labels_opened", "T_features_opened", "T_labels_opened",
                "individual_persona_rows_opened"
            )) or report["model_calls"] != 0):
        raise ValueError("aggregate report scope or provenance differs")
    replay = prepare(config, plan, input_config)
    if (report["category_pairs"] != [list(pair) for pair in replay["pairs"]]
            or report["human_source_counts"] != replay["human_source_counts"]
            or report["synthetic_source_count"] != replay["synthetic_source_count"]):
        raise ValueError("category grid or source proportions differ")
    if (sum(report["human_source_counts"].values()) != 3378
            or report["synthetic_source_count"] != 400000):
        raise ValueError("candidate counts differ from registered pool")
    input_hashes = {f"features_{name}.csv": input_config["input_hashes"][f"features_{name}.csv"]
                    for name in ("D", "R")}
    if report["input_hashes"] != input_hashes:
        raise ValueError("frozen D/R feature hashes differ")
    variation = {}
    for split in ("D", "R"):
        records = [json.loads(line) for line in (run / f"summary_{split}_PRIVATE.jsonl").read_text(
            encoding="utf-8"
        ).splitlines()]
        expected = replay["records"][split]
        if (records != expected or report["rows"][split] != len(records)
                or [row["row_id"] for row in records]
                != [row["row_id"] for row in _features(input_config, split)]):
            raise ValueError(f"{split} aggregate differs from frozen X/S replay")
        for row in records:
            if set(row) != {"row_id", "human_aggregate", "synthetic_aggregate"}:
                raise ValueError("unexpected field in family-level aggregate")
            for key in ("human_aggregate", "synthetic_aggregate"):
                numbers = [Decimal(value) for value in row[key]]
                if (len(numbers) != 20 or any(not value.is_finite() or not 0 <= value <= 1 for value in numbers)
                        or sum(numbers) != 1):
                    raise ValueError("rounded 20-category distribution is invalid")
        variation[split] = {
            "rows": len(records),
            "distinct_human_aggregates": len({tuple(row["human_aggregate"]) for row in records}),
            "distinct_synthetic_aggregates": len({tuple(row["synthetic_aggregate"]) for row in records}),
        }
    return {
        "status": "verified", "code_commit": report["code_commit"],
        "files_verified": len(hashes), "splits": variation,
        "human_candidate_rows": sum(report["human_source_counts"].values()),
        "synthetic_candidate_rows": report["synthetic_source_count"],
        "labels_opened": False, "T_opened": False, "model_calls": 0,
    }


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: python -m thesis.persona1m_audit.verify_aggregate <run-directory>")
    print(json.dumps(verify(Path(sys.argv[1])), indent=2, ensure_ascii=False))
