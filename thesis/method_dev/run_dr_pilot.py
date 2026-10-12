"""Run the deterministic D/R Persona probability technical pilot."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import time
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "thesis/e1_data/runs/e1-data-20261011T042328.304949Z/private"
FALLBACK = 87 / 1410
MODEL = "qwen3.5:9b"
SEED = "20261012-dr-pilot-v1"
PILOT_D = 20
PILOT_R = 100

PROMPTS = {
    "v1": (
        "Estimate the probability that this household redeems at least one valid coupon "
        "during the supplied campaign. Use only the facts provided. Return exactly one JSON "
        "object with a finite number from 0 to 1 under the key p_redeem."
    ),
    "v2": (
        "Using only the observed household facts and campaign facts, estimate the chance of "
        "at least one valid coupon redemption during this campaign. Treat unknown information "
        "as unknown and do not invent facts. Return only {\"p_redeem\": number}, where number "
        "is finite and between 0 and 1."
    ),
    "v3": (
        "Make a calibrated probability estimate for at least one valid coupon redemption in "
        "this campaign. Base it only on the supplied household history and campaign facts; "
        "do not use IDs, labels, or unstated demographics. Output no prose, only JSON exactly "
        "as {\"p_redeem\": number} with number in [0,1]."
    ),
}


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def read_csv(path: Path) -> list[dict[str, str]]:
    if "T" in path.name or "/T" in str(path):
        raise RuntimeError(f"T path rejected: {path}")
    with path.open(newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def read_jsonl(path: Path) -> list[dict]:
    if "T" in path.name or "/T" in str(path):
        raise RuntimeError(f"T path rejected: {path}")
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def load_split(split: str) -> list[dict]:
    links = read_csv(DATA / f"links_{split}.csv")
    labels = {row["row_id"]: row for row in read_csv(DATA / f"labels_{split}.csv")}
    groups = {row["row_id"]: row for row in read_csv(DATA / f"groups_{split}.csv")}
    index = read_csv(DATA / f"model_payload_index_{split}.csv")
    payloads = read_jsonl(DATA / f"model_payloads_{split}.jsonl")
    if not (len(links) == len(labels) == len(groups) == len(index) == len(payloads)):
        raise RuntimeError(f"{split} input lengths differ")
    by_id = {}
    for metadata, payload in zip(index, payloads):
        by_id[metadata["row_id"]] = payload
    rows = []
    for link in links:
        row_id = link["row_id"]
        if row_id not in by_id or row_id not in labels or row_id not in groups:
            raise RuntimeError(f"{split} row join failed: {row_id}")
        rows.append({"row_id": row_id, "campaign": int(link["campaign"]),
                     "payload": by_id[row_id], "label": int(labels[row_id]["y1_redeemed"]),
                     "group": tuple(groups[row_id][key] for key in (
                         "frequency_group", "historical_redemption_group", "target_category_match_group"))})
    return rows


def select_d(rows: list[dict]) -> list[dict]:
    audit_path = DATA / "persona_audit_sample_D.csv"
    selected_ids = {row["row_id"] for row in read_csv(audit_path)}
    selected = [row for row in rows if row["row_id"] in selected_ids]
    if len(selected) != PILOT_D:
        raise RuntimeError("the frozen D boundary sample is incomplete")
    return sorted(selected, key=lambda row: digest(f"{SEED}:D:{row['row_id']}"))


def select_r(rows: list[dict]) -> list[dict]:
    strata = defaultdict(list)
    for row in rows:
        strata[(row["campaign"], row["group"])].append(row)
    for values in strata.values():
        values.sort(key=lambda row: digest(f"{SEED}:R:{row['row_id']}"))
    selected = []
    cursors = {key: 0 for key in strata}
    keys = sorted(strata)
    while len(selected) < PILOT_R and keys:
        progressed = False
        for key in keys:
            cursor = cursors[key]
            if cursor < len(strata[key]):
                selected.append(strata[key][cursor])
                cursors[key] += 1
                progressed = True
                if len(selected) == PILOT_R:
                    break
        if not progressed:
            break
    return selected


def call_ollama(payload: dict, attempt: int) -> tuple[float | None, dict]:
    request = urllib.request.Request(
        "http://127.0.0.1:11434/api/chat",
        data=json.dumps(payload, ensure_ascii=False).encode(),
        headers={"Content-Type": "application/json"},
    )
    started = time.monotonic()
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(request, timeout=120) as response:
            raw = response.read()
        result = json.loads(raw)
        content = result.get("message", {}).get("content", "")
        parsed = json.loads(content)
        value = parsed.get("p_redeem")
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
            raise ValueError("p_redeem is not a finite number")
        value = float(value)
        if not 0 <= value <= 1:
            raise ValueError("p_redeem outside [0,1]")
        return value, {"status": "valid", "elapsed_seconds": time.monotonic() - started,
                       "response": result, "raw_content": content, "attempt": attempt}
    except Exception as error:  # retain exact failure and continue under the preregistered rule
        return None, {"status": "invalid", "elapsed_seconds": time.monotonic() - started,
                      "error": f"{type(error).__name__}: {error}", "attempt": attempt}


def ap(y: list[int], p: list[float]) -> float | None:
    positives = sum(y)
    if positives == 0 or positives == len(y):
        return None
    order = sorted(range(len(y)), key=lambda i: (-p[i], digest(f"{SEED}:tie:{i}")))
    hits = 0
    total = 0.0
    for rank, index in enumerate(order, 1):
        if y[index]:
            hits += 1
            total += hits / rank
    return total / positives


def run_variant(rows: list[dict], variant: str, output_dir: Path) -> list[dict]:
    results = []
    for row in rows:
        messages = [
            {"role": "system", "content": "Return only the requested JSON. Do not invent facts."},
            {"role": "user", "content": json.dumps(row["payload"], ensure_ascii=False, sort_keys=True)
             + "\n\n" + PROMPTS[variant]},
        ]
        request = {"model": MODEL, "messages": messages, "stream": False, "format": "json",
                   "think": False, "options": {"temperature": 0, "seed": 20261012,
                   "num_ctx": 8192, "num_predict": 64}}
        attempts = []
        value = None
        for attempt in range(1, 4):
            value, record = call_ollama(request, attempt)
            attempts.append(record)
            if value is not None:
                break
        results.append({"row_id": row["row_id"], "campaign": row["campaign"], "label": row["label"],
                        "p_redeem": FALLBACK if value is None else value,
                        "fallback": value is None, "attempts": attempts, "variant": variant})
    return results


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", action="store_true", required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    d_rows = load_split("D")
    r_rows = load_split("R")
    selected_d = select_d(d_rows)
    selected_r = select_r(r_rows)
    if len(selected_d) != PILOT_D or len(selected_r) != PILOT_R:
        raise RuntimeError("fixed pilot sample could not be formed")
    destination = args.output or ROOT / "thesis/method_dev/runs" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    destination.mkdir(parents=True, exist_ok=False)
    (destination / "selection.json").write_text(json.dumps({
        "seed": SEED, "D_rows": [row["row_id"] for row in selected_d],
        "R_rows": [row["row_id"] for row in selected_r], "T_read": 0,
        "model_calls_before_run": 0,
    }, indent=2), encoding="utf-8")
    all_results = {"D": {}, "R": {}}
    for variant in PROMPTS:
        all_results["D"][variant] = run_variant(selected_d, variant, destination)
    d_scores = {}
    for variant, records in all_results["D"].items():
        by_campaign = defaultdict(list)
        for record in records:
            by_campaign[record["campaign"]].append(record)
        values = [ap([item["label"] for item in group], [item["p_redeem"] for item in group])
                  for group in by_campaign.values()]
        values = [value for value in values if value is not None]
        d_scores[variant] = sum(values) / len(values) if values else None
    identifiable = {variant: score for variant, score in d_scores.items() if score is not None}
    selected_variant = None
    selection_status = "not_identifiable_on_boundary_sample"
    if identifiable:
        selected_variant = max(identifiable, key=lambda variant: (identifiable[variant], -len(PROMPTS[variant])))
        selection_status = "selected_by_D_macro_AP"
    if selected_variant is None:
        selected_variant = "v1"
        selection_status = "provisional_simplest_prompt_for_R_technical_checks_only"
    all_results["R"][selected_variant] = run_variant(selected_r, selected_variant, destination)
    (destination / "predictions.json").write_text(json.dumps(all_results, ensure_ascii=False), encoding="utf-8")
    summary = {"model": MODEL, "selected_variant": selected_variant,
               "prompt_selection_status": selection_status, "D_macro_AP": d_scores,
               "D_valid_rate": {}, "R_valid_rate": {}, "T_read": 0, "fallback": FALLBACK}
    for split, variants in all_results.items():
        for variant, records in variants.items():
            summary[f"{split}_valid_rate"][variant] = sum(not r["fallback"] for r in records) / len(records)
    (destination / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps({"status": "dr_pilot_complete", "run": str(destination), **summary}, ensure_ascii=False))


if __name__ == "__main__":
    main()
