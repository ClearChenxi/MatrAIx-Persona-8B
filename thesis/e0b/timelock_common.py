"""Shared validation for the public drand timelock workflow."""

from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from audit import digest


SHA256 = re.compile(r"^[0-9a-f]{64}$")
COMMIT = re.compile(r"^[0-9a-f]{40}$")
EXPECTED_BRANCH = "thesis-integration-3633d8d"


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
            "condition_id": "E1_E1a_predictions_frozen",
            "required_prediction_sets": ["E1", "E1a"],
        },
        "final_evaluation": {
            "authorized_objects": [
                "T_final_evaluation_labels", "private_label_summary", "redemption_source",
                "transaction_source", "original_archive",
            ],
            "condition_id": "E1_E1a_E2_predictions_frozen",
            "required_prediction_sets": ["E1", "E1a", "E2"],
        },
    }
    if policy.get("release_stages") != expected_release:
        raise ValueError("timelock release rules changed")
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


def fetch_json(url, opener=urllib.request.urlopen):
    request = urllib.request.Request(url, headers={"User-Agent": "thesis-timelock/1.0"})
    with opener(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_beacon_state(timelock, opener=urllib.request.urlopen):
    base = f"{timelock['network_url'].rstrip('/')}/{timelock['chain_hash']}"
    info = fetch_json(f"{base}/info", opener)
    latest = fetch_json(f"{base}/public/latest", opener)
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
    return {"info": info, "latest": latest}


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


def verify_inaccessible(paths):
    readable = [name for name, path in paths.items() if path.exists() and os.access(path, os.R_OK)]
    if readable:
        raise ValueError(f"protected plaintext remains readable by executor: {', '.join(sorted(readable))}")


def object_rules(policy):
    return policy["logical_label_objects"] | policy["protected_source_objects"]
