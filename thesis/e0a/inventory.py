"""Read-only version inventory of selected research materials; no dataset records."""

import argparse
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
RESEARCH = REPO.parents[1]
SOURCES = [
    "thesis-experiment-blueprint/README.md",
    "thesis-experiment-blueprint/实验执行手册.md",
    "thesis-experiment-blueprint/REVIEW_FIXES.md",
] + ["matraix-thesis-feasibility/" + p for p in [
    "README.md", "RESEARCH_QUESTION.md", "BACKGROUND_RESEARCH.md", "EXPERIMENT_SPEC.md",
    "DUNNHUMBY_AUDIT.md", "PERSONA_COMPATIBILITY_AUDIT.md", "DATASET_COMPARISON.md",
    "DATASET_SCHEMA_AUDIT.md", "X5_DATASET_AUDIT.md", "SHORT_VIDEO_DATASET_AUDIT.md",
    "LOCAL_SMOKE_TEST.md", "SMOKE_TEST_LOG.md", "data/SOURCES.md", "papers/READING_GUIDE.md",
    "papers/00_MatrAIx_8_3_Billion_Persona_Agents.pdf",
]]


def inventory(root=RESEARCH):
    result = []
    for name in SOURCES:
        path = root / name
        with path.open("rb") as stream:
            sha = hashlib.file_digest(stream, "sha256").hexdigest()
        result.append({"path_relative_to_research_root": name, "bytes": path.stat().st_size, "sha256": sha})
    return {"schema_version": "1.0", "recorded_on": "2026-10-10", "files": result}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--research-root", type=Path, default=RESEARCH)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    actual = inventory(args.research_root)
    if args.check:
        expected = json.loads((HERE / "materials.snapshot.json").read_text())
        if actual != expected:
            raise SystemExit("Material versions changed; preserve old snapshot and review a new version.")
        print(f"Verified {len(actual['files'])} material versions, read only.")
    else:
        print(json.dumps(actual, ensure_ascii=False, indent=2))
