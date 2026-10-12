"""Run and resume the full D comparison of the frozen prompt candidates.

This module deliberately imports the input loader and request contract from the
technical pilot.  It never opens T files and writes one append-only event for
every model attempt before moving to the next attempt.
"""

from __future__ import annotations

import argparse
import json
import math
import subprocess
import time
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import run_dr_pilot as pilot


VARIANTS = tuple(pilot.PROMPTS)
MODEL = pilot.MODEL
OLLAMA = "http://127.0.0.1:11434"


def git_commit(root: Path) -> str | None:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=root, text=True, timeout=10
        ).strip()
    except (OSError, subprocess.SubprocessError):
        return None


def source_hashes() -> dict[str, str]:
    hashes = {}
    for path in (pilot.DATA / "links_D.csv", pilot.DATA / "labels_D.csv",
                 pilot.DATA / "groups_D.csv", pilot.DATA / "model_payload_index_D.csv",
                 pilot.DATA / "model_payloads_D.jsonl"):
        hashes[path.name] = pilot.digest(path.read_bytes().decode("utf-8"))
    return hashes


def model_metadata() -> dict:
    request = urllib.request.Request(f"{OLLAMA}/api/show", data=json.dumps({"name": MODEL}).encode(),
                                     headers={"Content-Type": "application/json"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=30) as response:
        raw = response.read()
    parsed = json.loads(raw)
    return {"model": MODEL, "digest": pilot.digest(raw.decode("utf-8")),
            "api_show": parsed}


def brier(y: list[int], p: list[float]) -> float:
    return sum((float(score) - value) ** 2 for value, score in zip(y, p)) / len(y)


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _append_jsonl(path: Path, value: dict) -> None:
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n")
        stream.flush()


def _request(row: dict, variant: str) -> dict:
    return {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": "Return only the requested JSON. Do not invent facts."},
            {"role": "user", "content": json.dumps(row["payload"], ensure_ascii=False, sort_keys=True)
             + "\n\n" + pilot.PROMPTS[variant]},
        ],
        "stream": False,
        "format": "json",
        "think": False,
        "options": {"temperature": 0, "seed": 20261012, "num_ctx": 8192, "num_predict": 64},
    }


def run_one(request: dict, attempt: int) -> tuple[float | None, dict]:
    started = time.monotonic()
    try:
        encoded = json.dumps(request, ensure_ascii=False).encode("utf-8")
        http_request = urllib.request.Request(f"{OLLAMA}/api/chat", data=encoded,
                                              headers={"Content-Type": "application/json"})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(http_request, timeout=120) as response:
            raw = response.read()
        result = json.loads(raw)
        content = result.get("message", {}).get("content", "")
        parsed = json.loads(content)
        value = parsed.get("p_redeem")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("p_redeem is not numeric")
        value = float(value)
        if not math.isfinite(value) or not 0 <= value <= 1:
            raise ValueError("p_redeem is outside [0,1]")
        return value, {"status": "valid", "attempt": attempt,
                       "elapsed_seconds": time.monotonic() - started,
                       "response": result, "raw_content": content}
    except Exception as error:  # the frozen fallback rule handles all failures
        return None, {"status": "invalid", "attempt": attempt,
                      "elapsed_seconds": time.monotonic() - started,
                      "error": f"{type(error).__name__}: {error}"}


def run(output: Path, resume: bool = True) -> dict:
    rows = sorted(pilot.load_split("D"), key=lambda row: pilot.digest(f"{pilot.SEED}:D:{row['row_id']}"))
    if len(rows) != 729:
        raise RuntimeError(f"expected 729 D rows, found {len(rows)}")
    output.mkdir(parents=True, exist_ok=True)
    manifest_path = output / "manifest.json"
    predictions_path = output / "predictions.jsonl"
    events_path = output / "events.jsonl"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("D_rows") != len(rows) or tuple(manifest.get("variants", [])) != VARIANTS:
            raise RuntimeError("existing run manifest does not match the frozen D comparison")
    else:
        manifest = {
            "status": "running", "created_at": datetime.now(timezone.utc).isoformat(),
            "model": MODEL, "variants": list(VARIANTS), "D_rows": len(rows),
            "expected_calls": len(rows) * len(VARIANTS), "T_read": 0,
            "seed": pilot.SEED, "prompt_hashes": {name: pilot.digest(pilot.PROMPTS[name]) for name in VARIANTS},
            "input_sha256": source_hashes(), "code_commit": git_commit(pilot.ROOT),
            "model_metadata": model_metadata(),
        }
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    completed = {}
    if resume:
        for record in _read_jsonl(predictions_path):
            completed[(record["variant"], record["row_id"])] = record
    total = len(rows) * len(VARIANTS)
    for variant in VARIANTS:
        for index, row in enumerate(rows, 1):
            key = (variant, row["row_id"])
            if key in completed:
                continue
            request = _request(row, variant)
            request_hash = pilot.digest(json.dumps(request, ensure_ascii=False, sort_keys=True))
            value = None
            attempts = []
            for attempt in range(1, 4):
                value, result = run_one(request, attempt)
                attempts.append(result)
                pilot.append_event(events_path, {
                    "variant": variant, "row_id": row["row_id"], "campaign": row["campaign"],
                    "attempt": attempt, "request_sha256": request_hash, "result": result,
                })
                if value is not None:
                    break
            record = {
                "variant": variant, "row_id": row["row_id"], "campaign": row["campaign"],
                "label": row["label"], "p_redeem": pilot.FALLBACK if value is None else value,
                "fallback": value is None, "attempts": attempts, "request_sha256": request_hash,
            }
            _append_jsonl(predictions_path, record)
            completed[key] = record
            if len(completed) % 25 == 0 or len(completed) == total:
                print(json.dumps({"status": "running", "completed": len(completed), "total": total,
                                  "variant": variant, "row_index": index}, ensure_ascii=False), flush=True)
    scores = {}
    for variant in VARIANTS:
        by_campaign = defaultdict(list)
        for record in completed.values():
            if record["variant"] == variant:
                by_campaign[record["campaign"]].append(record)
        campaign_scores = {}
        campaign_brier = {}
        for campaign, records in sorted(by_campaign.items()):
            ys = [record["label"] for record in records]
            ps = [record["p_redeem"] for record in records]
            campaign_scores[str(campaign)] = pilot.ap(ys, ps)
            campaign_brier[str(campaign)] = brier(ys, ps)
        identifiable = [value for value in campaign_scores.values() if value is not None]
        scores[variant] = {"macro_AP": sum(identifiable) / len(identifiable) if identifiable else None,
                           "campaign_AP": campaign_scores, "campaign_Brier": campaign_brier,
                           "valid_rate": sum(not r["fallback"] for r in completed.values()
                                              if r["variant"] == variant) / len(rows),
                           "fallback_count": sum(r["fallback"] for r in completed.values()
                                                  if r["variant"] == variant)}
    available = [name for name in VARIANTS if scores[name]["macro_AP"] is not None]
    if not available:
        raise RuntimeError("no D activity has identifiable AP")
    best_score = max(scores[name]["macro_AP"] for name in available)
    tied = [name for name in available if best_score - scores[name]["macro_AP"] <= 0.0001]
    ranked = sorted(tied, key=lambda name: (len(pilot.PROMPTS[name]), name))
    summary = {"status": "complete", "completed": len(completed), "expected": total,
               "selected_variant": ranked[0], "selection_rule": "D activity-macro AP; ties <= 0.0001 use simpler prompt",
               "scores": scores, "T_read": 0, "model": MODEL, "code_commit": manifest.get("code_commit")}
    manifest["status"] = "complete"
    manifest["completed_at"] = datetime.now(timezone.utc).isoformat()
    manifest["summary"] = summary
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--no-resume", action="store_true")
    args = parser.parse_args()
    print(json.dumps(run(args.output, resume=not args.no_resume), ensure_ascii=False))


if __name__ == "__main__":
    main()
