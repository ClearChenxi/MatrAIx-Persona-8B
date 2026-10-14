"""Ordinary local LLM controls L0/L1/L2, explicitly outside Agent workflow."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from thesis.e1b.agent_workflow import (
    INSTRUMENT, bind_task, build_task, e0a_run, partition_fields,
)


def messages_for(method: str, row: dict[str, str], task: Path, destination: Path) -> list[dict]:
    """Return the exact local model messages for one direct-LLM condition."""
    if method not in {"L0", "L1", "L2"}:
        raise ValueError("unknown direct LLM control")
    from playground.inprocess.survey_eval import build_survey_task_prompt
    from playground.survey_task_content import load_survey_task_content_for_task_path

    household, _ = partition_fields(row)
    content = load_survey_task_content_for_task_path(str(task), repo_root=e0a_run.ROOT)
    user = build_survey_task_prompt(instrument=content.instrument)
    if method == "L0":
        system = "Use only the campaign facts in the task. No household behavior is supplied. Return the specified probability JSON."
    elif method == "L1":
        system = (
            "Estimate household coupon redemption from these observed pre-campaign fields. "
            "Treat every field as an observation, not an inferred motive.\n\n"
            + "\n".join(f"- {name}: {value}" for name, value in household.items())
        )
    else:
        # Same exact model-visible Persona text and task prompt as P1, but no
        # PersonaJsonSurvey/HostEnvironment invocation. Identical messages
        # must be reported honestly under the manual's §17 merge rule.
        prepared = e0a_run.preflight(
            {"profiles": [{"id": "direct_household", "behavior": household}]},
            destination, task=task,
        )
        return prepared[0][2]
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def run_direct_batch(
    method: str, rows: tuple[dict[str, str], ...], destination: Path, *,
    model: str, options: dict, expected_digest: str, fallback_rate: float,
    retries: int = 2,
) -> list[dict]:
    if method not in {"L0", "L1", "L2"} or retries not in (0, 1, 2):
        raise ValueError("invalid direct LLM condition or retry count")
    if not 0 <= fallback_rate <= 1:
        raise ValueError("invalid historical fallback rate")
    destination.mkdir(parents=True, exist_ok=False)
    e0a_run.bootstrap()
    task = build_task(rows, destination)
    metadata_config = {"model": model}
    before = e0a_run.metadata(metadata_config)
    if before["model_digest"] != expected_digest:
        raise ValueError("local model differs from frozen control configuration")
    e0a_run.save(destination / "ollama_before.json", before)
    actual_calls = 0
    output = []
    with bind_task(task):
        # L0 has only activity facts, so one score is generated per activity.
        scored_rows = rows[:1] if method == "L0" else rows
        for index, row in enumerate(scored_rows, start=1):
            trial = destination / f"case_{index:04d}"
            trial.mkdir()
            messages = messages_for(method, row, task, trial)
            e0a_run.save(trial / "expected_messages.json", messages)
            message_hash = hashlib.sha256(e0a_run.encode(messages)).hexdigest()
            score = None
            attempts = []
            for attempt in range(1, retries + 2):
                request = {
                    "model": model, "messages": messages, "stream": False,
                    "format": "json", "think": False, "options": options,
                    "keep_alive": "5m",
                }
                e0a_run.save(trial / f"ollama_request_{attempt}.json", request)
                actual_calls += 1
                try:
                    status, headers, body = e0a_run.native("/api/chat", request)
                    (trial / f"ollama_response_{attempt}.raw.json").write_bytes(body)
                    e0a_run.save(trial / f"response_headers_{attempt}.json", headers)
                    if status != 200:
                        raise RuntimeError(f"Ollama HTTP {status}")
                    response = json.loads(body)
                    if (response.get("model") != model or response.get("done") is not True
                            or response.get("done_reason") != "stop"):
                        raise ValueError("incomplete or wrong-model generation")
                    score = e0a_run.probability(
                        json.loads(response["message"]["content"]), INSTRUMENT,
                    )
                    attempts.append({"number": attempt, "status": "passed", "score": score})
                    break
                except Exception as error:
                    attempts.append({"number": attempt, "status": "failed", "error": str(error)})
            fallback = score is None
            output.append({
                "row_id": row["row_id"], "method_id": method,
                "p_redeem": fallback_rate if fallback else score,
                "status": "fallback" if fallback else "passed", "fallback": fallback,
                "attempts": attempts, "message_sha256": message_hash,
                "model_digest": before["model_digest"],
            })
    if method == "L0":
        one = output[0]
        output = [dict(one, row_id=row["row_id"], broadcast_from=one["row_id"])
                  for row in rows]
    after = e0a_run.metadata(metadata_config)
    e0a_run.save(destination / "ollama_after.json", after)
    if before["model_digest"] != after["model_digest"]:
        raise RuntimeError("local model changed during control run")
    e0a_run.save(destination / "run_manifest.json", {
        "method": method, "model": model, "model_digest": expected_digest,
        "options": options, "input_rows": len(rows), "output_rows": len(output),
        "actual_model_calls": actual_calls, "broadcast_per_activity": method == "L0",
        "retries_allowed": retries, "T_labels_opened": False,
    })
    e0a_run.save(destination / "checksums.json", e0a_run.hashes(destination))
    return output
