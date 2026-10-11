"""Register immutable drand rounds for the two T-label release stages."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from audit import canonical_json
from timelock_common import (
    fetch_beacon_state, first_round_at_or_after, parse_utc, round_timestamp,
    utc_text, validate_policy,
)


def register(policy, adaptation_at, final_at, beacon_state):
    timelock = validate_policy(policy, require_schedule=False)
    adaptation_requested = int(parse_utc(adaptation_at).timestamp())
    final_requested = int(parse_utc(final_at).timestamp())
    latest_round = beacon_state["latest"]["round"]
    latest_time = round_timestamp(timelock, latest_round)
    if adaptation_requested - latest_time < timelock["minimum_preparation_lead_seconds"]:
        raise ValueError("adaptation unlock must be at least seven days after the live beacon round")
    if final_requested - adaptation_requested < timelock["minimum_stage_separation_seconds"]:
        raise ValueError("final unlock must be at least seven days after adaptation unlock")
    requested = {"adaptation": adaptation_requested, "final_evaluation": final_requested}
    for stage, timestamp in requested.items():
        round_number = first_round_at_or_after(timelock, timestamp)
        timelock["stages"][stage] = {
            "requested_unlock_at_utc": utc_text(timestamp),
            "unlock_at_utc": utc_text(round_timestamp(timelock, round_number)),
            "round": round_number,
        }
    timelock["schedule_registration"] = {
        "registered_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "observed_latest_round": latest_round,
        "observed_latest_randomness": beacon_state["latest"]["randomness"],
        "observed_latest_signature": beacon_state["latest"]["signature"],
    }
    timelock["status"] = "registered"
    policy["status"] = "timelock_schedule_registered"
    validate_policy(policy)
    return policy


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("adaptation_unlock_utc", help="ISO 8601 UTC, for example 2026-12-01T00:00:00Z")
    parser.add_argument("final_unlock_utc", help="ISO 8601 UTC, later than adaptation")
    parser.add_argument("--policy", type=Path, default=Path(__file__).with_name("timelock-policy.json"))
    args = parser.parse_args()
    path = args.policy.resolve(strict=True)
    policy = json.loads(path.read_text(encoding="utf-8"))
    timelock = validate_policy(policy, require_schedule=False)
    state = fetch_beacon_state(timelock)
    updated = register(policy, args.adaptation_unlock_utc, args.final_unlock_utc, state)
    path.write_text(canonical_json(updated), encoding="utf-8")
    print(canonical_json({"status": "timelock_schedule_registered", "stages": updated["timelock"]["stages"]}), end="")


if __name__ == "__main__":
    main()
