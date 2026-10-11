#!/usr/bin/env python3
"""Run a reproducible OpinionQA Persona smoke test with a local Ollama model."""

from __future__ import annotations

import argparse
import ast
import csv
from datetime import datetime
import json
import math
from pathlib import Path
import random
import re
import subprocess
import time
from typing import Any


ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data" / "opinionqa_w26"
DEFAULT_OUTPUT = ROOT / "outputs" / "local_persona_smoke"
PERSONA_FIELDS = ("AGE", "SEX", "EDUCATION", "CREGION", "MARITAL", "INCOME")


def read_rows(path: Path, delimiter: str = ",") -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle, delimiter=delimiter))


def normalize(values: list[float]) -> list[float]:
    total = sum(values)
    return [value / total for value in values] if total else []


def tv_distance(left: list[float], right: list[float]) -> float:
    return 0.5 * sum(abs(a - b) for a, b in zip(left, right))


def kl_divergence(left: list[float], right: list[float]) -> float:
    return sum(a * math.log(a / b, 2) for a, b in zip(left, right) if a > 0)


def js_distance(left: list[float], right: list[float]) -> float:
    midpoint = [(a + b) / 2 for a, b in zip(left, right)]
    divergence = 0.5 * kl_divergence(left, midpoint) + 0.5 * kl_divergence(right, midpoint)
    return math.sqrt(divergence)


def weighted_human_distribution(
    responses: list[dict[str, str]], question_key: str, options: list[str], age_group: str
) -> list[float]:
    totals = {option: 0.0 for option in options}
    for row in responses:
        answer = row.get(question_key, "")
        if row.get("AGE") == age_group and answer in totals:
            totals[answer] += float(row["WEIGHT_W26"])
    return normalize([totals[option] for option in options])


def sample_personas(
    responses: list[dict[str, str]], age_groups: list[str], count: int, seed: int
) -> dict[str, list[dict[str, str]]]:
    rng = random.Random(seed)
    cohorts: dict[str, list[dict[str, str]]] = {}
    for age_group in age_groups:
        eligible = [row for row in responses if row.get("AGE") == age_group]
        if len(eligible) < count:
            raise ValueError(f"Only {len(eligible)} respondents available for age group {age_group}")
        sampled = rng.sample(eligible, count)
        cohorts[age_group] = [
            {field: row[field] for field in PERSONA_FIELDS if row.get(field)} for row in sampled
        ]
    return cohorts


def build_prompt(
    condition: str,
    age_group: str,
    persona: dict[str, str],
    question: str,
    options: list[str],
) -> str:
    labels = [chr(ord("A") + index) for index in range(len(options))]
    if condition == "no_persona":
        identity = "Answer the survey question directly."
    elif condition == "age":
        identity = f"Answer as a survey respondent whose age group is {age_group}."
    elif condition == "structured":
        lines = [f"- {field.lower()}: {value}" for field, value in persona.items()]
        identity = (
            "Answer as the survey respondent described below. Do not add facts that are not in "
            "the profile.\n" + "\n".join(lines)
        )
    else:
        raise ValueError(f"Unknown condition: {condition}")

    choices = "\n".join(f"{label}. {option}" for label, option in zip(labels, options))
    return f"""{identity}

Choose the one option this respondent would most likely select.

Question: {question}

Options:
{choices}

Return JSON only, with exactly this shape: {{"answer":"A"}}
The answer must be one of: {", ".join(labels)}.
"""


def parse_answer(raw: str, option_count: int) -> tuple[str | None, str | None]:
    labels = {chr(ord("A") + index) for index in range(option_count)}
    candidates = [raw.strip()]
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL | re.IGNORECASE)
    if fenced:
        candidates.insert(0, fenced.group(1))
    object_match = re.search(r"\{.*?\}", raw, re.DOTALL)
    if object_match:
        candidates.insert(0, object_match.group(0))
    for candidate in candidates:
        try:
            answer = str(json.loads(candidate).get("answer", "")).strip().upper()
        except (json.JSONDecodeError, AttributeError):
            continue
        if answer in labels:
            return answer, None
    return None, "Could not parse a valid option label from model output"


def ollama_ready() -> bool:
    try:
        completed = subprocess.run(
            ["ollama", "list"], capture_output=True, text=True, timeout=10, check=False
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0


def start_ollama_if_needed(output_dir: Path) -> subprocess.Popen[str] | None:
    if ollama_ready():
        return None
    log_handle = (output_dir / "ollama_server.log").open("a", encoding="utf-8")
    process = subprocess.Popen(
        ["ollama", "serve"], stdout=log_handle, stderr=subprocess.STDOUT, text=True
    )
    for _ in range(30):
        if process.poll() is not None:
            raise RuntimeError(f"Ollama server exited early; see {output_dir / 'ollama_server.log'}")
        if ollama_ready():
            return process
        time.sleep(1)
    process.terminate()
    raise RuntimeError("Ollama did not become ready within 30 seconds")


def invoke_model(model: str, prompt: str, timeout: int) -> tuple[str, float, str | None]:
    started = time.perf_counter()
    try:
        completed = subprocess.run(
            [
                "ollama",
                "run",
                model,
                "--format",
                "json",
                "--hidethinking",
                "--think=false",
                "--keepalive",
                "10m",
                prompt,
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return exc.stdout or "", time.perf_counter() - started, f"timeout after {timeout}s"
    duration = time.perf_counter() - started
    if completed.returncode != 0:
        return completed.stdout, duration, completed.stderr.strip() or f"exit {completed.returncode}"
    return completed.stdout, duration, None


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def run(args: argparse.Namespace) -> dict[str, Any]:
    if args.preset == "tiny":
        question_count, persona_count, age_groups = 1, 1, ["18-29"]
    else:
        question_count, persona_count, age_groups = 10, 10, ["18-29", "65+"]
    if args.questions is not None:
        question_count = args.questions
    if args.personas is not None:
        persona_count = args.personas
    if args.age_groups:
        age_groups = args.age_groups.split(",")

    conditions = [value.strip() for value in args.conditions.split(",") if value.strip()]
    invalid = set(conditions) - {"no_persona", "age", "structured"}
    if invalid:
        raise ValueError(f"Invalid conditions: {sorted(invalid)}")

    run_id = datetime.now().astimezone().strftime("%Y%m%dT%H%M%S%z")
    output_dir = args.output_dir / run_id
    output_dir.mkdir(parents=True, exist_ok=False)
    started_at = datetime.now().astimezone()

    info_rows = read_rows(DATA_DIR / "info.csv")[:question_count]
    responses = read_rows(DATA_DIR / "responses.csv")
    cohorts = sample_personas(responses, age_groups, persona_count, args.seed)
    write_json(output_dir / "config.json", vars(args) | {
        "output_dir": str(output_dir),
        "resolved_question_count": question_count,
        "resolved_persona_count": persona_count,
        "resolved_age_groups": age_groups,
        "resolved_conditions": conditions,
        "started_at": started_at.isoformat(),
    })
    write_json(output_dir / "personas.json", cohorts)

    try:
        server = start_ollama_if_needed(output_dir)
    except Exception as exc:
        write_json(output_dir / "FAILED.json", {
            "failed_at": datetime.now().astimezone().isoformat(),
            "stage": "ollama_startup",
            "error_type": type(exc).__name__,
            "error": str(exc),
            "status": "The model smoke test did not run.",
        })
        raise
    records: list[dict[str, Any]] = []
    records_path = output_dir / "responses.jsonl"
    try:
        for info in info_rows:
            all_options = ast.literal_eval(info["references"])
            options = all_options[: len(ast.literal_eval(info["option_ordinal"]))]
            for age_group in age_groups:
                for persona_index, persona in enumerate(cohorts[age_group]):
                    for condition in conditions:
                        prompt = build_prompt(
                            condition, age_group, persona, info["question"], options
                        )
                        raw, duration, call_error = invoke_model(args.model, prompt, args.timeout)
                        answer, parse_error = parse_answer(raw, len(options)) if not call_error else (None, None)
                        record = {
                            "run_id": run_id,
                            "timestamp": datetime.now().astimezone().isoformat(),
                            "model": args.model,
                            "condition": condition,
                            "question_key": info["key"],
                            "question": info["question"],
                            "options": options,
                            "age_group": age_group,
                            "persona_index": persona_index,
                            "persona": persona if condition == "structured" else {},
                            "prompt": prompt,
                            "raw_output": raw,
                            "answer_label": answer,
                            "answer": options[ord(answer) - ord("A")] if answer else None,
                            "duration_seconds": duration,
                            "error": call_error or parse_error,
                        }
                        records.append(record)
                        with records_path.open("a", encoding="utf-8") as handle:
                            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                        print(
                            f"[{len(records)}] {condition} {info['key']} {age_group} "
                            f"persona={persona_index} answer={answer or 'ERROR'} time={duration:.2f}s",
                            flush=True,
                        )
    finally:
        if server is not None:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()

    result_rows = []
    for info in info_rows:
        all_options = ast.literal_eval(info["references"])
        options = all_options[: len(ast.literal_eval(info["option_ordinal"]))]
        for age_group in age_groups:
            human = weighted_human_distribution(responses, info["key"], options, age_group)
            for condition in conditions:
                subset = [
                    row for row in records
                    if row["question_key"] == info["key"]
                    and row["age_group"] == age_group
                    and row["condition"] == condition
                    and row["answer"] is not None
                ]
                counts = [sum(row["answer"] == option for row in subset) for option in options]
                model_distribution = normalize(counts)
                result_rows.append({
                    "question_key": info["key"],
                    "age_group": age_group,
                    "condition": condition,
                    "valid_responses": len(subset),
                    "human_distribution": human,
                    "model_distribution": model_distribution,
                    "tv_distance": tv_distance(human, model_distribution) if model_distribution else None,
                    "js_distance": js_distance(human, model_distribution) if model_distribution else None,
                })
    write_json(output_dir / "results.json", result_rows)

    valid = [row for row in records if row["answer"] is not None]
    distances = [row["tv_distance"] for row in result_rows if row["tv_distance"] is not None]
    summary = {
        "run_id": run_id,
        "started_at": started_at.isoformat(),
        "finished_at": datetime.now().astimezone().isoformat(),
        "model": args.model,
        "runner": "MatrAIx-compatible local Persona runner; not the official MatrAIx runtime",
        "planned_responses": question_count * persona_count * len(age_groups) * len(conditions),
        "completed_responses": len(records),
        "valid_responses": len(valid),
        "parse_success_rate": len(valid) / len(records) if records else 0.0,
        "total_duration_seconds": sum(row["duration_seconds"] for row in records),
        "mean_response_seconds": (
            sum(row["duration_seconds"] for row in records) / len(records) if records else None
        ),
        "mean_tv_distance": sum(distances) / len(distances) if distances else None,
        "output_directory": str(output_dir),
    }
    write_json(output_dir / "summary.json", summary)
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--preset", choices=("tiny", "pilot"), default="tiny")
    parser.add_argument("--model", default="qwen3.5:0.8b")
    parser.add_argument("--conditions", default="structured")
    parser.add_argument("--questions", type=int)
    parser.add_argument("--personas", type=int)
    parser.add_argument("--age-groups", help="Comma-separated OpinionQA AGE values")
    parser.add_argument("--seed", type=int, default=20260918)
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    if args.questions is not None and args.questions < 1:
        parser.error("--questions must be at least 1")
    if args.personas is not None and args.personas < 1:
        parser.error("--personas must be at least 1")
    run(args)


if __name__ == "__main__":
    main()
