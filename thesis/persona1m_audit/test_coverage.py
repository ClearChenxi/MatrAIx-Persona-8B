"""Tiny invented Parquet fixtures for the aggregate-only full-release scanner."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from thesis.e1a.methods.data import sha256
from thesis.persona1m_audit.coverage import summarize


FIELDS = ["shopping", "frugality", "saving", "brand", "size"]


def fixture(root: Path) -> dict:
    data = root / "data"
    data.mkdir()
    bitmap = bytearray(162)
    bitmap[0] = 1 << 1  # synthetic row's frugality field is missing
    overrides = pa.array(
        [[], [{"field_index": 0, "value": "unlisted shopper style"}], []],
        type=pa.list_(pa.struct([("field_index", pa.uint16()), ("value", pa.string())])),
    )
    table = pa.table({
        "source": pa.array(["wiki", "wiki", "synthetic"]),
        "attributes": pa.array([bytes(645)] * 3, type=pa.binary(645)),
        "null_bitmap": pa.array([bytes(162), bytes(162), bytes(bitmap)], type=pa.binary(162)),
        "attribute_overrides": overrides,
    })
    shard = data / "persona-1m-0000.parquet"
    pq.write_table(table, shard)
    schema_path = root / "persona_codes.schema.json"
    schema_path.write_text(json.dumps({"columns": [
        {"id": name, "values": ["A", "B"]} for name in FIELDS
    ]}), encoding="utf-8")
    manifest_path = root / "manifest.json"
    manifest_path.write_text(json.dumps({
        "rows": 3, "bytes": shard.stat().st_size,
        "sources": {"wiki": 2, "synthetic": 1},
        "files": [{"path": "data/persona-1m-0000.parquet", "rows": 3,
                   "bytes": shard.stat().st_size, "sha256": sha256(shard)}],
    }), encoding="utf-8")
    return {
        "dataset_repo": "invented/release", "dataset_revision": "invented_revision",
        "metadata_sha256": {"manifest.json": sha256(manifest_path),
                            "persona_codes.schema.json": sha256(schema_path)},
        "expected_persona_rows": 3, "expected_persona_fields": 5,
        "coverage_audit": {
            "status": "approved_full_release_coverage_only",
            "expected_shards": 1, "expected_bytes": shard.stat().st_size,
            "candidate_fields": FIELDS, "shopping_joint_fields": FIELDS[:4],
            "reporting": "aggregate_counts_only_no_persona_rows_or_identifiers",
        },
    }


class CoverageTests(unittest.TestCase):
    def test_counts_missing_override_and_joint_coverage_without_rows(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            result = summarize(fixture(root), root)
            self.assertEqual(result["verified_rows"], 3)
            wiki = result["coverage_by_source"]["wiki"]
            synthetic = result["coverage_by_source"]["synthetic"]
            self.assertEqual(wiki["fields"]["shopping"]["override"], 1)
            self.assertEqual(wiki["fields"]["shopping"]["known_category_counts"], {"A": 1})
            self.assertEqual(wiki["joint"]["all_shopping_present"], 2)
            self.assertEqual(wiki["combination_coverage"]["shopping & frugality"], 2)
            self.assertEqual(synthetic["fields"]["frugality"]["missing"], 1)
            self.assertEqual(synthetic["joint"]["not_all_shopping_present"], 1)
            self.assertEqual(synthetic["combination_coverage"]["shopping & frugality"], 0)
            self.assertFalse(result["person_rows_written"])
            self.assertFalse(result["household_data_opened"])
            self.assertNotIn("unlisted shopper style", json.dumps(result))

    def test_rejects_tampered_shard_and_unapproved_scope(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            plan = fixture(root)
            plan["coverage_audit"]["status"] = "pending"
            with self.assertRaisesRegex(ValueError, "not authorized"):
                summarize(plan, root)
            plan["coverage_audit"]["status"] = "approved_full_release_coverage_only"
            path = root / "data/persona-1m-0000.parquet"
            with path.open("ab") as handle:
                handle.write(b"tampered")
            with self.assertRaisesRegex(ValueError, "hash or size differs"):
                summarize(plan, root)


if __name__ == "__main__":
    unittest.main()
