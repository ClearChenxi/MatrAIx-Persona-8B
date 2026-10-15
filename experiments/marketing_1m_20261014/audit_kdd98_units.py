#!/usr/bin/env python3
"""Audit account-unit warning flags using only TCODE, GENDER, ADATE_2.

No filtering or person matching. Run test_kdd98_units.py then this file after
hash-bound review and GitHub backup. Pinned ZIP bytes remain local.
"""
import collections
import datetime
import json
from pathlib import Path
import platform
import re
import zipfile

import acquire_persona as common
import audit_kdd98 as base

HERE = Path(__file__).resolve().parent
WHITELIST = ["TCODE", "GENDER", "ADATE_2"]
CODE = ("audit_kdd98_units.py", "test_kdd98_units.py", "audit_kdd98.py", "acquire_persona.py")
# All paired or explicitly plural titles enumerated in pinned cup98dic.txt.
# These are warnings about addressee plurality, not person-identity claims.
PAIRED_OR_PLURAL = frozenset({
    1001, 1002, 2002, 3003, 4002, 4004, 10002, 10010, 11002,
    12002, 13002, 14002, 15002, 17002, 18002, 21002, 22002,
    28028, 31002, 59002, 72002, 65, 131, 135,
})


def preflight():
    path = HERE / "local_artifacts/kdd98_units_review.json"
    review = json.loads(path.read_text())
    if review.get("passed") is not True or review.get("decoded_whitelist") != WHITELIST:
        raise RuntimeError("Passing scope-matched review required")
    for name in CODE:
        if base.digest(HERE / name) != review["reviewed_files"].get(name):
            raise RuntimeError("Reviewed code changed")
        rel = str((HERE / name).relative_to(common.ROOT))
        common.git("ls-files", "--error-unmatch", rel)
        common.git("diff", "--exit-code", "HEAD", "--", rel)
    commit = common.git("rev-parse", "HEAD")
    if commit != common.git("rev-parse", "@{upstream}"):
        raise RuntimeError("Push reviewed code before execution")
    common.safe_path(common.ROOT, str(base.LOCAL.relative_to(common.ROOT)))
    return commit, review


def title_codes():
    path = common.safe_path(base.LOCAL, "cup98dic.txt")
    if base.digest(path) != base.DOCS["cup98dic.txt"]:
        raise RuntimeError("Dictionary fingerprint mismatch")
    block = path.read_text().split("TCODE                       Donor title code", 1)[1].split("STATE                       State abbreviation", 1)[0]
    codes = {int(value) for value in re.findall(r"^\s+(\d+)\s+=", block, re.M)}
    if not PAIRED_OR_PLURAL <= codes or 0 not in codes:
        raise RuntimeError("Title classification disagrees with dictionary")
    return codes


def classify(title, gender, date, known):
    code = base.integer(title)
    if code in PAIRED_OR_PLURAL:
        kind = "paired_or_plural_title"
    elif title in ("", ".") or code == 0:
        kind = "no_title_information"
    elif code in known:
        kind = "other_documented_title_not_proof_of_one_person"
    else:
        kind = "undocumented_or_invalid_title"
    g = gender if gender in ("J", "F", "M", "U") else "blank" if not gender else "undocumented"
    n = base.integer(date)
    period = "1997-04" if n == 9704 else "1997-06" if n == 9706 else "other_or_invalid"
    return kind, g, period


def audit(stream, width, expected_rows, expected_bytes, known, learning):
    raw = stream.readline(65537)
    names = base.project_bytes(raw, {i: str(i) for i in range(width)}, width)
    header = [names[str(i)] for i in range(width)]
    if len(set(header)) != width or not set(WHITELIST) <= set(header):
        raise RuntimeError("Unexpected header; no values read")
    if set(header) & base.TARGETS != (base.TARGETS if learning else set()):
        raise RuntimeError("Unexpected outcome header; no values read")
    selected = {i: name for i, name in enumerate(header) if name in WHITELIST}
    counts = collections.Counter()
    rows = 0
    total = len(raw)
    while raw := stream.readline(65537):
        total += len(raw)
        if total > expected_bytes or rows >= expected_rows:
            raise RuntimeError("Dimensions exceed approved bounds")
        try:
            values = base.project_bytes(raw, selected, width)
            counts[classify(values["TCODE"], values["GENDER"], values["ADATE_2"], known)] += 1
        except (ValueError, UnicodeError):
            raise RuntimeError("Malformed input; values suppressed") from None
        rows += 1
    is_title = lambda key: key[0] == "paired_or_plural_title"
    return {"rows": rows, "decompressed_bytes": total,
            "paired_or_plural_title": sum(n for k, n in counts.items() if is_title(k)),
            "gender_J": sum(n for k, n in counts.items() if k[1] == "J"),
            "both_signals": sum(n for k, n in counts.items() if is_title(k) and k[1] == "J"),
            "either_signal": sum(n for k, n in counts.items() if is_title(k) or k[1] == "J"),
            "plural_title_gender_not_J": sum(n for k, n in counts.items() if is_title(k) and k[1] != "J"),
            "contingency": [{"title_class": k[0], "gender_class": k[1], "target_month": k[2], "count": n}
                            for k, n in sorted(counts.items())],
            "checks": {"rows_match": rows == expected_rows, "bytes_match": total == expected_bytes,
                       "counts_sum_to_rows": sum(counts.values()) == rows},
            "no_signal_is_not_proof_of_single_person": True,
            "outcomes_decoded": False, "rows_excluded": 0, "matching_performed": False}


def main():
    commit, review = preflight()
    known = title_codes()
    splits, inputs = {}, []
    for split, name, size, sha, member, unpacked, rows, columns in base.ARCHIVES:
        path = common.safe_path(base.LOCAL, name)
        if path.stat().st_size != size or base.digest(path) != sha:
            raise RuntimeError("Input fingerprint mismatch")
        with zipfile.ZipFile(path) as archive:
            if archive.namelist() != [member] or archive.getinfo(member).file_size != unpacked:
                raise RuntimeError("Unexpected archive structure")
            with archive.open(member) as stream:
                splits[split] = audit(stream, columns, rows, unpacked, known, split == "learning")
        inputs.append({"file": name, "sha256": sha, "bytes": size, "crc_verified": True})
    report = {"checked_at": common.now(), "code_commit": commit, "python": platform.python_version(),
              "reviewed_files": review["reviewed_files"], "decoded_whitelist": WHITELIST,
              "dictionary_sha256": base.DOCS["cup98dic.txt"], "inputs": inputs,
              "paired_or_plural_title_codes": sorted(PAIRED_OR_PLURAL), "splits": splits,
              "scope": "Unit warning flags; not a frozen exclusion policy or a person-identity audit",
              "status": "completed" if all(all(v["checks"].values()) for v in splits.values()) else "needs_review"}
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    output = common.safe_path(base.LOCAL, f"unit_audit_{stamp}.json")
    common.save_report(output, report)
    print(json.dumps({"status": report["status"], "report": str(output),
                      "splits": {k: {n: v[n] for n in ("rows", "paired_or_plural_title", "gender_J", "both_signals", "either_signal", "plural_title_gender_not_J")}
                                 for k, v in splits.items()}}))


if __name__ == "__main__":
    main()
