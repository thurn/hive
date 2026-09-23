"""Malformed external data must fail before it becomes domain state."""

import unittest

from hive.errors import HiveError
from hive.jsonvalue import integer, parse, record, sequence, string


class BoundaryTests(unittest.TestCase):
    def test_decode_nested_provider_data(self) -> None:
        value = record(parse('{"issues":[{"title":"repair","priority":2}]}'))
        issue = record(sequence(value["issues"], "issues")[0])
        self.assertEqual(string(issue["title"], "title"), "repair")
        self.assertEqual(integer(issue["priority"], "priority"), 2)

    def test_boolean_is_not_an_integer(self) -> None:
        with self.assertRaises(HiveError):
            integer(True, "capacity")

    def test_invalid_shapes_fail_visibly(self) -> None:
        for value in [None, [], 7, "not an object", {1: "bad key"}]:
            with self.subTest(value=value), self.assertRaises(HiveError):
                record(value)
        with self.assertRaises(HiveError):
            parse('{"incomplete":')
        with self.assertRaises(HiveError):
            string("  ", "owner")
