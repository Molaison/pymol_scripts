import unittest
from types import SimpleNamespace

from pymolplugins.annotation.review_config import (
    decode_value, encode_value, missing_required, validate_fields,
)
from pymolplugins.annotation.score_summary import format_score, prepare_score_summary


class ReviewGuidanceTests(unittest.TestCase):
    def test_required_optional_and_multiple_values(self):
        fields = validate_fields([
            {"name": "quality", "type": "single", "required": True, "values": ["Good", "Bad"]},
            {"name": "flags", "type": "multiple", "required": True, "values": ["A", "B"]},
            {"name": "notes", "type": "text", "required": False},
        ])
        self.assertEqual(missing_required(fields, {}), ["quality", "flags"])
        values = {"quality": "Good", "flags": encode_value(fields[1], ["B", "A"])}
        self.assertEqual(decode_value(fields[1], values["flags"]), ["A", "B"])
        self.assertEqual(missing_required(fields, values), [])
        self.assertIsNone(encode_value(fields[2], "  "))
        with self.assertRaises(ValueError):
            encode_value(fields[0], "Unknown")

    def test_number_formatting_and_missing_values(self):
        cases = {"0.873241": "0.873", "87.3456": "87.35", "1234": "1,234",
                 "0.000012345": "1.23e-05", "0": "0", "NaN": "—", "inf": "—", "": "—"}
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(format_score(raw), expected)

    def test_summary_uses_only_selected_columns_and_current_cohort(self):
        entries = [SimpleNamespace(metadata={"AC.iptm": str(value), "hidden": "10", "score_ac_iptm_pass": "True"})
                   for value in (0.2, 0.4, 0.8, 0.9)]
        full = prepare_score_summary(entries, ["AC.iptm"])
        filtered = prepare_score_summary(entries[2:], ["AC.iptm"])
        self.assertEqual(full.columns, ("AC.iptm",))
        self.assertNotEqual(full.cards(entries[2])[0]["percentile"], filtered.cards(entries[2])[0]["percentile"])
        self.assertEqual(full.cards(entries[2])[0]["relative_tone"], "neutral")
        self.assertEqual(full.cards(entries[2])[0]["absolute"], "CSV: pass")
        self.assertEqual(prepare_score_summary(entries, []).cards(entries[0]), [])

    def test_explicit_direction_and_thresholds_do_not_conflate_rank_with_pass(self):
        entries = [SimpleNamespace(metadata={"score": str(i), "score_pass": "True"}) for i in range(10)]
        summary = prepare_score_summary(entries, ["score"], {"rules": {"score": {"direction": "lower", "good": 1, "bad": 8}}})
        low, high = summary.cards(entries[0])[0], summary.cards(entries[-1])[0]
        self.assertEqual(low["absolute_tone"], "good")
        self.assertEqual(low["relative_tone"], "good")
        self.assertEqual(high["absolute_tone"], "bad")
        self.assertEqual(high["relative_tone"], "bad")


if __name__ == "__main__":
    unittest.main()
