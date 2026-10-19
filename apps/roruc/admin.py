from django import forms
from django.contrib import admin, messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.forms import BaseInlineFormSet
from django.urls import reverse
from django.utils.html import format_html, format_html_join

from .models import (
    Unit,
    UnitAlternateName,
    UnitExternalIdentifier,
    UnitHierarchyEdge,
    UnitRelation,
    UnitStatus,
    UnitSuccession,
)


def unit_link(unit):
    url = reverse(f"admin:{Unit._meta.app_label}_unit_change", args=[unit.pk])
    return format_html('<a href="{}">{}</a>', url, unit.name)


# --------------------------------------------------------------------------- #
# Inlines
# --------------------------------------------------------------------------- #
class AlternateNameInline(admin.TabularInline):
    model = UnitAlternateName
    extra = 0


class ExternalIdentifierInline(admin.TabularInline):
    model = UnitExternalIdentifier
    extra = 0


class HierarchyEdgeFormSet(BaseInlineFormSet):
    """Validates hierarchy edges so mistakes show up as form errors, not a server error.

    Subclasses set ``other_field`` ("parent" or "child") and implement ``problem()``.
    """

    other_field = None

    def problem(self, unit, other):
        raise NotImplementedError

    def clean(self):
        super().clean()
        if any(self.errors):
            return
        unit = self.instance
        seen = set()
        for form in self.forms:
            data = getattr(form, "cleaned_data", None)
            if not data or data.get("DELETE"):
                continue
            other = data.get(self.other_field)
            if other is None:
                continue
            if other.pk in seen:
                form.add_error(
                    self.other_field, f"{other.name} is listed more than once."
                )
                continue
            seen.add(other.pk)
            if form.has_changed():
                message = self.problem(unit, other)
                if message:
                    form.add_error(self.other_field, message)


class ParentEdgeFormSet(HierarchyEdgeFormSet):
    other_field = "parent"

    def problem(self, unit, other):
        if unit.pk is None:
            return None  # brand-new unit: nothing it could be an ancestor of yet
        if other.pk == unit.pk:
            return "A unit can't be its own parent."
        if unit.descendants().filter(pk=other.pk).exists():
            return (
                f"{other.name} is below {unit.name} in the hierarchy, so it can't "
                "also be its parent (that would create a loop)."
            )


class ChildEdgeFormSet(HierarchyEdgeFormSet):
    other_field = "child"

    def problem(self, unit, other):
        if unit.pk is None:
            return None
        if other.pk == unit.pk:
            return "A unit can't be its own child."
        if unit.ancestors().filter(pk=other.pk).exists():
            return (
                f"{other.name} is above {unit.name} in the hierarchy, so it can't "
                "also be its child (that would create a loop)."
            )


class ParentEdgeInline(admin.TabularInline):
    """Edges where this unit is the child, i.e. its parents."""

    model = UnitHierarchyEdge
    formset = ParentEdgeFormSet
    fk_name = "child"
    fields = ("parent",)
    autocomplete_fields = ("parent",)
    extra = 0
    verbose_name = "parent"
    verbose_name_plural = "parents"


class ChildEdgeInline(admin.TabularInline):
    """Edges where this unit is the parent, i.e. its children."""

    model = UnitHierarchyEdge
    formset = ChildEdgeFormSet
    fk_name = "parent"
    fields = ("child",)
    autocomplete_fields = ("child",)
    extra = 0
    verbose_name = "child"
    verbose_name_plural = "children"


class SuccessorInline(admin.TabularInline):
    """This unit is the predecessor; rows list its successors."""

    model = UnitSuccession
    fk_name = "predecessor"
    fields = ("successor", "effective_date", "note")
    autocomplete_fields = ("successor",)
    extra = 0
    verbose_name = "successor"
    verbose_name_plural = "successors"


class PredecessorInline(admin.TabularInline):
    """This unit is the successor; rows list its predecessors."""

    model = UnitSuccession
    fk_name = "successor"
    fields = ("predecessor", "effective_date", "note")
    autocomplete_fields = ("predecessor",)
    extra = 0
    verbose_name = "predecessor"
    verbose_name_plural = "predecessors"


# --------------------------------------------------------------------------- #
# Unit
# --------------------------------------------------------------------------- #
def _set_status(status):
    def action(modeladmin, request, queryset):
        updated = queryset.update(status=status)
        modeladmin.message_user(
            request, f"{updated} unit(s) marked {status}.", messages.SUCCESS
        )

    action.short_description = f"Mark selected units as {status}"
    action.__name__ = f"mark_{status}"
    return action


@admin.register(Unit)
class UnitAdmin(admin.ModelAdmin):
    list_display = ("name", "uid", "type", "status", "parent_names", "domain_list")
    list_filter = ("type", "status")
    search_fields = (
        "name",
        "uid",
        "alternate_names__value",
        "external_ids__value",
    )
    ordering = ("name",)
    readonly_fields = ("uid", "created", "modified", "related_display")
    actions = [_set_status(s.value) for s in UnitStatus]
    inlines = [
        AlternateNameInline,
        ExternalIdentifierInline,
        ParentEdgeInline,
        ChildEdgeInline,
        PredecessorInline,
        SuccessorInline,
    ]
    fieldsets = (
        (None, {"fields": ("uid", "name", "type", "status")}),
        (
            "Web presence",
            {
                "fields": ("domains", "links"),
                "description": "Comma-separated lists. Domains are lower-cased on save.",
            },
        ),
        (
            "Related units",
            {
                "fields": ("related_display",),
                "description": "Symmetric links; add or remove them under Unit relations.",
            },
        ),
        ("Timestamps", {"classes": ("collapse",), "fields": ("created", "modified")}),
    )

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related("parents")

    def save_formset(self, request, form, formset, change):
        """Last line of defence: show model-level validation errors as a message.

        Catches problems the formset checks can't see (e.g. the same unit added as both
        a parent and a child in one submit), instead of returning a server error.
        """
        try:
            with transaction.atomic():  # roll back just this formset
                super().save_formset(request, form, formset, change)
        except ValidationError as e:
            self.message_user(
                request,
                f"Couldn't save {formset.model._meta.verbose_name_plural}: "
                + " ".join(e.messages),
                messages.ERROR,
            )

    # -- list columns ------------------------------------------------------ #
    @admin.display(description="Parents")
    def parent_names(self, obj):
        return ", ".join(p.name for p in obj.parents.all()) or "—"

    @admin.display(description="Domains")
    def domain_list(self, obj):
        return ", ".join(obj.domains) or "—"

    # -- detail ------------------------------------------------------------ #
    @admin.display(description="Related units")
    def related_display(self, obj):
        if not obj or not obj.pk:
            return "—"
        units = list(obj.related_units())
        if not units:
            return "—"
        return format_html_join(", ", "{}", ((unit_link(u),) for u in units))

    # -- search ------------------------------------------------------------ #
    def get_search_results(self, request, queryset, search_term):
        """Also match exact domains (ArrayField doesn't support icontains search)."""
        results, may_have_duplicates = super().get_search_results(
            request, queryset, search_term
        )
        term = search_term.strip().lower()
        if not term:
            return results, may_have_duplicates
        # pk__in avoids duplicate rows from the joins in the default search.
        combined = queryset.filter(
            Q(pk__in=results.values("pk")) | Q(domains__contains=[term])
        )
        return combined, False


# --------------------------------------------------------------------------- #
# Symmetric "related" links
# --------------------------------------------------------------------------- #
class UnitRelationForm(forms.ModelForm):
    class Meta:
        model = UnitRelation
        fields = ("unit_a", "unit_b")
        labels = {"unit_a": "Unit", "unit_b": "Related unit"}
        help_texts = {"unit_b": "Order doesn't matter; the pair is stored once."}

    def clean(self):
        cleaned = super().clean()
        a, b = cleaned.get("unit_a"), cleaned.get("unit_b")
        if a and b:
            if a.pk == b.pk:
                raise ValidationError("A unit cannot be related to itself.")
            # Put the pair in canonical order *before* model constraint validation.
            if a.pk > b.pk:
                cleaned["unit_a"], cleaned["unit_b"] = b, a
        return cleaned


@admin.register(UnitRelation)
class UnitRelationAdmin(admin.ModelAdmin):
    form = UnitRelationForm
    list_display = ("unit_a", "unit_b")
    list_select_related = ("unit_a", "unit_b")
    autocomplete_fields = ("unit_a", "unit_b")
    search_fields = ("unit_a__name", "unit_b__name")
