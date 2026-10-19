"""
Management command: export a all units as a ROR (v2.1 schema) JSON.

Usage:
    python manage.py export_units
    python manage.py export_units -o all_units.json
    python manage.py export_units --id-prefix https://ror.example.edu/

"""
import json
import sys

from django.conf import settings
from django.core.management.base import BaseCommand

from ...logic import DEFAULT_ID_PREFIX, unit_to_ror
from ...models import Unit, UnitStatus

class Command(BaseCommand):
    help = "Export all units as a ROR-format JSON."

    def add_arguments(self, parser):
        parser.add_argument(
            '--export-all', 
            action='store_true', 
            help='If used export all units regardless of status else export only active'
        )
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
        if opts.get("export_all", False):
            units = Unit.objects.all()
        else:
            units = Unit.objects.filter(status=UnitStatus.ACTIVE)

        unit_results = []
        for unit in units:
            record = unit_to_ror(
                unit,
                id_prefix=opts["id_prefix"],
                include_unit_type=opts["include_unit_type"],
            )
            unit_results.append(record)

        text = json.dumps(unit_results, indent=opts["indent"], ensure_ascii=False)

        if opts["output"]:
            with open(opts["output"], "w", encoding="utf-8") as f:
                f.write(text)
            self.stderr.write(self.style.SUCCESS(f"Wrote {opts['output']}"))
        else:
            sys.stdout.write(text)