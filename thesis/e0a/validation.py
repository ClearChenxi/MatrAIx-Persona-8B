"""Strict raw-answer checks independent of official survey normalization."""

import math
import re

INSTRUMENT_ID = "thesis_e0a_synthetic_v1"


def probability(payload, instrument_id=INSTRUMENT_ID):
    if not isinstance(payload, dict):
        raise ValueError("answer envelope must be an object")
    if set(payload) != {"instrument", "answers"}:
        raise ValueError("unexpected or missing envelope fields")
    instrument = payload["instrument"]
    if not isinstance(instrument, dict) or instrument.get("id") != instrument_id:
        raise ValueError("wrong instrument identity")
    answers = payload["answers"]
    if not isinstance(answers, list) or len(answers) != 1:
        raise ValueError("expected exactly one raw answer")
    answer = answers[0]
    if not isinstance(answer, dict) or set(answer) != {"questionId", "value"}:
        raise ValueError("answer must contain only questionId and value")
    if answer["questionId"] != "p_redeem":
        raise ValueError("wrong question identity")
    value = answer["value"]
    if not isinstance(value, str) or not re.fullmatch(
        r"(?:0(?:\.\d+)?|1(?:\.0+)?)", value
    ):
        raise ValueError("probability must be a decimal string in [0,1]")
    number = float(value)
    if not math.isfinite(number) or not 0 <= number <= 1:
        raise ValueError("invalid probability")
    return number


def check_messages(actual, expected):
    if actual != expected:
        raise ValueError(
            "actual prompts differ from strict pre-render; refusing generation"
        )


class CallBudget:
    def __init__(self, maximum):
        self.maximum = maximum
        self.count = 0
        self.used_trials = set()

    def claim(self, trial):
        if trial in self.used_trials or self.count >= self.maximum:
            raise ValueError("generation budget exhausted or duplicate trial")
        self.used_trials.add(trial)
        self.count += 1
