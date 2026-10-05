import unittest
import json

from pymolplugins.annotation.command_templates import format_commands, validate_commands
from pymolplugins.annotation.ai_commands import SCHEMA, parse_draft, output_schema


class CommandTemplateTests(unittest.TestCase):
    def test_complete_template_binds_object_and_selection_to_same_structure(self):
        commands = format_commands("# Header\nzoom {object}\ncolor cyan, {selection}", {"object": "sa_current"})
        self.assertEqual(commands, [(2, "zoom sa_current"), (3, "color cyan, sa_current")])

    def test_incomplete_unknown_or_empty_selections_are_rejected_with_line_number(self):
        for template, context in (("zoom {object}\ncolor cyan, {obj", {"object": "sa_current"}),
                                  ("color cyan, {missing}", {"object": "sa_current"}),
                                  ("zoom {object}", {"object": ""})):
            with self.assertRaisesRegex(ValueError, "Command line"):
                format_commands(template, context)

    def test_step_cannot_use_bucket_fields(self):
        with self.assertRaisesRegex(ValueError, "unknown placeholder"):
            validate_commands("color {value}, {object}", {})
        validate_commands("zoom {object}", {"Keep": "color {value}, {object}"})

    def test_empty_ai_step_reports_the_provider_reason_and_trigger(self):
        document = {"schema": SCHEMA, "steps": [{"step_id": "on_open", "commands": [],
                    "note": "Creating a membrane model is not a display operation."}]}
        with self.assertRaisesRegex(ValueError, "On open: Creating a membrane model"):
            parse_draft(json.dumps(document), {"on_open": ""}, {}, None)

    def test_empty_ai_step_without_reason_is_actionable(self):
        document = {"schema": SCHEMA, "steps": [{"step_id": "after_label/Keep", "commands": []}]}
        with self.assertRaisesRegex(ValueError, "Keep: No commands returned. Try a viewing preset"):
            parse_draft(json.dumps(document), {"after_label/Keep": ""}, {}, None)

    def test_provider_schema_requires_an_explanation_field(self):
        step = output_schema({"on_open": ""})["properties"]["steps"]["items"]
        self.assertIn("note", step["required"])
