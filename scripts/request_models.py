#!/usr/bin/env python3
"""Schema + linter for IDC reference-data *request* files.

A contribution to the IDC is a single flat YAML file placed under::

    data-managers/<data_manager>/<version>.yaml

where ``<data_manager>`` is the name of the data manager / primary data table
(e.g. ``metaphlan_database_versioned``) and ``<version>`` is the version being
requested (the file stem, e.g. ``mpa_vJan21_CHOCOPhlAnSGB_202103``).

The file describes how to build one reference-data *bundle* on a Galaxy server
(the build itself runs a gxformat2 data-manager-bundle workflow via planemo -
see ``workflows/`` and ``scripts/generate_build.py``). This module validates
those files and is run as the Stage 1 CI lint::

    python scripts/request_models.py                 # lint everything
    python scripts/request_models.py data-managers/motus_db_versioned/3.1.0.yaml

Exit code is non-zero if any file fails validation.
"""
import sys
from pathlib import Path
from typing import Optional

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    field_validator,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_MANAGERS_DIR = REPO_ROOT / "data-managers"
PUBLISHED_PATH = REPO_ROOT / "published.yml"

# A toolshed GUID looks like:
#   toolshed.g2.bx.psu.edu/repos/<owner>/<repo>/<tool>/<version>
TOOL_ID_PREFIX = "toolshed.g2.bx.psu.edu/repos/"


class Request(BaseModel):
    """One requested reference-data version -> one bundle build."""

    model_config = ConfigDict(extra="forbid")

    # Full toolshed GUID of the data manager tool that builds this data.
    tool_id: str
    # Data table(s) the data manager populates (its bundle carries these rows).
    data_tables: list[str]
    # Tool parameters for this specific build, e.g. {"index": "mpa_vJan21_..."}.
    params: dict[str, object] = {}
    # For chained builds: maps an upstream data table name -> the upstream
    # version this build depends on. e.g. samestr depends on a metaphlan db:
    #   depends_on: {metaphlan_database_versioned: mpa_vJan21_CHOCOPhlAnSGB_202103}
    depends_on: Optional[dict[str, str]] = None

    # Human-facing provenance (unused by the build, but reviewed in the PR).
    description: Optional[str] = None
    doi: Optional[str] = None

    @field_validator("tool_id")
    @classmethod
    def _tool_id_is_a_guid(cls, v: str) -> str:
        if not v.startswith(TOOL_ID_PREFIX):
            raise ValueError(
                f"tool_id must be a production Tool Shed GUID starting with {TOOL_ID_PREFIX!r}, got: {v!r}"
            )
        # Require the full, version-pinned GUID:
        #   host/repos/owner/repo/tool/version   (6 slash-separated parts)
        # The pinned tool version is what makes a reference-data build
        # reproducible and auditable, so it is mandatory - not left to whatever
        # revision happens to be installed at build time.
        if len(v.split("/")) < 6:
            raise ValueError(
                f"tool_id must be a version-pinned GUID host/repos/owner/repo/tool/version "
                f"(the trailing tool version is required for reproducibility): {v!r}"
            )
        return v

    @field_validator("data_tables")
    @classmethod
    def _data_tables_non_empty(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("data_tables must list at least one data table")
        return v


class LintError(Exception):
    pass


def data_manager_name(path: Path) -> str:
    """The data manager identity == the parent directory name."""
    return path.parent.name


def version_id(path: Path) -> str:
    """The requested version == the file stem."""
    return path.stem


def load_published() -> dict[str, list[str]]:
    if not PUBLISHED_PATH.exists():
        return {}
    doc = yaml.safe_load(PUBLISHED_PATH.read_text()) or {}
    return doc.get("published", {}) or {}


def iter_request_files() -> list[Path]:
    if not DATA_MANAGERS_DIR.is_dir():
        return []
    return sorted(p for p in DATA_MANAGERS_DIR.rglob("*.y*ml") if p.is_file())


def lint_file(path: Path, published: dict[str, list[str]]) -> list[str]:
    """Return a list of error strings for one request file (empty == ok)."""
    errors: list[str] = []
    try:
        rel = path.relative_to(REPO_ROOT)
    except ValueError:
        rel = path

    # Structural: must be data-managers/<dm>/<version>.yaml (exactly one level deep).
    try:
        depth = path.relative_to(DATA_MANAGERS_DIR).parts
    except ValueError:
        return [f"{rel}: request files must live under data-managers/"]
    if len(depth) != 2:
        errors.append(
            f"{rel}: request files must be at data-managers/<data_manager>/<version>.yaml "
            f"(got {len(depth)} path component(s) under data-managers/)"
        )
        return errors

    dm = data_manager_name(path)
    version = version_id(path)

    try:
        doc = yaml.safe_load(path.read_text())
    except yaml.YAMLError as exc:
        return [f"{rel}: invalid YAML: {exc}"]
    if not isinstance(doc, dict):
        return [f"{rel}: top-level YAML must be a mapping"]

    try:
        req = Request(**doc)
    except Exception as exc:  # pydantic ValidationError et al.
        return [f"{rel}: schema validation failed:\n{exc}"]

    # The directory name should be one of the data tables the DM populates,
    # so the on-disk identity matches what the bundle actually writes.
    if dm not in req.data_tables:
        errors.append(
            f"{rel}: directory name {dm!r} is not in data_tables {req.data_tables} - "
            f"the folder must be named after the data manager's primary data table"
        )

    # Already published? Then this request is a no-op / duplicate.
    if version in published.get(dm, []):
        errors.append(
            f"{rel}: version {version!r} is already published for {dm!r} (see published.yml)"
        )

    # Chained builds: the upstream version must itself have a request file, so the
    # generator can build the upstream step of the workflow.
    for up_table, up_version in (req.depends_on or {}).items():
        up_path = DATA_MANAGERS_DIR / up_table / f"{up_version}.yaml"
        up_path_yml = DATA_MANAGERS_DIR / up_table / f"{up_version}.yml"
        if not up_path.exists() and not up_path_yml.exists():
            errors.append(
                f"{rel}: depends_on {up_table}=={up_version} but no request file "
                f"exists at data-managers/{up_table}/{up_version}.yaml - "
                f"add the upstream request so it can be built first"
            )

    return errors


def main(argv: Optional[list[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    published = load_published()

    if argv:
        files = [Path(a).resolve() for a in argv]
    else:
        files = iter_request_files()

    if not files:
        print("No request files found under data-managers/ - nothing to lint.")
        return 0

    all_errors: list[str] = []
    for path in files:
        errs = lint_file(path, published)
        if errs:
            all_errors.extend(errs)
        else:
            print(f"ok: {path.relative_to(REPO_ROOT)}")

    if all_errors:
        print("\n".join(["", "Lint failed:", *all_errors]), file=sys.stderr)
        return 1
    print(f"\nAll {len(files)} request file(s) valid.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
