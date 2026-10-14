"""Finite D-time-holdout selection and full-D fitting of B2 and B3."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from catboost import CatBoostClassifier
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from .baselines import campaign_macro_ap
from .data import Split
from .representation import Representation


@dataclass(frozen=True)
class Trained:
    model: object
    representation: Representation
    selection: dict

    def predict(self, rows: tuple[dict[str, str], ...]) -> list[float]:
        frame = self.representation.frame(rows)
        scores = self.model.predict_proba(frame)[:, 1]
        values = [float(score) for score in scores]
        if any(not (0 <= value <= 1) for value in values):
            raise ValueError("model returned an invalid probability")
        return values


def _labels(split: Split) -> np.ndarray:
    if split.labels is None:
        raise ValueError("supervised fitting requires permitted historical labels")
    return np.asarray([split.labels[row["row_id"]] for row in split.features], dtype=int)


def _time_partitions(split: Split, config: dict) -> tuple[list[int], list[int]]:
    train = set(config["historical_train_campaigns"])
    holdout = set(config["historical_holdout_campaigns"])
    if train & holdout:
        raise ValueError("D train and holdout campaigns overlap")
    train_idx, held_idx = [], []
    for index, row in enumerate(split.features):
        campaign = int(split.links[row["row_id"]]["campaign"])
        (train_idx if campaign in train else held_idx if campaign in holdout else []).append(index)
    if not train_idx or not held_idx or len(train_idx) + len(held_idx) != len(split.features):
        raise ValueError("D time partition is incomplete")
    return train_idx, held_idx


def _subset(split: Split, indices: list[int]) -> Split:
    rows = tuple(split.features[index] for index in indices)
    links = {row["row_id"]: split.links[row["row_id"]] for row in rows}
    labels = None if split.labels is None else {row["row_id"]: split.labels[row["row_id"]] for row in rows}
    return Split(rows, links, labels)


def _logistic(C: float, representation: Representation):
    preprocessing = ColumnTransformer([
        ("number", make_pipeline(SimpleImputer(strategy="median", keep_empty_features=True),
                                 StandardScaler()), list(representation.all_numeric_columns)),
        ("category", OneHotEncoder(handle_unknown="ignore", sparse_output=False),
         list(representation.categorical_columns)),
    ])
    return make_pipeline(preprocessing, LogisticRegression(C=C, max_iter=2000))


def fit_b2(development: Split, config: dict) -> Trained:
    train_idx, held_idx = _time_partitions(development, config)
    train = _subset(development, train_idx)
    held = _subset(development, held_idx)
    selection_representation = Representation.fit(train.features)
    train_x = selection_representation.frame(train.features)
    held_x = selection_representation.frame(held.features)
    train_y = _labels(train)
    if set(train_y) != {0, 1}:
        raise ValueError("early D training split lacks a label class")
    candidates = {}
    for C in config["logistic_c_grid"]:
        model = _logistic(C, selection_representation)
        model.fit(train_x, train_y)
        score = model.predict_proba(held_x)[:, 1].tolist()
        candidates[str(C)] = campaign_macro_ap(held, score)["macro_ap"]
    if any(value is None for value in candidates.values()):
        raise ValueError("D holdout AP is not identifiable")
    best = max(candidates.values())
    chosen = next(C for C in sorted(config["logistic_c_grid"])
                  if best - candidates[str(C)] <= 0.0001)
    representation = Representation.fit(development.features)
    model = _logistic(chosen, representation)
    model.fit(representation.frame(development.features), _labels(development))
    return Trained(model, representation, {
        "C": chosen, "candidate_holdout_macro_ap": candidates,
        "train_campaigns": config["historical_train_campaigns"],
        "holdout_campaigns": config["historical_holdout_campaigns"],
        "full_D_rows": len(development.features),
    })


def _catboost_frame(representation: Representation, rows: tuple[dict[str, str], ...]):
    frame = representation.frame(rows)
    frame[list(representation.all_numeric_columns)] = frame[list(representation.all_numeric_columns)].fillna(-1.0)
    return frame


def fit_b3(development: Split, config: dict) -> Trained:
    train_idx, held_idx = _time_partitions(development, config)
    train = _subset(development, train_idx)
    held = _subset(development, held_idx)
    selection_representation = Representation.fit(train.features)
    train_x = _catboost_frame(selection_representation, train.features)
    held_x = _catboost_frame(selection_representation, held.features)
    train_y = _labels(train)
    if set(train_y) != {0, 1}:
        raise ValueError("early D training split lacks a label class")
    candidates = {}
    best = None
    for depth in config["catboost_depth_grid"]:
        for rate in config["catboost_learning_rate_grid"]:
            model = CatBoostClassifier(
                iterations=config["catboost_max_iterations"], depth=depth,
                learning_rate=rate, loss_function="Logloss", eval_metric="Logloss",
                random_seed=config["seed"], thread_count=4,
                cat_features=list(selection_representation.categorical_columns),
                allow_writing_files=False, verbose=False,
            )
            model.fit(train_x, train_y, eval_set=(held_x, _labels(held)),
                      early_stopping_rounds=config["catboost_early_stop_patience"])
            score = model.predict_proba(held_x)[:, 1].tolist()
            ap = campaign_macro_ap(held, score)["macro_ap"]
            if ap is None:
                raise ValueError("D holdout AP is not identifiable")
            rounds = max(1, int(model.get_best_iteration()) + 1)
            key = f"depth={depth},learning_rate={rate}"
            candidates[key] = {"macro_ap": ap, "selected_rounds": rounds}
            rank = (-ap, depth, rate)
            if best is None or rank < best[0]:
                best = (rank, depth, rate, rounds)
    if best is None:
        raise ValueError("no B3 candidate fitted")
    _, depth, rate, rounds = best
    representation = Representation.fit(development.features)
    full_x = _catboost_frame(representation, development.features)
    final = CatBoostClassifier(
        iterations=rounds, depth=depth, learning_rate=rate,
        loss_function="Logloss", random_seed=config["seed"], thread_count=4,
        cat_features=list(representation.categorical_columns),
        allow_writing_files=False, verbose=False,
    )
    final.fit(full_x, _labels(development))
    return Trained(final, representation, {
        "depth": depth, "learning_rate": rate, "iterations": rounds,
        "candidate_holdout": candidates, "full_D_rows": len(development.features),
    })
