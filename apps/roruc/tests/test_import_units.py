"""
Tests for the import_units management command.
"""

import os
import tempfile
from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase

from ..management.commands.import_units import parse_and_validate, read_tsv
from ..models import (
    ExternalIdentifierType,
    Unit,
    UnitExternalIdentifier,
    UnitHierarchyEdge,
    UnitStatus,
    UnitType,
)

LEGACY = ExternalIdentifierType.ESCHOLARSHIP

# A small, valid hierarchy:
#   root -> ucb -> (dept, journal);  root -> ucla;  journal also under ucla (two parents)
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


class TempFilesMixin:
    """Helpers to write TSV fixtures into a per-test temp directory."""

    def setUp(self):
        super().setUp()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmpdir = tmp.name

    def write_tsv(self, name, rows, raw=None):
        path = os.path.join(self.tmpdir, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(
                raw if raw is not None else "".join("\t".join(r) + "\n" for r in rows)
            )
        return path

    def write_both(self, units=UNITS, relations=RELATIONS):
        return (
            self.write_tsv("all_units.tsv", units),
            self.write_tsv("all_relations.tsv", relations),
        )


# --------------------------------------------------------------------------- #
# read_tsv / parse_and_validate (no database)
# --------------------------------------------------------------------------- #
class ReadTsvTests(TempFilesMixin, SimpleTestCase):
    def test_strips_whitespace_and_skips_blank_lines(self):
        path = self.write_tsv("f.tsv", None, raw="  a \t b  \n\n   \t  \nc\td\n")
        self.assertEqual(read_tsv(path, 2), [["a", "b"], ["c", "d"]])

    def test_wrong_column_count_reports_line_number(self):
        path = self.write_tsv("f.tsv", None, raw="a\tb\nonly-one\n")
        with self.assertRaises(CommandError) as ctx:
            read_tsv(path, 2)
        self.assertIn(":2:", str(ctx.exception))
        self.assertIn("expected 2", str(ctx.exception))

    def test_missing_file(self):
        with self.assertRaises(CommandError) as ctx:
            read_tsv(os.path.join(self.tmpdir, "nope.tsv"), 2)
        self.assertIn("Cannot read", str(ctx.exception))

    def test_quotes_are_not_interpreted(self):
        path = self.write_tsv("f.tsv", None, raw='x\tThe "Quoted" Name\n')
        self.assertEqual(read_tsv(path, 2), [["x", 'The "Quoted" Name']])


class ParseAndValidateTests(TempFilesMixin, SimpleTestCase):
    def parse(self, units=UNITS, relations=RELATIONS):
        return parse_and_validate(*self.write_both(units, relations))

    def test_valid_input(self):
        units, edges = self.parse()
        self.assertEqual(set(units), {u[0] for u in UNITS})
        self.assertEqual(
            edges,
            sorted((parent, child) for child, parent in RELATIONS),
        )

    def test_status_is_translated(self):
        units, _ = self.parse()
        self.assertEqual(units["root"]["status"], UnitStatus.ACTIVE)
        self.assertEqual(units["dept"]["status"], UnitStatus.INACTIVE)  # archived
        self.assertEqual(units["journal"]["status"], UnitStatus.INACTIVE)  # hidden

    def test_names_are_stripped(self):
        units, _ = self.parse(
            units=[("a", "  Padded Name  ", "oru", "active")], relations=[]
        )
        self.assertEqual(units["a"]["name"], "Padded Name")

    def test_duplicate_relations_are_collapsed(self):
        _, edges = self.parse(relations=RELATIONS + [("ucb", "root")])
        self.assertEqual(len(edges), len(RELATIONS))

    def assertProblem(self, text, **kwargs):
        with self.assertRaises(CommandError) as ctx:
            self.parse(**kwargs)
        self.assertIn(text, str(ctx.exception))
        return str(ctx.exception)

    def test_duplicate_unit_id(self):
        self.assertProblem("duplicate unit id: ucb", units=UNITS + [UNITS[1]])

    def test_empty_name(self):
        self.assertProblem(
            "empty name", units=[("a", "", "oru", "active")], relations=[]
        )

    def test_unknown_type(self):
        self.assertProblem(
            "unknown type 'bogus'", units=[("a", "A", "bogus", "active")], relations=[]
        )

    def test_unknown_status(self):
        self.assertProblem(
            "unknown status 'weird'", units=[("a", "A", "oru", "weird")], relations=[]
        )

    def test_self_relation(self):
        self.assertProblem("itself", relations=[("ucb", "ucb")])

    def test_unknown_child_in_relation(self):
        self.assertProblem("unknown child 'ghost'", relations=[("ghost", "root")])

    def test_unknown_parent_in_relation(self):
        self.assertProblem("unknown parent 'ghost'", relations=[("ucb", "ghost")])

    def test_all_problems_reported_together(self):
        msg = self.assertProblem(
            "3 problem(s)",
            units=[("a", "A", "bogus", "active"), ("b", "", "oru", "weird")],
            relations=[],
        )
        self.assertIn("unknown type", msg)
        self.assertIn("empty name", msg)
        self.assertIn("unknown status", msg)

    def test_long_problem_lists_are_truncated(self):
        units = [(f"u{i}", f"U{i}", "bogus", "active") for i in range(50)]
        msg = self.assertProblem("50 problem(s)", units=units, relations=[])
        self.assertIn("and 10 more", msg)


# --------------------------------------------------------------------------- #
# The command itself
# --------------------------------------------------------------------------- #
class ImportUnitsCommandTests(TempFilesMixin, TestCase):
    def run_import(self, units=UNITS, relations=RELATIONS, **options):
        u, r = self.write_both(units, relations)
        out, err = StringIO(), StringIO()
        call_command("import_units", u, r, stdout=out, stderr=err, **options)
        return out.getvalue(), err.getvalue()

    @staticmethod
    def unit(legacy_id):
        return UnitExternalIdentifier.objects.get(type=LEGACY, value=legacy_id).unit

    def edge_exists(self, parent_legacy, child_legacy):
        return UnitHierarchyEdge.objects.filter(
            parent=self.unit(parent_legacy), child=self.unit(child_legacy)
        ).exists()

    # ---- fresh import ---------------------------------------------------- #
    def test_creates_units_with_fields_mapped(self):
        out, _ = self.run_import()
        self.assertEqual(Unit.objects.count(), len(UNITS))
        self.assertIn(f"Units: {len(UNITS)} created", out)

        ucb = self.unit("ucb")
        self.assertEqual(
            (ucb.name, ucb.type, ucb.status),
            ("UC Berkeley", UnitType.CAMPUS, UnitStatus.ACTIVE),
        )
        self.assertEqual(self.unit("dept").status, UnitStatus.INACTIVE)
        self.assertEqual(self.unit("root").type, UnitType.ROOT)

    def test_legacy_ids_stored_as_external_identifiers(self):
        self.run_import()
        legacy = UnitExternalIdentifier.objects.filter(type=LEGACY)
        self.assertEqual(
            set(legacy.values_list("value", flat=True)), {u[0] for u in UNITS}
        )
        self.assertEqual(legacy.count(), len(UNITS))

    def test_new_public_uids_are_generated_not_legacy_ids(self):
        self.run_import()
        uids = list(Unit.objects.values_list("uid", flat=True))
        self.assertEqual(len(set(uids)), len(UNITS))
        self.assertTrue(set(uids).isdisjoint({u[0] for u in UNITS}))

    def test_creates_hierarchy(self):
        out, _ = self.run_import()
        self.assertEqual(UnitHierarchyEdge.objects.count(), len(RELATIONS))
        self.assertIn(f"Edges: {len(RELATIONS)} added", out)
        for child, parent in RELATIONS:
            self.assertTrue(self.edge_exists(parent, child), f"{parent} -> {child}")

    def test_multiple_parents_supported(self):
        self.run_import()
        parents = set(self.unit("journal").parents.all())
        self.assertEqual(parents, {self.unit("ucb"), self.unit("ucla")})

    # ---- re-runs --------------------------------------------------------- #
    def test_rerun_is_idempotent(self):
        self.run_import()
        before_units = set(Unit.objects.values_list("pk", "uid"))
        before_edges = UnitHierarchyEdge.objects.count()

        out, _ = self.run_import()

        self.assertEqual(set(Unit.objects.values_list("pk", "uid")), before_units)
        self.assertEqual(UnitHierarchyEdge.objects.count(), before_edges)
        self.assertEqual(
            UnitExternalIdentifier.objects.filter(type=LEGACY).count(), len(UNITS)
        )
        self.assertIn(f"0 created, 0 updated, {len(UNITS)} unchanged", out)
        self.assertIn(f"0 added, {len(RELATIONS)} already present", out)

    def test_rerun_updates_changed_fields_but_keeps_uid(self):
        self.run_import()
        original_uid = self.unit("dept").uid

        changed = [
            u if u[0] != "dept" else ("dept", "Art & Design", "series", "active")
            for u in UNITS
        ]
        out, _ = self.run_import(units=changed)

        dept = self.unit("dept")
        self.assertEqual(
            (dept.name, dept.type, dept.status),
            ("Art & Design", UnitType.SERIES, UnitStatus.ACTIVE),
        )
        self.assertEqual(dept.uid, original_uid)
        self.assertIn("1 updated", out)
        self.assertEqual(Unit.objects.count(), len(UNITS))

    def test_rerun_adds_only_new_units_and_edges(self):
        self.run_import()
        units = UNITS + [("newj", "New Journal", "journal", "active")]
        relations = RELATIONS + [("newj", "ucla")]

        out, _ = self.run_import(units=units, relations=relations)

        self.assertEqual(Unit.objects.count(), len(UNITS) + 1)
        self.assertTrue(self.edge_exists("ucla", "newj"))
        self.assertIn("1 created", out)
        self.assertIn("1 added", out)

    def test_existing_unit_is_matched_by_legacy_id_not_duplicated(self):
        pre = Unit.objects.create(name="Old Berkeley Name", type=UnitType.CAMPUS)
        UnitExternalIdentifier.objects.create(unit=pre, type=LEGACY, value="ucb")

        self.run_import()

        self.assertEqual(Unit.objects.count(), len(UNITS))
        pre.refresh_from_db()
        self.assertEqual(pre.name, "UC Berkeley")
        self.assertEqual(self.unit("ucb").pk, pre.pk)

    def test_other_identifier_types_do_not_match(self):
        # A ROR id that happens to equal a legacy id must not be treated as the legacy id.
        other = Unit.objects.create(name="Unrelated", type=UnitType.ORU)
        UnitExternalIdentifier.objects.create(
            unit=other, type=ExternalIdentifierType.ROR, value="ucb"
        )

        self.run_import()

        other.refresh_from_db()
        self.assertEqual(other.name, "Unrelated")
        self.assertEqual(Unit.objects.count(), len(UNITS) + 1)

    # ---- dry run --------------------------------------------------------- #
    def test_dry_run_persists_nothing(self):
        out, _ = self.run_import(dry_run=True)
        self.assertIn("Dry run", out)
        self.assertIn(
            f"Units: {len(UNITS)} created", out
        )  # still reports what would happen
        self.assertEqual(Unit.objects.count(), 0)
        self.assertEqual(UnitExternalIdentifier.objects.count(), 0)
        self.assertEqual(UnitHierarchyEdge.objects.count(), 0)

    def test_dry_run_does_not_modify_existing_data(self):
        self.run_import()
        changed = [
            u if u[0] != "dept" else ("dept", "Renamed", "oru", "active") for u in UNITS
        ]
        self.run_import(units=changed, dry_run=True)
        self.assertEqual(self.unit("dept").name, "Department of Art")

    # ---- bad input ------------------------------------------------------- #
    def test_invalid_input_aborts_before_writing_anything(self):
        bad = UNITS + [("bad", "Bad", "not_a_type", "active")]
        with self.assertRaises(CommandError):
            self.run_import(units=bad)
        self.assertEqual(Unit.objects.count(), 0)
        self.assertEqual(UnitExternalIdentifier.objects.count(), 0)

    def test_relation_to_unknown_unit_aborts(self):
        with self.assertRaises(CommandError):
            self.run_import(relations=RELATIONS + [("ucb", "ghost")])
        self.assertEqual(Unit.objects.count(), 0)

    def test_missing_files(self):
        with self.assertRaises(CommandError):
            call_command(
                "import_units",
                os.path.join(self.tmpdir, "nope1.tsv"),
                os.path.join(self.tmpdir, "nope2.tsv"),
            )

    # ---- cycles ---------------------------------------------------------- #
    def test_cycle_is_reported_and_other_edges_still_imported(self):
        units = [
            ("a", "A", "oru", "active"),
            ("b", "B", "oru", "active"),
            ("c", "C", "oru", "active"),
        ]
        # parent->child edges: a->b, b->c, c->a  (the last one closes a cycle)
        relations = [("b", "a"), ("c", "b"), ("a", "c")]

        out, err = self.run_import(units=units, relations=relations)

        self.assertEqual(Unit.objects.count(), 3)
        self.assertEqual(UnitHierarchyEdge.objects.count(), 2)
        self.assertIn("Could not add c -> a", err)
        self.assertIn("2 added", out)
        self.assertIn("1 failed", out)
        self.assertTrue(self.edge_exists("a", "b"))
        self.assertTrue(self.edge_exists("b", "c"))
        self.assertFalse(self.edge_exists("c", "a"))
