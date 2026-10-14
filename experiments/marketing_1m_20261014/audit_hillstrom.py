#!/usr/bin/env python3
"""Acquire the approved Hillstrom mirror and audit inputs/assignment only.

The three trailing outcomes are skipped before any value decoding. Python 3.10+
and curl suffice. Proxy is optional via PERSONA_DOWNLOAD_PROXY; artifacts stay local.
"""
import collections
import datetime
import gzip
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import subprocess

import acquire_persona as common
from audit_uci import project_features

HERE = Path(__file__).resolve().parent
LOCAL = HERE / "local_artifacts/hillstrom"
URL = "https://hillstorm1.s3.us-east-2.amazonaws.com/hillstorm_no_indices.csv.gz"
SIZE = 443070
MD5 = "a68a81291f53a14f4e29002629803ba3"
INPUTS = "recency history_segment history mens womens zip_code newbie channel segment".split()
OUTCOMES = ["visit", "conversion", "spend"]
HEADER = INPUTS + OUTCOMES
INTERNAL_HEADER = INPUTS + ["y"]
GROUPS = {"Mens E-Mail", "Womens E-Mail", "No E-Mail"}


def preflight():
    review = json.loads((HERE / "local_artifacts/hillstrom_review.json").read_text())
    if review.get("passed") is not True:
        raise RuntimeError("Passing review required")
    for name in ("audit_hillstrom.py", "test_hillstrom.py", "audit_uci.py", "acquire_persona.py"):
        if review["reviewed_files"].get(name) != hashlib.sha256((HERE / name).read_bytes()).hexdigest():
            raise RuntimeError(f"Reviewed code changed: {name}")
        relative = str((HERE / name).relative_to(common.ROOT))
        common.git("ls-files", "--error-unmatch", relative)
        common.git("diff", "--exit-code", "HEAD", "--", relative)
    head = common.git("rev-parse", "HEAD")
    if head != common.git("rev-parse", "@{upstream}"):
        raise RuntimeError("Push code before execution")
    common.safe_path(common.ROOT, str(LOCAL.relative_to(common.ROOT)))
    LOCAL.mkdir(parents=True, exist_ok=True)
    return head


def acquire():
    target = common.safe_path(LOCAL, "raw/hillstrom.csv.gz")
    part = common.safe_path(LOCAL, "raw/hillstrom.csv.gz.part")
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        command = ["curl", "--silent", "--show-error", "--fail", "--location",
                   "--proto", "=https", "--proto-redir", "=https",
                   "--connect-timeout", "20", "--max-time", "120",
                   "--max-filesize", str(SIZE), "--retry", "2", "--retry-all-errors",
                   "--output", str(part)]
        if os.environ.get("PERSONA_DOWNLOAD_PROXY"):
            command.extend(["--proxy", os.environ["PERSONA_DOWNLOAD_PROXY"]])
        result = subprocess.run(command + [URL], capture_output=True)
        if result.returncode:
            raise RuntimeError(f"Download failed; curl exit {result.returncode}")
        candidate = part
    else:
        candidate = target
    payload = candidate.read_bytes()
    if len(payload) != SIZE or hashlib.md5(payload).hexdigest() != MD5:
        raise RuntimeError("Mirror size or maintainer MD5 mismatch; file preserved")
    if candidate == part:
        part.replace(target)
    return target, {"url": URL, "bytes": len(payload), "maintainer_md5": MD5,
                    "sha256": hashlib.sha256(payload).hexdigest(),
                    "original_publication": "https://blog.minethatdata.com/2008/03/minethatdata-e-mail-analytics-and-data.html",
                    "mirror_reference": "https://github.com/maks-sh/scikit-uplift/blob/master/sklift/datasets/datasets.py",
                    "provenance_level": "maintainer-documented mirror; original-file byte equality not established"}


def audit(stream):
    import csv
    header_line = stream.readline(65537)
    header = next(csv.reader([header_line.decode("utf-8-sig").strip()]))
    if header != HEADER:
        raise RuntimeError("Unexpected source header; no records decoded")
    counters = {name: collections.Counter() for name in INPUTS}
    seen_features = set()
    seen_with_assignment = set()
    rows = 0
    total_bytes = len(header_line)
    while raw := stream.readline(65537):
        total_bytes += len(raw)
        if len(raw) > 65536 or total_bytes > 20000000:
            raise RuntimeError("Decompressed size/record limit exceeded")
        try:
            # Stop at the comma before visit; the whole three-outcome tail stays
            # unparsed. The internal y is just a stop marker, never a source label.
            values = project_features(raw, INTERNAL_HEADER, ",")
        except (ValueError, UnicodeError):
            raise RuntimeError(f"Input parser rejected record {rows + 1}; values suppressed") from None
        for name, value in zip(INPUTS, values):
            counters[name][value] += 1
        seen_features.add(hashlib.sha256(json.dumps(values[:-1]).encode()).hexdigest())
        seen_with_assignment.add(hashlib.sha256(json.dumps(values).encode()).hexdigest())
        rows += 1
    fields = {}
    for name, counts in counters.items():
        info = {"blank_count": counts[""], "unknown_count": counts["unknown"],
                "distinct_values": len(counts)}
        numeric = []
        for value in counts:
            if value == "":
                continue
            try:
                number = float(value)
                if not math.isfinite(number):
                    raise ValueError()
                numeric.append(number)
            except ValueError:
                numeric = []
                break
        if numeric:
            info.update({"numeric_min": min(numeric), "numeric_max": max(numeric)})
        elif len(counts) <= 40:
            info["categories"] = sorted(counts)
        fields[name] = info
    return {"rows": rows, "header": header, "audited_inputs": INPUTS,
            "excluded_unread_outcomes": OUTCOMES, "fields": fields,
            "assignment_counts": dict(sorted(counters["segment"].items())),
            "checks": {"expected_64000_rows": rows == 64000,
                       "expected_assignment_categories": set(counters["segment"]) == GROUPS,
                       "binary_input_columns": all(set(counters[name]) <= {"0", "1"}
                                                   for name in ("mens", "womens", "newbie"))},
            "repeated_input_combinations_beyond_first": rows - len(seen_features),
            "repeated_inputs_and_assignment_beyond_first": rows - len(seen_with_assignment),
            "duplicates_are_not_person_identity": True,
            "outcome_values_decoded": False, "outcome_statistics_computed": False}


def main():
    commit = preflight()
    target, receipt = acquire()
    with gzip.open(target, "rb") as stream:
        result = audit(stream)
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    report = {"checked_at": common.now(), "code_commit": commit,
              "python": platform.python_version(), "acquisition": receipt, "audit": result,
              "scope": "pre-treatment fields and assignment only",
              "status": "completed" if all(result["checks"].values()) else "needs_review",
              "not_evaluated": ["outcome completeness", "response rates", "causal effects",
                                "model performance", "individual identities", "1M coverage"]}
    path = LOCAL / f"audit_{stamp}.json"
    common.save_report(path, report)
    print(json.dumps({"status": report["status"], "rows": result["rows"],
                      "assignments": result["assignment_counts"], "report": str(path),
                      "outcomes_decoded": False}))


if __name__ == "__main__":
    main()
