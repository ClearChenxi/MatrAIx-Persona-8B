"""Small synthetic end-to-end checks of the two frozen model families."""

from __future__ import annotations

import unittest

from thesis.e1a.methods.data import Split
from thesis.e1a.methods.representation import JSON_COLUMNS
from thesis.e1a.methods.supervised import fit_b2, fit_b3


class SupervisedTests(unittest.TestCase):
    def test_d_time_holdout_and_full_d_refit(self):
        rows, links, labels = [], {}, {}
        for campaign in (27, 28, 1, 2):
            for index in range(4):
                row_id = f"synthetic-{campaign}-{index}"
                row = {
                    "row_id": row_id,
                    "basket_count": str(index + 1),
                    "campaign_type": "TypeC" if campaign == 28 else "TypeB",
                    "top_commodity_1": "FOOD" if index % 2 else "DAIRY",
                }
                row.update({name: "{}" for name in JSON_COLUMNS})
                rows.append(row)
                links[row_id] = {"campaign": str(campaign), "household_key": row_id}
                labels[row_id] = index % 2
        split = Split(tuple(rows), links, labels)
        config = {
            "historical_train_campaigns": [27, 28],
            "historical_holdout_campaigns": [1, 2],
            "logistic_c_grid": [0.01, 0.1],
            "catboost_depth_grid": [3],
            "catboost_learning_rate_grid": [0.1],
            "catboost_max_iterations": 20,
            "catboost_early_stop_patience": 5,
            "seed": 20261014,
        }
        for model in (fit_b2(split, config), fit_b3(split, config)):
            scores = model.predict(split.features)
            self.assertEqual(len(scores), len(rows))
            self.assertTrue(all(0 <= score <= 1 for score in scores))
            self.assertEqual(model.selection["full_D_rows"], len(rows))


if __name__ == "__main__":
    unittest.main()
