"""Verify public timelock packages and prediction-gated release stages."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from audit import canonical_json, digest
from timelock_common import (
    COMMIT, EXPECTED_BRANCH, SHA256, fetch_beacon_state, object_rules,
    parse_ciphertext_identity, parse_utc, plaintext_paths, run_git,
    source_commitments, validate_policy, verify_inaccessible,
)


def validate_seal_manifest(policy, policy_path, run_dir, manifest, manifest_dir):
    if manifest.get("schema_version") != "3.0" or manifest.get("stage") != "public_timelock_seal":
        raise ValueError("invalid timelock seal manifest identity")
    timelock = validate_policy(policy)
    if manifest.get("policy_sha256") != digest(policy_path):
        raise ValueError("seal manifest policy commitment mismatch")
    if not COMMIT.fullmatch(manifest.get("code_commit") or ""):
        raise ValueError("seal manifest code commit is invalid")
    if manifest.get("split_plaintext_written_to_disk") is not False:
        raise ValueError("seal manifest does not confirm in-memory label splitting")
    drand = manifest.get("drand", {})
    for key in ("network_url", "chain_hash", "beacon_id"):
        if drand.get(key) != timelock[key]:
            raise ValueError(f"seal manifest drand field differs from policy: {key}")
    runtime = manifest.get("tle_runtime", {})
    expected_runtime = {
        "module": timelock["implementation"].rsplit("/cmd/tle", 1)[0],
        "module_version": timelock["module_version"],
        "module_sum": timelock["module_sum"],
    }
    for key, expected in expected_runtime.items():
        if runtime.get(key) != expected:
            raise ValueError(f"seal manifest tle runtime differs from policy: {key}")

    commitments = source_commitments(run_dir)
    objects = manifest.get("objects", {})
    rules = object_rules(policy)
    if set(objects) != set(rules):
        raise ValueError("seal object inventory differs from policy")
    parent_sha = commitments["labels_T_parent"]
    total_rows = 0
    for object_id, rule in rules.items():
        record = objects[object_id]
        stage = rule["unlock_stage"]
        expected_round = timelock["stages"][stage]["round"]
        if record.get("unlock_stage") != stage or record.get("drand_round") != expected_round:
            raise ValueError(f"timelock stage or round mismatch: {object_id}")
        if record.get("unlock_at_utc") != timelock["stages"][stage]["unlock_at_utc"]:
            raise ValueError(f"timelock UTC commitment mismatch: {object_id}")
        if object_id in policy["logical_label_objects"]:
            if record.get("source_parent_sha256") != parent_sha or record.get("e2_role") != rule["e2_role"]:
                raise ValueError(f"logical label source or role mismatch: {object_id}")
            if record.get("rows") != rule["expected_rows"]:
                raise ValueError(f"logical label row count mismatch: {object_id}")
            total_rows += record["rows"]
        elif record.get("plaintext_sha256") != commitments[object_id]:
            raise ValueError(f"protected source commitment mismatch: {object_id}")
        if not SHA256.fullmatch(record.get("plaintext_sha256") or ""):
            raise ValueError(f"plaintext commitment missing: {object_id}")
        ciphertext = (manifest_dir / record.get("ciphertext_file", "")).resolve()
        if ciphertext.parent != manifest_dir.resolve() or not ciphertext.is_file() or ciphertext.is_symlink():
            raise ValueError(f"timelock ciphertext is missing or unsafe: {object_id}")
        if digest(ciphertext) != record.get("ciphertext_sha256"):
            raise ValueError(f"timelock ciphertext commitment mismatch: {object_id}")
        identity = parse_ciphertext_identity(ciphertext)
        if identity != {"round": expected_round, "chain_hash": timelock["chain_hash"]}:
            raise ValueError(f"ciphertext does not encode the registered timelock: {object_id}")
    if total_rows != 1714:
        raise ValueError("logical label partitions do not cover the frozen T row count")
    return timelock


def validate_seal_commit(policy_path, code_commit, repository):
    repository_root = repository.resolve(strict=True)
    try:
        relative = policy_path.resolve(strict=True).relative_to(repository_root)
    except ValueError as error:
        raise ValueError("timelock policy must be inside the repository") from error
    committed_policy = run_git(repository, "show", f"{code_commit}:{relative.as_posix()}", binary=True)
    if committed_policy != policy_path.read_bytes():
        raise ValueError("working timelock policy differs from the seal code commit")
    completed = subprocess.run(
        ["git", "merge-base", "--is-ancestor", code_commit, f"origin/{EXPECTED_BRANCH}"],
        cwd=repository, check=False, capture_output=True,
    )
    if completed.returncode != 0:
        raise ValueError("timelock seal code was not pushed to origin")


def verify_package(policy_path, run_dir, manifest_path, repository):
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    timelock = validate_seal_manifest(policy, policy_path, run_dir, manifest, manifest_path.parent)
    validate_seal_commit(policy_path, manifest["code_commit"], repository)
    return {
        "status": "timelock_package_verified",
        "protected_objects": len(manifest["objects"]),
        "adaptation_unlock_at_utc": timelock["stages"]["adaptation"]["unlock_at_utc"],
        "final_unlock_at_utc": timelock["stages"]["final_evaluation"]["unlock_at_utc"],
        "plaintext_access_checked": False,
    }


def verify_initial(policy_path, run_dir, manifest_path, repository):
    result = verify_package(policy_path, run_dir, manifest_path, repository)
    verify_inaccessible(plaintext_paths(run_dir))
    result.update({
        "status": "public_timelock_seal_verified",
        "plaintext_access_checked": True,
        "protected_plaintext_readable": False,
    })
    return result


def validate_prediction_freeze(policy, stage, freeze_path, freeze_commit, manifest_path, repository):
    rule = policy["release_stages"][stage]
    freeze = json.loads(freeze_path.read_text(encoding="utf-8"))
    if freeze.get("schema_version") != "2.0" or freeze.get("status") != rule["condition_id"]:
        raise ValueError("prediction freeze identity differs from release condition")
    if freeze.get("code_committed_and_pushed") is not True:
        raise ValueError("prediction freeze does not confirm committed and pushed code")
    if set(freeze.get("prediction_sets", [])) != set(rule["required_prediction_sets"]):
        raise ValueError("prediction sets differ from timelock policy")
    checksums = freeze.get("prediction_checksums")
    if not isinstance(checksums, dict) or not checksums:
        raise ValueError("prediction checksum inventory is empty")
    if any(not isinstance(name, str) or not name or not SHA256.fullmatch(value or "") for name, value in checksums.items()):
        raise ValueError("prediction checksum inventory contains an invalid entry")
    repository_root = repository.resolve(strict=True)
    for name, expected in checksums.items():
        candidate = (repository_root / name).resolve(strict=True)
        try:
            candidate.relative_to(repository_root)
        except ValueError as error:
            raise ValueError("prediction artifact escapes the repository") from error
        if not candidate.is_file() or candidate.is_symlink():
            raise ValueError("prediction artifact is missing, unsafe, or not a regular file")
        actual = hashlib.sha256(candidate.read_bytes()).hexdigest()
        if actual != expected:
            raise ValueError(f"prediction artifact commitment mismatch: {name}")
    if freeze.get("timelock_seal_manifest_sha256") != digest(manifest_path):
        raise ValueError("prediction freeze points to a different timelock seal")
    code_commit = freeze.get("code_commit")
    if not COMMIT.fullmatch(code_commit or "") or not COMMIT.fullmatch(freeze_commit or ""):
        raise ValueError("prediction freeze commit is invalid")
    try:
        relative = freeze_path.resolve(strict=True).relative_to(repository.resolve(strict=True))
    except ValueError as error:
        raise ValueError("prediction freeze manifest must be inside the repository") from error
    committed_bytes = run_git(repository, "show", f"{freeze_commit}:{relative.as_posix()}", binary=True)
    if committed_bytes != freeze_path.read_bytes():
        raise ValueError("working prediction freeze differs from the specified commit")
    for commit in (code_commit, freeze_commit):
        completed = subprocess.run(
            ["git", "merge-base", "--is-ancestor", commit, f"origin/{EXPECTED_BRANCH}"],
            cwd=repository, check=False, capture_output=True,
        )
        if completed.returncode != 0:
            raise ValueError("prediction freeze references code not pushed to origin")
    completed = subprocess.run(
        ["git", "merge-base", "--is-ancestor", code_commit, freeze_commit],
        cwd=repository, check=False, capture_output=True,
    )
    if completed.returncode != 0:
        raise ValueError("prediction code commit is not an ancestor of the freeze commit")
    unlock_at = parse_utc(policy["timelock"]["stages"][stage]["unlock_at_utc"])
    if parse_utc(freeze.get("frozen_at_utc")) >= unlock_at:
        raise ValueError("prediction freeze time is not before the registered unlock")
    commit_time = datetime.fromisoformat(
        run_git(repository, "show", "-s", "--format=%cI", freeze_commit)
    ).astimezone(timezone.utc)
    if commit_time >= unlock_at:
        raise ValueError("prediction freeze commit time is not before the registered unlock")
    return freeze


def verify_release(
    policy_path, stage, run_dir, manifest_path, freeze_path, freeze_commit, repository,
    beacon_state=None,
):
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    timelock = validate_seal_manifest(policy, policy_path, run_dir, manifest, manifest_path.parent)
    validate_seal_commit(policy_path, manifest["code_commit"], repository)
    validate_prediction_freeze(policy, stage, freeze_path, freeze_commit, manifest_path, repository)
    beacon_state = beacon_state or fetch_beacon_state(timelock)
    required_round = timelock["stages"][stage]["round"]
    if beacon_state["latest"]["round"] < required_round:
        raise ValueError("registered drand round has not been published")
    return {
        "status": f"{stage}_timelock_release_verified",
        "authorized_objects": policy["release_stages"][stage]["authorized_objects"],
        "drand_round": required_round,
        "observed_latest_round": beacon_state["latest"]["round"],
        "prediction_freeze_commit": freeze_commit,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", type=Path, default=Path(__file__).with_name("timelock-policy.json"))
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("package", "initial"):
        command = subparsers.add_parser(name)
        command.add_argument("run_directory", type=Path)
        command.add_argument("seal_manifest", type=Path)
        command.add_argument("--repository", type=Path, default=Path(__file__).resolve().parents[2])
    release = subparsers.add_parser("release")
    release.add_argument("stage", choices=("adaptation", "final_evaluation"))
    release.add_argument("run_directory", type=Path)
    release.add_argument("seal_manifest", type=Path)
    release.add_argument("freeze_manifest", type=Path)
    release.add_argument("freeze_commit")
    release.add_argument("--repository", type=Path, default=Path(__file__).resolve().parents[2])
    args = parser.parse_args()
    policy_path = args.policy.resolve(strict=True)
    if args.command == "package":
        result = verify_package(
            policy_path, args.run_directory.resolve(strict=True), args.seal_manifest.resolve(strict=True),
            args.repository.resolve(strict=True),
        )
    elif args.command == "initial":
        result = verify_initial(
            policy_path, args.run_directory.resolve(strict=True), args.seal_manifest.resolve(strict=True),
            args.repository.resolve(strict=True),
        )
    else:
        result = verify_release(
            policy_path, args.stage, args.run_directory.resolve(strict=True),
            args.seal_manifest.resolve(strict=True), args.freeze_manifest.resolve(strict=True),
            args.freeze_commit, args.repository.resolve(strict=True),
        )
    print(canonical_json(result), end="")


if __name__ == "__main__":
    main()
