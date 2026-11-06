"""Behavior tests for the real customer export command."""
import csv
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ExportTests(unittest.TestCase):
    def run_input(self, text):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "contacts.json"
            source.write_text(text, encoding="utf-8")
            return subprocess.run(
                [sys.executable, str(ROOT / "export_csv.py"), str(source)],
                capture_output=True, encoding="utf-8", timeout=10,
            )

    def assert_export(self, records, expected):
        result = self.run_input(json.dumps(records, ensure_ascii=False))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(list(csv.reader(io.StringIO(result.stdout))), expected)
        return result.stdout

    def assert_rejected(self, text, message):
        result = self.run_input(text)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertIn(message, result.stderr)

    def test_header_values_and_input_order(self):
        self.assert_export(
            [{"email": "z@example.com", "name": "Zoe", "extra": 7},
             {"name": "Amy", "email": "a@example.com"}],
            [["name", "email"], ["Zoe", "z@example.com"], ["Amy", "a@example.com"]],
        )

    def test_chinese_commas_quotes_and_newlines_round_trip(self):
        output = self.assert_export(
            [{"name": '张三, "朋友"\n李四', "email": '"a,b"@example.com'}],
            [["name", "email"], ['张三, "朋友"\n李四', '"a,b"@example.com']],
        )
        self.assertIn('""朋友""', output)

    def test_empty_list_emits_only_header(self):
        self.assert_export([], [["name", "email"]])

    def test_invalid_json_is_rejected(self):
        self.assert_rejected("[{", "JSON")

    def test_non_array_is_rejected(self):
        for value in ({}, None, "contacts", 5):
            with self.subTest(value=value):
                self.assert_rejected(json.dumps(value), "array")

    def test_missing_fields_in_later_record_are_atomic(self):
        for field in ("name", "email"):
            records = [{"name": "Valid", "email": "v@example.com"},
                       {"name": "Later", "email": "l@example.com"}]
            del records[1][field]
            with self.subTest(field=field):
                self.assert_rejected(json.dumps(records), field)

    def test_non_object_and_non_string_fields_are_rejected(self):
        for record in (None, [], "bad", {"name": 1, "email": "a"},
                       {"name": "A", "email": None}):
            with self.subTest(record=record):
                self.assert_rejected(json.dumps([record]), "record 1")


if __name__ == "__main__":
    unittest.main()
