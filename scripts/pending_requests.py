#!/usr/bin/env python3
"""Filter request files down to those not yet published (pending a build).

Given candidate request paths, print (one per line) those whose (data_manager,
version) does not already appear in published.yml. Used by the Stage 2 build
workflow to skip requests whose reference data is already on CVMFS.

Usage::

    python scripts/pending_requests.py --from-file candidates.txt
    python scripts/pending_requests.py data-managers/motus_db_versioned/3.1.0.yaml
    python scripts/pending_requests.py --all
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from request_models import (  # noqa: E402
    data_manager_name,
    iter_request_files,
    load_published,
    version_id,
)


def pending(paths: list[Path], published: dict[str, list[str]]) -> list[Path]:
    result = []
    for path in paths:
        if version_id(path) not in (published.get(data_manager_name(path)) or []):
            result.append(path)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("requests", nargs="*", help="Candidate request file(s)")
    parser.add_argument("--from-file", help="Read candidate request paths from this file (one per line)")
    parser.add_argument("--all", action="store_true", help="Consider every request under data-managers/")
    args = parser.parse_args(argv)

    if args.all:
        candidates = iter_request_files()
    else:
        raw = list(args.requests)
        if args.from_file:
            raw += [line.strip() for line in Path(args.from_file).read_text().splitlines() if line.strip()]
        candidates = [Path(r) for r in raw if Path(r).is_file()]

    for path in pending(candidates, load_published()):
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
