"""Hash-check and stream the pinned Persona 1M release for aggregate field coverage.

No person rows, text descriptions, provenance IDs, household data or model
requests are written to the report. This is a source feasibility audit only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
from collections import Counter, defaultdict
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path

import pyarrow.parquet as pq

from thesis.e1a.methods.data import ROOT, sha256


HERE = Path(__file__).resolve().parent
BRANCH = "thesis-restart-20261014"
SHARD_NAME = re.compile(r"data/persona-1m-\d{4}\.parquet\Z")
READ_COLUMNS = ("source", "attributes", "null_bitmap", "attribute_overrides")


def _git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def _checked(source: Path, name: str, expected: str) -> Path:
    path = source / name
    if not path.is_file() or path.is_symlink() or sha256(path) != expected:
        raise ValueError(f"Persona 1M source file is absent or changed: {name}")
    return path


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _value(attributes: bytes, null_bitmap: bytes | None, overrides: dict[int, str],
           index: int, allowed: tuple[str, ...]) -> tuple[str, str | None]:
    if index in overrides:
        # Exact out-of-codebook values are never published. Presence is counted.
        return "override", None
    if null_bitmap is not None and null_bitmap[index // 8] & (1 << (index % 8)):
        return "missing", None
    code = (attributes[index // 2] >> (4 * (index % 2))) & 15
    if code >= len(allowed):
        return "invalid_code", None
    return "coded", allowed[code]


def summarize(plan: dict, source: Path) -> dict:
    scope = plan["coverage_audit"]
    if scope["status"] != "approved_full_release_coverage_only":
        raise ValueError("full-release coverage audit is not authorized")
    if scope["reporting"] != "aggregate_counts_only_no_persona_rows_or_identifiers":
        raise ValueError("coverage report may contain aggregate counts only")
    if len(set(scope["candidate_fields"])) != len(scope["candidate_fields"]):
        raise ValueError("candidate attribute field is repeated")
    if set(scope["shopping_joint_fields"]) - set(scope["candidate_fields"]):
        raise ValueError("joint coverage references an unregistered field")
    if (len(scope["primary_two_field_candidate"]) != 2
            or len(set(scope["primary_two_field_candidate"])) != 2
            or set(scope["primary_two_field_candidate"]) - set(scope["shopping_joint_fields"])):
        raise ValueError("primary two-field pair is not registered")

    hashes = plan["metadata_sha256"]
    codebook = json.loads(_checked(source, "persona_codes.schema.json",
                                   hashes["persona_codes.schema.json"]).read_text(encoding="utf-8"))
    manifest = json.loads(_checked(source, "manifest.json", hashes["manifest.json"]).read_text(encoding="utf-8"))
    if len(codebook["columns"]) != plan["expected_persona_fields"]:
        raise ValueError("Persona 1M codebook has a different number of fields")
    if (manifest["rows"] != plan["expected_persona_rows"]
            or manifest["bytes"] != scope["expected_bytes"]
            or len(manifest["files"]) != scope["expected_shards"]):
        raise ValueError("Persona 1M release manifest differs from the registered scope")
    expected_sources = dict(manifest["sources"])
    columns = {col["id"]: (index, tuple(col["values"]))
               for index, col in enumerate(codebook["columns"])}
    if len(columns) != len(codebook["columns"]):
        raise ValueError("Persona 1M codebook repeats a field")
    if set(scope["candidate_fields"]) - set(columns):
        raise ValueError("registered candidate attribute is absent from codebook")

    field_counters = defaultdict(Counter)
    value_counters = defaultdict(Counter)
    joint_counters = defaultdict(Counter)
    combination_counters = defaultdict(Counter)
    coded_pair_counts = defaultdict(Counter)
    coded_pair_totals = Counter()
    joint_fields = tuple(scope["shopping_joint_fields"])
    combinations_to_count = [tuple(group) for size in range(2, len(joint_fields) + 1)
                             for group in combinations(joint_fields, size)]
    source_rows = Counter()
    shard_rows = {}
    verified_bytes = 0
    paths_seen = set()
    for file in manifest["files"]:
        relative = file["path"]
        if not SHARD_NAME.fullmatch(relative) or relative in paths_seen:
            raise ValueError("unexpected or repeated Persona 1M shard path")
        paths_seen.add(relative)
        path = source / relative
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"Persona 1M shard is absent or unsafe: {relative}")
        if path.stat().st_size != file["bytes"] or _file_hash(path) != file["sha256"]:
            raise ValueError(f"Persona 1M shard hash or size differs: {relative}")
        verified_bytes += file["bytes"]
        reader = pq.ParquetFile(path)
        if reader.metadata.num_rows != file["rows"] or not set(READ_COLUMNS) <= set(reader.schema_arrow.names):
            raise ValueError(f"Persona 1M shard row count or schema differs: {relative}")
        shard_rows[relative] = file["rows"]
        for batch in reader.iter_batches(batch_size=1024, columns=list(READ_COLUMNS)):
            records = batch.to_pylist()
            for record in records:
                origin = record["source"]
                if origin not in expected_sources:
                    raise ValueError("Persona 1M shard has an unregistered source")
                source_rows[origin] += 1
                attributes = record["attributes"]
                bitmap = record["null_bitmap"]
                if len(attributes) != 645 or (bitmap is not None and len(bitmap) != 162):
                    raise ValueError("Persona 1M packed attribute shape differs")
                overrides = {item["field_index"]: item["value"]
                             for item in (record["attribute_overrides"] or [])}
                present = set()
                coded_values = {}
                for field in scope["candidate_fields"]:
                    index, allowed = columns[field]
                    kind, value = _value(attributes, bitmap, overrides, index, allowed)
                    field_counters[(origin, field)][kind] += 1
                    if kind in {"coded", "override"}:
                        present.add(field)
                    if value is not None:
                        value_counters[(origin, field)][value] += 1
                        coded_values[field] = value
                joint_counters[origin]["all_shopping_present" if set(scope["shopping_joint_fields"]) <= present
                                       else "not_all_shopping_present"] += 1
                joint_counters[origin]["any_candidate_present" if present
                                       else "no_candidate_present"] += 1
                for group in combinations_to_count:
                    if all(field in present for field in group):
                        combination_counters[origin][" & ".join(group)] += 1
                primary = scope["primary_two_field_candidate"]
                if all(field in coded_values for field in primary):
                    coded_pair_totals[origin] += 1
                    coded_pair_counts[origin][" | ".join(coded_values[field] for field in primary)] += 1

    if verified_bytes != manifest["bytes"] or dict(source_rows) != expected_sources:
        raise ValueError("verified shard totals differ from the official manifest")
    if sum(shard_rows.values()) != manifest["rows"]:
        raise ValueError("verified shard rows differ from the official manifest")
    coverage = {}
    for origin in sorted(source_rows):
        fields = {}
        for field in scope["candidate_fields"]:
            counts = field_counters[(origin, field)]
            if sum(counts.values()) != source_rows[origin]:
                raise ValueError("field coverage does not account for every source row")
            fields[field] = {
                "coded": counts["coded"], "override": counts["override"],
                "missing": counts["missing"], "invalid_code": counts["invalid_code"],
                "known_category_counts": dict(sorted(value_counters[(origin, field)].items())),
            }
        if (joint_counters[origin]["all_shopping_present"]
                + joint_counters[origin]["not_all_shopping_present"] != source_rows[origin]):
            raise ValueError("joint attribute coverage does not account for every row")
        coverage[origin] = {"rows": source_rows[origin], "fields": fields,
                            "joint": dict(sorted(joint_counters[origin].items())),
                            "primary_pair_both_coded": coded_pair_totals[origin],
                            "primary_pair_category_counts": dict(sorted(coded_pair_counts[origin].items())),
                            "combination_coverage": {
                                " & ".join(group): combination_counters[origin][" & ".join(group)]
                                for group in combinations_to_count
                            }}
    return {
        "status": "full_release_coverage_audited",
        "dataset_repo": plan["dataset_repo"], "dataset_revision": plan["dataset_revision"],
        "manifest_sha256": hashes["manifest.json"],
        "schema_sha256": hashes["persona_codes.schema.json"],
        "verified_shards": len(shard_rows), "verified_bytes": verified_bytes,
        "verified_rows": sum(source_rows.values()),
        "candidate_fields": scope["candidate_fields"],
        "joint_fields": scope["shopping_joint_fields"],
        "primary_two_field_candidate": scope["primary_two_field_candidate"],
        "coverage_by_source": coverage,
        "matching_quality_measured": False,
        "household_data_opened": False, "T_labels_opened": False,
        "model_calls": 0, "person_rows_written": False,
    }


def execute(plan: dict, source: Path) -> dict:
    if _git("status", "--porcelain", "--untracked-files=all"):
        raise ValueError("coverage code or plan has uncommitted changes")
    if _git("branch", "--show-current") != BRANCH:
        raise ValueError("coverage code is on another branch")
    commit = _git("rev-parse", "HEAD")
    if commit != _git("rev-parse", "origin/" + BRANCH):
        raise ValueError("coverage code is not backed up on GitHub")
    report = summarize(plan, source)
    report["code_commit"] = commit
    os.umask(0o077)
    destination = HERE / "runs" / datetime.now(timezone.utc).strftime("coverage-%Y%m%dT%H%M%S.%fZ")
    destination.mkdir(parents=True, exist_ok=False)
    report_path = destination / "report.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (destination / "checksums.json").write_text(
        json.dumps({"report.json": sha256(report_path)}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {"run": str(destination), **report}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=HERE / "source")
    args = parser.parse_args()
    plan = json.loads((HERE / "plan.json").read_text(encoding="utf-8"))
    result = execute(plan, args.source.resolve(strict=True))
    print(json.dumps({key: value for key, value in result.items() if key != "coverage_by_source"},
                     indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
