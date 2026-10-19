"""
Tests for the Unit data model.

Place at: <your_app>/tests/test_models.py  (with an empty tests/__init__.py)
Run with: python manage.py test <your_app>

Needs a PostgreSQL test database (ArrayField, GinIndex, django-postgresql-dag).
"""
from unittest import mock

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import SimpleTestCase, TestCase

from .. import models as unit_models
from ..models import (
    UID_ALPHABET,
    UID_LENGTH,
    AlternateNameType,
    ExternalIdentifierType,
    Unit,
    UnitAlternateName,
    UnitExternalIdentifier,
    UnitRelation,
    UnitStatus,
    UnitSuccession,
    UnitType,
    generate_uid,
)


def make_unit(name="Test Unit", type=UnitType.ORU, **kwargs):
    return Unit.objects.create(name=name, type=type, **kwargs)


# --------------------------------------------------------------------------- #
# Identifier generation
# --------------------------------------------------------------------------- #
class GenerateUidTests(SimpleTestCase):
    def test_length_and_alphabet(self):
        for _ in range(200):
            uid = generate_uid()
            self.assertEqual(len(uid), UID_LENGTH)
            self.assertTrue(set(uid) <= set(UID_ALPHABET))

    def test_alphabet_excludes_ambiguous_characters(self):
        for ch in "ilou":
            self.assertNotIn(ch, UID_ALPHABET)

    def test_values_vary(self):
        self.assertGreater(len({generate_uid() for _ in range(100)}), 90)


# --------------------------------------------------------------------------- #
# Unit
# --------------------------------------------------------------------------- #
class UnitTests(TestCase):
    def test_defaults(self):
        unit = make_unit()
        self.assertEqual(unit.status, UnitStatus.ACTIVE)
        self.assertEqual(unit.domains, [])
        self.assertEqual(unit.links, [])
        self.assertIsNotNone(unit.created)
        self.assertIsNotNone(unit.modified)

    def test_uid_assigned_and_unique(self):
        a, b = make_unit("A"), make_unit("B")
        self.assertEqual(len(a.uid), UID_LENGTH)
        self.assertNotEqual(a.uid, b.uid)

    def test_uid_is_not_editable(self):
        self.assertFalse(Unit._meta.get_field("uid").editable)

    def test_uid_immutable_after_creation(self):
        unit = make_unit()
        unit.uid = "zzzzzzzz"
        with self.assertRaises(ValueError):
            unit.save()

    def test_resaving_with_same_uid_is_fine(self):
        unit = make_unit()
        original = unit.uid
        unit.name = "Renamed"
        unit.save()
        unit.refresh_from_db()
        self.assertEqual(unit.uid, original)
        self.assertEqual(unit.name, "Renamed")

    def test_uid_collision_on_create_is_regenerated(self):
        existing = make_unit("Existing")
        clash = Unit(name="Clash", type=UnitType.ORU)
        clash.uid = existing.uid  # force a collision
        with mock.patch.object(unit_models, "generate_uid", side_effect=["abcd0123"]):
            clash.save()
        self.assertEqual(clash.uid, "abcd0123")
        self.assertNotEqual(clash.uid, existing.uid)

    def test_domains_are_normalized(self):
        unit = make_unit(domains=[" Example.COM ", "b.org", "example.com", "", "   "])
        unit.refresh_from_db()
        self.assertEqual(unit.domains, ["b.org", "example.com"])

    def test_str(self):
        unit = make_unit("Dept of Art")
        self.assertEqual(str(unit), f"Dept of Art ({unit.uid})")

    def test_default_ordering_is_by_name(self):
        make_unit("Charlie")
        make_unit("Alpha")
        make_unit("Bravo")
        self.assertEqual(
            list(Unit.objects.values_list("name", flat=True)), ["Alpha", "Bravo", "Charlie"]
        )

    def test_invalid_type_fails_validation(self):
        unit = Unit(name="Bad", type="not_a_type")
        with self.assertRaises(ValidationError):
            unit.full_clean()

    def test_all_unit_types_are_valid(self):
        for value in UnitType.values:
            Unit(name=f"u-{value}", type=value).full_clean()


# --------------------------------------------------------------------------- #
# Hierarchy (django-postgresql-dag)
# --------------------------------------------------------------------------- #
class HierarchyTests(TestCase):
    def test_add_child_creates_parent_and_child_links(self):
        parent, child = make_unit("Parent", UnitType.CAMPUS), make_unit("Child")
        parent.add_child(child)
        self.assertIn(child, parent.children.all())
        self.assertIn(parent, child.parents.all())

    def test_unit_can_have_multiple_parents(self):
        p1, p2 = make_unit("P1"), make_unit("P2")
        child = make_unit("Child", UnitType.JOURNAL)
        p1.add_child(child)
        p2.add_child(child)
        self.assertEqual(set(child.parents.all()), {p1, p2})

    def test_cycle_is_rejected(self):
        a, b, c = make_unit("A"), make_unit("B"), make_unit("C")
        a.add_child(b)
        b.add_child(c)
        with self.assertRaises(ValidationError):
            c.add_child(a)

    def test_self_loop_is_rejected(self):
        a = make_unit("A")
        with self.assertRaises(ValidationError):
            a.add_child(a)

    def test_descendants_and_ancestors(self):
        root, campus, oru = make_unit("Root", UnitType.ROOT), make_unit("Campus"), make_unit("ORU")
        root.add_child(campus)
        campus.add_child(oru)
        self.assertEqual(set(root.descendants()), {campus, oru})
        self.assertEqual(set(oru.ancestors()), {root, campus})


# --------------------------------------------------------------------------- #
# Alternate names and names()
# --------------------------------------------------------------------------- #
class AlternateNameTests(TestCase):
    def test_names_lists_label_first_then_alternates_ordered(self):
        unit = make_unit("Department of Art")
        UnitAlternateName.objects.create(unit=unit, type=AlternateNameType.ALIAS, value="Art Dept")
        UnitAlternateName.objects.create(unit=unit, type=AlternateNameType.ACRONYM, value="DoA")
        self.assertEqual(
            unit.names(),
            [
                {"value": "Department of Art", "types": ["label"]},
                {"value": "DoA", "types": ["acronym"]},
                {"value": "Art Dept", "types": ["alias"]},
            ],
        )

    def test_names_with_no_alternates(self):
        unit = make_unit("Solo")
        self.assertEqual(unit.names(), [{"value": "Solo", "types": ["label"]}])

    def test_duplicate_alternate_name_rejected(self):
        unit = make_unit()
        UnitAlternateName.objects.create(unit=unit, type=AlternateNameType.ALIAS, value="X")
        with self.assertRaises(IntegrityError), transaction.atomic():
            UnitAlternateName.objects.create(unit=unit, type=AlternateNameType.ALIAS, value="X")

    def test_same_value_allowed_for_different_type_or_unit(self):
        a, b = make_unit("A"), make_unit("B")
        UnitAlternateName.objects.create(unit=a, type=AlternateNameType.ALIAS, value="X")
        UnitAlternateName.objects.create(unit=a, type=AlternateNameType.ACRONYM, value="X")
        UnitAlternateName.objects.create(unit=b, type=AlternateNameType.ALIAS, value="X")
        self.assertEqual(UnitAlternateName.objects.count(), 3)

    def test_deleting_unit_cascades(self):
        unit = make_unit()
        UnitAlternateName.objects.create(unit=unit, type=AlternateNameType.ALIAS, value="X")
        unit.delete()
        self.assertEqual(UnitAlternateName.objects.count(), 0)

    def test_str(self):
        unit = make_unit()
        alt = UnitAlternateName.objects.create(
            unit=unit, type=AlternateNameType.PREVIOUS_NAME, value="Old Name"
        )
        self.assertEqual(str(alt), "Previous name: Old Name")


# --------------------------------------------------------------------------- #
# External identifiers
# --------------------------------------------------------------------------- #
class ExternalIdentifierTests(TestCase):
    def test_identifier_identifies_exactly_one_unit(self):
        a, b = make_unit("A"), make_unit("B")
        UnitExternalIdentifier.objects.create(unit=a, type=ExternalIdentifierType.ROR, value="abc")
        with self.assertRaises(IntegrityError), transaction.atomic():
            UnitExternalIdentifier.objects.create(
                unit=b, type=ExternalIdentifierType.ROR, value="abc"
            )

    def test_same_value_different_type_allowed(self):
        a, b = make_unit("A"), make_unit("B")
        UnitExternalIdentifier.objects.create(unit=a, type=ExternalIdentifierType.ROR, value="abc")
        UnitExternalIdentifier.objects.create(unit=b, type=ExternalIdentifierType.GRID, value="abc")
        self.assertEqual(UnitExternalIdentifier.objects.count(), 2)

    def test_unit_can_have_several_identifiers(self):
        unit = make_unit()
        UnitExternalIdentifier.objects.create(unit=unit, type=ExternalIdentifierType.ROR, value="r")
        UnitExternalIdentifier.objects.create(unit=unit, type=ExternalIdentifierType.ISNI, value="i")
        self.assertEqual(unit.external_ids.count(), 2)

    def test_legacy_lookup(self):
        unit = make_unit()
        UnitExternalIdentifier.objects.create(
            unit=unit, type=ExternalIdentifierType.ESCHOLARSHIP, value="aah"
        )
        found = UnitExternalIdentifier.objects.get(
            type=ExternalIdentifierType.ESCHOLARSHIP, value="aah"
        )
        self.assertEqual(found.unit, unit)

    def test_str(self):
        unit = make_unit()
        ident = UnitExternalIdentifier.objects.create(
            unit=unit, type=ExternalIdentifierType.WIKIDATA, value="Q42"
        )
        self.assertEqual(str(ident), "wikidata:Q42")


# --------------------------------------------------------------------------- #
# Succession
# --------------------------------------------------------------------------- #
class SuccessionTests(TestCase):
    def test_successors_and_predecessors(self):
        old, new = make_unit("Old Journal", UnitType.JOURNAL), make_unit("New Journal", UnitType.JOURNAL)
        UnitSuccession.objects.create(predecessor=old, successor=new)
        self.assertEqual(list(old.successors.all()), [new])
        self.assertEqual(list(new.predecessors.all()), [old])
        self.assertEqual(list(old.predecessors.all()), [])

    def test_succession_is_directional(self):
        a, b = make_unit("A"), make_unit("B")
        UnitSuccession.objects.create(predecessor=a, successor=b)
        # The reverse link is a distinct, allowed row.
        UnitSuccession.objects.create(predecessor=b, successor=a)
        self.assertEqual(UnitSuccession.objects.count(), 2)

    def test_duplicate_rejected(self):
        a, b = make_unit("A"), make_unit("B")
        UnitSuccession.objects.create(predecessor=a, successor=b)
        with self.assertRaises(IntegrityError), transaction.atomic():
            UnitSuccession.objects.create(predecessor=a, successor=b)

    def test_self_succession_rejected(self):
        a = make_unit("A")
        with self.assertRaises(IntegrityError), transaction.atomic():
            UnitSuccession.objects.create(predecessor=a, successor=a)

    def test_optional_fields(self):
        a, b = make_unit("A"), make_unit("B")
        link = UnitSuccession.objects.create(predecessor=a, successor=b)
        self.assertIsNone(link.effective_date)
        self.assertEqual(link.note, "")

    def test_str(self):
        a, b = make_unit("A"), make_unit("B")
        link = UnitSuccession.objects.create(predecessor=a, successor=b)
        self.assertEqual(str(link), f"{a.pk} -> {b.pk}")


# --------------------------------------------------------------------------- #
# Symmetric relations
# --------------------------------------------------------------------------- #
class RelationTests(TestCase):
    def setUp(self):
        self.a, self.b = make_unit("A"), make_unit("B")
        self.low, self.high = sorted([self.a, self.b], key=lambda u: u.pk)

    def test_canonical_orders_by_pk(self):
        self.assertEqual(
            UnitRelation.canonical(self.high, self.low),
            {"unit_a": self.low, "unit_b": self.high},
        )
        self.assertEqual(
            UnitRelation.canonical(self.low, self.high),
            {"unit_a": self.low, "unit_b": self.high},
        )

    def test_canonical_rejects_self_relation(self):
        with self.assertRaises(ValueError):
            UnitRelation.canonical(self.a, self.a)

    def test_save_swaps_out_of_order_units(self):
        rel = UnitRelation(unit_a=self.high, unit_b=self.low)
        rel.save()
        rel.refresh_from_db()
        self.assertEqual((rel.unit_a, rel.unit_b), (self.low, self.high))

    def test_check_constraint_enforces_canonical_order(self):
        # bulk_create bypasses save(), so the DB constraint is the only guard.
        with self.assertRaises(IntegrityError), transaction.atomic():
            UnitRelation.objects.bulk_create([UnitRelation(unit_a=self.high, unit_b=self.low)])

    def test_add_related_is_symmetric_and_idempotent(self):
        self.a.add_related(self.b)
        self.b.add_related(self.a)
        self.a.add_related(self.b)
        self.assertEqual(UnitRelation.objects.count(), 1)
        self.assertEqual(list(self.a.related_units()), [self.b])
        self.assertEqual(list(self.b.related_units()), [self.a])

    def test_related_units_excludes_self_and_unrelated(self):
        c = make_unit("C")
        self.a.add_related(self.b)
        self.assertNotIn(self.a, self.a.related_units())
        self.assertNotIn(c, self.a.related_units())
        self.assertEqual(list(c.related_units()), [])

    def test_related_units_across_multiple_relations(self):
        c = make_unit("C")
        self.a.add_related(self.b)
        self.a.add_related(c)
        self.assertEqual(set(self.a.related_units()), {self.b, c})

    def test_str(self):
        rel = self.a.add_related(self.b)
        self.assertEqual(str(rel), f"{self.low.pk} <-> {self.high.pk}")


# --------------------------------------------------------------------------- #
# relationships() aggregate
# --------------------------------------------------------------------------- #
class RelationshipsTests(TestCase):
    def setUp(self):
        self.unit = make_unit("Unit")
        self.parent = make_unit("Parent", UnitType.CAMPUS)
        self.child = make_unit("Child", UnitType.SERIES)
        self.predecessor = make_unit("Predecessor", UnitType.JOURNAL)
        self.successor = make_unit("Successor", UnitType.JOURNAL)
        self.related = make_unit("Related")

        self.parent.add_child(self.unit)
        self.unit.add_child(self.child)
        UnitSuccession.objects.create(predecessor=self.predecessor, successor=self.unit)
        UnitSuccession.objects.create(predecessor=self.unit, successor=self.successor)
        self.unit.add_related(self.related)

    @staticmethod
    def as_set(rels):
        return {(r["type"], r["id"], r["label"]) for r in rels}

    def test_all_relationship_kinds_reported(self):
        self.assertEqual(
            self.as_set(self.unit.relationships()),
            {
                ("parent", self.parent.uid, "Parent"),
                ("child", self.child.uid, "Child"),
                ("predecessor", self.predecessor.uid, "Predecessor"),
                ("successor", self.successor.uid, "Successor"),
                ("related", self.related.uid, "Related"),
            },
        )

    def test_relationships_report_both_directions(self):
        self.assertIn(("child", self.unit.uid, "Unit"), self.as_set(self.parent.relationships()))
        self.assertIn(("parent", self.unit.uid, "Unit"), self.as_set(self.child.relationships()))
        self.assertIn(("successor", self.unit.uid, "Unit"), self.as_set(self.predecessor.relationships()))
        self.assertIn(("predecessor", self.unit.uid, "Unit"), self.as_set(self.successor.relationships()))
        self.assertIn(("related", self.unit.uid, "Unit"), self.as_set(self.related.relationships()))

    def test_relationships_use_public_uid_not_pk(self):
        for rel in self.unit.relationships():
            self.assertEqual(len(rel["id"]), UID_LENGTH)

    def test_unit_without_relationships(self):
        self.assertEqual(make_unit("Lonely").relationships(), [])