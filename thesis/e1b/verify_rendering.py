"""Check D/R Persona rendering against the previous pilot without an LLM call."""

from __future__ import annotations

import argparse
import json
import tempfile
from collections import defaultdict
from pathlib import Path

from thesis.e1a.methods.data import configuration, load_split
from thesis.e1b.agent_workflow import bind_task, build_task, e0a_run, partition_fields
from thesis.e1b.direct_controls import messages_for
from thesis.e1b.pilot import selected
from thesis.e1b.verify_pilot import verify


def check_split(split_name: str, previous_run: Path, pilot_config: dict, input_config: dict) -> dict:
    previous = verify(previous_run)
    if previous["status"] != "verified" or previous_run.name.split("-")[1] != split_name:
        raise ValueError("previous pilot run does not match the requested D/R split")
    if previous["code_commit"] != "3ce3278adef49e6022200036a952bfb9fa679fda":
        raise ValueError("previous pilot has an unexpected code version")
    rows, selection = selected(split_name, pilot_config, input_config)
    source = load_split(split_name, config=input_config)
    by_campaign = defaultdict(list)
    for row in rows:
        by_campaign[int(source.links[row["row_id"]]["campaign"])].append(row)
    checked = 0
    with tempfile.TemporaryDirectory(prefix=f"e1b-render-{split_name}-") as temporary:
        base = Path(temporary)
        for campaign, campaign_rows in sorted(by_campaign.items()):
            campaign_dir = base / f"campaign_{campaign}"
            campaign_dir.mkdir()
            task = build_task(tuple(campaign_rows), campaign_dir)
            with bind_task(task):
                for index, row in enumerate(campaign_rows, 1):
                    household, _ = partition_fields(row)
                    trial = campaign_dir / f"case_{index:04d}"
                    trial.mkdir()
                    prepared = e0a_run.preflight(
                        {"profiles": [{"id": f"household_{index:04d}", "behavior": household}]},
                        trial, task=task,
                    )
                    new_messages = prepared[0][2]
                    l2 = trial / "l2"
                    l2.mkdir()
                    if messages_for("L2", row, task, l2) != new_messages:
                        raise ValueError("L2 and P1 model-visible messages differ")
                    expected_lines = [
                        f"- {name.replace('_', ' ').title()}: {value}"
                        for name, value in household.items()
                    ]
                    actual_lines = [line for line in new_messages[0]["content"].splitlines()
                                    if line.startswith("- ")]
                    if actual_lines != expected_lines:
                        raise ValueError("Persona behavior facts are missing, reordered or joined")
                    prior_file = (previous_run / f"campaign_{campaign}"
                                  / f"case_{index:04d}_attempt_1"
                                  / f"household_{index:04d}_r1/expected_messages.json")
                    old_messages = json.loads(prior_file.read_text(encoding="utf-8"))
                    if (old_messages[1] != new_messages[1]
                            or old_messages[0]["role"] != new_messages[0]["role"]
                            or old_messages[0]["content"].replace("\n", "")
                            != new_messages[0]["content"].replace("\n", "")):
                        raise ValueError("rendering patch changed something beyond line breaks")
                    checked += 1
    if checked != len(rows):
        raise ValueError("not every D/R pilot row was checked")
    return {"split": split_name, "rows": checked, "selection": selection,
            "behavior_facts_one_per_line": True, "P1_L2_messages_equal": True,
            "only_persona_newlines_changed": True, "model_calls": 0,
            "T_labels_opened": False}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--D-run", required=True, type=Path)
    parser.add_argument("--R-run", required=True, type=Path)
    args = parser.parse_args()
    pilot_config = json.loads((Path(__file__).with_name("pilot_config.json")).read_text(encoding="utf-8"))
    input_config = configuration()
    e0a_run.bootstrap()
    results = [check_split(name, run.resolve(strict=True), pilot_config, input_config)
               for name, run in (("D", args.D_run), ("R", args.R_run))]
    print(json.dumps({"status": "verified", "splits": results}, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
