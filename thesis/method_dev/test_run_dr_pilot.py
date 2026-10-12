from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import run_dr_pilot
import run_d_prompt_compare


class PilotTests(unittest.TestCase):
    def test_t_paths_are_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "T path rejected"):
            run_dr_pilot.read_csv(Path("links_T.csv"))

    def test_ap_is_none_without_positive_labels(self):
        self.assertIsNone(run_dr_pilot.ap([0, 0], [0.2, 0.8]))

    def test_ap_is_none_without_negative_labels(self):
        self.assertIsNone(run_dr_pilot.ap([1], [0.8]))

    def test_ap_is_exact_for_ranked_positive(self):
        self.assertEqual(run_dr_pilot.ap([1, 0], [0.8, 0.2]), 1.0)

    def test_r_selection_is_fixed_and_stratified(self):
        rows = run_dr_pilot.load_split("R")
        first = run_dr_pilot.select_r(rows)
        second = run_dr_pilot.select_r(list(reversed(rows)))
        self.assertEqual([row["row_id"] for row in first], [row["row_id"] for row in second])
        self.assertEqual(len(first), run_dr_pilot.PILOT_R)

    def test_events_are_appended_immediately(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.jsonl"
            run_dr_pilot.append_event(path, {"attempt": 1})
            run_dr_pilot.append_event(path, {"attempt": 2})
            self.assertEqual(path.read_text().count("\n"), 2)

    def test_full_d_contract_and_request_are_fixed(self):
        rows = run_dr_pilot.load_split("D")
        self.assertEqual(len(rows), 729)
        request = run_d_prompt_compare._request(rows[0], "v1")
        self.assertEqual(request["options"]["temperature"], 0)
        self.assertEqual(request["options"]["num_ctx"], 8192)
        self.assertEqual(request["options"]["num_predict"], 64)
        self.assertFalse(request["think"])

    def test_prompt_hash_is_stable(self):
        first = run_d_prompt_compare.pilot.digest(run_d_prompt_compare.pilot.PROMPTS["v1"])
        second = run_d_prompt_compare.pilot.digest(run_d_prompt_compare.pilot.PROMPTS["v1"])
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
