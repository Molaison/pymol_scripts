import inspect
import unittest

from pymolplugins.core.parameters import (
    console_command,
    console_text,
    extend_command,
    is_console_command,
)


class FakeCmd:
    def __init__(self):
        self.extensions = {}

    def extend(self, name, function):
        self.extensions[name] = function


class ConsoleParameterTests(unittest.TestCase):
    def test_console_text_removes_exactly_one_paired_layer(self):
        self.assertEqual(console_text('"alpha,beta"'), "alpha,beta")
        self.assertEqual(console_text("'chain A'"), "chain A")
        self.assertEqual(console_text('""'), "")
        self.assertEqual(console_text('""alpha""'), '"alpha"')
        self.assertEqual(console_text("'''alpha,beta'''"), "alpha,beta")

    def test_console_text_preserves_mismatches_internal_text_and_backslashes(self):
        self.assertEqual(console_text('"unclosed'), '"unclosed')
        self.assertEqual(console_text("'mismatch\""), "'mismatch\"")
        self.assertEqual(
            console_text("'label \"A\", {target}'"),
            'label "A", {target}',
        )
        self.assertEqual(
            console_text(r'"C:\tmp\new folder\model.pdb"'),
            r"C:\tmp\new folder\model.pdb",
        )
        self.assertEqual(
            console_text(r'"^(?:alpha,beta|walk_\d+)$"'),
            r"^(?:alpha,beta|walk_\d+)$",
        )

    def test_wrapper_normalizes_explicit_positional_and_keyword_strings(self):
        calls = []

        def sample(value, mode="default", count=0):
            calls.append((value, mode, count))
            return calls[-1]

        adapted = console_command(sample)
        result = adapted('"a,b"', mode="'exact'", count='"2"')

        self.assertEqual(result, ("a,b", "exact", "2"))
        self.assertTrue(is_console_command(adapted))
        self.assertIs(inspect.unwrap(adapted), sample)
        self.assertEqual(inspect.signature(adapted), inspect.signature(sample))

    def test_direct_python_function_and_defaults_remain_unchanged(self):
        def sample(value, optional='"literal-default"'):
            return value, optional

        adapted = console_command(sample)

        self.assertEqual(sample('"literal"'), ('"literal"', '"literal-default"'))
        self.assertEqual(adapted('"literal"'), ("literal", '"literal-default"'))

    def test_extend_command_registers_the_adapted_callback(self):
        fake = FakeCmd()

        def sample(value):
            return value

        registered = extend_command(fake, "sample", sample)

        self.assertIs(fake.extensions["sample"], registered)
        self.assertTrue(is_console_command(registered))
        self.assertEqual(fake.extensions["sample"]("'value'"), "value")


if __name__ == "__main__":
    unittest.main()
