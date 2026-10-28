"""
Tests for the export_units management command.
"""

import json
import os
import tempfile
from contextlib import redirect_stdout
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.test import TestCase, override_settings

from apps.roruc.logic import DEFAULT_ID_PREFIX
from apps.roruc.models import (
    Unit,
    UnitStatus,
    UnitHierarchyEdge,
    UnitSuccession,
    UnitExternalIdentifier,
    UnitAlternateName,
    AlternateNameType,
    ExternalIdentifierType,
)

UNIT_TO_ROR = "apps.roruc.management.commands.export_units.unit_to_ror"


def make_unit(name="Test Unit", status=UnitStatus.ACTIVE, **kwargs):
    return Unit.objects.create(name=name, status=status, **kwargs)


def make_parent(parent, child):
    e = UnitHierarchyEdge(parent=parent, child=child)
    e.save()
    return e


def make_succession(predecessor, successor):
    return UnitSuccession.objects.create(predecessor=predecessor, successor=successor)


def make_external_identifier(unit):
    return UnitExternalIdentifier.objects.create(
        unit=unit, type=ExternalIdentifierType.ESCHOLARSHIP, value="qtXXXXXX"
    )


def make_alternate_name(unit):
    return UnitAlternateName.objects.create(
        unit=unit, type=AlternateNameType.ALIAS, value="ABCD"
    )


def inactive_status():
    return next(s for s in UnitStatus if s != UnitStatus.ACTIVE)


def run_command(*args, **kwargs):
    """
    Run the command and return (stdout_text, stderr_text).

    The command writes to sys.stdout directly (not self.stdout), so
    call_command(stdout=...) would not capture it; redirect_stdout does.
    """
    out, err = StringIO(), StringIO()
    with redirect_stdout(out):
        call_command("export_units", *args, stderr=err, **kwargs)
    return out.getvalue(), err.getvalue()


def fake_unit_to_ror(unit, id_prefix=None, include_unit_type=False):
    """Stand-in for unit_to_ror that echoes its inputs so they can be asserted."""
    return {
        "name": unit.name,
        "id_prefix": id_prefix,
        "include_unit_type": include_unit_type,
    }


class ExportUnitsFilteringTests(TestCase):
    def setUp(self):
        self.active = make_unit("Active Unit", UnitStatus.ACTIVE)
        self.other = make_unit("Other Unit", inactive_status())

    @mock.patch(UNIT_TO_ROR, side_effect=fake_unit_to_ror)
    def test_default_exports_only_active_units(self, _mock):
        out, _ = run_command()
        names = [r["name"] for r in json.loads(out)]
        self.assertEqual(names, ["Active Unit"])

    @mock.patch(UNIT_TO_ROR, side_effect=fake_unit_to_ror)
    def test_export_all_includes_every_status(self, _mock):
        out, _ = run_command("--export-all")
        names = {r["name"] for r in json.loads(out)}
        self.assertEqual(names, {"Active Unit", "Other Unit"})

    @mock.patch(UNIT_TO_ROR, side_effect=fake_unit_to_ror)
    def test_no_units_outputs_empty_json_list(self, _mock):
        Unit.objects.all().delete()
        out, _ = run_command()
        self.assertEqual(json.loads(out), [])

    @mock.patch(UNIT_TO_ROR, side_effect=fake_unit_to_ror)
    def test_no_active_units_outputs_empty_list(self, _mock):
        Unit.objects.filter(status=UnitStatus.ACTIVE).delete()
        out, _ = run_command()
        self.assertEqual(json.loads(out), [])


class ExportUnitsOptionTests(TestCase):
    def setUp(self):
        make_unit("Unit A")

    @mock.patch(UNIT_TO_ROR, side_effect=fake_unit_to_ror)
    def test_id_prefix_is_passed_through(self, mock_ror):
        run_command("--id-prefix", "https://ror.example.edu/")
        _, kwargs = mock_ror.call_args
        self.assertEqual(kwargs["id_prefix"], "https://ror.example.edu/")

    @mock.patch(UNIT_TO_ROR, side_effect=fake_unit_to_ror)
    def test_id_prefix_defaults_to_module_default(self, mock_ror):
        with override_settings():
            from django.conf import settings

            if hasattr(settings, "ROR_ID_PREFIX"):
                del settings.ROR_ID_PREFIX
            run_command()
        _, kwargs = mock_ror.call_args
        self.assertEqual(kwargs["id_prefix"], DEFAULT_ID_PREFIX)

    @override_settings(ROR_ID_PREFIX="https://from-settings.example/")
    @mock.patch(UNIT_TO_ROR, side_effect=fake_unit_to_ror)
    def test_id_prefix_defaults_to_setting(self, mock_ror):
        run_command()
        _, kwargs = mock_ror.call_args
        self.assertEqual(kwargs["id_prefix"], "https://from-settings.example/")

    @mock.patch(UNIT_TO_ROR, side_effect=fake_unit_to_ror)
    def test_include_unit_type_defaults_false(self, mock_ror):
        run_command()
        _, kwargs = mock_ror.call_args
        self.assertFalse(kwargs["include_unit_type"])

    @mock.patch(UNIT_TO_ROR, side_effect=fake_unit_to_ror)
    def test_include_unit_type_flag(self, mock_ror):
        run_command("--include-unit-type")
        _, kwargs = mock_ror.call_args
        self.assertTrue(kwargs["include_unit_type"])

    @mock.patch(UNIT_TO_ROR, side_effect=fake_unit_to_ror)
    def test_default_indent_is_2(self, _mock):
        out, _ = run_command()
        self.assertIn('\n  {\n    "name"', out)

    @mock.patch(UNIT_TO_ROR, side_effect=fake_unit_to_ror)
    def test_custom_indent(self, _mock):
        out, _ = run_command("--indent", "4")
        self.assertIn('\n    {\n        "name"', out)

    @mock.patch(UNIT_TO_ROR, side_effect=fake_unit_to_ror)
    def test_indent_zero_still_valid_json(self, _mock):
        out, _ = run_command("--indent", "0")
        self.assertEqual(len(json.loads(out)), 1)

    @mock.patch(UNIT_TO_ROR, side_effect=lambda unit, **kw: {"name": "Zürich – 東京"})
    def test_non_ascii_is_not_escaped(self, _mock):
        out, _ = run_command()
        self.assertIn("Zürich – 東京", out)
        self.assertNotIn("\\u", out)


class ExportUnitsOutputTests(TestCase):
    def setUp(self):
        make_unit("Unit A")
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.path = os.path.join(self.tmpdir.name, "units.json")

    @mock.patch(UNIT_TO_ROR, side_effect=fake_unit_to_ror)
    def test_stdout_is_default_and_stderr_quiet(self, _mock):
        out, err = run_command()
        self.assertTrue(out.startswith("["))
        self.assertEqual(err, "")

    @mock.patch(UNIT_TO_ROR, side_effect=fake_unit_to_ror)
    def test_writes_to_file_when_output_given(self, _mock):
        out, _ = run_command("-o", self.path)
        self.assertEqual(out, "")  # nothing on stdout
        with open(self.path, encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual([r["name"] for r in data], ["Unit A"])

    @mock.patch(UNIT_TO_ROR, side_effect=fake_unit_to_ror)
    def test_long_output_option_and_success_message(self, _mock):
        _, err = run_command("--output", self.path)
        self.assertIn(f"Wrote {self.path}", err)

    @mock.patch(UNIT_TO_ROR, side_effect=lambda unit, **kw: {"name": "Zürich"})
    def test_file_is_utf8_encoded(self, _mock):
        run_command("-o", self.path)
        with open(self.path, "rb") as f:
            self.assertIn("Zürich".encode("utf-8"), f.read())

    @mock.patch(UNIT_TO_ROR, side_effect=fake_unit_to_ror)
    def test_unwritable_path_raises(self, _mock):
        bad = os.path.join(self.tmpdir.name, "missing_dir", "units.json")
        with self.assertRaises(OSError):
            run_command("-o", bad)


UNITS = [
    ("root", "eScholarship", "root", "active"),
    ("ucb", "UC Berkeley", "campus", "active"),
    ("ucla", "UC Los Angeles", "campus", "active"),
    ("dept", "Department of Art", "oru", "archived"),
    ("journal", "Journal of Things", "journal", "hidden"),
]
RELATIONS = [  # child, parent
    ("ucb", "root"),
    ("ucla", "root"),
    ("dept", "ucb"),
    ("journal", "ucb"),
    ("journal", "ucla"),
]


class ExportUnitsIntegrationTests(TestCase):
    """One test with the real unit_to_ror to catch signature drift."""

    def test_real_conversion_produces_json_list(self):
        make_unit("Real Unit")
        out, _ = run_command()
        data = json.loads(out)
        self.assertIsInstance(data, list)
        self.assertEqual(len(data), 1)
        self.assertIsInstance(data[0], dict)

    def test_parent(self):
        p = make_unit("Parent Unit")
        c = make_unit("Child Unit")
        make_parent(p, c)
        out, _ = run_command()
        data = json.loads(out)
        self.assertIsInstance(data, list)
        self.assertEqual(len(data), 2)
        self.assertEqual(len(data[0].get("relationships")), 1)
        self.assertEqual(len(data[1].get("relationships")), 1)

    def test_alternate_name(self):
        u = make_unit("Real Unit")
        make_alternate_name(u)
        out, _ = run_command()
        data = json.loads(out)
        self.assertIsInstance(data, list)
        self.assertEqual(len(data), 1)
        self.assertEqual(len(data[0].get("names")), 2)

    def test_external_identifier(self):
        u = make_unit("Real Unit")
        make_external_identifier(u)
        out, _ = run_command()
        data = json.loads(out)
        self.assertIsInstance(data, list)
        self.assertEqual(len(data), 1)
        self.assertEqual(len(data[0].get("external_ids")), 1)

    def test_succession(self):
        o = make_unit("Old Unit", status=UnitStatus.INACTIVE)
        n = make_unit("New Unit")
        make_succession(o, n)
        out, _ = run_command()
        data = json.loads(out)
        self.assertIsInstance(data, list)
        self.assertEqual(len(data), 1)
        self.assertEqual(len(data[0].get("relationships")), 1)
