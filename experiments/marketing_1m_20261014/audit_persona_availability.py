#!/usr/bin/env python3
"""Aggregate 14 approved persona fields by source; never export individual data.

Requires numpy==2.4.4 and pyarrow==23.0.1. Reads only source, packed attributes,
null bitmap, sparse overrides and grounding. Does not open external outcomes.
"""
import collections
import datetime
import hashlib
import json
import math
from pathlib import Path
import platform

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

import acquire_persona as common

HERE = Path(__file__).resolve().parent
FIELDS = (
    "age_bracket", "urbanicity", "highest_education", "demo_marital_status",
    "demo_household_income", "demo_employment_status", "demo_housing_status",
    "lstyle_shopping_style", "lstyle_coffee_ritual", "lstyle_giving",
    "lstyle_banking_style", "lstyle_investment_style", "lstyle_fashion_sense",
    "pref_save_vs_spend",
)
SOURCES = ("wiki", "amazon", "stackoverflow", "prism", "gss", "real_human_survey", "synthetic")
READ_COLUMNS = ["source", "attributes", "null_bitmap", "attribute_overrides", "grounding"]
ASSIGNMENT_TYPES = {"direct", "structured_claim", "summary_inference", "unsupported"}
COUNTS = ("rows", "nonmissing", "nonempty", "invalid_base_code", "override_rows",
          "blank_override_rows", "duplicate_override_entries", "grounding_rows",
          "grounding_records", "evidence_rows", "nonempty_with_evidence_rows",
          "evidence_without_nonempty_value_rows", "confidence_present_rows",
          "confidence_finite_records", "confidence_in_unit_interval_records",
          "confidence_outside_unit_interval_records", "direct_with_evidence_rows")


def initialize(specs):
    return {source: {field[0]: {**{key: 0 for key in COUNTS}, "confidence_sum": 0.0,
                               "confidence_min": None, "confidence_max": None,
                               "assignment_records": collections.Counter()}
                     for field in specs} for source in SOURCES}


def selected_structs(array, approved_indices):
    """Filter nested entries by approved field index before reading their values."""
    flat = pc.list_flatten(array)
    parents = pc.list_parent_indices(array)
    mask = pc.is_in(flat.field("field_index"), value_set=pa.array(approved_indices, type=pa.uint16()))
    return flat.filter(mask), parents.filter(mask).to_numpy(zero_copy_only=False)


def accumulate(batch, specs, totals):
    n = batch.num_rows
    source_values = batch.column("source").to_pylist()
    if any(value not in SOURCES for value in source_values):
        raise RuntimeError("Unexpected source tag; individual contents suppressed")
    source_ids = np.array([SOURCES.index(value) for value in source_values], dtype=np.int8)
    indices = [item[1] for item in specs]
    lookup = {index: column for column, index in enumerate(indices)}
    attributes = batch.column("attributes").to_pylist()
    bitmaps = batch.column("null_bitmap").to_pylist()
    if any(value is None or len(value) != 645 for value in attributes):
        raise RuntimeError("Invalid packed attribute width")
    if any(value is not None and len(value) != 162 for value in bitmaps):
        raise RuntimeError("Invalid missingness bitmap width")
    packed = np.frombuffer(b"".join(attributes), dtype=np.uint8).reshape(n, 645)
    missing = np.frombuffer(b"".join(value if value is not None else bytes(162)
                                    for value in bitmaps), dtype=np.uint8).reshape(n, 162)
    codes = np.column_stack([(packed[:, index // 2] >> (4 * (index % 2))) & 15 for index in indices])
    declared = np.column_stack([((missing[:, index // 8] >> (index % 8)) & 1) == 0 for index in indices])
    valid = codes < np.array([item[2] for item in specs])
    invalid = declared & ~valid
    present = declared & valid
    nonempty = present.copy()
    overrides = np.zeros_like(present)
    blank_overrides = np.zeros_like(present)
    duplicate_overrides = np.zeros_like(codes, dtype=np.int64)
    entries, parents = selected_structs(batch.column("attribute_overrides"), indices)
    seen = set()
    for parent, item in zip(parents, entries.to_pylist()):
        col = lookup[item["field_index"]]
        key = (int(parent), col)
        if key in seen:
            duplicate_overrides[parent, col] += 1
        seen.add(key)
        value = item["value"]
        if value is not None:
            present[parent, col] = True
            nonempty[parent, col] = bool(str(value).strip())
            overrides[parent, col] = True
            blank_overrides[parent, col] = not bool(str(value).strip())
    grounded = np.zeros_like(present)
    evidence = np.zeros_like(present)
    confidence_present = np.zeros_like(present)
    direct_evidence = np.zeros_like(present)
    entries, parents = selected_structs(batch.column("grounding"), indices)
    # Evidence content is never materialized as Python strings or written out.
    evidence_flags = pc.fill_null(pc.greater(pc.utf8_length(pc.utf8_trim_whitespace(
        entries.field("evidence"))), 0), False).to_numpy(zero_copy_only=False)
    confidence_values = entries.field("confidence").to_pylist()
    types = entries.field("assignment_type").to_pylist()
    field_indices = entries.field("field_index").to_pylist()
    for parent, field_index, has_evidence, confidence, assignment in zip(
            parents, field_indices, evidence_flags, confidence_values, types):
        col = lookup[field_index]
        field = specs[col][0]
        stats = totals[SOURCES[source_ids[parent]]][field]
        grounded[parent, col] = True
        evidence[parent, col] |= bool(has_evidence)
        stats["grounding_records"] += 1
        tag = assignment if assignment in ASSIGNMENT_TYPES else ("missing" if not assignment else "other_unclassified")
        stats["assignment_records"][tag] += 1
        if assignment == "direct" and has_evidence:
            direct_evidence[parent, col] = True
        if confidence is not None:
            confidence_present[parent, col] = True
            if math.isfinite(confidence):
                stats["confidence_finite_records"] += 1
                stats["confidence_sum"] += confidence
                stats["confidence_min"] = confidence if stats["confidence_min"] is None else min(stats["confidence_min"], confidence)
                stats["confidence_max"] = confidence if stats["confidence_max"] is None else max(stats["confidence_max"], confidence)
                stats["confidence_in_unit_interval_records" if 0 <= confidence <= 1 else
                      "confidence_outside_unit_interval_records"] += 1
    row_metrics = {
        "nonmissing": present, "nonempty": nonempty, "invalid_base_code": invalid,
        "override_rows": overrides, "blank_override_rows": blank_overrides,
        "duplicate_override_entries": duplicate_overrides, "grounding_rows": grounded,
        "evidence_rows": evidence, "nonempty_with_evidence_rows": nonempty & evidence,
        "evidence_without_nonempty_value_rows": evidence & ~nonempty,
        "confidence_present_rows": confidence_present, "direct_with_evidence_rows": direct_evidence,
    }
    for source_id, source in enumerate(SOURCES):
        selected = source_ids == source_id
        count = int(selected.sum())
        if not count:
            continue
        for col, (field, _, _) in enumerate(specs):
            stats = totals[source][field]
            stats["rows"] += count
            for name, array in row_metrics.items():
                stats[name] += int(array[selected, col].sum())


def finalize(totals):
    for fields in totals.values():
        for stats in fields.values():
            if not (0 <= stats["nonempty"] <= stats["nonmissing"] <= stats["rows"] and
                    0 <= stats["evidence_rows"] <= stats["grounding_rows"] <= stats["rows"] and
                    stats["nonempty_with_evidence_rows"] <= min(stats["nonempty"], stats["evidence_rows"]) and
                    sum(stats["assignment_records"].values()) == stats["grounding_records"]):
                raise RuntimeError("Aggregate accounting invariant failed")
            stats["missing"] = stats["rows"] - stats["nonmissing"]
            stats["nonmissing_fraction"] = stats["nonmissing"] / stats["rows"] if stats["rows"] else None
            stats["evidence_fraction_of_nonempty"] = (stats["nonempty_with_evidence_rows"] / stats["nonempty"]
                                                       if stats["nonempty"] else None)
            stats["confidence_mean_finite_records"] = (stats["confidence_sum"] / stats["confidence_finite_records"]
                                                        if stats["confidence_finite_records"] else None)
            stats["assignment_records"] = dict(sorted(stats["assignment_records"].items()))
    return totals


def combine_sources(totals, sources, specs):
    merged = initialize(specs)["synthetic"]
    for source in sources:
        for field, _, _ in specs:
            output, item = merged[field], totals[source][field]
            for name in COUNTS:
                output[name] += item[name]
            output["confidence_sum"] += item["confidence_sum"]
            output["assignment_records"].update(item["assignment_records"])
            for key, operation in (("confidence_min", min), ("confidence_max", max)):
                if item[key] is not None:
                    output[key] = item[key] if output[key] is None else operation(output[key], item[key])
    return merged


def preflight():
    if pa.__version__ != "23.0.1" or np.__version__ != "2.4.4":
        raise RuntimeError("Expected pyarrow 23.0.1 and numpy 2.4.4; review environment change")
    review = json.loads((HERE / "local_artifacts/availability_review.json").read_text())
    if review.get("passed") is not True or tuple(review.get("fields", [])) != FIELDS:
        raise RuntimeError("Passing review for exactly the approved fields is required")
    for name in ("audit_persona_availability.py", "test_persona_availability.py", "acquire_persona.py"):
        if hashlib.sha256((HERE / name).read_bytes()).hexdigest() != review["reviewed_files"].get(name):
            raise RuntimeError(f"Reviewed code changed: {name}")
        relative = str((HERE / name).relative_to(common.ROOT))
        common.git("ls-files", "--error-unmatch", relative)
        common.git("diff", "--exit-code", "HEAD", "--", relative)
    head = common.git("rev-parse", "HEAD")
    if head != common.git("rev-parse", "@{upstream}"):
        raise RuntimeError("Push code before execution")
    return head


def main():
    head = preflight()
    entries = {item[0]: item for item in common.FILES}
    for name in ("manifest.json", "persona_codes.schema.json"):
        common.verify(common.safe_path(common.DEST, name), entries[name])
    manifest = json.loads((common.DEST / "manifest.json").read_text())
    schema = json.loads((common.DEST / "persona_codes.schema.json").read_text())
    columns = schema["columns"]
    positions = {column["id"]: (index, len(column["values"])) for index, column in enumerate(columns)}
    specs = [(field, *positions[field]) for field in FIELDS]
    totals = initialize(specs)
    rows = 0
    verified = []
    for item in manifest["files"]:
        name = item["path"]
        if name not in entries or not name.startswith("data/"):
            raise RuntimeError("Unapproved input file")
        path = common.safe_path(common.DEST, name)
        verified.append(common.verify(path, entries[name]))
        parquet = pq.ParquetFile(path)
        shard_rows = 0
        for batch in parquet.iter_batches(batch_size=1024, columns=READ_COLUMNS, use_threads=False):
            accumulate(batch, specs, totals)
            rows += batch.num_rows
            shard_rows += batch.num_rows
            if rows // 50000 != (rows - batch.num_rows) // 50000:
                print(json.dumps({"event": "progress", "rows": rows}), flush=True)
        if shard_rows != item["rows"]:
            raise RuntimeError("Shard row count mismatch")
        print(json.dumps({"event": "shard_complete", "file": name, "rows": rows}), flush=True)
    source_rows = {source: totals[source][FIELDS[0]]["rows"] for source in SOURCES}
    if rows != manifest["rows"] or source_rows != manifest["sources"]:
        raise RuntimeError("Actual source counts or total differ from manifest")
    grouped = {"human_grounded": combine_sources(totals, [s for s in SOURCES if s != "synthetic"], specs),
               "all_sources": combine_sources(totals, SOURCES, specs)}
    report = {"checked_at": common.now(), "code_commit": head, "revision": common.REVISION,
              "python": platform.python_version(), "numpy": np.__version__, "pyarrow": pa.__version__,
              "fields": list(FIELDS), "columns_read": READ_COLUMNS, "rows": rows,
              "source_rows": source_rows, "verified_files": verified,
              "by_source": finalize(totals), "by_group": finalize(grouped),
              "status": "availability_audit_complete",
              "interpretation": {"nonmissing": "valid unmasked code or non-null override; overrides applied last",
                                 "nonempty": "nonmissing excluding final blank overrides",
                                 "evidence": "at least one non-whitespace evidence string for that field",
                                 "confidence": "recorded metadata, not calibrated trust or independent verification",
                                 "direct": "source assignment tag, not independently verified truth"},
              "not_evaluated": ["individual record correctness", "evidence entailment", "joint distributions",
                                "target coverage", "matching rules", "M2 or H8 admission", "external outcomes"]}
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    output = HERE / f"local_artifacts/availability_{stamp}.json"
    common.save_report(output, report)
    print(json.dumps({"event": "complete", "rows": rows, "source_rows": source_rows,
                      "report": str(output)}), flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(json.dumps({"event": "failed", "error_type": type(error).__name__,
                          "message": "Audit stopped; individual contents suppressed"}), flush=True)
        raise SystemExit(1) from None
