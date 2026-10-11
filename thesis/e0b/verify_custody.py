"""Verify preregistered-custodian handoff and staged release receipts."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from pathlib import Path

from audit import canonical_json, digest


FINGERPRINT = re.compile(r"^(?:[0-9A-F]{40}|[0-9A-F]{64})$")


def normalize_fingerprint(value):
    return (value or "").replace(" ", "").upper()


def trusted_custodian(policy):
    custodian = policy["trusted_custodian"]
    fingerprint = normalize_fingerprint(custodian.get("gpg_fingerprint"))
    if custodian.get("status") != "registered" or not FINGERPRINT.fullmatch(fingerprint):
        raise ValueError("trusted custodian fingerprint is not preregistered in policy")
    if not custodian.get("name") or not custodian.get("role"):
        raise ValueError("trusted custodian identity is incomplete in policy")
    if not re.fullmatch(r"[0-9a-f]{64}", custodian.get("public_key_sha256") or ""):
        raise ValueError("trusted custodian public-key commitment is missing")
    if not custodian.get("external_attestation_type"):
        raise ValueError("trusted custodian external attestation type is missing")
    if not re.fullmatch(r"[0-9a-f]{64}", custodian.get("external_attestation_sha256") or ""):
        raise ValueError("trusted custodian external attestation commitment is missing")
    return fingerprint


def source_commitments(run_dir):
    seal = json.loads((run_dir / "public/label_seal_manifest.json").read_text(encoding="utf-8"))
    sources = json.loads((run_dir / "public/source_manifest.json").read_text(encoding="utf-8"))
    dataset = json.loads((run_dir / "public/dataset_source.json").read_text(encoding="utf-8"))
    return {
        "labels_T_parent": seal["sealed_files"]["labels_T_SEALED.csv"],
        "private_label_summary": seal["private_summary_sha256"],
        "redemption_source": sources["coupon_redempt.csv"]["sha256"],
        "transaction_source": sources["transaction_data.csv"]["sha256"],
        "original_archive": dataset["archive_sha256"],
    }


def plaintext_paths(run_dir):
    sources = json.loads((run_dir / "public/source_manifest.json").read_text(encoding="utf-8"))
    dataset = json.loads((run_dir / "public/dataset_source.json").read_text(encoding="utf-8"))
    return {
        "sealed_T_labels_parent": run_dir / "private/labels_T_SEALED.csv",
        "private_label_summary": run_dir / "private/label_summary_PRIVATE.json",
        "redemption_source": Path(sources["coupon_redempt.csv"]["local_path"]),
        "transaction_source": Path(sources["transaction_data.csv"]["local_path"]),
        "original_archive": Path(dataset["local_archive"]),
    }


def verify_signature(receipt_path, signature_path, policy_fingerprint):
    completed = subprocess.run(
        ["gpg", "--batch", "--status-fd", "1", "--verify", str(signature_path), str(receipt_path)],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise ValueError("custody signature verification failed")
    valid_signatures = []
    for line in completed.stdout.splitlines():
        marker = "[GNUPG:] VALIDSIG "
        if line.startswith(marker):
            fields = line[len(marker):].split()
            signature_fingerprints = {fields[0].upper()}
            if FINGERPRINT.fullmatch(fields[-1].upper()):
                signature_fingerprints.add(fields[-1].upper())
            valid_signatures.append(signature_fingerprints)
    if len(valid_signatures) != 1 or policy_fingerprint not in valid_signatures[0]:
        raise ValueError("signature does not match the preregistered custodian fingerprint")


def verify_inaccessible(paths):
    readable = [object_id for object_id, path in paths.items() if path.exists() and os.access(path, os.R_OK)]
    if readable:
        raise ValueError(f"protected plaintext remains readable by executor: {', '.join(sorted(readable))}")


def validate_handoff_manifest(policy, policy_path, run_dir, manifest, manifest_dir):
    if manifest.get("schema_version") != "2.0" or manifest.get("stage") != "custody_handoff":
        raise ValueError("invalid custody handoff manifest identity")
    fingerprint = trusted_custodian(policy)
    if normalize_fingerprint(manifest.get("encryption_recipient_fingerprint")) != fingerprint:
        raise ValueError("handoff encryption recipient differs from preregistered custodian")
    if manifest.get("policy_sha256") != digest(policy_path):
        raise ValueError("handoff manifest policy commitment mismatch")
    if not re.fullmatch(r"[0-9a-f]{40}", manifest.get("code_commit") or ""):
        raise ValueError("handoff manifest code commit is invalid")
    commitments = source_commitments(run_dir)
    objects = manifest.get("objects", {})
    expected_ids = set(policy["logical_label_objects"]) | set(policy["custody_only_objects"])
    if set(objects) != expected_ids:
        raise ValueError("handoff object inventory differs from policy")
    parent_sha = commitments["labels_T_parent"]
    total_rows = 0
    for object_id, rule in policy["logical_label_objects"].items():
        record = objects[object_id]
        if record.get("source_parent_sha256") != parent_sha or record.get("e2_role") != rule["e2_role"]:
            raise ValueError(f"logical label source or role mismatch: {object_id}")
        if record.get("rows") != rule["expected_rows"]:
            raise ValueError(f"logical label row count mismatch: {object_id}")
        if not re.fullmatch(r"[0-9a-f]{64}", record.get("plaintext_sha256") or ""):
            raise ValueError(f"logical label plaintext commitment missing: {object_id}")
        total_rows += record["rows"]
    if total_rows != 1714:
        raise ValueError("logical label partitions do not cover the frozen T row count")
    for object_id in policy["custody_only_objects"]:
        if objects[object_id].get("plaintext_sha256") != commitments[object_id]:
            raise ValueError(f"custody source commitment mismatch: {object_id}")
    for object_id, record in objects.items():
        ciphertext = (manifest_dir / record.get("ciphertext_file", "")).resolve()
        if ciphertext.parent != manifest_dir.resolve() or not ciphertext.is_file() or ciphertext.is_symlink():
            raise ValueError(f"custody ciphertext is missing or unsafe: {object_id}")
        if digest(ciphertext) != record.get("ciphertext_sha256"):
            raise ValueError(f"custody ciphertext commitment mismatch: {object_id}")


def validate_initial_receipt(policy, receipt, handoff_manifest_path):
    expected_objects = set(policy["logical_label_objects"]) | set(policy["custody_only_objects"])
    if receipt.get("schema_version") != "2.0" or receipt.get("stage") != "initial_custody":
        raise ValueError("invalid initial custody receipt identity")
    if receipt.get("status") != "accepted_by_preregistered_custodian":
        raise ValueError("initial custody receipt is not accepted")
    if receipt.get("executor_has_decryption_key") is not False:
        raise ValueError("experiment executor must not hold the custody decryption key")
    if set(receipt.get("accepted_objects", [])) != expected_objects:
        raise ValueError("custodian did not accept the complete protected object inventory")
    if receipt.get("handoff_manifest_sha256") != digest(handoff_manifest_path):
        raise ValueError("custody receipt handoff-manifest commitment mismatch")


def verify_initial(policy_path, run_dir, handoff_manifest_path, receipt_path, signature_path):
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    fingerprint = trusted_custodian(policy)
    manifest = json.loads(handoff_manifest_path.read_text(encoding="utf-8"))
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    validate_handoff_manifest(policy, policy_path, run_dir, manifest, handoff_manifest_path.parent)
    validate_initial_receipt(policy, receipt, handoff_manifest_path)
    verify_signature(receipt_path, signature_path, fingerprint)
    verify_inaccessible(plaintext_paths(run_dir))
    return {
        "status": "independent_custody_verified",
        "protected_objects": len(manifest["objects"]),
        "two_stage_release": True,
        "executor_has_decryption_key": False,
        "protected_plaintext_readable": False,
    }


def validate_release_receipt(policy, stage, receipt, freeze_manifest_path, handoff_manifest_path):
    rule = policy["release_stages"][stage]
    freeze = json.loads(freeze_manifest_path.read_text(encoding="utf-8"))
    if freeze.get("schema_version") != "1.0" or freeze.get("status") != rule["condition_id"]:
        raise ValueError("freeze manifest identity differs from the release condition")
    if freeze.get("code_committed_and_pushed") is not True:
        raise ValueError("freeze manifest does not confirm committed and pushed code")
    if set(freeze.get("prediction_sets", [])) != set(rule["required_prediction_sets"]):
        raise ValueError("freeze manifest prediction sets differ from policy")
    checksums = freeze.get("prediction_checksums")
    if not isinstance(checksums, dict) or not checksums:
        raise ValueError("freeze manifest prediction checksum inventory is empty")
    if any(not re.fullmatch(r"[0-9a-f]{64}", value or "") for value in checksums.values()):
        raise ValueError("freeze manifest contains an invalid prediction checksum")
    if receipt.get("schema_version") != "2.0" or receipt.get("stage") != stage:
        raise ValueError("release receipt stage differs from requested stage")
    if receipt.get("status") != "release_authorized_by_preregistered_custodian":
        raise ValueError("release receipt is not authorized")
    if set(receipt.get("authorized_objects", [])) != set(rule["authorized_objects"]):
        raise ValueError("release receipt authorizes the wrong objects")
    if receipt.get("condition_id") != rule["condition_id"]:
        raise ValueError("release receipt condition differs from policy")
    if receipt.get("delivery_mode") not in rule["allowed_delivery_modes"]:
        raise ValueError("release receipt delivery mode differs from policy")
    if receipt.get("freeze_manifest_sha256") != digest(freeze_manifest_path):
        raise ValueError("release receipt freeze-manifest commitment mismatch")
    if receipt.get("custody_handoff_manifest_sha256") != digest(handoff_manifest_path):
        raise ValueError("release receipt custody-manifest commitment mismatch")


def verify_release(policy_path, stage, receipt_path, signature_path, freeze_manifest_path, handoff_manifest_path):
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    fingerprint = trusted_custodian(policy)
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    validate_release_receipt(policy, stage, receipt, freeze_manifest_path, handoff_manifest_path)
    verify_signature(receipt_path, signature_path, fingerprint)
    return {
        "status": f"{stage}_release_verified",
        "authorized_objects": policy["release_stages"][stage]["authorized_objects"],
        "signature_matches_preregistered_custodian": True,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", type=Path, default=Path(__file__).with_name("custody-policy.json"))
    subparsers = parser.add_subparsers(dest="command", required=True)
    initial = subparsers.add_parser("initial")
    initial.add_argument("run_directory", type=Path)
    initial.add_argument("handoff_manifest", type=Path)
    initial.add_argument("receipt", type=Path)
    initial.add_argument("signature", type=Path)
    release = subparsers.add_parser("release")
    release.add_argument("stage", choices=("adaptation", "final_evaluation"))
    release.add_argument("receipt", type=Path)
    release.add_argument("signature", type=Path)
    release.add_argument("freeze_manifest", type=Path)
    release.add_argument("handoff_manifest", type=Path)
    args = parser.parse_args()
    policy_path = args.policy.resolve(strict=True)
    if args.command == "initial":
        result = verify_initial(
            policy_path,
            args.run_directory.resolve(strict=True),
            args.handoff_manifest.resolve(strict=True),
            args.receipt.resolve(strict=True),
            args.signature.resolve(strict=True),
        )
    else:
        result = verify_release(
            policy_path,
            args.stage,
            args.receipt.resolve(strict=True),
            args.signature.resolve(strict=True),
            args.freeze_manifest.resolve(strict=True),
            args.handoff_manifest.resolve(strict=True),
        )
    print(canonical_json(result), end="")


if __name__ == "__main__":
    main()
