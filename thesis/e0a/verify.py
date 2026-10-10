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
    if (
        manifest["status"] != "passed"
        or summary["status"] != "passed"
        or manifest["actual_generations"] != 6
    ):
        raise ValueError("run did not pass six generations")
    before, after = [
        read(directory / f"ollama_{name}.json") for name in ["before", "after"]
    ]
    if (
        before["model_digest"] != after["model_digest"]
        or before["model_digest"] != manifest["model_digest"]
        or before["version"]["data"] != after["version"]["data"]
    ):
        raise ValueError("model/runtime fingerprint changed")
    rows = []
    for profile in config["profiles"]:
        repeated = []
        for repetition in [1, 2]:
            trial = directory / f"{profile['id']}_r{repetition}"
            if (
                len(list(trial.glob("official_request_*.json"))) != 1
                or len(list(trial.glob("call_*.json"))) != 1
            ):
                raise ValueError("unexpected HTTP attempts")
            expected = read(trial / "expected_messages.json")
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
        check_messages(repeated[0], repeated[1])
    if len(rows) != 6 or len(summary["rows"]) != 6:
        raise ValueError("unexpected trial count")
    return {
        "verified_files": len(actual),
        "verified_generations": 6,
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
