"""Synthetic-only boundary tests; reviewed and pushed before any execution."""
import collections
import io
import unittest

import audit_kdd98 as audit


class KDDTests(unittest.TestCase):
    def fixture(self, poison=False, ids=("1", "2")):
        # Outcomes deliberately in the middle, followed by whitelisted fields.
        names = audit.WHITELIST[:4] + ["TARGET_B", "IGNORED", "TARGET_D"] + audit.WHITELIST[4:]
        rows = [(",".join(names) + "\n").encode()]
        for key in ids:
            values = {"CONTROLN": key, "AGE": "65", "AGEFLAG": "E", "HOMEOWNR": "U",
                      "GENDER": "J", "ADATE_2": "9704", "MAXADATE": "9702", "ADATE_3": "9606"}
            record = []
            for name in names:
                if name in ("TARGET_B", "TARGET_D", "IGNORED"):
                    record.append(b'"\xff,\xfe"' if poison else b"0")
                else:
                    record.append(values.get(name, "").encode())
            rows.append(b",".join(record) + b"\n")
        data = b"".join(rows)
        return io.BytesIO(data), len(names), len(ids), len(data), True

    def test_middle_outcomes_and_other_exclusions_never_decode(self):
        plain, keys = audit.audit_stream(*self.fixture())
        poisoned, other_keys = audit.audit_stream(*self.fixture(True))
        # Different raw lengths are transport metadata, not outcome statistics.
        plain.pop("decompressed_bytes")
        poisoned.pop("decompressed_bytes")
        self.assertEqual(plain, poisoned)
        self.assertEqual(keys, other_keys)
        self.assertTrue(set(plain["fields"]).isdisjoint(audit.TARGETS))

    def test_selected_invalid_text_is_rejected(self):
        with self.assertRaises(UnicodeError):
            audit.project_bytes(b"\xff,1", {0: "AGE"}, 2)

    def test_quoted_delimiters_escaped_quotes_and_trailing_blank(self):
        result = audit.project_bytes(b'"a,b","c""d",\xff,\r\n', {0: "a", 1: "b", 3: "c"}, 4)
        self.assertEqual(result, {"a": "a,b", "b": 'c"d', "c": ""})

    def test_invalid_framing_fails_closed(self):
        for value in (b'"x,y', b'"x"z,y', b'x"z,y', b'"x\ny",z', b'x,y,z'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                audit.project_bytes(value, {0: "a"}, 2)

    def test_rows_do_not_reveal_control_numbers(self):
        report, keys = audit.audit_stream(*self.fixture(ids=("77", "77")))
        self.assertEqual(report["duplicate_controln_beyond_first"], 1)
        self.assertFalse(report["checks"]["unique_valid_record_keys"])
        self.assertNotIn("CONTROLN", report["fields"])
        self.assertEqual(audit.linkage(keys, {77, 88})["overlap_controln"], 1)

    def test_invalid_identifiers_not_silently_dropped(self):
        report, keys = audit.audit_stream(*self.fixture(ids=("", "bad")))
        self.assertEqual(report["rows"], 2)
        self.assertEqual(report["missing_or_invalid_controln"], 2)
        self.assertEqual(keys, set())

    def test_same_month_is_not_pre_intervention_evidence(self):
        self.assertEqual(audit.date_relation("9704", "9704"), "same_month_day_order_unknown")
        self.assertEqual(audit.date_relation("9704", "9705"), "after")
        self.assertEqual(audit.date_relation("9704", "9703"), "before")
        self.assertEqual(audit.date_relation("9704", "9713"), "missing_or_invalid")

    def test_missing_and_unknown_semantics(self):
        count = collections.Counter({"": 2, "0": 3, "65": 4})
        self.assertEqual(audit.summarize("AGE", count)["positive_count"], 4)
        self.assertEqual(audit.summarize("AGE", count)["zero_meaning"], "documented_missing")
        self.assertFalse(audit.summarize("CARDS", collections.Counter({"": 2}))["blank_is_negative"])
        self.assertEqual(audit.summarize("HOMEOWNR", collections.Counter({"U": 2}))["explicit_unknown_count"], 2)
        self.assertEqual(audit.summarize("MAILCODE", collections.Counter({"": 2}))["blank_meaning"], "address_OK_per_dictionary_not_missing")

    def test_changed_header_stops_before_payload(self):
        with self.assertRaises(RuntimeError):
            audit.audit_stream(io.BytesIO(b"A,B\n\xff,\xfe\n"), 2, 1, 10, True)

    def test_validation_rejects_outcome_columns(self):
        args = list(self.fixture())
        args[-1] = False
        with self.assertRaises(RuntimeError):
            audit.audit_stream(*args)

    def test_transport_limits(self):
        args = list(self.fixture())
        args[3] -= 1
        with self.assertRaises(RuntimeError):
            audit.audit_stream(*args)


if __name__ == "__main__":
    audit.preflight()
    unittest.main()
