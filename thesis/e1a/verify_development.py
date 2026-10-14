"""Read-only integrity and prediction replay check for a D/R development run."""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import joblib

from thesis.e1a.methods.baselines import n0_rate, predict_b1, predict_n1
from thesis.e1a.methods.data import configuration, load_split, sha256


METHODS = ("N0", "N1", "B1", "B2", "B3")


def verify(run: Path) -> dict:
    run = run.resolve(strict=True)
    checksums = json.loads((run / "checksums.json").read_text(encoding="utf-8"))
    actual_files = {p.name for p in run.iterdir() if p.is_file() and p.name != "checksums.json"}
    if actual_files != set(checksums):
        raise ValueError("development artifact inventory differs from checksum list")
    for name, expected in checksums.items():
        if sha256(run / name) != expected:
            raise ValueError(f"development artifact hash mismatch: {name}")
    manifest = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
    if manifest["status"] != "historical_method_development_complete":
        raise ValueError("development run did not complete")
    if manifest["test_labels_opened"] is not False or manifest["T_predictions_written"] is not False:
        raise ValueError("development stage recorded T use")
    config = configuration()
    if manifest["input_run"] != config["input_run"] or manifest["input_hashes"] != config["input_hashes"]:
        raise ValueError("development run used another input version")

    models = {name: joblib.load(run / f"{name}.joblib") for name in ("N1", "B2", "B3")}
    if models["B2"].selection != manifest["model_selection"]["B2"]:
        raise ValueError("B2 model selection differs from manifest")
    if models["B3"].selection != manifest["model_selection"]["B3"]:
        raise ValueError("B3 model selection differs from manifest")
    D = load_split("D", config=config)
    expected = {"D": D, "R": load_split("R", config=config)}
    for split_name, split in expected.items():
        if len(split.features) != manifest["rows"][split_name]:
            raise ValueError("D/R row count differs from manifest")
        predictions = {
            "N0": [n0_rate(D)] * len(split.features),
            "N1": predict_n1(models["N1"], split),
            "B1": predict_b1(split),
            "B2": models["B2"].predict(split.features),
            "B3": models["B3"].predict(split.features),
        }
        for method in METHODS:
            rows = [json.loads(line) for line in (run / f"{split_name}_{method}.jsonl").read_text(encoding="utf-8").splitlines()]
            if len(rows) != len(split.features):
                raise ValueError("prediction file row count differs from D/R input")
            for index, (record, source) in enumerate(zip(rows, split.features)):
                score = float(record["raw_score"])
                if (record["row_id"] != source["row_id"] or record["method_id"] != method
                        or record["campaign"] != int(split.links[source["row_id"]]["campaign"])
                        or not math.isfinite(score) or not 0 <= score <= 1
                        or abs(score - predictions[method][index]) > 1e-12):
                    raise ValueError(f"{split_name} {method} replay mismatch at row {index}")
                if record["score_kind"] != ("ranking" if method == "B1" else "probability"):
                    raise ValueError("score kind differs from method definition")
    return {
        "status": "verified",
        "code_commit": manifest["code_commit"],
        "files_verified": len(checksums),
        "rows": manifest["rows"],
        "methods": list(METHODS),
        "test_labels_opened": False,
    }


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: python -m thesis.e1a.verify_development <run-directory>")
    print(json.dumps(verify(Path(sys.argv[1])), indent=2, ensure_ascii=False))
