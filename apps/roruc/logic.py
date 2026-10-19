from urllib.parse import urlparse

SCHEMA_VERSION = "2.1"
DEFAULT_ID_PREFIX = "https://ror.org/"

def link_type(url):
    host = (urlparse(url).hostname or "").lower()
    return "wikipedia" if host == "wikipedia.org" or host.endswith(".wikipedia.org") else "website"


def unit_to_ror(unit, id_prefix=DEFAULT_ID_PREFIX, include_unit_type=False):
    """Build the ROR-format dict for ``unit``."""

    def ror_id(uid):
        return f"{id_prefix}{uid}"

    # names: label first (also the ror_display name), then alternates.
    names = [{"lang": None, "types": ["label", "ror_display"], "value": unit.name}]
    seen = {(unit.name, "label")}
    for alt in sorted(unit.alternate_names.all(), key=lambda n: (n.type, n.value)):
        if (alt.value, alt.type) in seen:
            continue
        seen.add((alt.value, alt.type))
        names.append({"lang": None, "types": [alt.type], "value": alt.value})

    # external ids grouped by type (ROR types only).
    grouped = {}
    for ext in unit.external_ids.all():
        grouped.setdefault(ext.type, []).append(ext.value)
    external_ids = [
        {
            "all": sorted(values),
            "preferred": values[0] if len(values) == 1 else None,
            "type": ext_type,
        }
        for ext_type, values in sorted(grouped.items())
    ]

    relationships = [
        {"label": r["label"], "type": r["type"], "id": ror_id(r["id"])}
        for r in unit.relationships()
    ]
    relationships.sort(key=lambda r: (r["type"], r["label"]))

    record = {
        "admin": {
            "created": {
                "date": unit.created.date().isoformat(),
                "schema_version": SCHEMA_VERSION,
            },
            "last_modified": {
                "date": unit.modified.date().isoformat(),
                "schema_version": SCHEMA_VERSION,
            },
        },
        "domains": sorted(unit.domains),
        "external_ids": external_ids,
        "id": ror_id(unit.uid),
        "links": [{"type": link_type(url), "value": url} for url in unit.links],
        "locations": [],
        "names": names,
        "relationships": relationships,
        "status": unit.status,
        "types": [unit.type],
    }
    if include_unit_type:
        record["unit_type"] = unit.type  # not part of the ROR schema
    return record
