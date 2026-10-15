"""Invented counts verify exact weak weighting without real family/person data."""

from __future__ import annotations

import unittest
from decimal import Decimal
from fractions import Fraction

from thesis.persona1m_audit.aggregate import (
    _midrank, _to_six_decimals, _weighted_distribution, prepare,
)
from thesis.persona1m_audit.verify_aggregate import _wire_record


class AggregateTests(unittest.TestCase):
    def test_D_midrank_ties_and_out_of_range(self):
        D = tuple(Decimal(text) for text in ("0", "0", "0.5", "1"))
        self.assertEqual(_midrank(D, Decimal("0")), Fraction(1, 4))
        self.assertEqual(_midrank(D, Decimal("0.5")), Fraction(5, 8))
        self.assertEqual(_midrank(D, Decimal("-1")), 0)
        self.assertEqual(_midrank(D, Decimal("2")), 1)

    def test_weak_tilt_and_source_mixture(self):
        pairs = (("Bargain hunter", "Frugal saver"), ("Researcher", "Balanced"))
        one_source = {"amazon": {pairs[0]: 1, pairs[1]: 1}}
        low = _weighted_distribution(pairs, one_source, ("amazon",), Fraction(0), Fraction(0))
        high = _weighted_distribution(pairs, one_source, ("amazon",), Fraction(1), Fraction(1))
        middle = _weighted_distribution(pairs, one_source, ("amazon",), Fraction(1, 2), Fraction(1, 2))
        self.assertEqual(low, (Fraction(4, 13), Fraction(9, 13)))
        self.assertEqual(middle, (Fraction(1, 2), Fraction(1, 2)))
        self.assertEqual(high, (Fraction(16, 25), Fraction(9, 25)))
        two_sources = {"amazon": {pairs[0]: 1, pairs[1]: 0},
                       "wiki": {pairs[0]: 0, pairs[1]: 1}}
        self.assertEqual(_weighted_distribution(pairs, two_sources, ("amazon", "wiki"),
                                                Fraction(1), Fraction(1)),
                         (Fraction(1, 2), Fraction(1, 2)))

    def test_rounding_preserves_unit_sum(self):
        result = _to_six_decimals((Fraction(1, 3), Fraction(2, 3)), 6)
        self.assertEqual(result, ("0.333333", "0.666667"))
        self.assertEqual(sum(Decimal(value) for value in result), 1)

    def test_verifier_compares_serialized_arrays_not_python_tuples(self):
        record = {"row_id": "invented", "human_aggregate": ("0.5", "0.5"),
                  "synthetic_aggregate": ("0.2", "0.8")}
        self.assertEqual(_wire_record(record), {"row_id": "invented",
                                               "human_aggregate": ["0.5", "0.5"],
                                               "synthetic_aggregate": ["0.2", "0.8"]})

    def test_requires_confirmation_before_reading_inputs(self):
        with self.assertRaisesRegex(ValueError, "not confirmed"):
            prepare({"status": "pending_exact_formula_confirmation", "splits": ["D", "R"],
                     "individual_persona_rows_permitted": False,
                     "response_labels_permitted": False, "T_permitted": False,
                     "model_calls_permitted": False, "human_sources": [],
                     "D_proxy_fields": [], "D_rank_policy": "", "weight_policy": "",
                     "source_mixture": "", "output_decimal_places": 6}, {}, {})


if __name__ == "__main__":
    unittest.main()
