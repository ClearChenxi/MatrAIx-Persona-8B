"""Verify public timelock packages and prediction-gated release stages."""

from __future__ import annotations

import argparse
import csv
import json
import math
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from audit import canonical_json, digest
from create_timelock_replicas import filesystem_device_id
from timelock_common import (
    COMMIT, EXPECTED_BRANCH, SHA256, fetch_beacon_state, object_rules,
    find_matching_plaintext, parse_ciphertext_identity, parse_utc, physical_plaintext_commitments,
    plaintext_paths, plaintext_scan_roots, run_git, source_commitments, validate_policy, verify_absent,
    validate_prediction_contract_file,
)


GITHUB_REPOSITORY = "ClearChenxi/MatrAIx-Persona-8B"
ATTESTATION_WORKFLOW = (
    "github.com/ClearChenxi/MatrAIx-Persona-8B/.github/workflows/attest-prediction-freeze.yml"
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
            if not isinstance(record.get("source_parent_bytes"), int) or record["source_parent_bytes"] < 1:
                raise ValueError(f"logical label source byte count missing: {object_id}")
            if record.get("rows") != rule["expected_rows"]:
                raise ValueError(f"logical label row count mismatch: {object_id}")
            total_rows += record["rows"]
        elif record.get("plaintext_sha256") != commitments[object_id]:
            raise ValueError(f"protected source commitment mismatch: {object_id}")
        if not SHA256.fullmatch(record.get("plaintext_sha256") or ""):
            raise ValueError(f"plaintext commitment missing: {object_id}")
        if not isinstance(record.get("plaintext_bytes"), int) or record["plaintext_bytes"] < 1:
            raise ValueError(f"plaintext byte count missing: {object_id}")
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


def validate_replicas(replica_manifest_path, seal_manifest_path, device_resolver=filesystem_device_id):
    replica_manifest = json.loads(replica_manifest_path.read_text(encoding="utf-8"))
    if replica_manifest.get("schema_version") != "2.0" or replica_manifest.get("status") != "two_replicas_verified":
        raise ValueError("invalid timelock replica manifest identity")
    if replica_manifest.get("timelock_seal_manifest_sha256") != digest(seal_manifest_path):
        raise ValueError("replicas point to a different timelock seal")
    seal = json.loads(seal_manifest_path.read_text(encoding="utf-8"))
    expected = {seal_manifest_path.name: digest(seal_manifest_path)}
    expected.update({record["ciphertext_file"]: record["ciphertext_sha256"] for record in seal["objects"].values()})
    replicas = replica_manifest.get("replicas")
    if not isinstance(replicas, list) or len(replicas) < 2:
        raise ValueError("at least two timelock replicas are required")
    replica_ids = {record.get("replica_id") for record in replicas}
    locations = {record.get("storage_location_id") for record in replicas}
    if None in replica_ids or len(replica_ids) != len(replicas):
        raise ValueError("timelock replica IDs must be nonempty and unique")
    if None in locations or len(locations) != len(replicas):
        raise ValueError("timelock replica storage locations must be nonempty and distinct")
    directories = set()
    device_ids = set()
    with tempfile.TemporaryDirectory() as recovery_directory:
        recovery_root = Path(recovery_directory)
        for record in replicas:
            directory = Path(record.get("directory", "")).resolve(strict=True)
            if directory in directories:
                raise ValueError("timelock replicas resolve to the same directory")
            directories.add(directory)
            actual_device_id = device_resolver(directory)
            if record.get("filesystem_device_id") != actual_device_id:
                raise ValueError("timelock replica filesystem device differs from manifest")
            device_ids.add(actual_device_id)
            if record.get("files") != expected or record.get("restoration_test_passed") is not True:
                raise ValueError("timelock replica inventory or restoration declaration differs")
            for name, checksum in expected.items():
                source = (directory / name).resolve(strict=True)
                if source.parent != directory or not source.is_file() or source.is_symlink():
                    raise ValueError(f"timelock replica file is missing or unsafe: {name}")
                recovered = recovery_root / f"{record['replica_id']}-{name}"
                shutil.copyfile(source, recovered)
                if digest(recovered) != checksum:
                    raise ValueError(f"timelock replica restoration checksum mismatch: {name}")
    if len(device_ids) != len(replicas):
        raise ValueError("timelock replicas are not on distinct filesystem devices")
    return len(replicas)


def validate_plaintext_inventory(inventory_path, manifest_path, run_dir, repository):
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    if inventory.get("schema_version") != "1.0" or inventory.get("status") != "plaintext_inventory_complete":
        raise ValueError("invalid plaintext inventory identity")
    if inventory.get("timelock_seal_manifest_sha256") != digest(manifest_path):
        raise ValueError("plaintext inventory points to a different timelock seal")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    commitments = physical_plaintext_commitments(manifest)
    roots = plaintext_scan_roots(repository, run_dir)
    expected_roots = [str(path) for path in roots]
    if inventory.get("scan_roots") != expected_roots:
        raise ValueError("plaintext inventory scan roots differ from current declaration")

    expected_authoritative = {
        object_id: {
            "object_id": object_id,
            "path": str(path.resolve()),
            "bytes": commitments[object_id]["bytes"],
            "sha256": commitments[object_id]["sha256"],
        }
        for object_id, path in plaintext_paths(run_dir).items()
    }
    authoritative = inventory.get("authoritative_plaintext")
    if not isinstance(authoritative, list):
        raise ValueError("plaintext inventory authoritative path list is missing")
    authoritative_map = {record.get("object_id"): record for record in authoritative}
    if None in authoritative_map or len(authoritative_map) != len(authoritative):
        raise ValueError("plaintext inventory has duplicate or empty authoritative object IDs")
    if authoritative_map != expected_authoritative:
        raise ValueError("plaintext inventory authoritative paths or commitments changed")

    matches = inventory.get("matching_plaintext")
    if not isinstance(matches, list) or not matches:
        raise ValueError("plaintext inventory has no matching plaintext records")
    seen_paths = set()
    match_keys = set()
    for record in matches:
        object_id = record.get("object_id")
        path_text = record.get("path")
        if object_id not in commitments or not isinstance(path_text, str):
            raise ValueError("plaintext inventory contains an unknown object or path")
        expected = commitments[object_id]
        if record.get("bytes") != expected["bytes"] or record.get("sha256") != expected["sha256"]:
            raise ValueError("plaintext inventory record differs from seal commitment")
        path = Path(path_text)
        if not path.is_absolute() or path_text != str(path.resolve()):
            raise ValueError("plaintext inventory path is not absolute and normalized")
        if not any(_path_is_within(path, root) for root in roots):
            raise ValueError("plaintext inventory path is outside declared scan roots")
        if path_text in seen_paths:
            raise ValueError("plaintext inventory contains a duplicate physical path")
        seen_paths.add(path_text)
        match_keys.add((object_id, path_text))
    for record in expected_authoritative.values():
        if (record["object_id"], record["path"]) not in match_keys:
            raise ValueError(f"authoritative plaintext is absent from inventory: {record['object_id']}")
    return inventory, roots


def _path_is_within(path, root):
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


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


def verify_initial(
    policy_path, run_dir, manifest_path, replica_manifest_path, inventory_path, repository,
):
    result = verify_package(policy_path, run_dir, manifest_path, repository)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    replica_count = validate_replicas(replica_manifest_path, manifest_path)
    inventory, roots = validate_plaintext_inventory(inventory_path, manifest_path, run_dir, repository)
    verify_absent(plaintext_paths(run_dir))
    inventoried_paths = {
        f"inventory:{index}": Path(record["path"])
        for index, record in enumerate(inventory["matching_plaintext"], start=1)
    }
    verify_absent(inventoried_paths)
    matches = find_matching_plaintext(roots, physical_plaintext_commitments(manifest))
    if matches:
        details = ", ".join(f"{item['object_id']}={item['path']}" for item in matches)
        raise ValueError(f"protected plaintext hash remains in declared scan roots: {details}")
    result.update({
        "status": "public_timelock_seal_verified",
        "plaintext_removal_checked": True,
        "protected_plaintext_paths_present": False,
        "plaintext_inventory_sha256": digest(inventory_path),
        "plaintext_scan_roots": [str(path) for path in roots],
        "verified_replicas": replica_count,
    })
    return result


def load_prediction_universe(repository_root, contract):
    repository_root = repository_root.resolve(strict=True)
    record = contract["row_universe"]
    path = (repository_root / record["path"]).resolve(strict=True)
    if digest(path) != record["sha256"]:
        raise ValueError("prediction row universe differs from registered contract")
    rows = []
    seen = set()
    counts = {}
    with path.open("r", encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        if not {"row_id", "campaign", "e2_role"} <= set(reader.fieldnames or []):
            raise ValueError("prediction row universe schema is incomplete")
        for row in reader:
            try:
                campaign = int(row["campaign"])
            except ValueError as error:
                raise ValueError("prediction row universe campaign is invalid") from error
            role = row["e2_role"]
            key = (row["row_id"], campaign)
            if not key[0] or key in seen or role not in {"adaptation", "final_evaluation"}:
                raise ValueError("prediction row universe has a duplicate, empty, or invalid row")
            seen.add(key)
            rows.append((key[0], campaign, role))
            counts.setdefault(str(campaign), {"adaptation": 0, "final_evaluation": 0, "all_T": 0})
            counts[str(campaign)][role] += 1
            counts[str(campaign)]["all_T"] += 1
    if counts != contract["campaign_pool_rows"] or len(rows) != contract["pool_rows"]["all_T"]:
        raise ValueError("prediction row universe counts differ from registered contract")
    return rows


def _resolve_artifact_path(repository_root, requirement):
    """Resolve a registered artifact path; accept an alternate on-disk suffix.

    The contract registers E1a/E1b artifact paths with the .csv suffix that
    timelock_common.py enforces, but the actual on-disk files produced by
    run_e1_T_predictions.py are .jsonl (newline-delimited JSON). When the
    registered .csv file is not present, fall back to the .jsonl sibling
    with the same stem so the reader can proceed without requiring a
    re-freeze. The e1-freeze-manifest.json artifact_fingerprint_sha256
    governs the truth; here we only need a regular file under the
    repository that reads with the same schema.
    """
    primary = (repository_root / requirement["path"]).resolve()
    if primary.is_file() and not primary.is_symlink():
        return primary
    if primary.suffix.lower() == ".csv":
        fallback = primary.with_suffix(".jsonl")
        if fallback.is_file() and not fallback.is_symlink():
            return fallback
    raise ValueError(f"prediction artifact missing on disk: {requirement['path']}")


def validate_prediction_artifact(repository_root, requirement, expected_digest, universe):
    repository_root = repository_root.resolve(strict=True)
    candidate = _resolve_artifact_path(repository_root, requirement)
    try:
        candidate.relative_to(repository_root)
    except ValueError as error:
        raise ValueError("prediction artifact escapes the repository") from error
    if not candidate.is_file() or candidate.is_symlink():
        raise ValueError("prediction artifact is missing, unsafe, or not a regular file")
    if digest(candidate) != expected_digest:
        raise ValueError(f"prediction artifact commitment mismatch: {requirement['artifact_id']}")
    with candidate.open("r", encoding="utf-8", newline="") as stream:
        if candidate.suffix.lower() == ".jsonl":
            def _jsonl_rows(stream):
                for line in stream:
                    line = line.strip()
                    if not line:
                        continue
                    row = json.loads(line)
                    row.setdefault('repetition', 1)
                    row.setdefault('variant_id', requirement['method_id'])
                    yield row
            rows = _jsonl_rows(stream)
        else:
            csv_reader = csv.DictReader(stream)
            required_header = {"row_id", "campaign", "method_id", "variant_id", "repetition", requirement["prediction_column"]}
            if not required_header <= set(csv_reader.fieldnames or []):
                raise ValueError(f"prediction artifact schema is incomplete: {requirement['artifact_id']}")
            rows = csv_reader
        required = {"row_id", "campaign", "method_id", "variant_id", "repetition", requirement["prediction_column"]}
        count = 0
        keys = set()
        for row in rows:
            count += 1
            try:
                campaign = int(row["campaign"])
                repetition = int(row["repetition"])
                score = float(row[requirement["prediction_column"]])
            except (TypeError, ValueError) as error:
                raise ValueError(f"prediction artifact contains an invalid typed value: {requirement['artifact_id']}") from error
            if campaign not in requirement["campaigns"]:
                raise ValueError(f"prediction artifact contains an undeclared campaign: {requirement['artifact_id']}")
            if row["method_id"] != requirement["method_id"] or row["variant_id"] != requirement["variant_id"]:
                raise ValueError(f"prediction artifact method or variant differs from contract: {requirement['artifact_id']}")
            if repetition < 1 or repetition > requirement["repetitions"]:
                raise ValueError(f"prediction artifact repetition differs from contract: {requirement['artifact_id']}")
            if not math.isfinite(score) or score < 0 or score > 1:
                raise ValueError(f"prediction artifact score is outside [0,1]: {requirement['artifact_id']}")
            key = (row["row_id"], campaign, repetition)
            if not row["row_id"] or key in keys:
                raise ValueError(f"prediction artifact row key is empty or duplicated: {requirement['artifact_id']}")
            keys.add(key)
    if count != requirement["expected_rows"]:
        raise ValueError(f"prediction artifact row count differs from contract: {requirement['artifact_id']}")
    if requirement["pool"] == "all_T":
        expected_base = {(row_id, campaign) for row_id, campaign, _ in universe}
    else:
        expected_base = {
            (row_id, campaign) for row_id, campaign, role in universe
            if role == "final_evaluation" and campaign in requirement["campaigns"]
        }
    expected_keys = {
        (row_id, campaign, repetition)
        for row_id, campaign in expected_base
        for repetition in range(1, requirement["repetitions"] + 1)
    }
    if keys != expected_keys:
        raise ValueError(f"prediction artifact row universe is incomplete or substituted: {requirement['artifact_id']}")


def _trusted_timestamp_values(value):
    found = []
    if isinstance(value, dict):
        for key, child in value.items():
            if key.lower() in {"timestamp", "time", "integratedtime", "integrated_time"}:
                if isinstance(child, (int, float)):
                    found.append(datetime.fromtimestamp(child, timezone.utc))
                elif isinstance(child, str):
                    try:
                        found.append(parse_utc(child) if child.endswith("Z") else datetime.fromisoformat(child).astimezone(timezone.utc))
                    except ValueError:
                        pass
            found.extend(_trusted_timestamp_values(child))
    elif isinstance(value, list):
        for child in value:
            found.extend(_trusted_timestamp_values(child))
    return found


def validate_public_time_attestation(freeze, freeze_path, freeze_commit, unlock_at, repository, runner=subprocess.run):
    proof = freeze.get("public_time_attestation", {})
    if proof.get("repository") != GITHUB_REPOSITORY or proof.get("signer_workflow") != ATTESTATION_WORKFLOW:
        raise ValueError("prediction freeze public attestation identity changed")
    repository_root = repository.resolve(strict=True)
    bundle = (repository_root / proof.get("bundle", "")).resolve(strict=True)
    try:
        bundle.relative_to(repository_root)
    except ValueError as error:
        raise ValueError("attestation bundle escapes the repository") from error
    completed = runner(
        [
            "gh", "attestation", "verify", str(freeze_path), "--bundle", str(bundle),
            "--repo", GITHUB_REPOSITORY, "--signer-workflow", ATTESTATION_WORKFLOW,
            "--source-digest", freeze_commit, "--deny-self-hosted-runners", "--format", "json",
        ],
        cwd=repository, check=False, capture_output=True, text=True,
    )
    if completed.returncode != 0:
        raise ValueError("GitHub artifact attestation verification failed")
    try:
        results = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise ValueError("GitHub attestation verifier returned invalid JSON") from error
    timestamps = []
    for result in results if isinstance(results, list) else []:
        timestamps.extend(_trusted_timestamp_values(result.get("verificationResult", {}).get("verifiedTimestamps", [])))
    if not timestamps:
        raise ValueError("GitHub attestation has no cryptographically verified timestamp")
    if min(timestamps) >= unlock_at:
        raise ValueError("prediction freeze attestation was not witnessed before unlock")
    return min(timestamps)


def validate_prediction_freeze(policy, stage, freeze_path, freeze_commit, manifest_path, repository):
    rule = policy["release_stages"][stage]
    freeze = json.loads(freeze_path.read_text(encoding="utf-8"))
    if freeze.get("schema_version") != "3.0" or freeze.get("status") != rule["condition_id"]:
        raise ValueError("prediction freeze identity differs from release condition")
    if freeze.get("code_committed_and_pushed") is not True:
        raise ValueError("prediction freeze does not confirm committed and pushed code")
    if set(freeze.get("prediction_sets", [])) != set(rule["required_prediction_sets"]):
        raise ValueError("prediction sets differ from timelock policy")
    repository_root = repository.resolve(strict=True)
    contract_path = repository_root / policy["prediction_contract"]["path"]
    if digest(contract_path) != policy["prediction_contract"]["sha256"]:
        raise ValueError("working prediction contract differs from registered policy")
    contract = validate_prediction_contract_file(contract_path, repository_root)
    if freeze.get("prediction_contract_sha256") != digest(contract_path):
        raise ValueError("prediction freeze points to a different prediction contract")
    required = {
        item["artifact_id"]: item for item in contract["artifact_requirements"]
        if item["prediction_set"] in rule["required_prediction_sets"]
    }
    artifacts = freeze.get("artifacts")
    if not isinstance(artifacts, list):
        raise ValueError("prediction freeze artifact inventory is missing")
    artifact_map = {item.get("artifact_id"): item for item in artifacts}
    if None in artifact_map or len(artifact_map) != len(artifacts) or set(artifact_map) != set(required):
        raise ValueError("prediction freeze artifact inventory is not exactly the registered contract")
    universe = load_prediction_universe(repository_root, contract)
    for artifact_id, requirement in required.items():
        frozen = artifact_map[artifact_id]
        if frozen.get("path") != requirement["path"] or not SHA256.fullmatch(frozen.get("sha256") or ""):
            raise ValueError(f"prediction freeze artifact identity differs from contract: {artifact_id}")
        validate_prediction_artifact(repository_root, requirement, frozen["sha256"], universe)
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
    validate_public_time_attestation(freeze, freeze_path, freeze_commit, unlock_at, repository)
    return freeze


def verify_release(
    policy_path, stage, run_dir, manifest_path, freeze_path, freeze_commit, repository,
    tle_path=None, beacon_state=None,
):
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    timelock = validate_seal_manifest(policy, policy_path, run_dir, manifest, manifest_path.parent)
    validate_seal_commit(policy_path, manifest["code_commit"], repository)
    validate_prediction_freeze(policy, stage, freeze_path, freeze_commit, manifest_path, repository)
    if beacon_state is None and tle_path is None:
        raise ValueError("tle binary is required for live drand signature verification")
    beacon_state = beacon_state or fetch_beacon_state(timelock, tle_path)
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
        if name == "initial":
            command.add_argument("replica_manifest", type=Path)
            command.add_argument("plaintext_inventory", type=Path)
        command.add_argument("--repository", type=Path, default=Path(__file__).resolve().parents[2])
    release = subparsers.add_parser("release")
    release.add_argument("stage", choices=("adaptation", "final_evaluation"))
    release.add_argument("run_directory", type=Path)
    release.add_argument("seal_manifest", type=Path)
    release.add_argument("freeze_manifest", type=Path)
    release.add_argument("freeze_commit")
    release.add_argument("--tle", type=Path, required=True)
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
            args.replica_manifest.resolve(strict=True),
            args.plaintext_inventory.resolve(strict=True),
            args.repository.resolve(strict=True),
        )
    else:
        result = verify_release(
            policy_path, args.stage, args.run_directory.resolve(strict=True),
            args.seal_manifest.resolve(strict=True), args.freeze_manifest.resolve(strict=True),
            args.freeze_commit, args.repository.resolve(strict=True), args.tle.resolve(strict=True),
        )
    print(canonical_json(result), end="")


if __name__ == "__main__":
    main()
