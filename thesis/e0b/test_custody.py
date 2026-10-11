from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from verify_custody import (
    trusted_custodian,
    validate_initial_receipt,
    validate_release_receipt,
    verify_inaccessible,
    verify_signature,
)


class CustodyPolicyTests(unittest.TestCase):
    def setUp(self):
        self.fingerprint = "A" * 40
        self.policy = {
            "trusted_custodian": {
                "status": "registered",
                "name": "Independent Custodian",
                "role": "label custodian",
                "gpg_fingerprint": self.fingerprint,
                "public_key_sha256": "b" * 64,
                "external_attestation_type": "advisor_signed_statement",
                "external_attestation_sha256": "c" * 64,
            },
            "logical_label_objects": {
                "T_adaptation_labels": {},
                "T_final_evaluation_labels": {},
            },
            "custody_only_objects": {"redemption_source": {}},
            "release_stages": {
                "adaptation": {
                    "authorized_objects": ["T_adaptation_labels"],
                    "condition_id": "E1_E1a_predictions_frozen",
                    "required_prediction_sets": ["E1", "E1a"],
                    "allowed_delivery_modes": ["custodian_delivers_adaptation_labels"],
                },
                "final_evaluation": {
                    "authorized_objects": ["T_final_evaluation_labels"],
                    "condition_id": "E1_E1a_E2_predictions_frozen",
                    "required_prediction_sets": ["E1", "E1a", "E2"],
                    "allowed_delivery_modes": ["custodian_runs_frozen_evaluator"],
                },
            },
        }

    def test_pending_or_incomplete_trust_anchor_is_rejected(self):
        self.policy["trusted_custodian"]["status"] = "pending_external_registration"
        with self.assertRaisesRegex(ValueError, "not preregistered"):
            trusted_custodian(self.policy)
        self.policy["trusted_custodian"]["status"] = "registered"
        self.policy["trusted_custodian"]["external_attestation_sha256"] = None
        with self.assertRaisesRegex(ValueError, "attestation commitment"):
            trusted_custodian(self.policy)

    @patch("verify_custody.subprocess.run")
    def test_signature_must_match_policy_not_receipt_claim(self, run):
        other_fingerprint = "D" * 40
        run.return_value = Mock(returncode=0, stdout=f"[GNUPG:] VALIDSIG {other_fingerprint} 2026 0 4 0 1 10 00\n")
        with self.assertRaisesRegex(ValueError, "preregistered custodian"):
            verify_signature(Path("receipt.json"), Path("receipt.asc"), self.fingerprint)
        run.return_value = Mock(returncode=0, stdout=f"[GNUPG:] VALIDSIG {self.fingerprint} 2026 0 4 0 1 10 00\n")
        verify_signature(Path("receipt.json"), Path("receipt.asc"), self.fingerprint)
        signing_subkey = "E" * 40
        run.return_value = Mock(
            returncode=0,
            stdout=f"[GNUPG:] VALIDSIG {signing_subkey} 2026 0 4 0 1 10 00 {self.fingerprint}\n",
        )
        verify_signature(Path("receipt.json"), Path("receipt.asc"), self.fingerprint)

    def test_initial_receipt_requires_complete_inventory_and_no_executor_key(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / "handoff.json"
            manifest.write_text("{}", encoding="utf-8")
            from audit import digest
            receipt = {
                "schema_version": "2.0",
                "stage": "initial_custody",
                "status": "accepted_by_preregistered_custodian",
                "handoff_manifest_sha256": digest(manifest),
                "executor_has_decryption_key": False,
                "accepted_objects": ["T_adaptation_labels", "T_final_evaluation_labels", "redemption_source"],
            }
            validate_initial_receipt(self.policy, receipt, manifest)
            receipt["executor_has_decryption_key"] = True
            with self.assertRaisesRegex(ValueError, "must not hold"):
                validate_initial_receipt(self.policy, receipt, manifest)

    def test_release_stages_cannot_authorize_each_others_labels(self):
        with tempfile.TemporaryDirectory() as directory:
            freeze = Path(directory) / "freeze.json"
            handoff = Path(directory) / "handoff.json"
            freeze.write_text(json.dumps({
                "schema_version": "1.0",
                "status": "E1_E1a_predictions_frozen",
                "code_committed_and_pushed": True,
                "prediction_sets": ["E1", "E1a"],
                "prediction_checksums": {"predictions.csv": "e" * 64},
            }), encoding="utf-8")
            handoff.write_text("{}", encoding="utf-8")
            from audit import digest
            receipt = {
                "schema_version": "2.0",
                "stage": "adaptation",
                "status": "release_authorized_by_preregistered_custodian",
                "authorized_objects": ["T_adaptation_labels"],
                "condition_id": "E1_E1a_predictions_frozen",
                "freeze_manifest_sha256": digest(freeze),
                "custody_handoff_manifest_sha256": digest(handoff),
                "delivery_mode": "custodian_delivers_adaptation_labels",
            }
            validate_release_receipt(self.policy, "adaptation", receipt, freeze, handoff)
            receipt["authorized_objects"] = ["T_final_evaluation_labels"]
            with self.assertRaisesRegex(ValueError, "wrong objects"):
                validate_release_receipt(self.policy, "adaptation", receipt, freeze, handoff)

    def test_readable_plaintext_is_rejected(self):
        expected = {"policy": Path(__file__).with_name("custody-policy.json")}
        with self.assertRaisesRegex(ValueError, "remains readable"):
            verify_inaccessible(expected)


if __name__ == "__main__":
    unittest.main()
