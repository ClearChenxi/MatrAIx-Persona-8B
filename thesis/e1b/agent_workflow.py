"""Real MatrAIx PersonaJsonSurvey workflow for one fixed campaign batch.

This module reuses only the previously verified E0a local adapter. It does
not use the withdrawn direct-Ollama implementation. Calls require a generated
activity-specific Survey task; the template task is never executed.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import shutil
import sys
import threading
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "thesis/e0a"))
from thesis.e0a import run as e0a_run  # noqa: E402


TEMPLATE = ROOT / "thesis/e1b/task"
INSTRUMENT = "thesis_coupon_restart_v1"
EXCLUSIONS = {"row_id", "household_key", "campaign", "split", "e2_role", "y1_redeemed"}
P3_HOUSEHOLD_FIELDS = ("recency_days", "basket_count", "sales_total")


def partition_fields(row: dict[str, str]) -> tuple[dict[str, str], dict[str, str]]:
    """Use the entire frozen 56-field X/S row exactly once, without IDs."""
    if EXCLUSIONS.intersection(set(row) - {"row_id"}):
        raise ValueError("identity, permission or label entered model input")
    if "row_id" not in row or len(row) != 57:
        raise ValueError("expected row_id plus the frozen 56-field input schema")
    household, campaign = {}, {}
    for name, value in row.items():
        if name == "row_id":
            continue
        target = campaign if name == "campaign_type" or name.startswith("campaign_") else household
        target[name] = value if value != "" else "unknown"
    if not household or not campaign:
        raise ValueError("missing household or campaign facts")
    return household, campaign


def campaign_fact_card(rows: tuple[dict[str, str], ...]) -> str:
    if not rows:
        raise ValueError("empty campaign batch")
    first = partition_fields(rows[0])[1]
    if any(partition_fields(row)[1] != first for row in rows[1:]):
        raise ValueError("campaign facts differ among households in one campaign")
    lines = [
        "# Activity facts known before the campaign starts", "",
        "The household is confirmed to receive the complete coupon bundle.",
        "The observation unit is a household; no individual psychology is observed.",
        "Coupon face values, creative, images and minimum spend are not supplied.",
        "The following fields are the complete pre-campaign activity fact card:", "",
    ]
    lines.extend(f"- {name}: {value}" for name, value in first.items())
    return "\n".join(lines) + "\n"


def build_task(rows: tuple[dict[str, str], ...], destination: Path) -> Path:
    task = destination / "task"
    shutil.copytree(TEMPLATE, task)
    (task / "input/context.md").write_text(campaign_fact_card(rows), encoding="utf-8")
    return task


@contextmanager
def bind_task(task: Path):
    """Resolve the generated questionnaire through the official task loader."""
    from playground.survey_task_content import SURVEY_TASK_FOLDER_BY_QUESTIONNAIRE_ID

    registry = SURVEY_TASK_FOLDER_BY_QUESTIONNAIRE_ID
    prior = registry.get(INSTRUMENT)
    registry[INSTRUMENT] = os.path.relpath(task.resolve(), ROOT / "application/tasks")
    try:
        yield
    finally:
        if prior is None:
            registry.pop(INSTRUMENT, None)
        else:
            registry[INSTRUMENT] = prior


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_persona_batch(
    rows: tuple[dict[str, str], ...], destination: Path, *, model: str,
    options: dict, expected_digest: str, fallback_rate: float,
    retries: int = 2, method_id: str = "P1",
    household_field_allowlist: tuple[str, ...] | None = None,
) -> list[dict]:
    """Run one Agent condition for one campaign, preserving every attempt."""
    if not 0 <= fallback_rate <= 1 or retries not in (0, 1, 2):
        raise ValueError("invalid fallback or retry policy")
    if method_id not in {"P1", "P2", "P3"}:
        raise ValueError("only Persona Agent conditions use this runner")
    destination.mkdir(parents=True, exist_ok=False)
    e0a_run.bootstrap()
    task = build_task(rows, destination)
    config = {
        "model": model, "options": options, "think": False,
        "repetitions": 1, "maximum_generations": len(rows) * (retries + 1),
        "instrument_id": INSTRUMENT, "environment_name": "thesis-coupon-restart",
    }
    before = e0a_run.metadata(config)
    if before["model_digest"] != expected_digest:
        raise RuntimeError("local model digest differs from frozen method")
    e0a_run.save(destination / "ollama_before.json", before)
    server = e0a_run.Adapter(config)
    os.environ["OPENAI_API_KEY"] = "local-dummy-no-secret"
    os.environ["OPENAI_BASE_URL"] = f"http://127.0.0.1:{server.server_port}/v1"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    output = []
    try:
        with bind_task(task):
            for index, row in enumerate(rows, start=1):
                behavior, _ = partition_fields(row)
                if household_field_allowlist is not None:
                    behavior = {key: value for key, value in behavior.items()
                                if key in household_field_allowlist}
                    if not behavior:
                        raise ValueError("Persona reduction removed all household behavior")
                attempts = []
                score = None
                message_hash = None
                for attempt in range(1, retries + 2):
                    attempt_dir = destination / f"case_{index:04d}_attempt_{attempt}"
                    attempt_dir.mkdir()
                    one = dict(config, profiles=[{"id": f"household_{index:04d}", "behavior": behavior}])
                    prepared = e0a_run.preflight(one, attempt_dir, task=task)
                    message_hash = e0a_run.sha(e0a_run.encode(prepared[0][2]))
                    try:
                        actual = asyncio.run(e0a_run.run_trials(one, prepared, attempt_dir, server, task=task))
                        score = float(actual[0]["p_redeem"])
                        attempts.append({"number": attempt, "status": "passed", "score": score})
                        break
                    except Exception as error:
                        attempts.append({"number": attempt, "status": "failed", "error": str(error)})
                fallback = score is None
                output.append({
                    "row_id": row["row_id"], "method_id": method_id,
                    "p_redeem": fallback_rate if fallback else score,
                    "status": "fallback" if fallback else "passed", "fallback": fallback,
                    "attempts": attempts, "message_sha256": message_hash,
                    "model_digest": before["model_digest"],
                    "task_sha256": _digest(task / "input/context.md"),
                })
    finally:
        server.shutdown()
        server.server_close()
        after = e0a_run.metadata(config)
        e0a_run.save(destination / "ollama_after.json", after)
        if after["model_digest"] != before["model_digest"]:
            raise RuntimeError("model changed during P1 run")
        e0a_run.save(destination / "run_manifest.json", {
            "method": method_id, "instrument": INSTRUMENT, "model": model,
            "model_digest": before["model_digest"], "options": options,
            "household_field_allowlist": household_field_allowlist,
            "input_rows": len(rows), "output_rows": len(output),
            "actual_model_calls": server.budget.count,
            "retries_allowed": retries, "fallback_rate": fallback_rate,
            "T_labels_opened": False,
        })
        e0a_run.save(destination / "checksums.json", e0a_run.hashes(destination))
    return output
