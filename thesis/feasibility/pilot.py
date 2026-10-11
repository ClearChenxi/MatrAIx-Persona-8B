#!/usr/bin/env python3
"""Run a small OpinionQA human-vs-model distribution smoke test."""

from __future__ import annotations

import argparse
import ast
import csv
import json
import math
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data" / "opinionqa_w26"
OUTPUT_DIR = ROOT / "outputs"


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


def extract_model_distribution(state: dict, options: list[str]) -> list[float]:
    top_logprobs = state["result"]["completions"][0]["tokens"][0]["top_logprobs"]
    answer_to_letter = {answer: letter for letter, answer in state["output_mapping"].items()}
    observed_probs = [math.exp(value) for value in top_logprobs.values()]
    remaining_prob = max(0.0, 1.0 - sum(observed_probs))
    minimum_prob = min(observed_probs)

    logprobs: list[float | None] = []
    missing = 0
    for option in options:
        letter = answer_to_letter[option]
        candidates = [top_logprobs[key] for key in (f" {letter}", letter) if key in top_logprobs]
        if candidates:
            logprobs.append(max(candidates))
        else:
            logprobs.append(None)
            missing += 1

    fallback_prob = max(min(minimum_prob, remaining_prob / missing), 1e-300) if missing else 1.0
    fallback = math.log(fallback_prob)
    probabilities = [math.exp(value if value is not None else fallback) for value in logprobs]
    return normalize(probabilities)


def weighted_human_distribution(
    responses: list[dict[str, str]],
    question_key: str,
    options: list[str],
    weight_key: str,
    group_key: str | None = None,
    group_value: str | None = None,
) -> list[float]:
    totals = {option: 0.0 for option in options}
    for row in responses:
        if group_key and row.get(group_key) != group_value:
            continue
        answer = row.get(question_key, "")
        if answer in totals:
            totals[answer] += float(row[weight_key])
    return normalize([totals[option] for option in options])


def run(limit: int) -> dict:
    if limit < 1:
        raise ValueError("limit must be at least 1")

    info_rows = read_rows(DATA_DIR / "info.csv")
    model_rows = read_rows(DATA_DIR / "model_input.csv", delimiter="\t")
    responses = read_rows(DATA_DIR / "responses.csv")
    metadata = read_rows(DATA_DIR / "metadata.csv")
    model_run = json.loads((DATA_DIR / "model_run.json").read_text(encoding="utf-8"))

    info_by_question = {row["question"]: row for row in info_rows}
    state_by_question = {
        state["instance"]["input"]["text"]: state for state in model_run["request_states"]
    }
    weight_key = "WEIGHT_W26"
    selected = model_rows[:limit]
    output_rows = []
    tasks = []
    group_baselines = []
    age_groups = next(ast.literal_eval(row["options"]) for row in metadata if row["key"] == "AGE")

    for model_row in selected:
        question = model_row["question"]
        info = info_by_question[question]
        all_options = ast.literal_eval(info["references"])
        ordinal_count = len(ast.literal_eval(info["option_ordinal"]))
        options = all_options[:ordinal_count]
        state = state_by_question[question]

        human = weighted_human_distribution(responses, info["key"], options, weight_key)
        model_full = extract_model_distribution(state, all_options)
        model = normalize(model_full[:ordinal_count])
        row = {
            "question_key": info["key"],
            "question": question,
            "options": options,
            "human_distribution": human,
            "model_distribution": model,
            "tv_distance": tv_distance(human, model),
            "js_distance": js_distance(human, model),
            "modal_match": max(range(len(human)), key=human.__getitem__)
            == max(range(len(model)), key=model.__getitem__),
        }
        output_rows.append(row)
        for age_group in age_groups:
            distribution = weighted_human_distribution(
                responses, info["key"], options, weight_key, "AGE", age_group
            )
            if distribution:
                group_baselines.append(
                    {
                        "task_id": info["key"],
                        "attribute": "AGE",
                        "group": age_group,
                        "options": options,
                        "human_distribution": distribution,
                    }
                )
        tasks.append(
            {
                "task_id": info["key"],
                "environment": "survey",
                "question": question,
                "options": options,
                "expected_output": "exactly one option",
            }
        )

    OUTPUT_DIR.mkdir(exist_ok=True)
    with (OUTPUT_DIR / "pilot_results.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=output_rows[0].keys())
        writer.writeheader()
        writer.writerows(output_rows)
    with (OUTPUT_DIR / "matraix_tasks.jsonl").open("w", encoding="utf-8") as handle:
        for task in tasks:
            handle.write(json.dumps(task, ensure_ascii=False) + "\n")
    with (OUTPUT_DIR / "human_age_baselines.jsonl").open("w", encoding="utf-8") as handle:
        for baseline in group_baselines:
            handle.write(json.dumps(baseline, ensure_ascii=False) + "\n")

    persona_groups = [
        {
            "persona_group_id": f"age_{group.replace('+', '_plus').replace('-', '_')}",
            "attributes": {"age": group},
            "validation_target": "OpinionQA AGE group distribution",
        }
        for group in age_groups
    ]
    (OUTPUT_DIR / "persona_groups.json").write_text(
        json.dumps(persona_groups, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    summary = {
        "survey": "Pew American Trends Panel W26",
        "questions_evaluated": len(output_rows),
        "human_response_rows": len(responses),
        "human_weight": weight_key,
        "smoke_test_model": model_run["adapter_spec"]["model"],
        "mean_tv_distance": sum(row["tv_distance"] for row in output_rows) / len(output_rows),
        "mean_js_distance": sum(row["js_distance"] for row in output_rows) / len(output_rows),
        "modal_match_rate": sum(row["modal_match"] for row in output_rows) / len(output_rows),
        "available_age_groups": age_groups,
        "age_group_baselines": len(group_baselines),
        "research_use_warning": "Smoke test only; the model is not a MatrAIx persona agent.",
    }
    (OUTPUT_DIR / "pilot_summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=10)
    args = parser.parse_args()
    print(json.dumps(run(args.limit), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
