"""D-only vocabulary and fixed X/S encoding shared by B2 and B3."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass

import pandas as pd


JSON_COLUMNS = (
    "department_spend_shares_json", "brand_type_spend_shares_json",
    "campaign_department_weights_json", "campaign_brand_type_weights_json",
)
FORBIDDEN = {"row_id", "household_key", "campaign", "split", "e2_role", "y1_redeemed"}


@dataclass(frozen=True)
class Representation:
    source_columns: tuple[str, ...]
    numeric_columns: tuple[str, ...]
    categorical_columns: tuple[str, ...]
    json_keys: dict[str, tuple[str, ...]]

    @classmethod
    def fit(cls, development_rows: tuple[dict[str, str], ...]) -> "Representation":
        if not development_rows:
            raise ValueError("D features are empty")
        fields = tuple(name for name in development_rows[0] if name != "row_id")
        if FORBIDDEN.intersection(fields):
            raise ValueError("identifier or label entered model features")
        if any(set(row) != set(development_rows[0]) for row in development_rows):
            raise ValueError("D feature schema differs between rows")
        numeric, categorical = [], []
        for name in fields:
            if name in JSON_COLUMNS:
                continue
            values = [row[name] for row in development_rows if row[name] != ""]
            try:
                for value in values:
                    number = float(value)
                    if not math.isfinite(number):
                        raise ValueError("non-finite numeric input")
            except ValueError:
                categorical.append(name)
            else:
                numeric.append(name)
        keys = {}
        for name in JSON_COLUMNS:
            observed = set()
            for row in development_rows:
                payload = json.loads(row[name] or "{}")
                if not isinstance(payload, dict):
                    raise ValueError(f"{name} must be an object")
                observed.update(payload)
            keys[name] = tuple(sorted(observed))
        return cls(fields, tuple(numeric), tuple(categorical), keys)

    @property
    def all_numeric_columns(self) -> tuple[str, ...]:
        return self.numeric_columns + tuple(
            f"{name}::{key}" for name in JSON_COLUMNS for key in self.json_keys[name]
        )

    def frame(self, rows: tuple[dict[str, str], ...]) -> pd.DataFrame:
        columns = self.all_numeric_columns + self.categorical_columns
        prepared = []
        for row in rows:
            if set(row) - {"row_id"} != set(self.source_columns):
                raise ValueError("input does not match frozen D schema")
            item = {}
            for name in self.numeric_columns:
                item[name] = float(row[name]) if row[name] else float("nan")
            for name in self.categorical_columns:
                item[name] = row[name] or "<MISSING>"
            for name in JSON_COLUMNS:
                payload = json.loads(row[name] or "{}")
                if not isinstance(payload, dict):
                    raise ValueError(f"{name} must be an object")
                for key in self.json_keys[name]:
                    value = float(payload.get(key, 0.0))
                    if not math.isfinite(value):
                        raise ValueError("non-finite category weight")
                    item[f"{name}::{key}"] = value
            prepared.append(item)
        return pd.DataFrame(prepared, columns=columns)
