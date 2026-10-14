"""D/R-only exact category aggregate from Persona 1M source counts, no people."""

from __future__ import annotations

import argparse
import bisect
import json
import os
import subprocess
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_EVEN, localcontext
from fractions import Fraction
from pathlib import Path

from thesis.e1a.methods.data import ROOT, configuration, sha256
from thesis.persona1m_audit.audit import _features


HERE = Path(__file__).resolve().parent
BRANCH = "thesis-restart-20261014"
COLLISION_FREE_HUMAN_SOURCES = ("amazon", "prism", "real_human_survey", "stackoverflow", "wiki")


def _git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def _midrank(ordered: tuple[Decimal, ...], value: Decimal) -> Fraction:
    if not ordered:
        raise ValueError("D proxy distribution is empty")
    lower = bisect.bisect_left(ordered, value)
    upper = bisect.bisect_right(ordered, value)
    return Fraction(lower + upper, 2 * len(ordered))


def _load_category_counts(config: dict, plan: dict) -> tuple[tuple[tuple[str, str], ...], dict[str, dict[tuple[str, str], int]]]:
    report_path = ROOT / config["source_coverage_report"]
    if not report_path.is_file() or report_path.is_symlink() or sha256(report_path) != config["source_coverage_report_sha256"]:
        raise ValueError("frozen Persona 1M category counts differ")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if (report["status"] != "full_release_coverage_audited"
            or report["dataset_revision"] != plan["dataset_revision"]
            or report["primary_two_field_candidate"] != config["fields"]
            or report["person_rows_written"] is not False):
        raise ValueError("Persona 1M category report has an unexpected scope")
    codebook_path = HERE / "source/persona_codes.schema.json"
    if (not codebook_path.is_file() or codebook_path.is_symlink()
            or sha256(codebook_path) != plan["metadata_sha256"]["persona_codes.schema.json"]):
        raise ValueError("Persona 1M category codebook differs")
    columns = {column["id"]: column["values"] for column in
               json.loads(codebook_path.read_text(encoding="utf-8"))["columns"]}
    pairs = tuple((style, frugality) for style in columns[config["fields"][0]]
                  for frugality in columns[config["fields"][1]])
    if len(pairs) != 20 or len(set(pairs)) != 20:
        raise ValueError("Persona 1M two-field category grid differs")
    expected_names = set(config["human_sources"]) | {config["synthetic_source"]}
    if set(report["coverage_by_source"]) - expected_names:
        extra = set(report["coverage_by_source"]) - expected_names
        if extra != {"gss"} or report["coverage_by_source"]["gss"]["primary_pair_both_coded"] != 0:
            raise ValueError("unregistered Persona 1M source entered the candidate pool")
    by_source = {}
    for name in expected_names:
        summary = report["coverage_by_source"][name]
        values = summary["primary_pair_category_counts"]
        allowed = {f"{style} | {frugality}": (style, frugality) for style, frugality in pairs}
        if set(values) - set(allowed):
            raise ValueError("a category pair is outside the official codebook")
        counts = {pair: 0 for pair in pairs}
        for text, count in values.items():
            if type(count) is not int or count < 0:
                raise ValueError("candidate category count is invalid")
            counts[allowed[text]] = count
        if sum(counts.values()) != summary["primary_pair_both_coded"]:
            raise ValueError("candidate category counts differ from coverage")
        by_source[name] = counts
    if (sum(sum(by_source[name].values()) for name in config["human_sources"])
            != config["expected_human_coded_rows"]
            or sum(by_source[config["synthetic_source"]].values())
            != config["expected_synthetic_coded_rows"]):
        raise ValueError("eligible candidate pool size differs")
    return pairs, by_source


def _to_six_decimals(probabilities: tuple[Fraction, ...], places: int) -> tuple[str, ...]:
    if sum(probabilities) != 1 or any(value < 0 for value in probabilities):
        raise ValueError("category probabilities are not a distribution")
    quantum = Decimal(1).scaleb(-places)
    with localcontext() as context:
        context.prec = 50
        rounded = [(Decimal(item.numerator) / Decimal(item.denominator)).quantize(
            quantum, rounding=ROUND_HALF_EVEN
        ) for item in probabilities]
        residual = Decimal(1) - sum(rounded)
        rounded[max(range(len(probabilities)), key=lambda index: probabilities[index])] += residual
    if sum(rounded) != 1 or any(not Decimal(0) <= value <= Decimal(1) for value in rounded):
        raise ValueError("rounded category probabilities are invalid")
    return tuple(f"{value:.{places}f}" for value in rounded)


def _weighted_distribution(pairs: tuple[tuple[str, str], ...],
                           by_source: dict[str, dict[tuple[str, str], int]],
                           names: tuple[str, ...], q_coupon: Fraction,
                           q_retail: Fraction) -> tuple[Fraction, ...]:
    if not 0 <= q_coupon <= 1 or not 0 <= q_retail <= 1:
        raise ValueError("D-fitted ranks are outside [0,1]")
    totals = {name: sum(by_source[name].values()) for name in names}
    overall = sum(totals.values())
    if overall < 1 or any(totals[name] < 1 for name in names):
        raise ValueError("an included Persona 1M source has no eligible candidates")
    bargain = Fraction(2, 3) * (1 + q_coupon)
    frugal = Fraction(2, 3) * (1 + q_retail)
    output = [Fraction(0) for _ in pairs]
    for name in names:
        tilted = [by_source[name][pair]
                  * (bargain if pair[0] == "Bargain hunter" else 1)
                  * (frugal if pair[1] == "Frugal saver" else 1)
                  for pair in pairs]
        mass = sum(tilted)
        if mass <= 0:
            raise ValueError("a Persona 1M source lost all category support")
        for index, value in enumerate(tilted):
            output[index] += Fraction(totals[name], overall) * Fraction(value, mass)
    if sum(output) != 1:
        raise ValueError("source-preserving mixture does not sum to one")
    return tuple(output)


def prepare(config: dict, plan: dict, input_config: dict) -> dict:
    if (config["status"] != "confirmed_D_R_aggregate_only"
            or config["splits"] != ["D", "R"]
            or config["individual_persona_rows_permitted"] is not False
            or config["response_labels_permitted"] is not False
            or config["T_permitted"] is not False or config["model_calls_permitted"] is not False
            or config["human_sources"] != list(COLLISION_FREE_HUMAN_SOURCES)
            or config["D_proxy_fields"] != ["coupon_discount_basket_share", "retail_discount_basket_share"]
            or config["D_rank_policy"] != "midrank_less_plus_half_equal_over_729"
            or config["weight_policy"] != "bargain=(1+q_coupon)/1.5;frugal=(1+q_retail)/1.5;product_if_both"
            or config["source_mixture"] != "normalize_within_each_source_then_mix_by_eligible_source_count_over_3378"
            or config["output_decimal_places"] != 6):
        raise ValueError("D/R aggregate formula or permissions are not confirmed")
    pairs, by_source = _load_category_counts(config, plan)
    D = _features(input_config, "D")
    R = _features(input_config, "R")
    if len(D) != 729 or len(R) != 681:
        raise ValueError("D/R input row counts differ")
    fields = config["D_proxy_fields"]
    ordered = {}
    for field in fields:
        values = []
        for row in D:
            value = Decimal(row[field])
            if not value.is_finite() or not 0 <= value <= 1:
                raise ValueError("D proxy is invalid")
            values.append(value)
        ordered[field] = tuple(sorted(values))
    output = {}
    for split_name, rows in (("D", D), ("R", R)):
        records = []
        for row in rows:
            values = [Decimal(row[field]) for field in fields]
            if any(not value.is_finite() or not 0 <= value <= 1 for value in values):
                raise ValueError("D/R proxy is invalid")
            q_coupon, q_retail = (_midrank(ordered[field], value)
                                  for field, value in zip(fields, values))
            human = _weighted_distribution(pairs, by_source, tuple(config["human_sources"]),
                                           q_coupon, q_retail)
            synthetic = _weighted_distribution(pairs, by_source, (config["synthetic_source"],),
                                               q_coupon, q_retail)
            records.append({
                "row_id": row["row_id"],
                "human_aggregate": _to_six_decimals(human, config["output_decimal_places"]),
                "synthetic_aggregate": _to_six_decimals(synthetic, config["output_decimal_places"]),
            })
        output[split_name] = records
    return {"pairs": pairs, "records": output,
            "human_source_counts": {name: sum(by_source[name].values()) for name in config["human_sources"]},
            "synthetic_source_count": sum(by_source[config["synthetic_source"]].values())}


def execute(config: dict, plan: dict, input_config: dict) -> dict:
    if _git("status", "--porcelain", "--untracked-files=all"):
        raise ValueError("aggregate code or configuration has uncommitted changes")
    if _git("branch", "--show-current") != BRANCH:
        raise ValueError("aggregate code is on another branch")
    commit = _git("rev-parse", "HEAD")
    if commit != _git("rev-parse", "origin/" + BRANCH):
        raise ValueError("aggregate code has not been pushed to GitHub")
    data = prepare(config, plan, input_config)
    os.umask(0o077)
    destination = HERE / "runs" / datetime.now(timezone.utc).strftime("aggregate-DR-%Y%m%dT%H%M%S.%fZ")
    destination.mkdir(parents=True, exist_ok=False)
    for split, records in data["records"].items():
        path = destination / f"summary_{split}_PRIVATE.jsonl"
        with path.open("w", encoding="utf-8") as handle:
            for item in records:
                handle.write(json.dumps(item, sort_keys=True) + "\n")
    report = {
        "status": "D_R_aggregate_prepared_no_model",
        "code_commit": commit, "dataset_revision": plan["dataset_revision"],
        "source_coverage_sha256": config["source_coverage_report_sha256"],
        "input_run": input_config["input_run"],
        "input_hashes": {f"features_{name}.csv": input_config["input_hashes"][f"features_{name}.csv"]
                         for name in config["splits"]},
        "rows": {split: len(records) for split, records in data["records"].items()},
        "category_pairs": [list(pair) for pair in data["pairs"]],
        "human_source_counts": data["human_source_counts"],
        "synthetic_source_count": data["synthetic_source_count"],
        "output_decimal_places": config["output_decimal_places"],
        "source_mixture": config["source_mixture"],
        "D_labels_opened": False, "R_labels_opened": False,
        "T_features_opened": False, "T_labels_opened": False,
        "individual_persona_rows_opened": False, "model_calls": 0,
    }
    report_path = destination / "report.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (destination / "checksums.json").write_text(json.dumps({
        path.name: sha256(path) for path in sorted(destination.iterdir()) if path.name != "checksums.json"
    }, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"run": str(destination), **report}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.parse_args()
    config = json.loads((HERE / "aggregate_config.json").read_text(encoding="utf-8"))
    plan = json.loads((HERE / "plan.json").read_text(encoding="utf-8"))
    print(json.dumps(execute(config, plan, configuration()), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
