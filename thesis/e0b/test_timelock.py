from __future__ import annotations

import base64
import tempfile
import unittest
from pathlib import Path

from register_timelock_schedule import register
from timelock_common import (
    first_round_at_or_after, parse_ciphertext_identity, round_timestamp,
    utc_text, validate_policy, verify_inaccessible,
)


def pending_policy():
    return {
        "schema_version": "3.0",
        "status": "awaiting_timelock_schedule_registration",
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
    }


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
        registered = register(policy, adaptation, final, state)
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
            register(policy, utc_text(latest_time + 60), utc_text(latest_time + 700000), state)
        with self.assertRaisesRegex(ValueError, "seven days after adaptation"):
            register(policy, utc_text(latest_time + 700000), utc_text(latest_time + 700060), state)

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

    def test_readable_plaintext_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "plaintext"
            path.write_text("sensitive", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "remains readable"):
                verify_inaccessible({"label": path})


if __name__ == "__main__":
    unittest.main()
