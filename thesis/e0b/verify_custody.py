"""Verify a signed independent-custody receipt without opening protected data."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path

from audit import canonical_json


def nested_value(data, dotted_key):
    value = data
    for key in dotted_key.split("."):
        value = value[key]
    return value


def expected_objects(policy, run_dir):
    seal = json.loads((run_dir / "public/label_seal_manifest.json").read_text(encoding="utf-8"))
    sources = json.loads((run_dir / "public/source_manifest.json").read_text(encoding="utf-8"))
    dataset = json.loads((run_dir / "public/dataset_source.json").read_text(encoding="utf-8"))
    result = {}
    for item in policy["protected_objects"]:
        if item["source"] == "e0b_private":
            path = run_dir / item["relative_path"]
            sha256 = nested_value(seal, item["commitment_field"])
        elif item["source"] == "source_manifest":
            record = sources[item["table"]]
            path = Path(record["local_path"])
            sha256 = record["sha256"]
        elif item["source"] == "dataset_source":
            path = Path(dataset[item["path_field"]])
            sha256 = dataset[item["commitment_field"]]
        else:
            raise ValueError("unknown custody object source")
        result[item["id"]] = {"sha256": sha256, "executor_path": str(path)}
    return result


def validate_receipt(policy, receipt, expected):
    missing = sorted(set(policy["receipt_required_fields"]) - set(receipt))
    if missing:
        raise ValueError(f"custody receipt missing required fields: {', '.join(missing)}")
    if receipt["schema_version"] != "1.0" or receipt["status"] != "accepted_by_independent_custodian":
        raise ValueError("custody receipt is not an accepted version 1.0 receipt")
    if not receipt["custodian_name"].strip() or not receipt["custodian_role"].strip():
        raise ValueError("custodian identity and role are required")
    if receipt["executor_has_decryption_key"] is not False:
        raise ValueError("experiment executor must not hold the custody decryption key")
    required_release = policy["custodian_requirements"]["release_condition"]
    if receipt["release_condition"] != required_release:
        raise ValueError("custody release condition differs from policy")
    objects = receipt["objects"]
    if set(objects) != set(expected):
        raise ValueError("custody receipt object inventory differs from policy")
    for object_id, record in expected.items():
        if objects[object_id].get("sha256") != record["sha256"]:
            raise ValueError(f"custody commitment mismatch for {object_id}")
        if objects[object_id].get("accepted") is not True:
            raise ValueError(f"custodian did not accept {object_id}")


def verify_signature(receipt_path, signature_path, expected_fingerprint):
    completed = subprocess.run(
        ["gpg", "--batch", "--status-fd", "1", "--verify", str(signature_path), str(receipt_path)],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise ValueError("custody receipt signature verification failed")
    fingerprints = []
    for line in completed.stdout.splitlines():
        marker = "[GNUPG:] VALIDSIG "
        if line.startswith(marker):
            fingerprints.append(line[len(marker):].split()[0].upper())
    normalized = expected_fingerprint.replace(" ", "").upper()
    if fingerprints != [normalized]:
        raise ValueError("custody receipt signature fingerprint mismatch")


def verify_inaccessible(expected):
    readable = []
    for object_id, record in expected.items():
        path = Path(record["executor_path"])
        if path.exists() and os.access(path, os.R_OK):
            readable.append(object_id)
    if readable:
        raise ValueError(f"protected plaintext remains readable by executor: {', '.join(sorted(readable))}")


def verify_custody(policy_path, run_dir, receipt_path, signature_path):
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    expected = expected_objects(policy, run_dir)
    validate_receipt(policy, receipt, expected)
    verify_signature(receipt_path, signature_path, receipt["custodian_signing_key_fingerprint"])
    verify_inaccessible(expected)
    return {
        "status": "independent_custody_verified",
        "protected_objects": len(expected),
        "signed_receipt": True,
        "executor_has_decryption_key": False,
        "protected_plaintext_readable": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_directory", type=Path)
    parser.add_argument("receipt", type=Path)
    parser.add_argument("signature", type=Path)
    parser.add_argument("--policy", type=Path, default=Path(__file__).with_name("custody-policy.json"))
    args = parser.parse_args()
    result = verify_custody(
        args.policy.resolve(strict=True),
        args.run_directory.resolve(strict=True),
        args.receipt.resolve(strict=True),
        args.signature.resolve(strict=True),
    )
    print(canonical_json(result), end="")


if __name__ == "__main__":
    main()
