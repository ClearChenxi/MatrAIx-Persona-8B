"""Synthetic checks; never open real personas, household labels or a model."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from thesis.e1a.methods.data import sha256
from thesis.persona1m_audit.audit import audit


class MatchabilityAuditTests(unittest.TestCase):
    def test_proxy_is_not_promoted_to_observed_equivalence(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary)
            files = {
                "README.md": "license_name: matraix-research-only\nNot a representative sample\n",
                "persona_codes.schema.json": json.dumps({"columns": [
                    {"id": "shopping_style", "label": "Shopping style", "values": ["bargain", "other"]}
                ]}),
                "audit.json": json.dumps({"source_stats": {"synthetic": {"rows": 2}}}),
            }
            for name, content in files.items():
                (source / name).write_text(content, encoding="utf-8")
            plan = {
                "status": "approved_metadata_only_matchability_audit",
                "dataset_repo": "invented/release", "dataset_revision": "invented_commit",
                "metadata_sha256": {name: sha256(source / name) for name in files},
                "expected_persona_rows": 2, "expected_persona_fields": 1,
                "dunnhumby_splits": ["D", "R"],
                "bridges": [{"persona_field": "shopping_style", "dunnhumby_fields": ["coupon_share"],
                             "relation": "unvalidated_proxy", "reason": "These are not the same measure."}],
            }
            rows = {"D": [{"row_id": "d1", "coupon_share": "0"}],
                    "R": [{"row_id": "r1", "coupon_share": ""}]}
            with patch("thesis.persona1m_audit.audit._features", side_effect=lambda _, split: rows[split]):
                result = audit(plan, source, {"input_run": "invented"})
            self.assertEqual(result["bridge_relation_counts"], {"unvalidated_proxy": 1})
            self.assertEqual(result["bridges"][0]["dunnhumby_nonmissing_rows"],
                             {"D": {"coupon_share": 1}, "R": {"coupon_share": 0}})
            self.assertFalse(result["identity_matching_authorized"])
            self.assertFalse(result["T_labels_opened"])
            self.assertFalse(result["persona_records_opened"])

    def test_rejects_T_scope_and_metadata_changes(self):
        with self.assertRaisesRegex(ValueError, "limited to D/R"):
            audit({"status": "approved_metadata_only_matchability_audit",
                   "dunnhumby_splits": ["D", "T"]}, Path("unused"), {})
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(ValueError, "metadata missing or differs"):
                from thesis.persona1m_audit.audit import _source
                _source(Path(temporary), "audit.json", "wrong-hash")


if __name__ == "__main__":
    unittest.main()
