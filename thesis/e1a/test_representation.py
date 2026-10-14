"""Synthetic checks for D-only vocabulary and identical R/T encoding."""

from __future__ import annotations

import unittest

from thesis.e1a.methods.representation import JSON_COLUMNS, Representation


class RepresentationTests(unittest.TestCase):
    def row(self, *, commodity="A", extra="{}"):
        row = {"row_id": "synthetic", "basket_count": "3", "top_commodity_1": commodity}
        row.update({name: "{}" for name in JSON_COLUMNS})
        row["department_spend_shares_json"] = extra
        return row

    def test_vocabulary_uses_only_development_rows(self):
        frozen = Representation.fit((self.row(extra='{"FOOD": 0.6}'),))
        future = self.row(commodity="UNSEEN", extra='{"FUTURE": 1.0}')
        frame = frozen.frame((future,))
        self.assertIn("department_spend_shares_json::FOOD", frame.columns)
        self.assertNotIn("department_spend_shares_json::FUTURE", frame.columns)
        self.assertEqual(frame.iloc[0]["top_commodity_1"], "UNSEEN")

    def test_no_identifier_as_feature(self):
        row = self.row()
        row["household_key"] = "secret"
        with self.assertRaises(ValueError):
            Representation.fit((row,))


if __name__ == "__main__":
    unittest.main()
