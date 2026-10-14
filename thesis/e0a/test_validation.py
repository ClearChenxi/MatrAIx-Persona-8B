"""Failure cases that would otherwise admit fabricated or normalized answers."""

import copy
import unittest

from validation import CallBudget, INSTRUMENT_ID, check_messages, probability
from run import local_network_guard


class AcceptanceTests(unittest.TestCase):
    def envelope(self, value="0.0"):
        return {
            "instrument": {"id": INSTRUMENT_ID},
            "answers": [{"questionId": "p_redeem", "value": value}],
        }

    def test_valid_zero_and_one(self):
        self.assertEqual(probability(self.envelope()), 0)
        self.assertEqual(probability(self.envelope("1.0")), 1)

    def test_invalid_raw_values(self):
        for value in [
            0,
            False,
            None,
            "",
            "nan",
            "inf",
            "-0.1",
            "1.1",
            "0.2 because",
            "1e-1",
        ]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                probability(self.envelope(value))

    def test_missing_duplicate_and_confidence_answers(self):
        source = self.envelope("0.4")
        malformed = [copy.deepcopy(source) for _ in range(4)]
        malformed[0]["answers"] = []
        malformed[1]["answers"] *= 2
        malformed[2]["answers"][0]["confidence"] = 0.9
        malformed[3]["instrument"]["id"] = "wrong"
        for item in malformed:
            with self.assertRaises(ValueError):
                probability(item)

    def test_prompt_fallback_rejected(self):
        with self.assertRaises(ValueError):
            check_messages(
                [{"content": "typical user"}], [{"content": "household facts"}]
            )

    def test_no_retry_or_extra_generation(self):
        budget = CallBudget(2)
        budget.claim("a")
        with self.assertRaises(ValueError):
            budget.claim("a")
        budget.claim("b")
        with self.assertRaises(ValueError):
            budget.claim("c")

    def test_cloud_networking_rejected(self):
        local_network_guard("socket.getaddrinfo", ("127.0.0.1",))
        local_network_guard("socket.getaddrinfo", ("::1",))
        for host in ["api.openai.com", "1.1.1.1", "www.micuapi.ai"]:
            with self.subTest(host=host), self.assertRaises(RuntimeError):
                local_network_guard("socket.getaddrinfo", (host,))


if __name__ == "__main__":
    unittest.main()
