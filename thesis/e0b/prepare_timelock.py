"""Prepare two-stage drand timelocks without writing split-label plaintext."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from audit import canonical_json, digest
from timelock_common import (
    fetch_beacon_state, inspect_tle_binary, object_rules, parse_ciphertext_identity,
    require_clean_pushed_repository, source_commitments, validate_policy,
)


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def csv_bytes(fieldnames, records):
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=fieldnames, lineterminator="\n")
    writer.writeheader()
    writer.writerows(records)
    return stream.getvalue().encode("utf-8")


def split_label_bytes(path, policy):
    with path.open("r", encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        fieldnames = reader.fieldnames
        if not fieldnames or "e2_role" not in fieldnames:
            raise ValueError("sealed T labels do not contain the frozen e2_role field")
        rows_by_role = {rule["e2_role"]: [] for rule in policy["logical_label_objects"].values()}
        for row in reader:
            role = row.get("e2_role")
            if role not in rows_by_role:
                raise ValueError("sealed T label row has an unknown E2 role")
            rows_by_role[role].append(row)
    result = {}
    for object_id, rule in policy["logical_label_objects"].items():
        records = rows_by_role[rule["e2_role"]]
        if len(records) != rule["expected_rows"]:
            raise ValueError(f"sealed T label partition row count differs from policy: {object_id}")
        result[object_id] = csv_bytes(fieldnames, records)
    return result


def protected_source_paths(run_dir):
    sources = json.loads((run_dir / "public/source_manifest.json").read_text(encoding="utf-8"))
    dataset = json.loads((run_dir / "public/dataset_source.json").read_text(encoding="utf-8"))
    return {
        "private_label_summary": run_dir / "private/label_summary_PRIVATE.json",
        "redemption_source": Path(sources["coupon_redempt.csv"]["local_path"]),
        "transaction_source": Path(sources["transaction_data.csv"]["local_path"]),
        "original_archive": Path(dataset["local_archive"]),
    }


def encrypt(tle, network, chain_hash, round_number, output_path, data=None, input_path=None):
    command = [
        str(tle), "-e", "-n", network, "-c", chain_hash, "-r", str(round_number),
        "-a", "-o", str(output_path),
    ]
    if input_path is not None:
        command.append(str(input_path))
    completed = subprocess.run(
        command, input=data, check=False, capture_output=True,
    )
    if completed.returncode != 0:
        raise ValueError(f"tle encryption failed for {output_path.name}")
    os.chmod(output_path, 0o600)
    identity = parse_ciphertext_identity(output_path)
    if identity != {"round": round_number, "chain_hash": chain_hash}:
        raise ValueError(f"tle ciphertext identity mismatch: {output_path.name}")


def prepare(policy_path, run_dir, output_dir, repository, tle_path, beacon_state=None):
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    timelock = validate_policy(policy)
    head = require_clean_pushed_repository(repository)
    tle = inspect_tle_binary(tle_path)
    beacon_state = beacon_state or fetch_beacon_state(timelock)
    latest_round = beacon_state["latest"]["round"]
    adaptation_round = timelock["stages"]["adaptation"]["round"]
    lead = (adaptation_round - latest_round) * timelock["period_seconds"]
    if lead < timelock["minimum_preparation_lead_seconds"]:
        raise ValueError("adaptation round is no longer at least seven days in the future")

    commitments = source_commitments(run_dir)
    label_path = run_dir / "private/labels_T_SEALED.csv"
    sources = protected_source_paths(run_dir)
    if digest(label_path) != commitments["labels_T_parent"]:
        raise ValueError("sealed T label parent changed before timelock preparation")
    for object_id, source in sources.items():
        if digest(source) != commitments[object_id]:
            raise ValueError(f"protected source changed before encryption: {object_id}")
    split_data = split_label_bytes(label_path, policy)
    if output_dir.exists():
        raise ValueError("timelock output directory must not already exist")
    output_dir.mkdir(mode=0o700, parents=True)

    objects = {}
    for object_id, rule in object_rules(policy).items():
        stage = rule["unlock_stage"]
        round_number = timelock["stages"][stage]["round"]
        if object_id in split_data:
            filename = f"{object_id}.csv.tle"
            data = split_data[object_id]
            plaintext_sha256 = sha256_bytes(data)
            encrypt(
                tle_path, timelock["network_url"], timelock["chain_hash"],
                round_number, output_dir / filename, data=data,
            )
            record = {
                "source_parent_sha256": commitments["labels_T_parent"],
                "e2_role": rule["e2_role"],
                "rows": rule["expected_rows"],
            }
        else:
            source = sources[object_id]
            filename = f"{object_id}{source.suffix}.tle"
            plaintext_sha256 = commitments[object_id]
            encrypt(
                tle_path, timelock["network_url"], timelock["chain_hash"],
                round_number, output_dir / filename, input_path=source,
            )
            record = {}
        encrypted = output_dir / filename
        record.update({
            "unlock_stage": stage,
            "drand_round": round_number,
            "unlock_at_utc": timelock["stages"][stage]["unlock_at_utc"],
            "plaintext_sha256": plaintext_sha256,
            "ciphertext_file": filename,
            "ciphertext_sha256": digest(encrypted),
        })
        objects[object_id] = record

    manifest = {
        "schema_version": "3.0",
        "stage": "public_timelock_seal",
        "created_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "code_commit": head,
        "policy_sha256": digest(policy_path),
        "e0b_run": run_dir.name,
        "drand": {
            "network_url": timelock["network_url"],
            "chain_hash": timelock["chain_hash"],
            "beacon_id": timelock["beacon_id"],
            "observed_latest_round": latest_round,
            "observed_latest_randomness": beacon_state["latest"]["randomness"],
            "observed_latest_signature": beacon_state["latest"]["signature"],
        },
        "tle_runtime": tle,
        "split_plaintext_written_to_disk": False,
        "plaintext_removal_performed": False,
        "objects": objects,
    }
    manifest_path = output_dir / "timelock-seal-manifest.json"
    manifest_path.write_text(canonical_json(manifest), encoding="utf-8")
    os.chmod(manifest_path, 0o600)
    return {
        "status": "timelock_packages_prepared",
        "manifest": str(manifest_path),
        "manifest_sha256": digest(manifest_path),
        "encrypted_objects": len(objects),
        "split_plaintext_written_to_disk": False,
        "plaintext_removal_authorized": False,
        "adaptation_unlock_at_utc": timelock["stages"]["adaptation"]["unlock_at_utc"],
        "final_unlock_at_utc": timelock["stages"]["final_evaluation"]["unlock_at_utc"],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_directory", type=Path)
    parser.add_argument("output_directory", type=Path)
    parser.add_argument("--tle", type=Path, required=True)
    parser.add_argument("--policy", type=Path, default=Path(__file__).with_name("timelock-policy.json"))
    parser.add_argument("--repository", type=Path, default=Path(__file__).resolve().parents[2])
    args = parser.parse_args()
    result = prepare(
        args.policy.resolve(strict=True), args.run_directory.resolve(strict=True),
        args.output_directory.resolve(), args.repository.resolve(strict=True),
        args.tle.resolve(strict=True),
    )
    print(canonical_json(result), end="")


if __name__ == "__main__":
    main()
