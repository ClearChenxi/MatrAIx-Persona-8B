"""Synthetic unit audit tests. Does not read real records."""
import io
import unittest

import audit_kdd98_units as units


class UnitTests(unittest.TestCase):
    def test_joint_title_does_not_depend_on_gender_J(self):
        self.assertEqual(units.classify("001002", "F", "9706", {0, 1, 1002})[:2], ("paired_or_plural_title", "F"))
        self.assertEqual(units.classify("1002.0", "M", "9704", {0, 1, 1002})[0], "paired_or_plural_title")

    def test_no_title_not_proof_of_one_person(self):
        for title in ("", ".", "000"):
            self.assertEqual(units.classify(title, "J", "9706", {0, 1})[0], "no_title_information")
        self.assertEqual(units.classify("1", "M", "9706", {0, 1})[0], "other_documented_title_not_proof_of_one_person")

    def test_undocumented_values_remain_unknown(self):
        self.assertEqual(units.classify("999999", "Q", "9705", {0, 1}),
                         ("undocumented_or_invalid_title", "undocumented", "other_or_invalid"))

    def fixture(self, tail):
        payload = b"TCODE,TARGET_B,GENDER,ADATE_2,TARGET_D\n" + b"1002," + tail + b",F,9706," + tail + b"\n0," + tail + b",J,9704," + tail + b"\n"
        return units.audit(io.BytesIO(payload), 5, 2, len(payload), {0, 1002}, True)

    def test_outcome_bytes_never_decoded(self):
        plain, poisoned = self.fixture(b"1"), self.fixture(b'"\xff,\xfe"')
        plain.pop("decompressed_bytes")
        poisoned.pop("decompressed_bytes")
        self.assertEqual(plain, poisoned)
        self.assertEqual(plain["either_signal"], 2)
        self.assertEqual(plain["plural_title_gender_not_J"], 1)
        self.assertEqual(plain["both_signals"], 0)
        self.assertEqual(plain["rows_excluded"], 0)

    def test_changed_header_stops(self):
        with self.assertRaises(RuntimeError):
            units.audit(io.BytesIO(b"A,B\n\xff,\xfe\n"), 2, 1, 12, {0, 1}, False)

    def test_complete_paired_plural_code_set(self):
        self.assertTrue({10010, 17002, 18002, 28028, 131, 135} <= units.PAIRED_OR_PLURAL)
        self.assertNotIn(1, units.PAIRED_OR_PLURAL)


if __name__ == "__main__":
    units.preflight()
    unittest.main()
