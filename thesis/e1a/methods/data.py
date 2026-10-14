"""Read only the frozen same-information X/S bundle and permitted D/R labels."""

from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
CONFIG = ROOT / "thesis/e1a/config.json"


@dataclass(frozen=True)
class Split:
    features: tuple[dict[str, str], ...]
    links: dict[str, dict[str, str]]
    labels: dict[str, int] | None


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def configuration() -> dict:
    return json.loads(CONFIG.read_text(encoding="utf-8"))


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def load_split(name: str, *, config: dict | None = None) -> Split:
    if name not in {"D", "R", "T"}:
        raise ValueError("split must be D, R or T")
    config = configuration() if config is None else config
    run = (ROOT / config["input_run"] / "private").resolve(strict=True)

    def checked(filename: str) -> Path:
        path = run / filename
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"missing or unsafe frozen input: {filename}")
        if sha256(path) != config["input_hashes"].get(filename):
            raise ValueError(f"frozen input hash differs: {filename}")
        return path

    features = _read_csv(checked(f"features_{name}.csv"))
    links_rows = _read_csv(checked(f"links_{name}.csv"))
    feature_ids = [row["row_id"] for row in features]
    link_ids = [row["row_id"] for row in links_rows]
    if not feature_ids or len(feature_ids) != len(set(feature_ids)) or len(link_ids) != len(set(link_ids)) or set(feature_ids) != set(link_ids):
        raise ValueError("feature/link row identity mismatch")
    if any(key in features[0] for key in ("household_key", "campaign", "e2_role", "y1_redeemed")):
        raise ValueError("identifier, permission or label entered model features")
    links = {row["row_id"]: row for row in links_rows}

    if name == "T":
        if (run / "labels_T.csv").exists():
            raise ValueError("T label appeared in the model input bundle")
        return Split(tuple(features), links, None)
    labels_rows = _read_csv(checked(f"labels_{name}.csv"))
    label_ids = [row["row_id"] for row in labels_rows]
    if len(label_ids) != len(set(label_ids)) or set(label_ids) != set(feature_ids):
        raise ValueError("feature/label row identity mismatch")
    labels = {row["row_id"]: int(row["y1_redeemed"]) for row in labels_rows}
    if set(labels.values()) - {0, 1}:
        raise ValueError("invalid historical binary label")
    return Split(tuple(features), links, labels)


def campaign_rows(split: Split) -> list[int]:
    return [int(split.links[row["row_id"]]["campaign"]) for row in split.features]
