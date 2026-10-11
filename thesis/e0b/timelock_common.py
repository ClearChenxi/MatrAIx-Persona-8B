"""Shared validation for the public drand timelock workflow."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import subprocess
import tempfile
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from audit import digest


SHA256 = re.compile(r"^[0-9a-f]{64}$")
COMMIT = re.compile(r"^[0-9a-f]{40}$")
EXPECTED_BRANCH = "thesis-integration-3633d8d"
MAX_BEACON_AGE_PERIODS = 5


def parse_utc(value):
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError("timestamp must use ISO 8601 UTC with a trailing Z")
    parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    if parsed.tzinfo != timezone.utc:
        raise ValueError("timestamp must be UTC")
    return parsed


def utc_text(timestamp):
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat().replace("+00:00", "Z")


def round_timestamp(timelock, round_number):
    if not isinstance(round_number, int) or round_number < 1:
        raise ValueError("drand round must be a positive integer")
    return timelock["genesis_time"] + (round_number - 1) * timelock["period_seconds"]


def first_round_at_or_after(timelock, requested_timestamp):
    delta = requested_timestamp - timelock["genesis_time"]
    if delta < 0:
        raise ValueError("requested unlock predates the drand chain")
    period = timelock["period_seconds"]
    return (delta + period - 1) // period + 1


def validate_policy(policy, require_schedule=True):
    if policy.get("schema_version") != "3.0":
        raise ValueError("timelock policy schema must be 3.0")
    timelock = policy.get("timelock", {})
    fixed = {
        "implementation": "github.com/drand/tlock/cmd/tle",
        "module_version": "v1.2.0",
        "module_sum": "h1:YmbH2PXsq6UeUXljq+GMZcDicUlVnLIW9QbLqYoDp6g=",
        "network_url": "https://api.drand.sh/",
        "beacon_id": "quicknet",
        "chain_hash": "52db9ba70e0cc0f6eaf7803dd07447a1f5477735fd3f661792ba94600c84e971",
        "scheme_id": "bls-unchained-g1-rfc9380",
        "public_key": "83cf0f2896adee7eb8b5f01fcad3912212c437e0073e911fb90022d3e760183c8c4b450b6a0a6c3ac6a5776a2d1064510d1fec758c921cc22b0e17e63aaf4bcb5ed66304de9cf809bd274ca73bab4af5a6e9c76a4bc09e76eae8991ef5ece45a",
        "genesis_time": 1692803367,
        "period_seconds": 3,
        "minimum_preparation_lead_seconds": 604800,
        "minimum_stage_separation_seconds": 604800,
    }
    for key, expected in fixed.items():
        if timelock.get(key) != expected:
            raise ValueError(f"timelock policy changed fixed Quicknet field: {key}")
    expected_labels = {
        "T_adaptation_labels": {
            "source": "private/labels_T_SEALED.csv", "e2_role": "adaptation",
            "expected_rows": 328, "unlock_stage": "adaptation",
        },
        "T_final_evaluation_labels": {
            "source": "private/labels_T_SEALED.csv", "e2_role": "final_evaluation",
            "expected_rows": 1386, "unlock_stage": "final_evaluation",
        },
    }
    if policy.get("logical_label_objects") != expected_labels:
        raise ValueError("logical T label inventory changed")
    sources = policy.get("protected_source_objects", {})
    if set(sources) != {"private_label_summary", "redemption_source", "transaction_source", "original_archive"}:
        raise ValueError("protected source inventory changed")
    if any(record.get("unlock_stage") != "final_evaluation" for record in sources.values()):
        raise ValueError("a reconstructing source is not locked until final evaluation")
    expected_release = {
        "adaptation": {
            "authorized_objects": ["T_adaptation_labels"],
            "condition_id": "E1a_E1b_predictions_frozen",
            "required_prediction_sets": ["E1a", "E1b"],
        },
        "final_evaluation": {
            "authorized_objects": [
                "T_final_evaluation_labels", "private_label_summary", "redemption_source",
                "transaction_source", "original_archive",
            ],
            "condition_id": "E1a_E1b_E2_predictions_frozen",
            "required_prediction_sets": ["E1a", "E1b", "E2"],
        },
    }
    if policy.get("release_stages") != expected_release:
        raise ValueError("timelock release rules changed")
    contract = policy.get("prediction_contract", {})
    if contract.get("path") != "thesis/e0b/prediction-contract.json":
        raise ValueError("prediction contract path changed")
    if contract.get("status") not in {"pending_protocol_freeze", "registered"}:
        raise ValueError("prediction contract has an invalid status")
    if contract["status"] == "pending_protocol_freeze" and contract.get("sha256") is not None:
        raise ValueError("pending prediction contract must not have a registered digest")
    if contract["status"] == "registered" and not SHA256.fullmatch(contract.get("sha256") or ""):
        raise ValueError("registered prediction contract digest is invalid")
    expected_governance = {
        "plaintext_scan_scope": [
            "repository:thesis/e0b/runs",
            "derived:redemption_and_transaction_source_parent",
            "derived:original_archive_parent",
        ],
        "replicas": {
            "minimum": 2,
            "distinct_resolved_directories": True,
            "distinct_storage_location_ids": True,
            "distinct_filesystem_device_ids": True,
            "live_copy_restore_and_sha256": True,
        },
        "authoritative_plaintext_paths_must_not_exist": True,
        "pre_removal_inventory_required": True,
        "unreadable_candidate_fails_closed": True,
        "public_time_attestation": {
            "repository": "ClearChenxi/MatrAIx-Persona-8B",
            "signer_workflow": "github.com/ClearChenxi/MatrAIx-Persona-8B/.github/workflows/attest-prediction-freeze.yml",
            "trusted_timestamp_must_precede_unlock": True,
        },
    }
    if policy.get("governance_controls") != expected_governance:
        raise ValueError("timelock governance controls changed")
    expected_plaintext_paths = [
        "private/labels_T_SEALED.csv",
        "private/label_summary_PRIVATE.json",
        "coupon_redempt.csv",
        "transaction_data.csv",
        "original_archive",
    ]
    if policy.get("plaintext_paths_that_must_not_exist_after_seal") != expected_plaintext_paths:
        raise ValueError("protected plaintext removal rules changed")
    if not require_schedule:
        return timelock
    if policy.get("status") != "timelock_schedule_registered" or timelock.get("status") != "registered":
        raise ValueError("two-stage timelock schedule is not registered")
    stages = timelock.get("stages", {})
    if set(stages) != {"adaptation", "final_evaluation"}:
        raise ValueError("timelock stage inventory changed")
    for stage, record in stages.items():
        expected_time = utc_text(round_timestamp(timelock, record.get("round")))
        if record.get("unlock_at_utc") != expected_time:
            raise ValueError(f"timelock round and UTC time differ: {stage}")
        parse_utc(record.get("requested_unlock_at_utc"))
    adaptation = stages["adaptation"]["round"]
    final = stages["final_evaluation"]["round"]
    separation = (final - adaptation) * timelock["period_seconds"]
    if separation < timelock["minimum_stage_separation_seconds"]:
        raise ValueError("timelock stages are too close together")
    return timelock


def validate_pending_schedule(policy):
    timelock = validate_policy(policy, require_schedule=False)
    if policy.get("status") != "awaiting_timelock_schedule_registration":
        raise ValueError("timelock policy is not awaiting its one-time schedule registration")
    if timelock.get("status") != "pending_schedule_registration":
        raise ValueError("timelock schedule was already registered")
    if timelock.get("schedule_registration") is not None:
        raise ValueError("timelock schedule registration record already exists")
    expected = {"requested_unlock_at_utc": None, "unlock_at_utc": None, "round": None}
    stages = timelock.get("stages")
    if set(stages or {}) != {"adaptation", "final_evaluation"}:
        raise ValueError("timelock stage inventory changed")
    if any(record != expected for record in stages.values()):
        raise ValueError("timelock schedule contains a prior or partial registration")
    return timelock


def validate_prediction_contract_file(path, repository=None):
    contract = json.loads(path.read_text(encoding="utf-8"))
    if contract.get("schema_version") != "1.0" or contract.get("status") != "registered":
        raise ValueError("prediction contract is not fully registered")
    campaigns = contract.get("T_campaigns")
    if campaigns != [14, 15, 16, 17, 19, 20, 21, 22, 23, 25]:
        raise ValueError("prediction contract T campaign inventory changed")
    if contract.get("pool_rows") != {"adaptation": 328, "final_evaluation": 1386, "all_T": 1714}:
        raise ValueError("prediction contract T pool sizes changed")
    expected_campaign_rows = {
        "14": {"adaptation": 35, "final_evaluation": 188, "all_T": 223},
        "15": {"adaptation": 2, "final_evaluation": 14, "all_T": 16},
        "16": {"adaptation": 35, "final_evaluation": 153, "all_T": 188},
        "17": {"adaptation": 29, "final_evaluation": 173, "all_T": 202},
        "19": {"adaptation": 25, "final_evaluation": 105, "all_T": 130},
        "20": {"adaptation": 53, "final_evaluation": 191, "all_T": 244},
        "21": {"adaptation": 18, "final_evaluation": 47, "all_T": 65},
        "22": {"adaptation": 47, "final_evaluation": 229, "all_T": 276},
        "23": {"adaptation": 43, "final_evaluation": 140, "all_T": 183},
        "25": {"adaptation": 41, "final_evaluation": 146, "all_T": 187},
    }
    if contract.get("campaign_pool_rows") != expected_campaign_rows:
        raise ValueError("prediction contract per-campaign pool sizes changed")
    if contract.get("E1a_and_E1b_pool") != "final_evaluation":
        raise ValueError("E1a/E1b prediction pool must be final_evaluation")
    if contract.get("E2_prediction_pool") != "final_evaluation":
        raise ValueError("E2 prediction pool must be final_evaluation")
    if contract.get("required_prediction_sets") != ["E1a", "E1b", "E2"]:
        raise ValueError("prediction contract required set inventory changed")
    expected_e2_activity_sets = {
        "main": [14, 16, 17, 19, 20, 22, 23, 25],
        "sensitivity_n40": [20, 22, 23, 25],
    }
    if contract.get("E2_activity_sets") != expected_e2_activity_sets:
        raise ValueError("E2 activity sets differ from the frozen protocol")
    if contract.get("E2_budget_sets") != {
        "main": [0, 10, 20],
        "sensitivity_n40": [40],
    }:
        raise ValueError("E2 budget sets differ from the frozen protocol")
    expected_universe = {
        "path": "thesis/e1_data/runs/e1-data-20261011T042328.304949Z/private/links_T.csv",
        "sha256": "c4e2be2bfde72c2049cf8c78c824fedfbfe9212415ba0b35634c5407a9b9d9eb",
    }
    if contract.get("row_universe") != expected_universe:
        raise ValueError("prediction contract row universe changed")
    if repository is not None:
        universe_path = repository.resolve(strict=True) / expected_universe["path"]
        if digest(universe_path) != expected_universe["sha256"]:
            raise ValueError("prediction contract row universe file is missing or changed")
    artifacts = contract.get("artifact_requirements")
    if not isinstance(artifacts, list) or not artifacts:
        raise ValueError("prediction contract artifact inventory is empty")
    ids = [record.get("artifact_id") for record in artifacts]
    if any(not isinstance(value, str) or not value for value in ids) or len(set(ids)) != len(ids):
        raise ValueError("prediction contract artifact IDs must be nonempty and unique")
    covered_sets = set()
    paths = set()
    for record in artifacts:
        prediction_set = record.get("prediction_set")
        covered_sets.add(prediction_set)
        if prediction_set not in {"E1a", "E1b", "E2"}:
            raise ValueError("prediction contract contains an unknown prediction set")
        if not all(isinstance(record.get(key), str) and record[key] for key in ("method_id", "variant_id", "path", "prediction_column")):
            raise ValueError("prediction contract method, variant, and path must be explicit")
        if (
            record["path"] in paths or Path(record["path"]).is_absolute()
            or ".." in Path(record["path"]).parts or Path(record["path"]).suffix.lower() != ".csv"
        ):
            raise ValueError("prediction artifact paths must be unique repository-relative paths")
        paths.add(record["path"])
        record_campaigns = record.get("campaigns")
        if not isinstance(record_campaigns, list) or not record_campaigns or not set(record_campaigns) <= set(campaigns):
            raise ValueError("prediction artifact campaign inventory is invalid")
        repetitions = record.get("repetitions")
        expected_rows = record.get("expected_rows")
        if not isinstance(repetitions, int) or repetitions < 1 or not isinstance(expected_rows, int) or expected_rows < 1:
            raise ValueError("prediction artifact repetitions and row count must be positive integers")
        budget = record.get("adaptation_budget_per_campaign")
        if prediction_set in {"E1a", "E1b"}:
            if record.get("pool") != "final_evaluation" or record_campaigns != campaigns or budget != 0:
                raise ValueError("E1a/E1b artifacts must cover the final evaluation pool without adaptation labels")
            if expected_rows != 1386 * repetitions:
                raise ValueError("E1a/E1b expected rows must equal final evaluation rows times repetitions")
        else:
            if record.get("pool") != "final_evaluation" or not isinstance(budget, int):
                raise ValueError("E2 artifacts must declare an integer adaptation budget and final pool")
            if budget in {0, 10, 20}:
                if record_campaigns != expected_e2_activity_sets["main"]:
                    raise ValueError("E2 n=0/10/20 artifacts must cover the eight main campaigns")
            elif budget == 40:
                if record_campaigns != expected_e2_activity_sets["sensitivity_n40"]:
                    raise ValueError("E2 n=40 artifacts must cover the four sensitivity campaigns")
            else:
                raise ValueError("E2 adaptation budget is not registered")
            available = [expected_campaign_rows[str(campaign)]["adaptation"] for campaign in record_campaigns]
            if budget > min(available):
                raise ValueError("E2 adaptation budget exceeds an included campaign pool")
            expected = sum(
                expected_campaign_rows[str(campaign)]["final_evaluation"] for campaign in record_campaigns
            ) * repetitions
            if expected_rows != expected:
                raise ValueError("E2 expected rows do not equal registered campaign pools times repetitions")
    if covered_sets != {"E1a", "E1b", "E2"}:
        raise ValueError("prediction contract must fully enumerate E1a, E1b, and E2")
    return contract


def fetch_json(url, opener=urllib.request.urlopen):
    request = urllib.request.Request(url, headers={"User-Agent": "thesis-timelock/1.0"})
    with opener(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def verify_beacon_signature(timelock, latest, tle_path, runner=subprocess.run):
    inspect_tle_binary(tle_path)
    probe = b"thesis-drand-signature-probe-v1\n"
    with tempfile.TemporaryDirectory() as directory:
        encrypted = Path(directory) / "probe.tle"
        recovered = Path(directory) / "probe.txt"
        encrypted_result = runner(
            [
                str(tle_path), "-e", "-n", timelock["network_url"],
                "-c", timelock["chain_hash"], "-r", str(latest["round"]),
                "--force", "-a", "-o", str(encrypted),
            ],
            input=probe, check=False, capture_output=True,
        )
        if encrypted_result.returncode != 0:
            raise ValueError("official tle beacon probe encryption failed")
        decrypted_result = runner(
            [
                str(tle_path), "-d", "-n", timelock["network_url"],
                "-c", timelock["chain_hash"], "-o", str(recovered), str(encrypted),
            ],
            check=False, capture_output=True,
        )
        if decrypted_result.returncode != 0 or recovered.read_bytes() != probe:
            raise ValueError("latest drand signature failed official tle verification")


def validate_beacon_state(timelock, info, latest, now=None, signature_verifier=None):
    expected = {
        "hash": timelock["chain_hash"],
        "public_key": timelock["public_key"],
        "period": timelock["period_seconds"],
        "genesis_time": timelock["genesis_time"],
        "schemeID": timelock["scheme_id"],
    }
    for key, value in expected.items():
        if info.get(key) != value:
            raise ValueError(f"live drand metadata differs from policy: {key}")
    if info.get("metadata", {}).get("beaconID") != timelock["beacon_id"]:
        raise ValueError("live drand beacon ID differs from policy")
    if not isinstance(latest.get("round"), int) or not latest.get("randomness") or not latest.get("signature"):
        raise ValueError("latest drand response is incomplete")
    try:
        signature = bytes.fromhex(latest["signature"])
    except (TypeError, ValueError) as error:
        raise ValueError("latest drand signature is not hexadecimal") from error
    if hashlib.sha256(signature).hexdigest() != latest["randomness"]:
        raise ValueError("latest drand randomness is not the signature digest")
    observed_at = int((now or datetime.now(timezone.utc)).timestamp())
    round_time = round_timestamp(timelock, latest["round"])
    lag = observed_at - round_time
    if lag < 0:
        raise ValueError("latest drand response is from a future round")
    if lag > timelock["period_seconds"] * MAX_BEACON_AGE_PERIODS:
        raise ValueError("latest drand response is stale")
    if signature_verifier is None:
        raise ValueError("a pinned official-library drand signature verifier is required")
    signature_verifier(timelock, latest)
    return {"info": info, "latest": latest}


def fetch_beacon_state(
    timelock, tle_path, opener=urllib.request.urlopen, now=None,
    signature_verifier=None,
):
    base = f"{timelock['network_url'].rstrip('/')}/{timelock['chain_hash']}"
    info = fetch_json(f"{base}/info", opener)
    latest = fetch_json(f"{base}/public/latest", opener)
    verifier = signature_verifier or (
        lambda locked, beacon: verify_beacon_signature(locked, beacon, tle_path)
    )
    return validate_beacon_state(timelock, info, latest, now, verifier)


def run_git(repository, *args, binary=False):
    completed = subprocess.run(
        ["git", *args], cwd=repository, check=False, capture_output=True,
        text=not binary,
    )
    if completed.returncode != 0:
        raise ValueError(f"git {' '.join(args)} failed")
    return completed.stdout if binary else completed.stdout.strip()


def require_clean_pushed_repository(repository):
    if run_git(repository, "status", "--porcelain"):
        raise ValueError("timelock operation requires a clean repository")
    if run_git(repository, "branch", "--show-current") != EXPECTED_BRANCH:
        raise ValueError("timelock operation is on the wrong branch")
    head = run_git(repository, "rev-parse", "HEAD")
    remote = run_git(repository, "rev-parse", f"origin/{EXPECTED_BRANCH}")
    if head != remote:
        raise ValueError("timelock operation code is not pushed to origin")
    return head


def inspect_tle_binary(path):
    path = path.resolve(strict=True)
    completed = subprocess.run(
        ["go", "version", "-m", str(path)], check=False, capture_output=True, text=True,
    )
    if completed.returncode != 0:
        raise ValueError("cannot inspect tle Go build metadata")
    module = None
    for line in completed.stdout.splitlines():
        fields = line.strip().split("\t")
        if fields[:1] == ["mod"] and len(fields) >= 4:
            module = fields[1:4]
    expected = [
        "github.com/drand/tlock", "v1.2.0", "h1:YmbH2PXsq6UeUXljq+GMZcDicUlVnLIW9QbLqYoDp6g=",
    ]
    if module != expected:
        raise ValueError("tle binary is not the pinned official Go module build")
    build_lines = completed.stdout.splitlines()
    sanitized_build_metadata = "\n".join([path.name, *build_lines[1:]]) + "\n"
    return {
        "binary_name": path.name,
        "sha256": digest(path),
        "module": module[0],
        "module_version": module[1],
        "module_sum": module[2],
        "go_build_metadata": sanitized_build_metadata,
    }


def parse_ciphertext_identity(path):
    begin = "-----BEGIN AGE ENCRYPTED FILE-----"
    end = "-----END AGE ENCRYPTED FILE-----"
    payload_lines = []
    decoded = b""
    with path.open("r", encoding="ascii") as stream:
        if stream.readline().strip() != begin:
            raise ValueError(f"timelock ciphertext is not armored age data: {path.name}")
        for _ in range(32):
            line = stream.readline()
            if not line or line.strip() == end:
                break
            payload_lines.append(line.strip())
            payload = "".join(payload_lines)
            if len(payload) % 4:
                continue
            try:
                decoded = base64.b64decode(payload, validate=True)
            except ValueError as error:
                raise ValueError(f"timelock ciphertext armor is invalid: {path.name}") from error
            if decoded.count(b"\n") >= 2:
                break
    header = decoded.split(b"\n", 3)
    if len(header) < 3 or header[0] != b"age-encryption.org/v1":
        raise ValueError(f"timelock ciphertext age header is invalid: {path.name}")
    fields = header[1].decode("ascii").split()
    if len(fields) != 4 or fields[:2] != ["->", "tlock"] or not fields[2].isdigit():
        raise ValueError(f"timelock ciphertext recipient stanza is invalid: {path.name}")
    return {"round": int(fields[2]), "chain_hash": fields[3]}


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


def verify_absent(paths):
    present = [name for name, path in paths.items() if os.path.lexists(path)]
    if present:
        raise ValueError(f"protected plaintext path still exists: {', '.join(sorted(present))}")


def plaintext_scan_roots(repository, run_dir):
    paths = plaintext_paths(run_dir)
    roots = {
        (repository / "thesis/e0b/runs").resolve(),
        paths["redemption_source"].resolve().parent,
        paths["transaction_source"].resolve().parent,
        paths["original_archive"].resolve().parent,
    }
    return sorted(roots, key=str)


def physical_plaintext_commitments(manifest):
    label = manifest["objects"]["T_adaptation_labels"]
    commitments = {
        "sealed_T_labels_parent": {
            "sha256": label["source_parent_sha256"],
            "bytes": label["source_parent_bytes"],
        }
    }
    for object_id in ("private_label_summary", "redemption_source", "transaction_source", "original_archive"):
        record = manifest["objects"][object_id]
        commitments[object_id] = {
            "sha256": record["plaintext_sha256"],
            "bytes": record["plaintext_bytes"],
        }
    return commitments


def _raise_scan_error(error):
    raise ValueError(f"plaintext scan could not inspect declared scope: {error.filename or error}") from error


def find_matching_plaintext(search_roots, commitments, digest_file=digest):
    by_size = {}
    for object_id, record in commitments.items():
        by_size.setdefault(record["bytes"], {})[record["sha256"]] = object_id
    matches = []
    scanned = set()
    for root in search_roots:
        if not root.exists() or not root.is_dir():
            raise ValueError(f"plaintext scan root is missing or not a directory: {root}")
        for directory, directory_names, file_names in os.walk(root, followlinks=False, onerror=_raise_scan_error):
            parent = Path(directory)
            for name in directory_names:
                path = parent / name
                if path.is_symlink():
                    raise ValueError(f"plaintext scan cannot inspect symlinked directory: {path}")
            for name in file_names:
                path = parent / name
                if path.is_symlink():
                    raise ValueError(f"plaintext scan cannot inspect symlinked file: {path}")
                try:
                    resolved = path.resolve(strict=True)
                    stat = path.stat()
                except OSError as error:
                    raise ValueError(f"plaintext scan could not inspect candidate path: {path}") from error
                if not resolved.is_file():
                    continue
                candidates = by_size.get(stat.st_size)
                if not candidates:
                    continue
                if resolved in scanned:
                    continue
                scanned.add(resolved)
                try:
                    actual = digest_file(resolved)
                except OSError as error:
                    raise ValueError(f"candidate plaintext file cannot be inspected: {path}") from error
                if actual in candidates:
                    matches.append({
                        "object_id": candidates[actual],
                        "path": str(resolved),
                        "bytes": stat.st_size,
                        "sha256": actual,
                    })
    return sorted(matches, key=lambda item: (item["object_id"], item["path"]))


def object_rules(policy):
    return policy["logical_label_objects"] | policy["protected_source_objects"]
