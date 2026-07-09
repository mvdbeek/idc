#!/usr/bin/env python3
"""Check whether a request's reference data already exists in a Galaxy data table.

The most authoritative "does this already exist?" signal is the target Galaxy's
tool data table: ``GET /api/tool_data/<table>`` (public, no API key) lists the
entries actually available there - from *any* source, including the byhand
``data.galaxyproject.org`` CVMFS - so we never rebuild or re-import data a Galaxy
already has.

Matching the request's version to a table entry is done heuristically, because
the identifying column differs per data manager (e.g. MetaPhlAn keys on ``dbkey``,
mOTUs on ``value``, SameStr on the upstream MetaPhlAn value). An entry counts as
present if any of the request's identity strings - its version, its ``params``
values, or its ``depends_on`` versions - equals any field of a row, or is the
``value`` column optionally followed by a ``-<suffix>`` (e.g. a build date).

Usage::

    python scripts/check_data_exists.py --all                       # exit 1 if any exist
    python scripts/check_data_exists.py --all --warn                # annotate, exit 0
    python scripts/check_data_exists.py data-managers/motus_db_versioned/3.1.0.yaml
"""
import argparse
import json
import sys
import urllib.request
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from request_models import (  # noqa: E402
    Request,
    data_manager_name,
    iter_request_files,
    version_id,
)

DEFAULT_GALAXY = "https://test.galaxyproject.org"


def fetch_table(galaxy_url: str, table: str) -> dict:
    """GET /api/tool_data/<table> -> {columns, fields}. Public, no key needed."""
    url = f"{galaxy_url.rstrip('/')}/api/tool_data/{table}"
    with urllib.request.urlopen(url, timeout=30) as resp:  # noqa: S310 (fixed https host)
        return json.load(resp)


def identity_strings(request: Request, version: str) -> set[str]:
    """The strings that could identify this build in a data table entry."""
    candidates = {version}
    candidates |= {str(v) for v in (request.params or {}).values()}
    candidates |= {str(v) for v in (request.depends_on or {}).values()}
    return {c for c in candidates if c}


def matching_value(table_data: dict, candidates: set[str]) -> str | None:
    """The ``value`` column of the first row matching any candidate, else None."""
    for row in table_data.get("fields", []):
        row_strings = [str(x) for x in row]
        value = row_strings[0] if row_strings else ""
        for candidate in candidates:
            if candidate in row_strings or value == candidate or value.startswith(candidate + "-"):
                return value
    return None


def entry_exists(table_data: dict, candidates: set[str]) -> bool:
    return matching_value(table_data, candidates) is not None


def resolve_existing_value(galaxy_url: str, table: str, version: str) -> str | None:
    """The data-table ``value`` for an existing entry of ``version``, else None.

    Used to reference an already-built upstream database (e.g. a MetaPhlAn DB a
    SameStr build depends on) instead of rebuilding it.
    """
    try:
        table_data = fetch_table(galaxy_url, table)
    except Exception:
        return None
    return matching_value(table_data, {version})


def request_exists(request: Request, version: str, galaxy_url: str) -> bool:
    candidates = identity_strings(request, version)
    for table in request.data_tables:
        try:
            table_data = fetch_table(galaxy_url, table)
        except Exception:
            continue  # unknown/empty table -> treat as not-present
        if entry_exists(table_data, candidates):
            return True
    return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("requests", nargs="*", help="Request file(s) to check")
    parser.add_argument("--all", action="store_true", help="Check every request under data-managers/")
    parser.add_argument("--from-file", help="Read request paths from this file (one per line)")
    parser.add_argument("--reference-galaxy", default=DEFAULT_GALAXY, help="Galaxy whose data tables to query")
    parser.add_argument("--warn", action="store_true", help="Annotate and exit 0 instead of failing")
    parser.add_argument(
        "--print-new",
        action="store_true",
        help="Print (stdout) the requests whose data does NOT exist yet; always exit 0. For build/import filtering.",
    )
    args = parser.parse_args(argv)

    if args.all:
        paths = iter_request_files()
    else:
        raw = list(args.requests)
        if args.from_file:
            raw += [ln.strip() for ln in Path(args.from_file).read_text().splitlines() if ln.strip()]
        paths = [Path(r) for r in raw if Path(r).is_file()]

    new, existing = [], []
    for path in paths:
        request = Request(**yaml.safe_load(Path(path).read_text()))
        version = version_id(Path(path))
        if request_exists(request, version, args.reference_galaxy):
            existing.append((path, data_manager_name(Path(path)), version))
        else:
            new.append(path)

    if args.print_new:
        for path in new:
            print(path)
        return 0

    for path, dm, version in existing:
        prefix = "::warning:: " if args.warn else ""
        print(f"{prefix}{dm}/{version} already exists on {args.reference_galaxy} ({path})", file=sys.stderr)

    if not existing:
        print(f"No requested reference data already exists on {args.reference_galaxy}.")
        return 0
    return 0 if args.warn else 1


if __name__ == "__main__":
    raise SystemExit(main())
