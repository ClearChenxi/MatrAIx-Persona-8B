"""Synthetic-only tests for packed nulls, overrides and evidence aggregation."""
import json
import unittest

import pyarrow as pa

import audit_persona_availability as audit


class AvailabilityTests(unittest.TestCase):
    def batch(self):
        overrides = pa.list_(pa.struct([("field_index", pa.uint16()), ("value", pa.string())]))
        grounding = pa.list_(pa.struct([("field_index", pa.uint16()), ("evidence", pa.string()),
                                       ("confidence", pa.float32()), ("assignment_type", pa.string())]))
        schema = pa.schema([("source", pa.string()), ("attributes", pa.binary(645)),
                            ("null_bitmap", pa.binary(162)), ("attribute_overrides", overrides),
                            ("grounding", grounding)])
        missing = bytearray(162)
        missing[0] = 1
        missing[1] = 1
        invalid = bytearray(645)
        invalid[0] = 0xF0
        rows = [
            {"source": "synthetic", "attributes": bytes(645), "null_bitmap": None,
             "attribute_overrides": None, "grounding": [
                 {"field_index": 0, "evidence": "secret evidence canary", "confidence": 1.0, "assignment_type": "direct"},
                 {"field_index": 0, "evidence": " ", "confidence": None, "assignment_type": None},
                 {"field_index": 999, "evidence": "unapproved canary", "confidence": 0.5, "assignment_type": "direct"}]},
            {"source": "gss", "attributes": bytes(645), "null_bitmap": bytes(missing),
             "attribute_overrides": None, "grounding": [
                 {"field_index": 0, "evidence": "orphan evidence", "confidence": 2.0, "assignment_type": "structured_claim"}]},
            {"source": "synthetic", "attributes": bytes(invalid), "null_bitmap": None,
             "attribute_overrides": [{"field_index": 1, "value": "legacy canary"},
                                     {"field_index": 0, "value": "previous canary"},
                                     {"field_index": 0, "value": ""}], "grounding": []},
        ]
        return pa.RecordBatch.from_pylist(rows, schema=schema)

    def result(self):
        specs = [("even", 0, ("A", "B")), ("odd", 1, ("A", "B")), ("next_byte", 8, ("A", "B"))]
        totals = audit.initialize(specs)
        audit.accumulate(self.batch(), specs, totals)
        return audit.finalize(totals)

    def test_null_bitmap_even_odd_and_override_order(self):
        result = self.result()
        self.assertEqual(result["gss"]["even"]["nonmissing"], 0)
        self.assertEqual(result["gss"]["next_byte"]["nonmissing"], 0)
        self.assertEqual(result["gss"]["odd"]["nonmissing"], 1)
        self.assertEqual(result["synthetic"]["odd"]["invalid_base_code"], 1)
        self.assertEqual(result["synthetic"]["odd"]["nonempty"], 2)
        self.assertEqual(result["synthetic"]["even"]["nonmissing"], 2)
        self.assertEqual(result["synthetic"]["even"]["nonempty"], 1)

    def test_evidence_rows_are_deduplicated_and_not_treated_as_truth(self):
        result = self.result()
        self.assertEqual(result["synthetic"]["even"]["grounding_records"], 2)
        self.assertEqual(result["synthetic"]["even"]["grounding_rows"], 1)
        self.assertEqual(result["synthetic"]["even"]["evidence_rows"], 1)
        self.assertEqual(result["gss"]["even"]["evidence_without_nonempty_value_rows"], 1)
        self.assertEqual(result["gss"]["even"]["confidence_outside_unit_interval_records"], 1)

    def test_summary_contains_no_record_values_or_evidence_text(self):
        text = json.dumps(self.result())
        self.assertNotIn("canary", text)
        self.assertNotIn("orphan evidence", text)
        self.assertNotIn("999", text)

    def test_source_combination_preserves_denominators(self):
        specs = [("even", 0, ("A", "B")), ("odd", 1, ("A", "B")), ("next_byte", 8, ("A", "B"))]
        totals = audit.initialize(specs)
        audit.accumulate(self.batch(), specs, totals)
        merged = audit.combine_sources(totals, audit.SOURCES, specs)
        result = audit.finalize({"all": merged})["all"]
        self.assertEqual(result["even"]["rows"], 3)
        self.assertEqual(result["even"]["nonmissing"], 2)
        self.assertEqual(result["even"]["grounding_rows"], 2)

    def test_unknown_override_not_counted_as_informative(self):
        batch = self.batch()
        rows = batch.to_pylist()
        rows[2]["attribute_overrides"][-1]["value"] = "Unknown"
        specs = [("even", 0, ("A", "B")), ("odd", 1, ("A", "B")), ("next_byte", 8, ("A", "B"))]
        totals = audit.initialize(specs)
        audit.accumulate(pa.RecordBatch.from_pylist(rows, schema=batch.schema), specs, totals)
        result = audit.finalize(totals)["synthetic"]["even"]
        self.assertEqual(result["nonmissing"], 2)
        self.assertEqual(result["explicit_unavailable_marker_rows"], 1)
        self.assertEqual(result["informative_nonempty_rows"], 1)
        self.assertEqual(result["current_codebook_compatible_rows"], 1)
        self.assertFalse(audit.unavailable("None"))


if __name__ == "__main__":
    unittest.main()
