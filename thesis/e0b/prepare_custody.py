"""Prepare encrypted two-stage custody packages without writing split-label plaintext."""

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
from verify_custody import normalize_fingerprint, source_commitments, trusted_custodian


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


def run_git(repository, *args):
    completed = subprocess.run(
        ["git", *args], cwd=repository, check=False, capture_output=True, text=True,
    )
    if completed.returncode != 0:
        raise ValueError(f"git {' '.join(args)} failed")
    return completed.stdout.strip()


def require_clean_pushed_repository(repository, expected_branch):
    if run_git(repository, "status", "--porcelain"):
        raise ValueError("custody preparation requires a clean repository")
    branch = run_git(repository, "branch", "--show-current")
    if branch != expected_branch:
        raise ValueError("custody preparation is on the wrong branch")
    head = run_git(repository, "rev-parse", "HEAD")
    remote = run_git(repository, "rev-parse", f"origin/{expected_branch}")
    if head != remote:
        raise ValueError("custody preparation code is not pushed to origin")
    return head


def inspect_gpg_key(fingerprint, expected_public_key_sha256, attestation_path, expected_attestation_sha256):
    public = subprocess.run(
        ["gpg", "--batch", "--export", fingerprint], check=False, capture_output=True,
    )
    if public.returncode != 0 or not public.stdout:
        raise ValueError("preregistered custodian public key is unavailable")
    if sha256_bytes(public.stdout) != expected_public_key_sha256:
        raise ValueError("custodian public key differs from the policy commitment")
    secret = subprocess.run(
        ["gpg", "--batch", "--with-colons", "--list-secret-keys", fingerprint],
        check=False,
        capture_output=True,
        text=True,
    )
    if any(line.startswith(("sec:", "ssb:")) for line in secret.stdout.splitlines()):
        raise ValueError("executor environment holds the custodian secret key")
    if digest(attestation_path) != expected_attestation_sha256:
        raise ValueError("external trust-anchor attestation differs from policy")


def encrypt_bytes(data, output_path, fingerprint):
    completed = subprocess.run(
        [
            "gpg", "--batch", "--yes", "--trust-model", "always", "--recipient", fingerprint,
            "--output", str(output_path), "--encrypt",
        ],
        input=data,
        check=False,
        capture_output=True,
    )
    if completed.returncode != 0:
        raise ValueError(f"GPG encryption failed for {output_path.name}")
    os.chmod(output_path, 0o600)


def encrypt_file(input_path, output_path, fingerprint):
    completed = subprocess.run(
        [
            "gpg", "--batch", "--yes", "--trust-model", "always", "--recipient", fingerprint,
            "--output", str(output_path), "--encrypt", str(input_path),
        ],
        check=False,
        capture_output=True,
    )
    if completed.returncode != 0:
        raise ValueError(f"GPG encryption failed for {output_path.name}")
    os.chmod(output_path, 0o600)


def custody_source_paths(run_dir):
    sources = json.loads((run_dir / "public/source_manifest.json").read_text(encoding="utf-8"))
    dataset = json.loads((run_dir / "public/dataset_source.json").read_text(encoding="utf-8"))
    return {
        "private_label_summary": run_dir / "private/label_summary_PRIVATE.json",
        "redemption_source": Path(sources["coupon_redempt.csv"]["local_path"]),
        "transaction_source": Path(sources["transaction_data.csv"]["local_path"]),
        "original_archive": Path(dataset["local_archive"]),
    }


def prepare(policy_path, run_dir, output_dir, attestation_path, repository):
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    fingerprint = trusted_custodian(policy)
    head = require_clean_pushed_repository(repository, "thesis-integration-3633d8d")
    custodian = policy["trusted_custodian"]
    inspect_gpg_key(
        fingerprint,
        custodian["public_key_sha256"],
        attestation_path,
        custodian["external_attestation_sha256"],
    )
    commitments = source_commitments(run_dir)
    label_path = run_dir / "private/labels_T_SEALED.csv"
    sources = custody_source_paths(run_dir)
    if digest(label_path) != commitments["labels_T_parent"]:
        raise ValueError("sealed T label parent changed before custody preparation")
    for object_id, source in sources.items():
        if digest(source) != commitments[object_id]:
            raise ValueError(f"custody source changed before encryption: {object_id}")
    split_data = split_label_bytes(label_path, policy)
    if output_dir.exists():
        raise ValueError("custody output directory must not already exist")
    output_dir.mkdir(mode=0o700, parents=True)
    objects = {}
    for object_id, data in split_data.items():
        rule = policy["logical_label_objects"][object_id]
        filename = f"{object_id}.csv.gpg"
        encrypted = output_dir / filename
        encrypt_bytes(data, encrypted, fingerprint)
        objects[object_id] = {
            "source_parent_sha256": commitments["labels_T_parent"],
            "e2_role": rule["e2_role"],
            "rows": rule["expected_rows"],
            "plaintext_sha256": sha256_bytes(data),
            "ciphertext_file": filename,
            "ciphertext_sha256": digest(encrypted),
        }
    for object_id, source in sources.items():
        filename = f"{object_id}{source.suffix}.gpg"
        encrypted = output_dir / filename
        encrypt_file(source, encrypted, fingerprint)
        objects[object_id] = {
            "plaintext_sha256": commitments[object_id],
            "ciphertext_file": filename,
            "ciphertext_sha256": digest(encrypted),
        }
    manifest = {
        "schema_version": "2.0",
        "stage": "custody_handoff",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "code_commit": head,
        "policy_sha256": digest(policy_path),
        "e0b_run": run_dir.name,
        "encryption_recipient_fingerprint": fingerprint,
        "split_plaintext_written_to_disk": False,
        "executor_secret_key_present": False,
        "objects": objects,
    }
    manifest_path = output_dir / "handoff-manifest.json"
    manifest_path.write_text(canonical_json(manifest), encoding="utf-8")
    os.chmod(manifest_path, 0o600)
    return {
        "status": "encrypted_custody_packages_prepared",
        "handoff_manifest": str(manifest_path),
        "handoff_manifest_sha256": digest(manifest_path),
        "encrypted_objects": len(objects),
        "split_plaintext_written_to_disk": False,
        "plaintext_removal_authorized": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_directory", type=Path)
    parser.add_argument("output_directory", type=Path)
    parser.add_argument("attestation", type=Path)
    parser.add_argument("--policy", type=Path, default=Path(__file__).with_name("custody-policy.json"))
    parser.add_argument("--repository", type=Path, default=Path(__file__).resolve().parents[2])
    args = parser.parse_args()
    result = prepare(
        args.policy.resolve(strict=True),
        args.run_directory.resolve(strict=True),
        args.output_directory.resolve(),
        args.attestation.resolve(strict=True),
        args.repository.resolve(strict=True),
    )
    print(canonical_json(result), end="")


if __name__ == "__main__":
    main()
