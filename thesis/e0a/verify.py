"""Read-only evidence verification; never invokes a model."""

import argparse
import hashlib
import json
from pathlib import Path

from validation import check_messages, probability


def read(path):
    return json.loads(path.read_text())


def verify(directory):
    checksums = read(directory / "checksums.json")
    actual = {
        str(p.relative_to(directory)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in directory.rglob("*")
        if p.is_file() and p.name != "checksums.json"
    }
    if actual != checksums:
        raise ValueError("artifact inventory or checksum mismatch")
    config = read(directory / "config.json")
    manifest = read(directory / "agent_manifest.json")
    summary = read(directory / "summary.json")
    repetitions = config["repetitions"]
    expected_count = len(config["profiles"]) * repetitions
    if expected_count != config["maximum_generations"]:
        raise ValueError("configuration count does not match generation limit")
    expected_trials = {f"{p['id']}_r{r}" for p in config["profiles"] for r in range(1, repetitions + 1)}
    actual_trials = {str(p.parent.relative_to(directory)) for p in directory.rglob("official_request_*.json")}
    if actual_trials != expected_trials:
        raise ValueError("missing or additional trial requests")
    if (
        manifest["status"] != "passed"
        or summary["status"] != "passed"
        or manifest["actual_generations"] != expected_count
        or summary["actual_generations"] != expected_count
    ):
        raise ValueError("run did not pass the configured generation count")
    historical = config.get("instrument_id") == "thesis_e0a_history_v1"
    if historical:
        import history
        history.validate_plan(config, require_approval=True)
        audit = read(directory / "input_audit.json")
        if (audit["lower_inclusive"] != 153 or audit["cutoff_exclusive"] != 237
                or audit["label_tables_opened"] != [] or audit["accuracy_evaluated"] is not False
                or len(audit["selected"]) != config["sample_size"]
                or [r["profile"] for r in audit["selected"]] != [p["id"] for p in config["profiles"]]
                or len({r["household_key"] for r in audit["selected"]}) != config["sample_size"]):
            raise ValueError("historical input scope inconsistent")
        if set(read(directory / "source_hashes.json")) != set(history.TABLES):
            raise ValueError("historical source whitelist inconsistent")
        preflight = read(directory / "preflight_messages.json")
        for name, digest in manifest["prepared_input_hashes"].items():
            if checksums.get(name) != digest:
                raise ValueError("prepared inputs changed after preflight")
    before, after = [
        read(directory / f"ollama_{name}.json") for name in ["before", "after"]
    ]
    if (
        before["model_digest"] != after["model_digest"]
        or before["model_digest"] != manifest["model_digest"]
        or before["version"]["data"] != after["version"]["data"]
    ):
        raise ValueError("model/runtime fingerprint changed")
    if historical and (before["model_digest"] != config["expected_model_digest"]
                       or before["version"]["data"]["version"] != config["expected_ollama_version"]):
        raise ValueError("historical model/runtime differs from approved plan")
    rows = []
    for profile in config["profiles"]:
        repeated = []
        for repetition in range(1, repetitions + 1):
            trial = directory / f"{profile['id']}_r{repetition}"
            if (
                len(list(trial.glob("official_request_*.json"))) != 1
                or len(list(trial.glob("call_*.json"))) != 1
            ):
                raise ValueError("unexpected HTTP attempts")
            expected = read(trial / "expected_messages.json")
            if historical:
                check_messages(expected, preflight[profile["id"]])
            official = read(trial / "official_request_1.json")
            native = read(trial / "ollama_request.json")
            check_messages(official["messages"], expected)
            check_messages(native["messages"], expected)
            if (
                native["options"] != config["options"]
                or native["think"] != config["think"]
                or native["model"] != config["model"]
            ):
                raise ValueError("effective inference parameters changed")
            raw = read(trial / "ollama_response.raw.json")
            if (
                raw["model"] != config["model"]
                or not raw["done"]
                or raw["done_reason"] != "stop"
            ):
                raise ValueError("incomplete or wrong model response")
            value = probability(
                json.loads(raw["message"]["content"]),
                config.get("instrument_id", "thesis_e0a_synthetic_v1"),
            )
            adapted = read(trial / "adapter_response.raw.json")
            if adapted["choices"][0]["message"]["content"] != raw["message"]["content"]:
                raise ValueError("response content altered by adapter")
            result = read(trial / "artifacts/app/output/survey_result.json")
            if (
                len(result["answers"]) != 1
                or result["answers"][0]["questionId"] != "p_redeem"
                or float(result["answers"][0]["value"]) != value
            ):
                raise ValueError("official result differs from native raw response")
            call = read(trial / "call_1.json")
            if call["status"] != "passed" or call["p_redeem"] != value:
                raise ValueError("call acceptance record inconsistent")
            matches = [
                r
                for r in summary["rows"]
                if r["profile"] == profile["id"] and r["repetition"] == repetition
            ]
            if len(matches) != 1 or matches[0]["p_redeem"] != value:
                raise ValueError("summary differs from raw evidence")
            rows.append(matches[0])
            repeated.append(expected)
        for messages in repeated[1:]:
            check_messages(repeated[0], messages)
    if len(rows) != expected_count or len(summary["rows"]) != expected_count:
        raise ValueError("unexpected trial count")
    if historical:
        from run_history import repetition_summary
        if summary["repetition_comparison"] != repetition_summary(config, rows):
            raise ValueError("repetition summary differs from verified raw values")
    return {
        "verified_files": len(actual),
        "verified_generations": expected_count,
        "code_commit": manifest["code_commit"],
        "model_digest": manifest["model_digest"],
        "rows": rows,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("run_directory", type=Path)
    print(
        json.dumps(
            verify(parser.parse_args().run_directory), ensure_ascii=False, indent=2
        )
    )
