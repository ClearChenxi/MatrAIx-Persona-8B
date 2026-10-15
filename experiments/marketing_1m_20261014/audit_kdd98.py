#!/usr/bin/env python3
"""KDD98 structure and explicit non-outcome whitelist audit. Python 3.10+.

Run test_kdd98.py first, then this script. Both require a local, hash-bound
static review and a clean, pushed code version. No network or data extraction.
CSV delimiters are scanned as bytes; only approved fields are decoded.
Current and previous donation/response values are never decoded or summarized.
"""
import collections
import datetime
import hashlib
import json
from pathlib import Path
import platform
import re
import zipfile

import acquire_persona as common

HERE = Path(__file__).resolve().parent
LOCAL = HERE / "local_artifacts/kdd98"
CODE = ("audit_kdd98.py", "test_kdd98.py", "acquire_persona.py")
TARGETS = {"TARGET_B", "TARGET_D"}
BACKGROUND = "AGE AGEFLAG HOMEOWNR NUMCHLD INCOME GENDER DATASRCE LIFESRC MAILCODE DOMAIN GEOCODE2".split()
LIFESTYLE = "COLLECT1 VETERANS BIBLE CATLG HOMEE PETS CDPLAY STEREO PCOWNERS PHOTO CRAFTS FISHER GARDENIN BOATS WALKER KIDSTUFF CARDS PLATES".split()
DATES = [f"ADATE_{i}" for i in range(2, 25)] + ["MAXADATE"]
WHITELIST = ["CONTROLN"] + BACKGROUND + LIFESTYLE + DATES
ARCHIVES = (
    ("learning", "cup98lrn.zip", 36468735,
     "9517071741c689cf9a27aad4a84d453dc9675d2b2981f90d16a776457daf15bf",
     "cup98LRN.txt", 117167952, 95412, 481),
    ("validation", "cup98val.zip", 36763018,
     "3112e81cc1d830a534078e65b93067805b199848a35fd41db32f1a4286dbab96",
     "cup98VAL.txt", 117943347, 96367, 479),
)
DOCS = {
    "readme": "15491c26710317b512be7e935df838c727eeb9c21c267e66eba3da5082f3c62c",
    "cup98doc.txt": "3d5299c180184d417c51ac39c00eedf151b992d89b60354ce3dd56cfbe7da73e",
    "cup98dic.txt": "44f9b7098bd5a8628cb70f83b1a408dc9c7c9881cc3461cfb5b01774aca3602a",
    "valtargt.readme": "8053a2862ae009790a2811c539fa0d6363c761b8e0863700e5cc3d04b0e79f3d",
}


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            value.update(block)
    return value.hexdigest()


def preflight():
    review = json.loads((HERE / "local_artifacts/kdd98_audit_review.json").read_text())
    if review.get("passed") is not True or review.get("decoded_whitelist") != WHITELIST:
        raise RuntimeError("Passing scope-matched review required")
    for name in CODE:
        if review["reviewed_files"].get(name) != digest(HERE / name):
            raise RuntimeError("Reviewed code changed")
        relative = str((HERE / name).relative_to(common.ROOT))
        common.git("ls-files", "--error-unmatch", relative)
        common.git("diff", "--exit-code", "HEAD", "--", relative)
    head = common.git("rev-parse", "HEAD")
    if head != common.git("rev-parse", "@{upstream}"):
        raise RuntimeError("Push reviewed code before execution")
    common.safe_path(common.ROOT, str(LOCAL.relative_to(common.ROOT)))
    return head, review


def project_bytes(raw, selected, width):
    """One physical CSV record; multiline quoted fields fail closed.

    Selection precedes text decoding. Even excluded field payloads must obey
    CSV framing so that a later selected field cannot be misidentified.
    """
    if len(raw) > 65536:
        raise ValueError("Record too long")
    if raw.endswith(b"\n"):
        raw = raw[:-1]
        if raw.endswith(b"\r"):
            raw = raw[:-1]
    if b"\r" in raw or b"\n" in raw:
        raise ValueError("Multiline record unsupported")
    if b'"' not in raw:
        parts = raw.split(b",")
        if len(parts) != width:
            raise ValueError("Wrong column count")
        return {name: parts[i].decode("ascii").strip() for i, name in selected.items()}
    result = {}
    pos = 0
    column = 0
    while True:
        start = pos
        quoted = pos < len(raw) and raw[pos] == 34
        if quoted:
            pos += 1
            while True:
                end = raw.find(b'"', pos)
                if end < 0:
                    raise ValueError("Unclosed CSV quote")
                pos = end + 1
                if pos < len(raw) and raw[pos] == 34:
                    pos += 1
                    continue
                break
            if pos < len(raw) and raw[pos] != 44:
                raise ValueError("Unexpected byte after quote")
        else:
            end = raw.find(b",", pos)
            pos = len(raw) if end < 0 else end
            if b'"' in raw[start:pos]:
                raise ValueError("Quote inside unquoted field")
        if column in selected:
            payload = raw[start:pos]
            if quoted:
                payload = payload[1:-1].replace(b'""', b'"')
            result[selected[column]] = payload.decode("ascii").strip()
        column += 1
        if pos == len(raw):
            break
        pos += 1
    if column != width:
        raise ValueError("Wrong column count")
    return result


def integer(value):
    if not re.fullmatch(r"[0-9]+(?:\.0+)?", value):
        return None
    return int(value.split(".")[0])


def month(value):
    number = integer(value)
    if number is None or not 100 <= number <= 9912 or not 1 <= number % 100 <= 12:
        return None
    # KDD98-specific 20th-century interpretation; not a general YYMM converter.
    return (1900 + number // 100) * 12 + number % 100 - 1


def date_relation(reference, other):
    target, previous = month(reference), month(other)
    if target is None or previous is None:
        return "missing_or_invalid"
    if previous < target:
        return "before"
    return "same_month_day_order_unknown" if previous == target else "after"


def categories(counter, allowed, unknown=()):
    return {"blank_count": counter[""],
            "explicit_unknown_count": sum(counter[x] for x in unknown),
            "known_code_counts": {x: counter[x] for x in sorted(allowed)},
            "unexpected_nonblank_count": sum(n for x, n in counter.items() if x and x not in allowed)}


def summarize(name, counter):
    if name in DATES:
        valid = collections.Counter()
        invalid = 0
        for value, count in counter.items():
            parsed = month(value)
            if parsed is not None:
                year, mon = divmod(parsed, 12)
                valid[f"{year:04d}-{mon + 1:02d}"] += count
            elif value:
                invalid += count
        return {"blank_count": counter[""], "invalid_nonblank_count": invalid,
                "month_counts": dict(sorted(valid.items()))}
    if name in ("AGE", "NUMCHLD"):
        values = [integer(v) for v in counter if v and integer(v) is not None]
        positive = [n for n in values if n > 0]
        zero = sum(n for v, n in counter.items() if integer(v) == 0)
        return {"blank_count": counter[""], "zero_count": zero,
                "invalid_nonblank_count": sum(n for v, n in counter.items() if v and integer(v) is None),
                "positive_min": min(positive) if positive else None,
                "positive_max": max(positive) if positive else None,
                "zero_meaning": "documented_missing" if name == "AGE" else "unresolved",
                "positive_count": sum(n for v, n in counter.items() if integer(v) is not None and integer(v) > 0)}
    if name in LIFESTYLE:
        return {**categories(counter, {"Y", "N"}), "blank_is_negative": False}
    allowed = {
        "AGEFLAG": {"E", "I"}, "HOMEOWNR": {"H", "U"},
        "INCOME": set("1234567"), "GENDER": {"M", "F", "U", "J"},
        "DATASRCE": set("123"), "LIFESRC": set("123"), "MAILCODE": {"B"},
        "DOMAIN": {f"{letter}{n}" for letter in "UCSTR" for n in range(1, 5 if letter == "U" else 4)},
    }
    if name == "GEOCODE2":
        return {"blank_count": counter[""], "distinct_nonblank_codes": len(set(counter) - {""}),
                "category_semantics": "county-size code boundaries unresolved; values not mapped"}
    info = categories(counter, allowed[name], {"U", "J"} if name == "GENDER" else {"U"} if name == "HOMEOWNR" else ())
    if name == "MAILCODE":
        info["blank_meaning"] = "address_OK_per_dictionary_not_missing"
    if name == "INCOME":
        info["bin_boundaries_known"] = False
    return info


def audit_stream(stream, expected_columns, expected_rows, expected_bytes, learning):
    line = stream.readline(65537)
    header_map = project_bytes(line, {}, expected_columns)
    # Header is metadata, not an observation. CSV header labels are all decoded.
    header_map = project_bytes(line, {i: str(i) for i in range(expected_columns)}, expected_columns)
    header = [header_map[str(i)] for i in range(expected_columns)]
    if len(set(header)) != expected_columns or not set(WHITELIST) <= set(header):
        raise RuntimeError("Missing or duplicate header names; records not read")
    if (set(header) & TARGETS) != (TARGETS if learning else set()):
        raise RuntimeError("Unexpected outcome columns; records not read")
    selected = {i: name for i, name in enumerate(header) if name in WHITELIST}
    counters = {name: collections.Counter() for name in WHITELIST if name != "CONTROLN"}
    relations = {name: collections.Counter() for name in DATES if name != "ADATE_2"}
    identifiers = set()
    rows = duplicates = bad_ids = 0
    total = len(line)
    age_flags = collections.Counter()
    any_lifestyle = source_without_flags = flags_without_source = 0
    while raw := stream.readline(65537):
        total += len(raw)
        if total > expected_bytes or rows >= expected_rows:
            raise RuntimeError("Input exceeds approved dimensions")
        try:
            values = project_bytes(raw, selected, expected_columns)
        except (ValueError, UnicodeError):
            raise RuntimeError(f"CSV rejected at record {rows + 1}; payload suppressed") from None
        identifier = integer(values["CONTROLN"])
        if identifier is None or identifier <= 0:
            bad_ids += 1
        else:
            duplicates += identifier in identifiers
            identifiers.add(identifier)
        for name, counter in counters.items():
            counter[values[name]] += 1
        for name, counter in relations.items():
            counter[date_relation(values["ADATE_2"], values[name])] += 1
        valid_age = integer(values["AGE"])
        age_status = "positive" if valid_age is not None and valid_age > 0 else "missing_or_invalid"
        flag = values["AGEFLAG"] if values["AGEFLAG"] in ("", "E", "I") else "unexpected"
        age_flags[f"{age_status}|{flag or 'blank'}"] += 1
        has_flag = any(values[name] in ("Y", "N") for name in LIFESTYLE)
        has_source = values["LIFESRC"] in ("1", "2", "3")
        any_lifestyle += has_flag
        source_without_flags += has_source and not has_flag
        flags_without_source += has_flag and not has_source
        rows += 1
    report = {"rows": rows, "columns": len(header), "header": header,
              "decompressed_bytes": total, "unique_controln": len(identifiers),
              "duplicate_controln_beyond_first": duplicates, "missing_or_invalid_controln": bad_ids,
              "fields": {name: summarize(name, counter) for name, counter in counters.items()},
              "mailing_dates_relative_to_ADATE_2": {k: dict(sorted(v.items())) for k, v in relations.items()},
              "age_by_quality_flag": dict(sorted(age_flags.items())),
              "lifestyle_observability": {"any_explicit_Y_or_N": any_lifestyle,
                                          "source_present_all_flags_unobserved": source_without_flags,
                                          "explicit_flag_source_missing_or_invalid": flags_without_source},
              "checks": {"expected_rows": rows == expected_rows, "expected_bytes": total == expected_bytes,
                         "unique_valid_record_keys": duplicates == 0 and bad_ids == 0},
              "outcome_values_decoded": False, "outcome_statistics_computed": False}
    return report, identifiers


def linkage(left, right):
    return {"overlap_controln": len(left & right), "union_controln": len(left | right),
            "record_keys_do_not_prove_individual_identity": True}


def main():
    commit, review = preflight()
    for name, expected in DOCS.items():
        if digest(common.safe_path(LOCAL, name)) != expected:
            raise RuntimeError("Documentation fingerprint changed")
    reports, keys, inputs = {}, {}, []
    for split, name, size, sha, member, unpacked, rows, columns in ARCHIVES:
        path = common.safe_path(LOCAL, name)
        if path.stat().st_size != size or digest(path) != sha:
            raise RuntimeError("Pinned archive fingerprint mismatch")
        with zipfile.ZipFile(path) as archive:
            if archive.namelist() != [member] or archive.getinfo(member).file_size != unpacked:
                raise RuntimeError("Unexpected ZIP members or expanded size")
            with archive.open(member) as stream:
                reports[split], keys[split] = audit_stream(stream, columns, rows, unpacked, split == "learning")
            reports[split]["zip_crc_verified_by_complete_read"] = True
        inputs.append({"file": name, "bytes": size, "sha256": sha, "member": member})
        print(json.dumps({"split": split, "rows": reports[split]["rows"], "crc_verified": True}), flush=True)
    links = linkage(keys["learning"], keys["validation"])
    checks = {"headers_align_except_targets": [n for n in reports["learning"]["header"] if n not in TARGETS] == reports["validation"]["header"],
              "record_keys_disjoint": links["overlap_controln"] == 0,
              "expected_union": links["union_controln"] == 191779,
              "split_checks": all(all(r["checks"].values()) for r in reports.values())}
    report = {"checked_at": common.now(), "code_commit": commit, "python": platform.python_version(),
              "reviewed_files": review["reviewed_files"], "inputs": inputs, "documentation_sha256": DOCS,
              "sha256_provenance": "local download fingerprints; not publisher-supplied hashes",
              "decoded_whitelist": WHITELIST, "splits": reports, "linkage": links, "checks": checks,
              "status": "structural_checks_passed" if all(checks.values()) else "needs_review",
              "not_assessed": ["outcome completeness", "response rates", "historical donation values/dates",
                               "person identity equivalence", "complete outcome window", "per-record feature observation dates",
                               "1M matching or coverage", "model performance", "admission H1-H8"],
              "mapping_applied": False, "rows_removed_or_imputed": False}
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    path = common.safe_path(LOCAL, f"structure_audit_{stamp}.json")
    common.save_report(path, report)
    print(json.dumps({"status": report["status"], "report": str(path), "checks": checks}))


if __name__ == "__main__":
    main()
