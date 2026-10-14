"""Describe two observed D transaction shares without opening any response labels."""

from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal
from fractions import Fraction
from pathlib import Path

from thesis.e1a.methods.data import ROOT, configuration, sha256
from thesis.persona1m_audit.audit import _features


HERE = Path(__file__).resolve().parent
BRANCH = "thesis-restart-20261014"


def _git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def summarize(plan: dict, input_config: dict) -> dict:
    scope = plan["proxy_diagnostic"]
    if (scope["status"] != "approved_D_features_only" or scope["labels_permitted"] is not False
            or scope["fields"] != ["coupon_discount_basket_share", "retail_discount_basket_share"]):
        raise ValueError("D-only observed proxy diagnostic scope differs")
    rows = _features(input_config, "D")
    quantiles = [Fraction(value) for value in scope["quantiles"]]
    if quantiles != [Fraction(1, 3), Fraction(1, 2), Fraction(2, 3), Fraction(9, 10)]:
        raise ValueError("D proxy diagnostic quantiles differ")
    summary = {}
    for field in scope["fields"]:
        values = []
        for row in rows:
            if row[field] == "":
                raise ValueError("a required observed D proxy is missing")
            value = Decimal(row[field])
            if not value.is_finite() or not Decimal(0) <= value <= Decimal(1):
                raise ValueError("an observed D proxy is outside [0,1]")
            values.append(value)
        ordered = sorted(values)
        histogram = Counter(values)
        summary[field] = {
            "rows": len(values), "unique_values": len(histogram),
            "zero_rows": histogram[Decimal(0)], "one_rows": histogram[Decimal(1)],
            "min": str(ordered[0]), "max": str(ordered[-1]),
            "nearest_rank_quantiles": {
                label: str(ordered[max(0, math.ceil(float(fraction) * len(ordered)) - 1)])
                for label, fraction in zip(scope["quantiles"], quantiles)
            },
        }
    return {
        "status": "D_X_proxy_variation_audited",
        "input_run": input_config["input_run"],
        "D_features_sha256": input_config["input_hashes"]["features_D.csv"],
        "rows": len(rows), "fields": summary,
        "D_labels_opened": False, "R_opened": False,
        "T_features_opened": False, "T_labels_opened": False,
        "persona_records_opened": False, "model_calls": 0,
    }


def execute(plan: dict, input_config: dict) -> dict:
    if _git("status", "--porcelain", "--untracked-files=all"):
        raise ValueError("proxy diagnostic code or plan has uncommitted changes")
    if _git("branch", "--show-current") != BRANCH:
        raise ValueError("proxy diagnostic is on another branch")
    commit = _git("rev-parse", "HEAD")
    if commit != _git("rev-parse", "origin/" + BRANCH):
        raise ValueError("proxy diagnostic code is not backed up on GitHub")
    report = summarize(plan, input_config)
    report["code_commit"] = commit
    os.umask(0o077)
    directory = HERE / "runs" / datetime.now(timezone.utc).strftime("proxy-D-%Y%m%dT%H%M%S.%fZ")
    directory.mkdir(parents=True, exist_ok=False)
    report_path = directory / "report.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (directory / "checksums.json").write_text(
        json.dumps({"report.json": sha256(report_path)}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {"run": str(directory), **report}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.parse_args()
    plan = json.loads((HERE / "plan.json").read_text(encoding="utf-8"))
    print(json.dumps(execute(plan, configuration()), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
