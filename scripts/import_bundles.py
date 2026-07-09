#!/usr/bin/env python3
"""Import a build's reference-data bundles onto CVMFS (Stage 3).

Given a finished data-manager-bundle workflow invocation, this resolves every
bundle it produced (see scripts/get_bundle_urls.py) and imports each onto CVMFS
with ``galaxy-import-data-bundle`` (from galaxy-maintenance-scripts), which moves
the data under ``<cvmfs-root>/data``, appends the new ``.loc`` rows, relativizes
symlinks, and reloads the tables.

Idempotency mirrors the existing IDC importer's ``record/`` markers, generalized
to the reference-data identity: a build is skipped if
``<cvmfs-root>/record/<dm>/<version>`` already exists, and that marker is written
after a successful import.

This is meant to run inside the Jenkins CVMFS transaction (see .ci/jenkins.sh);
``--dry-run`` prints the exact commands without importing, so the wiring is
testable offline.
"""
import argparse
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from get_bundle_urls import (  # noqa: E402
    DEFAULT_BUNDLE_SUFFIX,
    bundle_dataset_ids_from_invocation,
    bundle_url,
    bundles_from_history,
)


def import_command(import_cmd: str, cvmfs_root: str, url: str) -> list[str]:
    """The galaxy-import-data-bundle invocation for one bundle URL."""
    return [
        import_cmd,
        "--tool-data-path",
        f"{cvmfs_root}/data",
        "--data-table-config-path",
        f"{cvmfs_root}/config/tool_data_table_conf.xml",
        url,
    ]


def record_marker(cvmfs_root: str, dm: str, version: str) -> Path:
    return Path(cvmfs_root) / "record" / dm / version


def _galaxy_connection(args):
    from bioblend.galaxy import GalaxyInstance

    api_key = args.galaxy_api_key or os.environ.get("EPHEMERIS_API_KEY")
    if not api_key:
        raise SystemExit("No Galaxy API key (use --galaxy-api-key or set $EPHEMERIS_API_KEY)")
    return GalaxyInstance(url=args.galaxy_url, key=api_key)


def resolve_bundles(args) -> dict[str, str]:
    """Map bundle label -> dataset id, from whichever source was given."""
    if args.invocation_json:
        import json

        with open(args.invocation_json) as fh:
            invocation = json.load(fh)
        return bundle_dataset_ids_from_invocation(invocation, suffix=args.bundle_suffix)
    if args.invocation_id:
        gi = _galaxy_connection(args)
        invocation = gi.invocations.show_invocation(args.invocation_id)
        return bundle_dataset_ids_from_invocation(invocation, suffix=args.bundle_suffix)
    # history-name fallback (the stable key shared by the build and import stages)
    return bundles_from_history(_galaxy_connection(args), args.history_name)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-g", "--galaxy-url", default="http://localhost:8080", help="Galaxy server URL")
    parser.add_argument("-a", "--galaxy-api-key", help="Galaxy API key (or set $EPHEMERIS_API_KEY)")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--invocation-json", help="Path to a saved invocation dict (offline)")
    source.add_argument("--invocation-id", help="Workflow invocation id to fetch from Galaxy")
    source.add_argument("--history-name", help="History to scan for data_manager_json bundles")
    parser.add_argument("--dm", required=True, help="Data manager name (record identity)")
    parser.add_argument("--version", required=True, help="Version being imported (record identity)")
    parser.add_argument("--cvmfs-root", default="/cvmfs/idc.galaxyproject.org", help="CVMFS repo root")
    parser.add_argument("--bundle-suffix", default=DEFAULT_BUNDLE_SUFFIX, help="Bundle output label suffix")
    parser.add_argument(
        "--import-cmd",
        default="galaxy-import-data-bundle",
        help="galaxy-import-data-bundle executable (path)",
    )
    parser.add_argument("--overwrite", action="store_true", help="Import even if a record marker exists")
    parser.add_argument("--dry-run", action="store_true", help="Print commands without importing")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)

    marker = record_marker(args.cvmfs_root, args.dm, args.version)
    if marker.exists() and not args.overwrite:
        print(f"Already imported: {args.dm}/{args.version} (record {marker} exists); skipping")
        return 0

    bundles = resolve_bundles(args)
    if not bundles:
        # Nothing to import - no build history/invocation (e.g. the build was
        # skipped because the data already exists). Not an error.
        print(f"No bundles to import for {args.dm}/{args.version}; skipping")
        return 0

    for label, dataset_id in bundles.items():
        url = bundle_url(args.galaxy_url, dataset_id)
        cmd = import_command(args.import_cmd, args.cvmfs_root, url)
        print(f"# import {label}")
        print(" ".join(cmd))
        if not args.dry_run:
            subprocess.run(cmd, check=True)

    if args.dry_run:
        print(f"# (dry-run) would record: {marker}")
        return 0

    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("\n".join(f"{label}: {ds}" for label, ds in bundles.items()) + "\n")
    print(f"Recorded import: {marker}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
