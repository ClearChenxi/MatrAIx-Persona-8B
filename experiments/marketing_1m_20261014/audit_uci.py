#!/usr/bin/env python3
"""Acquire three approved UCI files and audit only non-outcome columns.

Python 3.10+ and curl; no third-party dependencies. Proxy, if needed, is supplied
through PERSONA_DOWNLOAD_PROXY. Reports and original data stay under local_artifacts.
Outcome fields are removed at byte boundaries before decoding CSV values.
"""
import collections
import csv
import datetime
import hashlib
import io
import json
import math
import os
from pathlib import Path
import platform
import subprocess
import sys
import zipfile

import acquire_persona as common

HERE = Path(__file__).resolve().parent
LOCAL = HERE / "local_artifacts/uci"
ORIGIN = "https://archive.ics.uci.edu/ml/machine-learning-databases"
DOWNLOADS = (
    ("bank.zip", f"{ORIGIN}/00222/bank.zip", 600000),
    ("bank-additional.zip", f"{ORIGIN}/00222/bank-additional.zip", 500000),
    ("in-vehicle-coupon-recommendation.csv", f"{ORIGIN}/00603/in-vehicle-coupon-recommendation.csv", 2300000),
)
# On subsequent acquisitions this local receipt is the frozen snapshot contract;
# files must match its fingerprints, never silently refreshed from a mutable URL.
EXCLUDED = frozenset({"y", "Y", "poutcome"})
BANK = "age job marital education default balance housing loan contact day month duration campaign pdays previous poutcome y".split()
ADDITIONAL = "age job marital education default housing loan contact month day_of_week duration campaign pdays previous poutcome emp.var.rate cons.price.idx cons.conf.idx euribor3m nr.employed y".split()
COUPON = "destination passanger weather temperature time coupon expiration gender age maritalStatus has_children education occupation income car Bar CoffeeHouse CarryAway RestaurantLessThan20 Restaurant20To50 toCoupon_GEQ5min toCoupon_GEQ15min toCoupon_GEQ25min direction_same direction_opp Y".split()
CSV_MEMBERS = {
    "bank.zip": {"bank-full.csv": BANK, "bank.csv": BANK},
    "bank-additional.zip": {"bank-additional/bank-additional-full.csv": ADDITIONAL,
                            "bank-additional/bank-additional.csv": ADDITIONAL},
}
DICTIONARIES = {"bank.zip": "bank-names.txt",
                "bank-additional.zip": "bank-additional/bank-additional-names.txt"}


def fingerprint(path):
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            hasher.update(block)
    return hasher.hexdigest()


def preflight():
    review = json.loads((HERE / "local_artifacts/uci_review.json").read_text())
    if review.get("passed") is not True:
        raise RuntimeError("Passing review required")
    for name in ("audit_uci.py", "test_audit_uci.py", "acquire_persona.py"):
        if review["reviewed_files"].get(name) != fingerprint(HERE / name):
            raise RuntimeError(f"Reviewed code changed: {name}")
        relative = str((HERE / name).relative_to(common.ROOT))
        common.git("ls-files", "--error-unmatch", relative)
        common.git("diff", "--exit-code", "HEAD", "--", relative)
    head = common.git("rev-parse", "HEAD")
    if head != common.git("rev-parse", "@{upstream}"):
        raise RuntimeError("Code backup must precede execution")
    common.safe_path(common.ROOT, str(LOCAL.relative_to(common.ROOT)))
    LOCAL.mkdir(parents=True, exist_ok=True)
    return head


def acquire_all():
    receipt_path = LOCAL / "acquisition_receipt.json"
    prior = json.loads(receipt_path.read_text()) if receipt_path.exists() else None
    receipt = {"started_at": common.now(), "files": [], "publisher_checksums": "not provided",
               "source_version_policy": "official URLs; first received bytes frozen by SHA-256"}
    for name, url, ceiling in DOWNLOADS:
        target = common.safe_path(LOCAL, "raw/" + name)
        part = common.safe_path(LOCAL, "raw/" + name + ".part")
        headers = common.safe_path(LOCAL, "raw/" + name + ".headers")
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            command = ["curl", "--silent", "--show-error", "--fail", "--location",
                       "--proto", "=https", "--proto-redir", "=https",
                       "--connect-timeout", "20", "--max-time", "120",
                       "--max-filesize", str(ceiling), "--retry", "2",
                       "--retry-all-errors", "--output", str(part),
                       "--dump-header", str(headers)]
            proxy = os.environ.get("PERSONA_DOWNLOAD_PROXY")
            if proxy:
                command.extend(["--proxy", proxy])
            result = subprocess.run(command + [url], capture_output=True)
            if result.returncode:
                raise RuntimeError(f"Download failed: {name}, curl exit {result.returncode}")
            if part.stat().st_size > ceiling:
                raise RuntimeError(f"Download exceeds authorized ceiling: {name}")
            part.replace(target)
        size = target.stat().st_size
        if size == 0 or size > ceiling:
            raise RuntimeError(f"Invalid size: {name}")
        digest = fingerprint(target)
        if prior:
            old = {entry["path"]: entry for entry in prior["files"]}.get(name)
            if not old or old["sha256"] != digest or old["bytes"] != size:
                raise RuntimeError(f"Frozen acquisition fingerprint changed: {name}")
        receipt["files"].append({"path": name, "url": url, "bytes": size, "sha256": digest})
    if not prior:
        receipt["finished_at"] = common.now()
        common.save_report(receipt_path, receipt)
    return prior or receipt


def project_features(raw, header, delimiter):
    """Discard excluded byte spans before UTF-8 decoding or csv.reader.

    Only single physical-line records are accepted. The target must be last;
    its tail is not tokenized. Interior excluded fields are scanned only for
    CSV delimiters/quotes and never decoded, retained, emitted or counted.
    """
    if header[-1] not in ("y", "Y") or len(set(header)) != len(header):
        raise ValueError("Unexpected outcome position or duplicate headers")
    tokens = []
    start = 0
    position = 0
    quoted = False
    index = 0
    sep = ord(delimiter)
    while index < len(raw) and position < len(header) - 1:
        byte = raw[index]
        if byte == 34:
            if quoted and index + 1 < len(raw) and raw[index + 1] == 34:
                index += 2
                continue
            quoted = not quoted
        elif byte == sep and not quoted:
            if header[position] not in EXCLUDED:
                tokens.append(raw[start:index])
            position += 1
            start = index + 1
        index += 1
    if position != len(header) - 1 or quoted:
        raise ValueError("Malformed or multiline feature record; content suppressed")
    # A constant dummy column preserves a single empty feature as a CSV field.
    text = (bytes([sep]).join(tokens) + bytes([sep]) + b'""').decode("utf-8")
    try:
        decoded = next(csv.reader([text], delimiter=delimiter, strict=True))[:-1]
    except csv.Error:
        raise ValueError("Malformed feature CSV; content suppressed") from None
    if len(decoded) != len([name for name in header if name not in EXCLUDED]):
        raise ValueError("Feature width mismatch; content suppressed")
    return decoded


def audit_csv(stream, expected_header, delimiter, name):
    header = next(csv.reader([stream.readline().decode("utf-8-sig").strip()], delimiter=delimiter))
    if header != expected_header:
        raise RuntimeError(f"Header differs from approved schema: {name}")
    names = [field for field in header if field not in EXCLUDED]
    counters = {field: collections.Counter() for field in names}
    seen = collections.Counter()
    rows = 0
    for physical_line, raw in enumerate(stream, start=2):
        if not raw.strip():
            raise RuntimeError(f"Unexpected blank record in {name}, line {physical_line}")
        try:
            values = project_features(raw, header, delimiter)
        except (ValueError, UnicodeError):
            raise RuntimeError(f"Feature parser rejected {name}, line {physical_line}; no values logged") from None
        for field, value in zip(names, values):
            counters[field][value] += 1
        # This identifies repeated feature combinations, never repeated persons.
        seen[hashlib.sha256(json.dumps(values, ensure_ascii=False).encode()).hexdigest()] += 1
        rows += 1
    fields = {}
    for field, counts in counters.items():
        numeric = []
        numeric_only = True
        for value in counts:
            if value in ("", "unknown", "?", "nan", "NA"):
                continue
            try:
                number = float(value)
                if not math.isfinite(number):
                    raise ValueError()
                numeric.append(number)
            except ValueError:
                numeric_only = False
                break
        item = {"blank_count": counts[""], "unknown_count": counts["unknown"],
                "other_missing_marker_count": sum(counts[x] for x in ("?", "nan", "NA")),
                "distinct_values": len(counts)}
        if numeric_only and numeric:
            item.update({"numeric_min": min(numeric), "numeric_max": max(numeric)})
        elif len(counts) <= 40:
            item["categories"] = sorted(counts)
        if field == "pdays":
            item["sentinel_counts"] = {key: counts[key] for key in ("-1", "999")}
        fields[field] = item
    return {"dataset_file": name, "rows": rows, "header": header,
            "excluded_unread_fields": [field for field in header if field in EXCLUDED],
            "audited_feature_count": len(names), "fields": fields,
            "unique_feature_combinations": len(seen),
            "repeated_feature_rows_beyond_first": rows - len(seen),
            "duplicate_interpretation": "feature combinations only; no person identity inference",
            "outcome_values_decoded": False, "outcome_statistics_computed": False}


def inspect_archives():
    results = []
    dictionaries = []
    for archive_name, members in CSV_MEMBERS.items():
        with zipfile.ZipFile(LOCAL / "raw" / archive_name) as archive:
            items = archive.infolist()
            if len(items) > 40 or sum(item.file_size for item in items) > 40000000:
                raise RuntimeError("Archive exceeds approved expansion limit")
            if len({item.filename for item in items}) != len(items):
                raise RuntimeError("Duplicate archive member names")
            for member, header in members.items():
                with archive.open(member) as stream:
                    results.append(audit_csv(stream, header, ";", member))
            dictionary = DICTIONARIES[archive_name]
            payload = archive.read(dictionary)
            target = common.safe_path(LOCAL, "dictionaries/" + Path(dictionary).name)
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists() and target.read_bytes() != payload:
                raise RuntimeError("Existing dictionary differs; preserve both before change")
            target.write_bytes(payload)
            dictionaries.append({"path": str(target.relative_to(LOCAL)),
                                 "sha256": hashlib.sha256(payload).hexdigest()})
    with (LOCAL / "raw/in-vehicle-coupon-recommendation.csv").open("rb") as stream:
        results.append(audit_csv(stream, COUPON, ",", "in-vehicle-coupon-recommendation.csv"))
    return results, dictionaries


def main():
    commit = preflight()
    receipt = acquire_all()
    datasets, dictionaries = inspect_archives()
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    report = {"checked_at": common.now(), "code_commit": commit,
              "python": platform.python_version(), "acquisition": receipt,
              "scope": "structure and non-outcome features only", "datasets": datasets,
              "dictionaries": dictionaries, "status": "completed",
              "not_evaluated": ["current or historical outcome values", "label integrity",
                                "response rates", "prediction quality", "1M matching coverage"]}
    path = LOCAL / f"feature_audit_{stamp}.json"
    common.save_report(path, report)
    print(json.dumps({"status": "completed", "report": str(path),
                      "files": [{"name": item["dataset_file"], "rows": item["rows"],
                                 "excluded": item["excluded_unread_fields"]} for item in datasets]}))


if __name__ == "__main__":
    main()
