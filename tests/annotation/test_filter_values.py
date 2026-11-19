import unittest
from types import SimpleNamespace

from pymolplugins.annotation.filter_values import FilterValueCatalog


class FilterValueTests(unittest.TestCase):
    def test_suggestions_use_column_values_and_preserve_literal_types(self):
        rows = [SimpleNamespace(metadata={"task": task, "score": str(index)})
                for index, task in enumerate(("Alpha", "O'Brien", "Alpha", "01", "Beta", "", "Beta"))]
        dataset = SimpleNamespace(columns=("task", "score"), column_specs=(), entries=rows)
        catalog = FilterValueCatalog(dataset)
        self.assertEqual(catalog.suggestions("task", "br"), ['"O\'Brien"'])
        self.assertEqual(catalog.suggestions("task", "01"), ["'01'"])
        self.assertEqual(catalog.suggestions("task", "al", text=True, literal=False), ["Alpha"])
        self.assertEqual(catalog.suggestions("score"), ["0", "1", "3", "4", "6"])
        self.assertEqual(catalog.suggestions("score", "4", text=True), ["'4'"])
