"""Offline tests for the IDC reference-data pipeline scripts.

These exercise the pure, network-free logic: request validation, build-workflow
generation (+ gxformat2 validation), bundle-URL resolution from an invocation,
and the CVMFS import command assembly / record idempotency. No Galaxy or
toolshed access is required.
"""
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = REPO_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import check_data_exists as cde  # noqa: E402
import generate_build as gb  # noqa: E402
import get_bundle_urls as gburls  # noqa: E402
import import_bundles as imp  # noqa: E402
import pending_requests as pr  # noqa: E402
import request_models as rm  # noqa: E402

SEEDS = {
    "metaphlan": REPO_ROOT / "data-managers/metaphlan_database_versioned/mpa_vJan21_CHOCOPhlAnSGB_202103.yaml",
    "motus": REPO_ROOT / "data-managers/motus_db_versioned/3.1.0.yaml",
    "samestr": REPO_ROOT / "data-managers/samestr_db/marker_db_mpa_vJan21.yaml",
}


# --------------------------------------------------------------------------- #
# request_models
# --------------------------------------------------------------------------- #
def test_seed_requests_lint_clean():
    published: dict = {}
    for path in SEEDS.values():
        assert rm.lint_file(path, published) == [], path


def test_tool_id_must_be_version_pinned():
    with pytest.raises(Exception):
        rm.Request(tool_id="toolshed.g2.bx.psu.edu/repos/iuc/repo/tool", data_tables=["t"])
    with pytest.raises(Exception):
        rm.Request(tool_id="testtoolshed.g2.bx.psu.edu/repos/iuc/repo/tool/1.0", data_tables=["t"])
    # 6-part (version-pinned) is accepted
    rm.Request(tool_id="toolshed.g2.bx.psu.edu/repos/iuc/repo/tool/1.0", data_tables=["t"])


def test_request_rejects_unimplemented_checksum_field():
    with pytest.raises(Exception):
        rm.Request(
            tool_id="toolshed.g2.bx.psu.edu/repos/iuc/repo/tool/1.0",
            data_tables=["t"],
            checksum="sha256:abc",
        )


def test_lint_rejects_dir_table_mismatch(tmp_path):
    p = rm.DATA_MANAGERS_DIR / "motus_db_versioned" / "_probe.yaml"
    p.write_text("tool_id: toolshed.g2.bx.psu.edu/repos/iuc/a/b/1\ndata_tables: [other]\n")
    try:
        errors = rm.lint_file(p, {})
    finally:
        p.unlink()
    assert any("directory name" in e for e in errors)


def test_lint_rejects_already_published():
    errors = rm.lint_file(SEEDS["motus"], {"motus_db_versioned": ["3.1.0"]})
    assert any("already published" in e for e in errors)


# --------------------------------------------------------------------------- #
# generate_build
# --------------------------------------------------------------------------- #
def test_standalone_build_single_bundle_step():
    request, dm, version = gb.load_request(SEEDS["motus"])
    workflow, job = gb.build(request, dm, version)
    assert list(workflow["steps"]) == ["motus_db_versioned"]
    step = workflow["steps"]["motus_db_versioned"]
    assert step["tool_state"]["__data_manager_mode"] == "bundle"
    assert workflow["outputs"] == {"motus_db_versioned_bundle": {"outputSource": "motus_db_versioned/out_file"}}
    assert job == {"version": "3.1.0", "db_value": "db_from_2026-04-27T094930Z"}
    gb.validate_workflow(workflow)  # gxformat2 strict + native + lint


def test_chained_build_wires_upstream_bundle():
    request, dm, version = gb.load_request(SEEDS["samestr"])
    workflow, _ = gb.build(request, dm, version)
    assert list(workflow["steps"]) == ["metaphlan_database_versioned", "samestr_db"]
    samestr = workflow["steps"]["samestr_db"]
    # structural selector baked, database wired from the metaphlan step's bundle
    assert samestr["tool_state"]["db_source"]["db_type"] == "metaphlan"
    assert samestr["in"]["db_source|database"]["source"] == "metaphlan_database_versioned/out_file"
    # both bundles exposed as workflow outputs
    assert set(workflow["outputs"]) == {"metaphlan_database_versioned_bundle", "samestr_db_bundle"}
    gb.validate_workflow(workflow)


def test_chained_build_references_existing_upstream(monkeypatch):
    # When the upstream metaphlan already exists, reference it instead of rebuilding.
    monkeypatch.setattr(cde, "resolve_existing_value", lambda url, table, version: "mpa_vJan21_CHOCOPhlAnSGB_202103-04042023")
    request, dm, version = gb.load_request(SEEDS["samestr"])
    workflow, job = gb.build(request, dm, version, reference_galaxy="https://test.galaxyproject.org")
    # single step (samestr only) - no metaphlan build step
    assert list(workflow["steps"]) == ["samestr_db"]
    samestr = workflow["steps"]["samestr_db"]
    assert samestr["tool_state"]["db_source"]["db_type"] == "metaphlan"
    # database wired from a workflow input carrying the existing table value
    assert samestr["in"]["db_source|database"]["source"] == "db_source_database"
    assert job["db_source_database"] == "mpa_vJan21_CHOCOPhlAnSGB_202103-04042023"
    assert set(workflow["outputs"]) == {"samestr_db_bundle"}
    gb.validate_workflow(workflow)


def test_validate_rejects_broken_connection():
    workflow = {
        "class": "GalaxyWorkflow",
        "inputs": {},
        "outputs": {"b": {"outputSource": "s/out_file"}},
        "steps": {
            "s": {
                "tool_id": "toolshed.g2.bx.psu.edu/repos/iuc/r/t/1",
                "tool_version": "1",
                "tool_state": {"__data_manager_mode": "bundle"},
                "in": {"x": {"source": "nonexistent"}},
            }
        },
    }
    with pytest.raises(ValueError):
        gb.validate_workflow(workflow)


# --------------------------------------------------------------------------- #
# get_bundle_urls
# --------------------------------------------------------------------------- #
STANDALONE_INV = {"outputs": {"motus_db_versioned_bundle": {"id": "ds1", "src": "hda"}}}
CHAIN_INV = {
    "outputs": {
        "metaphlan_database_versioned_bundle": {"id": "dsMETA", "src": "hda"},
        "samestr_db_bundle": {"id": "dsSAM", "src": "hda"},
        "some_report": {"id": "dsREP", "src": "hda"},
    }
}


def test_resolve_standalone_bundle():
    assert gburls.bundle_dataset_ids_from_invocation(STANDALONE_INV) == {"motus_db_versioned_bundle": "ds1"}


def test_resolve_chain_returns_both_bundles_excluding_non_bundle():
    result = gburls.bundle_dataset_ids_from_invocation(CHAIN_INV)
    assert result == {"metaphlan_database_versioned_bundle": "dsMETA", "samestr_db_bundle": "dsSAM"}


def test_resolve_empty_suffix_takes_all_hda_outputs():
    assert set(gburls.bundle_dataset_ids_from_invocation(CHAIN_INV, suffix="")) == {
        "metaphlan_database_versioned_bundle",
        "samestr_db_bundle",
        "some_report",
    }


def test_bundle_url_format_and_trailing_slash():
    assert (
        gburls.bundle_url("https://test.galaxyproject.org/", "dsX")
        == "https://test.galaxyproject.org/api/datasets/dsX/display?to_ext=data_manager_json"
    )


# --------------------------------------------------------------------------- #
# import_bundles
# --------------------------------------------------------------------------- #
def test_import_command_assembly():
    cmd = imp.import_command("galaxy-import-data-bundle", "/cvmfs/idc.galaxyproject.org", "http://u/bundle")
    assert cmd == [
        "galaxy-import-data-bundle",
        "--tool-data-path",
        "/cvmfs/idc.galaxyproject.org/data",
        "--data-table-config-path",
        "/cvmfs/idc.galaxyproject.org/config/tool_data_table_conf.xml",
        "http://u/bundle",
    ]


def test_pending_requests_filters_published():
    paths = list(SEEDS.values())
    published = {"motus_db_versioned": ["3.1.0"]}
    remaining = pr.pending(paths, published)
    names = {p.parent.name for p in remaining}
    assert "motus_db_versioned" not in names  # already published -> filtered out
    assert {"metaphlan_database_versioned", "samestr_db"} <= names


# --------------------------------------------------------------------------- #
# check_data_exists
# --------------------------------------------------------------------------- #
_META_TABLE = {
    "columns": ["value", "name", "dbkey", "path", "db_version"],
    "fields": [["mpa_vJan21_CHOCOPhlAnSGB_202103-04042023", "n", "mpa_vJan21_CHOCOPhlAnSGB_202103", "/p", "SGB"]],
}
_MOTUS_TABLE = {"columns": ["value", "version", "name", "path"], "fields": [["3.1.0", "3.1.0", "n", "/p"]]}


def test_entry_exists_matches_exact_field_and_value_prefix():
    assert cde.entry_exists(_META_TABLE, {"mpa_vJan21_CHOCOPhlAnSGB_202103"})  # dbkey exact + value prefix
    assert not cde.entry_exists(_META_TABLE, {"mpa_vOct22_CHOCOPhlAnSGB_202212"})
    assert cde.entry_exists(_MOTUS_TABLE, {"3.1.0"})  # value exact
    assert not cde.entry_exists(_MOTUS_TABLE, {"3.0.0"})
    assert not cde.entry_exists({"fields": []}, {"anything"})


def test_identity_strings_include_params_and_depends_on():
    req = rm.Request(
        tool_id="toolshed.g2.bx.psu.edu/repos/iuc/data_manager_samestr/samestr_db/1",
        data_tables=["samestr_db"],
        params={},
        depends_on={"metaphlan_database_versioned": "mpa_vJan21_CHOCOPhlAnSGB_202103"},
    )
    ids = cde.identity_strings(req, "marker_db_mpa_vJan21")
    assert "marker_db_mpa_vJan21" in ids and "mpa_vJan21_CHOCOPhlAnSGB_202103" in ids


def test_request_exists_uses_table_lookup(monkeypatch):
    req = gb.load_request(SEEDS["metaphlan"])[0]
    monkeypatch.setattr(cde, "fetch_table", lambda url, table: _META_TABLE)
    assert cde.request_exists(req, "mpa_vJan21_CHOCOPhlAnSGB_202103", "http://g")
    monkeypatch.setattr(cde, "fetch_table", lambda url, table: {"fields": []})
    assert not cde.request_exists(req, "mpa_vJan21_CHOCOPhlAnSGB_202103", "http://g")


class _FakeGi:
    """Minimal stand-in for a bioblend GalaxyInstance for history resolution."""

    def __init__(self, invocations, invocation_detail, datasets=None):
        self._invocations = invocations
        self._invocation_detail = invocation_detail
        self._datasets = datasets or []

        outer = self

        class _Histories:
            def get_histories(self, name, deleted=False):
                return [{"id": "hist1", "name": name}]

        class _Invocations:
            def get_invocations(self, history_id):
                return outer._invocations

            def show_invocation(self, invocation_id):
                return outer._invocation_detail

        class _Datasets:
            def get_datasets(self, history_id, extension, order):
                return outer._datasets

        self.histories = _Histories()
        self.invocations = _Invocations()
        self.datasets = _Datasets()


def test_history_resolution_prefers_latest_invocation():
    gi = _FakeGi(
        invocations=[
            {"id": "old", "create_time": "2026-01-01T00:00:00"},
            {"id": "new", "create_time": "2026-02-01T00:00:00"},
        ],
        invocation_detail=CHAIN_INV,  # named *_bundle outputs
    )
    result = gburls.bundles_from_history(gi, "idc-samestr_db-v1")
    # precise: exactly the two bundle outputs, not a dataset scan
    assert result == {"metaphlan_database_versioned_bundle": "dsMETA", "samestr_db_bundle": "dsSAM"}


def test_history_resolution_returns_empty_when_history_missing():
    class _NoHistory:
        class histories:
            @staticmethod
            def get_histories(name, deleted=False):
                return []

    assert gburls.bundles_from_history(_NoHistory(), "idc-missing-1") == {}


def test_import_skips_gracefully_when_no_bundles(tmp_path, capsys):
    inv = tmp_path / "inv.json"
    inv.write_text('{"outputs": {}}')  # invocation with no bundle outputs
    rc = imp.main([
        "--invocation-json", str(inv),
        "--dm", "motus_db_versioned", "--version", "3.1.0",
        "--cvmfs-root", str(tmp_path),
    ])
    assert rc == 0
    assert "skipping" in capsys.readouterr().out


def test_history_resolution_falls_back_to_dataset_scan_without_invocation():
    gi = _FakeGi(
        invocations=[],
        invocation_detail={},
        datasets=[{"id": "d0"}, {"id": "d1"}],
    )
    result = gburls.bundles_from_history(gi, "idc-motus_db_versioned-3.1.0")
    assert list(result.values()) == ["d0", "d1"]


def test_import_dry_run_and_idempotency(tmp_path, capsys):
    inv = tmp_path / "inv.json"
    inv.write_text('{"outputs":{"samestr_db_bundle":{"id":"dsSAM","src":"hda"}}}')
    args = [
        "--galaxy-url", "https://test.galaxyproject.org",
        "--invocation-json", str(inv),
        "--dm", "samestr_db", "--version", "v1",
        "--cvmfs-root", str(tmp_path),
    ]
    # dry-run imports nothing and writes no marker
    assert imp.main(args + ["--dry-run"]) == 0
    assert not imp.record_marker(str(tmp_path), "samestr_db", "v1").exists()

    # write the marker directly, then a real run must skip (idempotent)
    marker = imp.record_marker(str(tmp_path), "samestr_db", "v1")
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("done\n")
    assert imp.main(args) == 0
    assert "skipping" in capsys.readouterr().out


def test_chain_import_skips_published_upstream_and_records_it(tmp_path, capsys):
    """A chained build also carries its upstream bundle. With --request, that
    bundle is skipped when the upstream request already published the database,
    and otherwise imported once with the upstream's own record marker written."""
    inv = tmp_path / "inv.json"
    inv.write_text(
        '{"outputs":{"motus_db_versioned_bundle":{"id":"dsMOTUS","src":"hda"},'
        '"samestr_db_bundle":{"id":"dsSAM","src":"hda"}}}'
    )
    req = tmp_path / "marker_db_motus_3.1.0.yaml"
    req.write_text(
        "tool_id: toolshed.g2.bx.psu.edu/repos/iuc/data_manager_samestr/samestr_db/1.2025.111+galaxy4\n"
        "data_tables: [samestr_db]\n"
        'depends_on: {motus_db_versioned: "3.1.0"}\n'
    )
    args = [
        "--galaxy-url", "https://test.galaxyproject.org",
        "--invocation-json", str(inv),
        "--dm", "samestr_db", "--version", "marker_db_motus_3.1.0",
        "--request", str(req),
        "--cvmfs-root", str(tmp_path),
        "--dry-run",
    ]
    up_marker = imp.record_marker(str(tmp_path), "motus_db_versioned", "3.1.0")

    # upstream not yet published: both bundles import, both markers would be written
    assert imp.main(args) == 0
    out = capsys.readouterr().out
    assert "# import motus_db_versioned_bundle" in out
    assert "# import samestr_db_bundle" in out
    assert f"would record: {up_marker}" in out

    # upstream already published by its own request: only samestr imports
    up_marker.parent.mkdir(parents=True, exist_ok=True)
    up_marker.write_text("done\n")
    assert imp.main(args) == 0
    out = capsys.readouterr().out
    assert "# import motus_db_versioned_bundle" not in out
    assert "# skip motus_db_versioned_bundle" in out
    assert "# import samestr_db_bundle" in out


def test_import_without_request_imports_every_bundle(tmp_path, capsys):
    """Without --request nothing is known about upstreams: behaviour unchanged."""
    inv = tmp_path / "inv.json"
    inv.write_text(
        '{"outputs":{"motus_db_versioned_bundle":{"id":"dsMOTUS","src":"hda"},'
        '"samestr_db_bundle":{"id":"dsSAM","src":"hda"}}}'
    )
    up_marker = imp.record_marker(str(tmp_path), "motus_db_versioned", "3.1.0")
    up_marker.parent.mkdir(parents=True, exist_ok=True)
    up_marker.write_text("done\n")
    assert imp.main([
        "--invocation-json", str(inv),
        "--dm", "samestr_db", "--version", "marker_db_motus_3.1.0",
        "--cvmfs-root", str(tmp_path), "--dry-run",
    ]) == 0
    out = capsys.readouterr().out
    assert "# import motus_db_versioned_bundle" in out
    assert "# import samestr_db_bundle" in out
