"""
Unit data model.
"""
import secrets

from django.contrib.postgres.fields import ArrayField
from django.contrib.postgres.indexes import GinIndex
from django.db import models
from django.db.models import F, Q
from django_postgresql_dag.models import edge_factory, node_factory

# --------------------------------------------------------------------------- #
# Identifier
# --------------------------------------------------------------------------- #
UID_ALPHABET = "0123456789abcdefghjkmnpqrstvwxyz"  # Crockford base32 (no i, l, o, u)
UID_LENGTH = 8  # 32**8 ~= 1.1e12 possibilities


def generate_uid():
    return "".join(secrets.choice(UID_ALPHABET) for _ in range(UID_LENGTH))


# --------------------------------------------------------------------------- #
# Choices
# --------------------------------------------------------------------------- #
class UnitType(models.TextChoices):
    ROOT = "root", "Root"
    CAMPUS = "campus", "Campus"
    ORU = "oru", "Organized research unit"
    JOURNAL = "journal", "Journal"
    SERIES = "series", "Series"
    MONOGRAPH_SERIES = "monograph_series", "Monograph series"
    SEMINAR_SERIES = "seminar_series", "Seminar series"
    CONFERENCE_PROCEEDINGS = "conference_proceedings", "Conference proceedings"


class UnitStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    INACTIVE = "inactive", "Inactive"
    WITHDRAWN = "withdrawn", "Withdrawn"


class AlternateNameType(models.TextChoices):
    ACRONYM = "acronym", "Acronym"
    ALIAS = "alias", "Alias"
    PREVIOUS_NAME = "previous_name", "Previous name"


class ExternalIdentifierType(models.TextChoices):
    FUNDREF = "fundref", "Crossref Funder Registry"
    GRID = "grid", "GRID"
    ISNI = "isni", "ISNI"
    WIKIDATA = "wikidata", "Wikidata"
    ROR = "ror", "ROR"
    ESCHOLARSHIP = "escholarship", "eScholarship (legacy unit id)"


class UnitHierarchyEdge(edge_factory("Unit", concrete=False)):
    """parent -> child. Cycles are rejected by django-postgresql-dag."""


class Unit(node_factory(UnitHierarchyEdge)):
    uid = models.CharField(
        max_length=UID_LENGTH,
        unique=True,
        editable=False,
        default=generate_uid,
        help_text="Opaque public identifier. Never changes once assigned.",
    )
    name = models.CharField(max_length=500, help_text="Primary name (serialized as type 'label').")
    type = models.CharField(max_length=32, choices=UnitType.choices)
    status = models.CharField(
        max_length=16, choices=UnitStatus.choices, default=UnitStatus.ACTIVE
    )
    domains = ArrayField(models.CharField(max_length=255), default=list, blank=True)
    links = ArrayField(models.URLField(max_length=500), default=list, blank=True)

    # Non-hierarchical relationships (one row per relationship, see through models).
    successors = models.ManyToManyField(
        "self",
        through="UnitSuccession",
        through_fields=("predecessor", "successor"),
        symmetrical=False,
        related_name="predecessors",
        blank=True,
    )

    created = models.DateTimeField(auto_now_add=True)
    modified = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        indexes = [
            GinIndex(fields=["domains"], name="unit_domains_gin"),
            models.Index(fields=["type", "status"], name="unit_type_status_idx"),
        ]

    def __str__(self):
        return f"{self.name} ({self.uid})"

    def save(self, *args, **kwargs):
        if self._state.adding:
            while Unit.objects.filter(uid=self.uid).exists():
                self.uid = generate_uid()
        else:
            stored = Unit.objects.filter(pk=self.pk).values_list("uid", flat=True).first()
            if stored and stored != self.uid:
                raise ValueError("Unit.uid is immutable once assigned.")
        self.domains = sorted({d.strip().lower() for d in self.domains if d and d.strip()})
        super().save(*args, **kwargs)

    def related_units(self):
        """Symmetric 'related' units (the relation is stored once, in canonical order)."""
        return Unit.objects.filter(
            Q(relations_as_b__unit_a=self) | Q(relations_as_a__unit_b=self)
        ).distinct()

    def add_related(self, other):
        return UnitRelation.objects.get_or_create(**UnitRelation.canonical(self, other))[0]

    def names(self):
        """ROR-style names list: the label first, then alternate names."""
        out = [{"value": self.name, "types": ["label"]}]
        out += [
            {"value": n.value, "types": [n.type]} for n in self.alternate_names.all()
        ]
        return out

    def relationships(self):
        """Both directions of every relationship, e.g. a child also reports its parent."""
        def rel(kind, units):
            return [{"type": kind, "id": u.uid, "label": u.name} for u in units]

        return (
            rel("parent", self.parents.all())
            + rel("child", self.children.all())
            + rel("predecessor", self.predecessors.all())
            + rel("successor", self.successors.all())
            + rel("related", self.related_units())
        )


# --------------------------------------------------------------------------- #
# Multi-valued attributes
# --------------------------------------------------------------------------- #
class UnitAlternateName(models.Model):
    unit = models.ForeignKey(Unit, on_delete=models.CASCADE, related_name="alternate_names")
    type = models.CharField(max_length=16, choices=AlternateNameType.choices)
    value = models.CharField(max_length=500)

    class Meta:
        ordering = ["type", "value"]
        constraints = [
            models.UniqueConstraint(
                fields=["unit", "type", "value"], name="unit_alt_name_unique"
            )
        ]

    def __str__(self):
        return f"{self.get_type_display()}: {self.value}"


class UnitExternalIdentifier(models.Model):
    unit = models.ForeignKey(Unit, on_delete=models.CASCADE, related_name="external_ids")
    type = models.CharField(max_length=16, choices=ExternalIdentifierType.choices)
    value = models.CharField(max_length=255)

    class Meta:
        ordering = ["type", "value"]
        constraints = [
            # An identifier identifies exactly one unit.
            models.UniqueConstraint(fields=["type", "value"], name="unit_ext_id_unique")
        ]

    def __str__(self):
        return f"{self.type}:{self.value}"


# --------------------------------------------------------------------------- #
# Non-hierarchical relationships
# --------------------------------------------------------------------------- #
class UnitSuccession(models.Model):
    """predecessor -> successor (e.g. a journal that was renamed or merged)."""

    predecessor = models.ForeignKey(
        Unit, on_delete=models.CASCADE, related_name="succession_links_out"
    )
    successor = models.ForeignKey(
        Unit, on_delete=models.CASCADE, related_name="succession_links_in"
    )
    effective_date = models.DateField(null=True, blank=True)
    note = models.TextField(blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["predecessor", "successor"], name="unit_succession_unique"
            ),
            models.CheckConstraint(
                condition=~Q(predecessor=F("successor")), name="unit_succession_not_self"
            ),
        ]

    def __str__(self):
        return f"{self.predecessor_id} -> {self.successor_id}"


class UnitRelation(models.Model):
    """Symmetric 'related' link, stored once with unit_a.pk < unit_b.pk."""

    unit_a = models.ForeignKey(Unit, on_delete=models.CASCADE, related_name="relations_as_a")
    unit_b = models.ForeignKey(Unit, on_delete=models.CASCADE, related_name="relations_as_b")

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["unit_a", "unit_b"], name="unit_relation_unique"),
            models.CheckConstraint(
                condition=Q(unit_a__lt=F("unit_b")), name="unit_relation_canonical_order"
            ),
        ]

    @staticmethod
    def canonical(x, y):
        """Return (unit_a=..., unit_b=...) kwargs in canonical order."""
        if x.pk == y.pk:
            raise ValueError("A unit cannot be related to itself.")
        a, b = (x, y) if x.pk < y.pk else (y, x)
        return {"unit_a": a, "unit_b": b}

    def save(self, *args, **kwargs):
        if self.unit_a_id and self.unit_b_id and self.unit_a_id > self.unit_b_id:
            self.unit_a_id, self.unit_b_id = self.unit_b_id, self.unit_a_id
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.unit_a_id} <-> {self.unit_b_id}"