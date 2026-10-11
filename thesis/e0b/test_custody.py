from __future__ import annotations

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from verify_custody import validate_receipt, verify_inaccessible


class CustodyPolicyTests(unittest.TestCase):
    def setUp(self):
        self.release = "all frozen E1/E1a predictions and any approved E2 predictions have been committed, pushed, and hash-registered"
        self.policy = {
            "receipt_required_fields": [
                "schema_version", "status", "custodian_name", "custodian_role", "accepted_at",
                "custodian_signing_key_fingerprint", "executor_has_decryption_key", "release_condition", "objects",
            ],
            "custodian_requirements": {"release_condition": self.release},
        }
        self.expected = {"sealed_T_labels": {"sha256": "a" * 64, "executor_path": "/absent/test/path"}}
        self.receipt = {
            "schema_version": "1.0",
            "status": "accepted_by_independent_custodian",
            "custodian_name": "Independent Custodian",
            "custodian_role": "label custodian",
            "accepted_at": "2026-10-11T00:00:00Z",
            "custodian_signing_key_fingerprint": "F" * 40,
            "executor_has_decryption_key": False,
            "release_condition": self.release,
            "objects": {"sealed_T_labels": {"sha256": "a" * 64, "accepted": True}},
        }

    def test_valid_receipt_and_inaccessible_plaintext(self):
        validate_receipt(self.policy, self.receipt, self.expected)
        verify_inaccessible(self.expected)

    def test_receipt_rejects_executor_key_or_commitment_change(self):
        self.receipt["executor_has_decryption_key"] = True
        with self.assertRaisesRegex(ValueError, "must not hold"):
            validate_receipt(self.policy, self.receipt, self.expected)
        self.receipt["executor_has_decryption_key"] = False
        self.receipt["objects"]["sealed_T_labels"]["sha256"] = "b" * 64
        with self.assertRaisesRegex(ValueError, "commitment mismatch"):
            validate_receipt(self.policy, self.receipt, self.expected)

    def test_readable_plaintext_is_rejected(self):
        expected = {"policy": {"sha256": "a" * 64, "executor_path": str(HERE / "custody-policy.json")}}
        with self.assertRaisesRegex(ValueError, "remains readable"):
            verify_inaccessible(expected)


if __name__ == "__main__":
    unittest.main()
