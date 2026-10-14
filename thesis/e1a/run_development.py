"""Train E1a traditional methods on D and report D/R checks only."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path

import joblib
from sklearn.metrics import brier_score_loss

from thesis.e1a.methods.baselines import (
    campaign_macro_ap, fit_n1, n0_rate, predict_b1, predict_n1,
)
from thesis.e1a.methods.data import ROOT, configuration, load_split, sha256
from thesis.e1a.methods.supervised import fit_b2, fit_b3


BRANCH = "thesis-restart-20261014"
METHODS = ("N0", "N1", "B1", "B2", "B3")


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def preflight() -> str:
    if git("status", "--porcelain", "--untracked-files=all"):
        raise ValueError("commit and push E1a code before D/R development")
    if git("branch", "--show-current") != BRANCH:
        raise ValueError("wrong experiment branch")
    commit = git("rev-parse", "HEAD")
    if commit != git("rev-parse", "origin/" + BRANCH):
        raise ValueError("E1a code is not backed up to origin")
    if git("rev-parse", "thesis-upstream-3633d8d^{commit}") != "3633d8dab149a9482a71b024418a49ae828cc941":
        raise ValueError("official MatrAIx baseline changed")
    return commit


def write_predictions(path: Path, split, scores: list[float], method: str) -> None:
    if len(scores) != len(split.features):
        raise ValueError("prediction count differs from input count")
    with path.open("w", encoding="utf-8") as handle:
        for row, score in zip(split.features, scores):
            if not (0 <= score <= 1):
                raise ValueError("invalid score")
            link = split.links[row["row_id"]]
            handle.write(json.dumps({
                "row_id": row["row_id"], "campaign": int(link["campaign"]),
                "method_id": method, "raw_score": score,
                "score_kind": "ranking" if method == "B1" else "probability",
            }, sort_keys=True) + "\n")


def metrics(split, scores: list[float], method: str) -> dict:
    result = campaign_macro_ap(split, scores)
    if method != "B1":
        result["macro_brier"] = sum(
            brier_score_loss(
                [split.labels[row["row_id"]] for row in split.features
                 if split.links[row["row_id"]]["campaign"] == campaign],
                [score for row, score in zip(split.features, scores)
                 if split.links[row["row_id"]]["campaign"] == campaign],
            )
            for campaign in sorted({link["campaign"] for link in split.links.values()})
        ) / len({link["campaign"] for link in split.links.values()})
    return result


def main() -> None:
    commit = preflight()
    config = configuration()
    start = time.monotonic()
    D, R = load_split("D", config=config), load_split("R", config=config)
    if D.labels is None or R.labels is None:
        raise ValueError("D/R labels must be present")
    run = ROOT / "thesis/e1a/runs" / datetime.now(timezone.utc).strftime("dev-%Y%m%dT%H%M%S.%fZ")
    run.mkdir(parents=True, exist_ok=False)
    n0 = n0_rate(D)
    n1 = fit_n1(D)
    b2 = fit_b2(D, config)
    b3 = fit_b3(D, config)
    models = {"N1": n1, "B2": b2, "B3": b3}
    for name, model in models.items():
        joblib.dump(model, run / f"{name}.joblib")
    summary = {}
    for name, split in (("D", D), ("R", R)):
        values = {
            "N0": [n0] * len(split.features),
            "N1": predict_n1(n1, split),
            "B1": predict_b1(split),
            "B2": b2.predict(split.features),
            "B3": b3.predict(split.features),
        }
        summary[name] = {}
        for method in METHODS:
            scores = values[method]
            write_predictions(run / f"{name}_{method}.jsonl", split, scores, method)
            summary[name][method] = metrics(split, scores, method)
    manifest = {
        "status": "historical_method_development_complete",
        "code_commit": commit,
        "input_run": config["input_run"],
        "input_hashes": config["input_hashes"],
        "runtime": {"python": sys.version, "scikit_learn": version("scikit-learn"),
                    "catboost": version("catboost"), "numpy": version("numpy"),
                    "pandas": version("pandas")},
        "model_selection": {"B2": b2.selection, "B3": b3.selection},
        "n0_rate": n0,
        "rows": {"D": len(D.features), "R": len(R.features)},
        "metrics": summary,
        "elapsed_seconds": time.monotonic() - start,
        "test_labels_opened": False,
        "T_predictions_written": False,
    }
    (run / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    checksums = {path.name: sha256(path) for path in sorted(run.iterdir()) if path.is_file()}
    (run / "checksums.json").write_text(json.dumps(checksums, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": manifest["status"], "run": str(run),
                      "rows": manifest["rows"], "elapsed_seconds": manifest["elapsed_seconds"],
                      "test_labels_opened": False}, ensure_ascii=False))


if __name__ == "__main__":
    main()
