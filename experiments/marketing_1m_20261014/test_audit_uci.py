"""Synthetic-only tests for exclusion of outcomes before value decoding."""
import io
import unittest

import audit_uci as audit


class ProjectionTests(unittest.TestCase):
    def test_outcome_bytes_are_never_decoded(self):
        # Invalid UTF-8 in both excluded outcomes must not reach the decoder.
        self.assertEqual(audit.project_features(b'40;"\xff";2;\xfe\n',
                         ["age", "poutcome", "previous", "y"], ";"), ["40", "2"])

    def test_quoted_delimiters_and_escaped_quotes(self):
        self.assertEqual(audit.project_features(b'"a,b";"c""d";ignored\n',
                         ["a", "b", "y"], ";"), ["a,b", 'c"d'])
        self.assertEqual(audit.project_features(b'"a;b";"c""d";ignored\n',
                         ["a", "b", "y"], ";"), ["a;b", 'c"d'])

    def test_outcome_replacement_does_not_change_audit(self):
        header = ["age", "poutcome", "y"]
        one = io.BytesIO(b'age;poutcome;y\n40;success;yes\n40;failure;no\n')
        two = io.BytesIO(b'age;poutcome;y\n40;other;\xff\n40;anything;\xfe\n')
        self.assertEqual(audit.audit_csv(one, header, ";", "synthetic"),
                         audit.audit_csv(two, header, ";", "synthetic"))

    def test_unapproved_header_or_broken_prefix_fails_closed(self):
        with self.assertRaises(RuntimeError):
            audit.audit_csv(io.BytesIO(b"x;y\n1;yes\n"), ["age", "y"], ";", "synthetic")
        with self.assertRaises(ValueError):
            audit.project_features(b'"unclosed;yes\n', ["age", "y"], ";")

    def test_unknown_is_separate_from_blank(self):
        result = audit.audit_csv(io.BytesIO(b"job;y\nunknown;yes\n;no\n"),
                                 ["job", "y"], ";", "synthetic")
        self.assertEqual(result["fields"]["job"]["blank_count"], 1)
        self.assertEqual(result["fields"]["job"]["unknown_count"], 1)
        self.assertNotIn("y", result["fields"])


if __name__ == "__main__":
    unittest.main()
