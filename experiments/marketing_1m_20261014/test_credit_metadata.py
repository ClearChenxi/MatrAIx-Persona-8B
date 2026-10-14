"""Synthetic DTA fixtures only: prove metadata reads do not consume observations."""
import datetime
import io
import unittest

import pandas as pd

import inspect_credit_metadata as inspection


class MetadataBoundaryTests(unittest.TestCase):
    def fixture(self, version):
        stream = io.BytesIO()
        pd.DataFrame({"age": [20, 30], "outcome": [0, 1]}).to_stata(
            stream, write_index=False, version=version,
            variable_labels={"age": "synthetic age", "outcome": "synthetic response"},
            time_stamp=datetime.datetime(2020, 1, 1))
        return stream.getvalue()

    def test_metadata_does_not_depend_on_record_bytes(self):
        for version in (114, 117, 118):
            with self.subTest(version=version):
                payload = self.fixture(version)
                before = inspection.stata_metadata(payload)
                start = before["data_start_byte"]
                corrupted = payload[:start] + b"\xff" * (len(payload) - start)
                self.assertEqual(before, inspection.stata_metadata(corrupted))
                self.assertEqual(before["observations_decoded"], 0)
                self.assertLessEqual(before["max_metadata_read_end"], start)

    def test_record_read_api_is_blocked(self):
        with inspection.NoRowsReader(io.BytesIO(self.fixture(117))) as reader:
            with self.assertRaises(RuntimeError):
                reader.read()

    def test_unbounded_read_is_blocked(self):
        with self.assertRaises(RuntimeError):
            inspection.TrackedBuffer(b"synthetic").read()


if __name__ == "__main__":
    unittest.main()
