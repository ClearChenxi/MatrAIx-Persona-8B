"""Unlock one registered stage only after its prediction freeze and drand round pass."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from audit import canonical_json, digest
from timelock_common import inspect_tle_binary
from verify_timelock import verify_release


def unlock(
    policy_path, stage, run_dir, manifest_path, freeze_path, freeze_commit,
    repository, tle_path, output_dir,
):
    release = verify_release(
        policy_path, stage, run_dir, manifest_path, freeze_path, freeze_commit, repository,
        tle_path,
    )
    inspect_tle_binary(tle_path)
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    timelock = policy["timelock"]
    if output_dir.exists():
        raise ValueError("unlock output directory must not already exist")
    output_dir.mkdir(mode=0o700, parents=True)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    outputs = {}
    for object_id in release["authorized_objects"]:
        record = manifest["objects"][object_id]
        ciphertext = manifest_path.parent / record["ciphertext_file"]
        suffix = ".csv" if object_id.startswith("T_") else ".recovered"
        output = output_dir / f"{object_id}{suffix}"
        completed = subprocess.run(
            [
                str(tle_path), "-d", "-n", timelock["network_url"],
                "-c", timelock["chain_hash"], "-o", str(output), str(ciphertext),
            ],
            check=False, capture_output=True,
        )
        if completed.returncode != 0:
            raise ValueError(f"tle decryption failed for {object_id}")
        os.chmod(output, 0o600)
        if digest(output) != record["plaintext_sha256"]:
            raise ValueError(f"unlocked plaintext commitment mismatch: {object_id}")
        outputs[object_id] = {"file": output.name, "sha256": digest(output)}
    unlock_manifest = {
        "schema_version": "1.0",
        "stage": stage,
        "status": "timelock_stage_unlocked",
        "unlocked_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "timelock_seal_manifest_sha256": digest(manifest_path),
        "prediction_freeze_manifest_sha256": digest(freeze_path),
        "prediction_freeze_commit": freeze_commit,
        "drand_round": release["drand_round"],
        "outputs": outputs,
    }
    path = output_dir / "unlock-manifest.json"
    path.write_text(canonical_json(unlock_manifest), encoding="utf-8")
    os.chmod(path, 0o600)
    return unlock_manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("adaptation", "final_evaluation"))
    parser.add_argument("run_directory", type=Path)
    parser.add_argument("seal_manifest", type=Path)
    parser.add_argument("freeze_manifest", type=Path)
    parser.add_argument("freeze_commit")
    parser.add_argument("output_directory", type=Path)
    parser.add_argument("--tle", type=Path, required=True)
    parser.add_argument("--policy", type=Path, default=Path(__file__).with_name("timelock-policy.json"))
    parser.add_argument("--repository", type=Path, default=Path(__file__).resolve().parents[2])
    args = parser.parse_args()
    result = unlock(
        args.policy.resolve(strict=True), args.stage, args.run_directory.resolve(strict=True),
        args.seal_manifest.resolve(strict=True), args.freeze_manifest.resolve(strict=True),
        args.freeze_commit, args.repository.resolve(strict=True), args.tle.resolve(strict=True),
        args.output_directory.resolve(),
    )
    print(canonical_json(result), end="")


if __name__ == "__main__":
    main()
