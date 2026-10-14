"""Synthetic-only tests for skipping all three Hillstrom outcomes."""
import io
import unittest

import audit_hillstrom as hillstrom


class HillstromIsolationTests(unittest.TestCase):
    def fixture(self, tail):
        header = ",".join(hillstrom.HEADER).encode() + b"\n"
        row = b'2,"1) $0 - $100",50.5,1,0,Urban,1,Web,Mens E-Mail,' + tail + b"\n"
        return io.BytesIO(header + row)

    def test_all_outcome_values_can_be_replaced_without_affecting_audit(self):
        ordinary = hillstrom.audit(self.fixture(b"0,1,42.0"))
        poisoned = hillstrom.audit(self.fixture(b'\xff,"\xfe,arbitrary,\xfd'))
        self.assertEqual(ordinary, poisoned)
        self.assertEqual(ordinary["excluded_unread_outcomes"], ["visit", "conversion", "spend"])
        self.assertTrue(set(ordinary["fields"]).isdisjoint(hillstrom.OUTCOMES))

    def test_header_change_stops_before_records(self):
        stream = io.BytesIO(b"recency,visit\n\xff,\xfe\n")
        with self.assertRaises(RuntimeError):
            hillstrom.audit(stream)

    def test_group_counts_do_not_require_outcomes(self):
        result = hillstrom.audit(self.fixture(b"\xff,\xff,\xff"))
        self.assertEqual(result["assignment_counts"], {"Mens E-Mail": 1})
        self.assertFalse(result["outcome_statistics_computed"])


if __name__ == "__main__":
    unittest.main()
