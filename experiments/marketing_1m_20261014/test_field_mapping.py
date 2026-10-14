"""Synthetic category tests; no real record values are read."""
import tempfile
from pathlib import Path
import unittest

import draft_field_mapping as draft


class DraftMappingTests(unittest.TestCase):
    def test_punctuation_alias_keeps_age_bounds(self):
        result = draft.categorize("age_bracket", "25–34", ["18-24", "25-34"], set())
        self.assertEqual(result["target"], "25-34")
        result = draft.categorize("age_bracket", "25-34 years old", ["25-34"], set())
        self.assertEqual(result["target"], "25-34")

    def test_coarse_age_is_not_forced_into_one_current_bin(self):
        result = draft.categorize("age_bracket", "65+", ["65-74", "75-84", "85+"], set())
        self.assertIsNone(result["target"])
        self.assertEqual(result["current_members"], ["65-74", "75-84", "85+"])
        result = draft.categorize("age_bracket", "Under 18 years old", ["Under 5", "5-12", "13-17"], set())
        self.assertIsNone(result["target"])
        self.assertEqual(len(result["current_members"]), 3)

    def test_unknown_and_none_are_distinct(self):
        self.assertEqual(draft.categorize("x", "Unknown", ["None"], set())["kind"], "explicit_unavailable")
        self.assertEqual(draft.categorize("x", "None", ["None"], set())["kind"], "already_current")

    def test_semantic_merge_not_silently_accepted(self):
        result = draft.categorize("demo_marital_status", "divorced / separated",
                                  ["Divorced", "Separated"], {"divorced / separated"})
        self.assertIsNone(result["target"])

    def test_unrecognized_personal_free_text_suppressed(self):
        value = "Private Person private@example.test"
        result = draft.categorize("x", value, ["Known"], set())
        self.assertEqual(result["kind"], "unlisted_value_suppressed")
        self.assertNotIn(value, str(result))

    def test_source_code_is_parsed_not_executed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "source.py"
            path.write_text('raise RuntimeError("must never execute")\nCROSSWALK = {"age_bracket": {"map": {"old": "new"}}}')
            self.assertEqual(draft.literal_crosswalk_maps(path), {"age_bracket": {"old": "new"}})


if __name__ == "__main__":
    unittest.main()
