"""
Management command: export a single Unit as a ROR (v2.1 schema) JSON record.

Usage:
    python manage.py export_unit 7k3m9x2q
    python manage.py export_unit westjem -o westjem.json
    python manage.py export_unit 7k3m9x2q --id-prefix https://ror.example.edu/

Notes on fields ROR requires or defines that our model doesn't have:
  * ``locations`` is emitted as [] (ROR requires at least one for its own registry).
  * ``established`` is omitted.
"""
import json
import sys

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from ...logic import DEFAULT_ID_PREFIX, unit_to_ror

from ...models import (
    Unit,
    UnitExternalIdentifier,
)


def find_unit(identifier):
    """Look a unit up by uid, falling back to any external identifier value."""
    qs = Unit.objects.prefetch_related(
        "alternate_names", "external_ids", "parents", "children", "predecessors", "successors"
    )
    unit = qs.filter(uid=identifier).first()
    if unit:
        return unit

    matches = UnitExternalIdentifier.objects.filter(value=identifier).values_list(
        "unit_id", flat=True
    ).distinct()
    ids = list(matches)
    if len(ids) > 1:
        raise CommandError(f"'{identifier}' matches several units' external identifiers.")
    if ids:
        return qs.get(pk=ids[0])
    raise CommandError(f"No unit with uid or external identifier '{identifier}'.")


class Command(BaseCommand):
    help = "Export a single unit as a ROR-format JSON record."

    def add_arguments(self, parser):
        parser.add_argument("unit", help="Unit uid, or an external identifier value (e.g. 'westjem').")
        parser.add_argument("-o", "--output", help="Write to this file instead of stdout.")
        parser.add_argument("--indent", type=int, default=2, help="JSON indent (default 2).")
        parser.add_argument(
            "--id-prefix",
            default=getattr(settings, "ROR_ID_PREFIX", DEFAULT_ID_PREFIX),
            help=f"Prefix for ids (default: settings.ROR_ID_PREFIX or {DEFAULT_ID_PREFIX}).",
        )
        parser.add_argument(
            "--include-unit-type",
            action="store_true",
            help="Add our own unit type as a non-ROR 'unit_type' key.",
        )

    def handle(self, *args, **opts):
        unit = find_unit(opts["unit"])
        record = unit_to_ror(
            unit,
            id_prefix=opts["id_prefix"],
            include_unit_type=opts["include_unit_type"],
        )
        text = json.dumps(record, indent=opts["indent"], ensure_ascii=False) + "\n"

        if opts["output"]:
            with open(opts["output"], "w", encoding="utf-8") as f:
                f.write(text)
            self.stderr.write(self.style.SUCCESS(f"Wrote {opts['output']}"))
        else:
            sys.stdout.write(text)