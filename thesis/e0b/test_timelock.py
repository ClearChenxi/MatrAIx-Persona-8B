from __future__ import annotations

import base64
import hashlib
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from create_timelock_replicas import create_replicas
from inventory_plaintext import create_inventory
from register_timelock_schedule import register
from timelock_common import (
    find_matching_plaintext, first_round_at_or_after, parse_ciphertext_identity,
    round_timestamp, utc_text, validate_beacon_state, validate_policy,
    validate_prediction_contract_file, verify_absent,
)
from verify_timelock import (
    load_prediction_universe, validate_prediction_artifact,
    validate_plaintext_inventory, validate_public_time_attestation, validate_replicas,
)
import verify_timelock


def pending_policy():
    return {
        "schema_version": "3.0",
        "status": "awaiting_timelock_schedule_registration",
        "prediction_contract": {
            "status": "pending_protocol_freeze",
            "path": "thesis/e0b/prediction-contract.json",
            "sha256": None,
        },
        "governance_controls": {
            "plaintext_scan_scope": [
                "repository:thesis/e0b/runs",
                "derived:redemption_and_transaction_source_parent",
                "derived:original_archive_parent",
            ],
            "replicas": {
                "minimum": 2, "distinct_resolved_directories": True,
                "distinct_storage_location_ids": True, "distinct_filesystem_device_ids": True,
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
        },
        "timelock": {
            "status": "pending_schedule_registration",
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
            "schedule_registration": None,
            "stages": {
                "adaptation": {"requested_unlock_at_utc": None, "unlock_at_utc": None, "round": None},
                "final_evaluation": {"requested_unlock_at_utc": None, "unlock_at_utc": None, "round": None},
            },
        },
        "logical_label_objects": {
            "T_adaptation_labels": {
                "source": "private/labels_T_SEALED.csv", "e2_role": "adaptation",
                "expected_rows": 328, "unlock_stage": "adaptation",
            },
            "T_final_evaluation_labels": {
                "source": "private/labels_T_SEALED.csv", "e2_role": "final_evaluation",
                "expected_rows": 1386, "unlock_stage": "final_evaluation",
            },
        },
        "protected_source_objects": {
            "private_label_summary": {"unlock_stage": "final_evaluation"},
            "redemption_source": {"unlock_stage": "final_evaluation"},
            "transaction_source": {"unlock_stage": "final_evaluation"},
            "original_archive": {"unlock_stage": "final_evaluation"},
        },
        "release_stages": {
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
        },
        "plaintext_paths_that_must_not_exist_after_seal": [
            "private/labels_T_SEALED.csv",
            "private/label_summary_PRIVATE.json",
            "coupon_redempt.csv",
            "transaction_data.csv",
            "original_archive",
        ],
    }


def plaintext_fixture(root):
    repository = root / "repository"
    run = repository / "thesis/e0b/runs/current"
    private = run / "private"
    public = run / "public"
    data = root / "data"
    archive_dir = root / "archive"
    for directory in (private, public, data, archive_dir):
        directory.mkdir(parents=True, exist_ok=True)
    files = {
        "sealed_T_labels_parent": private / "labels_T_SEALED.csv",
        "private_label_summary": private / "label_summary_PRIVATE.json",
        "redemption_source": data / "coupon_redempt.csv",
        "transaction_source": data / "transaction_data.csv",
        "original_archive": archive_dir / "source.zip",
    }
    payloads = {
        "sealed_T_labels_parent": b"parent-labels",
        "private_label_summary": b"private-summary",
        "redemption_source": b"redemption-source",
        "transaction_source": b"transaction-source",
        "original_archive": b"original-archive",
    }
    for object_id, path in files.items():
        path.write_bytes(payloads[object_id])
    (public / "source_manifest.json").write_text(json.dumps({
        "coupon_redempt.csv": {"local_path": str(files["redemption_source"])},
        "transaction_data.csv": {"local_path": str(files["transaction_source"])},
    }), encoding="utf-8")
    (public / "dataset_source.json").write_text(json.dumps({
        "local_archive": str(files["original_archive"]),
    }), encoding="utf-8")
    objects = {
        "T_adaptation_labels": {
            "source_parent_sha256": hashlib.sha256(payloads["sealed_T_labels_parent"]).hexdigest(),
            "source_parent_bytes": len(payloads["sealed_T_labels_parent"]),
        },
    }
    for object_id in ("private_label_summary", "redemption_source", "transaction_source", "original_archive"):
        objects[object_id] = {
            "plaintext_sha256": hashlib.sha256(payloads[object_id]).hexdigest(),
            "plaintext_bytes": len(payloads[object_id]),
        }
    seal = root / "timelock-seal-manifest.json"
    seal.write_text(json.dumps({"objects": objects}), encoding="utf-8")
    old_copy = repository / "thesis/e0b/runs/old/private/labels_T_SEALED.csv"
    old_copy.parent.mkdir(parents=True)
    old_copy.write_bytes(payloads["sealed_T_labels_parent"])
    return repository, run, seal, files, old_copy


class TimelockPolicyTests(unittest.TestCase):
    def test_pending_schedule_is_not_an_accepted_seal_policy(self):
        policy = pending_policy()
        validate_policy(policy, require_schedule=False)
        with self.assertRaisesRegex(ValueError, "schedule is not registered"):
            validate_policy(policy)

    def test_reconstructing_source_cannot_unlock_with_adaptation_labels(self):
        policy = pending_policy()
        policy["protected_source_objects"]["redemption_source"]["unlock_stage"] = "adaptation"
        with self.assertRaisesRegex(ValueError, "not locked until final"):
            validate_policy(policy, require_schedule=False)

    def test_release_inventory_cannot_omit_e2_or_final_sources(self):
        policy = pending_policy()
        policy["release_stages"]["final_evaluation"]["required_prediction_sets"] = ["E1", "E1a"]
        with self.assertRaisesRegex(ValueError, "release rules changed"):
            validate_policy(policy, require_schedule=False)

    def test_schedule_registration_uses_first_round_not_before_requested_time(self):
        policy = pending_policy()
        latest_round = 100
        latest_time = round_timestamp(policy["timelock"], latest_round)
        adaptation = utc_text(latest_time + 700000)
        final = utc_text(latest_time + 1400000)
        state = {"latest": {"round": latest_round, "randomness": "a", "signature": "b"}}
        registered = register(policy, adaptation, final, state, "a" * 64)
        timelock = validate_policy(registered)
        for stage in ("adaptation", "final_evaluation"):
            record = timelock["stages"][stage]
            self.assertEqual(record["unlock_at_utc"], utc_text(round_timestamp(timelock, record["round"])))

    def test_schedule_rejects_short_lead_and_short_stage_gap(self):
        policy = pending_policy()
        latest_round = 100
        latest_time = round_timestamp(policy["timelock"], latest_round)
        state = {"latest": {"round": latest_round, "randomness": "a", "signature": "b"}}
        with self.assertRaisesRegex(ValueError, "seven days after the live"):
            register(policy, utc_text(latest_time + 60), utc_text(latest_time + 700000), state, "a" * 64)
        with self.assertRaisesRegex(ValueError, "seven days after adaptation"):
            register(policy, utc_text(latest_time + 700000), utc_text(latest_time + 700060), state, "a" * 64)

    def test_schedule_registration_is_one_time_only(self):
        policy = pending_policy()
        latest_round = 100
        latest_time = round_timestamp(policy["timelock"], latest_round)
        state = {"latest": {"round": latest_round, "randomness": "a", "signature": "b"}}
        registered = register(
            policy, utc_text(latest_time + 700000), utc_text(latest_time + 1400000), state, "a" * 64,
        )
        with self.assertRaisesRegex(ValueError, "one-time schedule registration"):
            register(
                registered, utc_text(latest_time + 800000), utc_text(latest_time + 1500000), state, "a" * 64,
            )

    def test_round_mapping_is_ceiling_not_floor(self):
        timelock = pending_policy()["timelock"]
        genesis = timelock["genesis_time"]
        self.assertEqual(first_round_at_or_after(timelock, genesis), 1)
        self.assertEqual(first_round_at_or_after(timelock, genesis + 1), 2)
        self.assertEqual(first_round_at_or_after(timelock, genesis + 3), 2)

    def test_ciphertext_parser_reads_round_and_chain_from_armored_age_header(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sealed.tle"
            chain = pending_policy()["timelock"]["chain_hash"]
            payload = base64.b64encode(
                f"age-encryption.org/v1\n-> tlock 12345 {chain}\nstanza\nbody".encode("ascii")
            ).decode("ascii")
            path.write_text(
                f"-----BEGIN AGE ENCRYPTED FILE-----\n{payload}\n-----END AGE ENCRYPTED FILE-----\n",
                encoding="ascii",
            )
            self.assertEqual(parse_ciphertext_identity(path), {"round": 12345, "chain_hash": chain})

    def test_existing_plaintext_is_rejected_even_if_mode_is_zero(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "plaintext"
            path.write_text("sensitive", encoding="utf-8")
            path.chmod(0o000)
            try:
                with self.assertRaisesRegex(ValueError, "path still exists"):
                    verify_absent({"label": path})
            finally:
                path.chmod(0o600)
            path.unlink()
            verify_absent({"label": path})

    def test_plaintext_hash_scan_finds_duplicate_in_an_old_run(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old = root / "old-run/private/labels.csv"
            old.parent.mkdir(parents=True)
            old.write_bytes(b"sealed-label-copy")
            commitment = {
                "T_parent": {
                    "bytes": old.stat().st_size,
                    "sha256": hashlib.sha256(old.read_bytes()).hexdigest(),
                }
            }
            matches = find_matching_plaintext([root], commitment)
            self.assertEqual(matches[0]["object_id"], "T_parent")

    def test_plaintext_hash_scan_fails_closed_when_candidate_cannot_be_read(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "candidate"
            path.write_bytes(b"same-size")
            commitment = {
                "protected": {
                    "bytes": path.stat().st_size,
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                }
            }

            def denied(_path):
                raise PermissionError("denied")

            with self.assertRaisesRegex(ValueError, "cannot be inspected"):
                find_matching_plaintext([Path(directory)], commitment, digest_file=denied)

    def test_plaintext_hash_scan_rejects_symlinked_scope(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "target"
            target.write_bytes(b"candidate")
            (root / "linked").symlink_to(target)
            commitment = {
                "protected": {
                    "bytes": target.stat().st_size,
                    "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
                }
            }
            with self.assertRaisesRegex(ValueError, "symlinked file"):
                find_matching_plaintext([root], commitment)

    def test_pre_removal_inventory_finds_old_run_copy(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository, run, seal, _files, old_copy = plaintext_fixture(root)
            inventory_path = root / "plaintext-inventory.json"
            inventory = create_inventory(run, seal, repository, inventory_path)
            matches = {record["path"] for record in inventory["matching_plaintext"]}
            self.assertIn(str(old_copy.resolve()), matches)
            self.assertEqual(inventory_path.stat().st_mode & 0o777, 0o600)
            validate_plaintext_inventory(inventory_path, seal, run, repository)

    def test_inventory_paths_must_be_removed_not_made_unreadable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository, run, seal, files, old_copy = plaintext_fixture(root)
            inventory_path = root / "plaintext-inventory.json"
            inventory = create_inventory(run, seal, repository, inventory_path)
            old_copy.chmod(0o000)
            try:
                with self.assertRaisesRegex(ValueError, "path still exists"):
                    verify_absent({"old-copy": old_copy})
            finally:
                old_copy.chmod(0o600)
            for path in [*files.values(), old_copy]:
                path.unlink()
            inventory, _roots = validate_plaintext_inventory(inventory_path, seal, run, repository)
            verify_absent({
                str(index): Path(record["path"])
                for index, record in enumerate(inventory["matching_plaintext"])
            })

    def test_beacon_state_rejects_stale_response_and_checks_signature_callback(self):
        timelock = pending_policy()["timelock"]
        round_number = 100
        signature = bytes.fromhex("aa")
        latest = {
            "round": round_number,
            "signature": signature.hex(),
            "randomness": hashlib.sha256(signature).hexdigest(),
        }
        info = {
            "hash": timelock["chain_hash"], "public_key": timelock["public_key"],
            "period": timelock["period_seconds"], "genesis_time": timelock["genesis_time"],
            "schemeID": timelock["scheme_id"], "metadata": {"beaconID": timelock["beacon_id"]},
        }
        now = datetime.fromtimestamp(round_timestamp(timelock, round_number), timezone.utc)
        called = []
        validate_beacon_state(timelock, info, latest, now, lambda *_: called.append(True))
        self.assertEqual(called, [True])
        stale = datetime.fromtimestamp(round_timestamp(timelock, round_number) + 60, timezone.utc)
        with self.assertRaisesRegex(ValueError, "stale"):
            validate_beacon_state(timelock, info, latest, stale, lambda *_: None)

    def test_replica_verifier_reads_both_copies_and_detects_corruption(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir()
            ciphertext = source / "object.tle"
            ciphertext.write_bytes(b"ciphertext")
            seal = source / "timelock-seal-manifest.json"
            seal.write_text(json.dumps({
                "objects": {"object": {
                    "ciphertext_file": ciphertext.name,
                    "ciphertext_sha256": hashlib.sha256(ciphertext.read_bytes()).hexdigest(),
                }}
            }), encoding="utf-8")
            replica_manifest = root / "replicas.json"
            devices = {"copy-a": 101, "copy-b": 202}

            def resolver(path):
                return devices[path.name]

            create_replicas(
                seal, [root / "copy-a", root / "copy-b"], ["disk-a", "disk-b"],
                replica_manifest, device_resolver=resolver,
            )
            self.assertEqual(validate_replicas(replica_manifest, seal, device_resolver=resolver), 2)
            (root / "copy-b/object.tle").write_bytes(b"damaged")
            with self.assertRaisesRegex(ValueError, "checksum mismatch"):
                validate_replicas(replica_manifest, seal, device_resolver=resolver)

    def test_replica_creation_rejects_two_directories_on_same_device(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir()
            ciphertext = source / "object.tle"
            ciphertext.write_bytes(b"ciphertext")
            seal = source / "timelock-seal-manifest.json"
            seal.write_text(json.dumps({"objects": {"object": {
                "ciphertext_file": ciphertext.name,
                "ciphertext_sha256": hashlib.sha256(ciphertext.read_bytes()).hexdigest(),
            }}}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "distinct filesystem devices"):
                create_replicas(
                    seal, [root / "copy-a", root / "copy-b"], ["disk-a", "disk-b"],
                    root / "replicas.json", device_resolver=lambda _path: 101,
                )

    def test_replica_verifier_rejects_device_identity_change(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir()
            ciphertext = source / "object.tle"
            ciphertext.write_bytes(b"ciphertext")
            seal = source / "timelock-seal-manifest.json"
            seal.write_text(json.dumps({"objects": {"object": {
                "ciphertext_file": ciphertext.name,
                "ciphertext_sha256": hashlib.sha256(ciphertext.read_bytes()).hexdigest(),
            }}}), encoding="utf-8")
            replica_manifest = root / "replicas.json"
            create_devices = {"copy-a": 101, "copy-b": 202}
            create_replicas(
                seal, [root / "copy-a", root / "copy-b"], ["disk-a", "disk-b"],
                replica_manifest, device_resolver=lambda path: create_devices[path.name],
            )
            changed_devices = {"copy-a": 101, "copy-b": 303}
            with self.assertRaisesRegex(ValueError, "device differs"):
                validate_replicas(
                    replica_manifest, seal, device_resolver=lambda path: changed_devices[path.name],
                )

    def test_pending_prediction_contract_cannot_pass_registration_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "contract.json"
            path.write_text(json.dumps({"schema_version": "1.0", "status": "pending_protocol_freeze"}))
            with self.assertRaisesRegex(ValueError, "not fully registered"):
                validate_prediction_contract_file(path)

    def test_prediction_contract_derives_e2_rows_from_campaign_pool(self):
        source = Path(__file__).with_name("prediction-contract.json")
        fixed = json.loads(source.read_text(encoding="utf-8"))
        contract = {
            "schema_version": "1.0", "status": "registered",
            "T_campaigns": fixed["T_campaigns"], "pool_rows": fixed["pool_rows"],
            "campaign_pool_rows": fixed["campaign_pool_rows"],
            "row_universe": fixed["row_universe"],
            "artifact_requirements": [
                {
                    "artifact_id": "e1-main", "prediction_set": "E1", "method_id": "m1",
                    "variant_id": "main", "path": "predictions/e1.csv", "prediction_column": "p_redeem",
                    "pool": "all_T", "campaigns": fixed["T_campaigns"], "repetitions": 1,
                    "adaptation_budget_per_campaign": 0, "expected_rows": 1714,
                },
                {
                    "artifact_id": "e1a-ablation", "prediction_set": "E1a", "method_id": "m1",
                    "variant_id": "ablation", "path": "predictions/e1a.csv", "prediction_column": "p_redeem",
                    "pool": "all_T", "campaigns": fixed["T_campaigns"], "repetitions": 1,
                    "adaptation_budget_per_campaign": 0, "expected_rows": 1714,
                },
                {
                    "artifact_id": "e2-budget10", "prediction_set": "E2", "method_id": "m1",
                    "variant_id": "budget10", "path": "predictions/e2.csv", "prediction_column": "p_redeem",
                    "pool": "final_evaluation", "campaigns": [14], "repetitions": 1,
                    "adaptation_budget_per_campaign": 10, "expected_rows": 188,
                },
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "contract.json"
            path.write_text(json.dumps(contract), encoding="utf-8")
            validate_prediction_contract_file(path)
            contract["artifact_requirements"][-1]["expected_rows"] = 187
            path.write_text(json.dumps(contract), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "E2 expected rows"):
                validate_prediction_contract_file(path)

    def test_verify_cli_routes_replica_and_inventory_manifests_to_initial(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy = root / "policy.json"
            run = root / "run"
            seal = root / "seal.json"
            replica = root / "replica.json"
            inventory = root / "inventory.json"
            run.mkdir()
            for path in (policy, seal, replica, inventory):
                path.write_text("{}", encoding="utf-8")
            common = ["verify_timelock.py", "--policy", str(policy)]
            with mock.patch.object(verify_timelock, "verify_package", return_value={"status": "ok"}) as package:
                with mock.patch("sys.argv", [*common, "package", str(run), str(seal), "--repository", str(root)]):
                    with mock.patch("builtins.print"):
                        verify_timelock.main()
                self.assertEqual(len(package.call_args.args), 4)
            with mock.patch.object(verify_timelock, "verify_initial", return_value={"status": "ok"}) as initial:
                with mock.patch(
                    "sys.argv", [
                        *common, "initial", str(run), str(seal), str(replica), str(inventory),
                        "--repository", str(root),
                    ],
                ):
                    with mock.patch("builtins.print"):
                        verify_timelock.main()
                self.assertEqual(len(initial.call_args.args), 6)

    def test_public_attestation_requires_verified_time_before_unlock(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            freeze_path = root / "freeze.json"
            bundle = root / "bundle.jsonl"
            freeze_path.write_text("{}", encoding="utf-8")
            bundle.write_text("{}", encoding="utf-8")
            freeze = {"public_time_attestation": {
                "repository": verify_timelock.GITHUB_REPOSITORY,
                "signer_workflow": verify_timelock.ATTESTATION_WORKFLOW,
                "bundle": bundle.name,
            }}

            def result(timestamp):
                payload = [{"verificationResult": {"verifiedTimestamps": [{"timestamp": timestamp}]}}]
                return mock.Mock(returncode=0, stdout=json.dumps(payload))

            unlock = datetime(2030, 1, 2, tzinfo=timezone.utc)
            observed = validate_public_time_attestation(
                freeze, freeze_path, "a" * 40, unlock, root, runner=lambda *args, **kwargs: result("2030-01-01T00:00:00Z"),
            )
            self.assertLess(observed, unlock)
            with self.assertRaisesRegex(ValueError, "not witnessed before unlock"):
                validate_public_time_attestation(
                    freeze, freeze_path, "a" * 40, unlock, root,
                    runner=lambda *args, **kwargs: result("2030-01-03T00:00:00Z"),
                )

    def test_prediction_artifact_rejects_substituted_row_id(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifact = root / "predictions.csv"
            artifact.write_text(
                "row_id,campaign,method_id,variant_id,repetition,p_redeem\n"
                "expected-a,14,m1,main,1,0.1\n"
                "invented,14,m1,main,1,0.2\n",
                encoding="utf-8",
            )
            requirement = {
                "artifact_id": "e1-main", "path": artifact.name, "prediction_column": "p_redeem",
                "method_id": "m1", "variant_id": "main", "campaigns": [14],
                "repetitions": 1, "expected_rows": 2, "pool": "all_T",
            }
            checksum = hashlib.sha256(artifact.read_bytes()).hexdigest()
            universe = [("expected-a", 14, "adaptation"), ("expected-b", 14, "final_evaluation")]
            with self.assertRaisesRegex(ValueError, "incomplete or substituted"):
                validate_prediction_artifact(root, requirement, checksum, universe)

    def test_registered_row_universe_matches_current_label_free_T_links(self):
        repository = Path(__file__).resolve().parents[2]
        contract = json.loads(Path(__file__).with_name("prediction-contract.json").read_text(encoding="utf-8"))
        rows = load_prediction_universe(repository, contract)
        self.assertEqual(len(rows), 1714)


if __name__ == "__main__":
    unittest.main()
