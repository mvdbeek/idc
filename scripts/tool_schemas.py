#!/usr/bin/env python3
"""Per-data-manager parameter schemas, served by the Tool Shed.

A request's ``params`` are the data manager's own tool parameters, so the only
thing that can say whether a key or value is valid is the tool itself. Tool
Shed 2 runs Galaxy's tool-state models server-side and publishes the result as
JSON Schema, keyed by the GA4GH TRS tool id (``<owner>~<repo>~<tool>``) and
the tool version - both derivable from the request's ``tool_id`` GUID::

    GET {TOOL_SHED}/api/tools/<owner>~<repo>~<tool>/versions/<version>/parameter_request_schema

``params`` in a request file take the same nested shape the schema describes
(``db_source: {db_type: motus}``), so the schema applies verbatim in editors
and in the lint; ``generate_build.py`` flattens it to Galaxy's ``a|b`` paths.

This module turns a GUID into that URL, strips the parameters a contributor
cannot set (hidden ones), validates ``params``, and embeds the schemas into
``schemas/request.schema.json`` (see ``generate_schema.py``) so editors get
completion and the lint can run without the network for GUIDs already in use.
"""
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any, Optional

TOOL_SHED = "https://toolshed.g2.bx.psu.edu"
USER_AGENT = "idc-request-lint/1.0"
REPO_ROOT = Path(__file__).resolve().parent.parent
COMMITTED_SCHEMA = REPO_ROOT / "schemas" / "request.schema.json"
# Tool Shed GUID: host/repos/<owner>/<repo>/<tool>/<version>
GUID_RE = re.compile(r"^(?P<host>[^/]+)/repos/(?P<owner>[^/]+)/(?P<repo>[^/]+)/(?P<tool>[^/]+)/(?P<version>[^/]+)$")

# GUID -> contributor params schema. Raises SchemaUnavailable rather than returning None.
SchemaResolver = Callable[[str], dict]


class SchemaUnavailable(Exception):
    """No params schema could be obtained for this tool id + version."""


def _guid_parts(guid: str) -> re.Match:
    m = GUID_RE.match(guid)
    if not m:
        raise ValueError(f"not a Tool Shed GUID: {guid!r}")
    return m


def trs_id_and_version(guid: str) -> tuple[str, str]:
    """GUID -> (``owner~repo~tool``, version), the coordinates the tools API uses."""
    m = _guid_parts(guid)
    return f"{m['owner']}~{m['repo']}~{m['tool']}", m["version"]


def schema_url(guid: str, tool_shed: str = TOOL_SHED) -> str:
    trs_id, version = trs_id_and_version(guid)
    return (
        f"{tool_shed.rstrip('/')}/api/tools/{urllib.parse.quote(trs_id, safe='~')}"
        f"/versions/{urllib.parse.quote(version, safe='+')}/parameter_request_schema"
    )


def def_name(guid: str) -> str:
    """Name of the ``$defs`` entry holding this GUID's params schema.

    No ``~`` or ``/`` in it: both are escape characters in the JSON Pointers
    that ``$ref`` uses (``~1`` reads as ``/``), so the TRS id's ``~`` cannot be
    reused here.
    """
    m = _guid_parts(guid)
    return f"params__{m['owner']}__{m['repo']}__{m['tool']}__{m['version']}"


def fetch_request_schema(guid: str, tool_shed: str = TOOL_SHED) -> dict:
    """The raw ``parameter_request_schema`` for a GUID (hidden params included)."""
    url = schema_url(guid, tool_shed)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310 (fixed https host)
            return json.load(resp)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            m = _guid_parts(guid)
            raise SchemaUnavailable(
                f"the Tool Shed has no tool {m['tool']!r} version {m['version']!r} in repository "
                f"{m['owner']}/{m['repo']} - check the GUID's owner, repository, tool id and version"
            ) from exc
        raise SchemaUnavailable(f"the Tool Shed answered HTTP {exc.code} {exc.reason} for {url}") from exc
    except Exception as exc:
        raise SchemaUnavailable(f"the Tool Shed could not be reached: {exc} ({url})") from exc


def _strip_hidden(node: Any) -> Any:
    """Recursively drop ``gx_hidden`` properties (and their ``required`` entries) from every object schema."""
    if isinstance(node, list):
        return [_strip_hidden(v) for v in node]
    if not isinstance(node, dict):
        return node
    out = {k: _strip_hidden(v) for k, v in node.items()}
    props = out.get("properties")
    if isinstance(props, dict):
        hidden = [k for k, v in props.items() if isinstance(v, dict) and v.get("gx_type") == "gx_hidden"]
        for k in hidden:
            props.pop(k)
        if hidden and isinstance(out.get("required"), list):
            out["required"] = [r for r in out["required"] if r not in hidden]
            if not out["required"]:
                out.pop("required")
    return out


def contributor_schema(schema: dict) -> dict:
    """The served schema, minus what a contributor cannot set and what must not be embedded.

    Hidden params are filled by Galaxy at run time, but the request model lists
    them as required - and one without a static default (motus's
    ``test_data_manager``) can never be satisfied from a request file. They are
    stripped at every level (conditional branches and sections included).
    ``$schema``/``$id`` go too: the schema is spliced into a parent document and
    must not establish its own resource.
    """
    out = _strip_hidden(json.loads(json.dumps(schema)))
    out.pop("$schema", None)
    out.pop("$id", None)
    return out


def _rewrite_refs(node: Any, old: str, new: str) -> Any:
    """Rewrite every local ``$ref`` (and OpenAPI ``discriminator.mapping`` target) from ``old`` to ``new``.

    Only ``#/``-pointer refs are supported. Anchors (``#name``), the bare
    ``#`` and relative/absolute URIs would silently change meaning once the
    schema is embedded, so they are refused.
    """

    def rewrite(ref: str) -> str:
        if ref.startswith(old):
            return new + ref[len(old):]
        if ref.startswith("#/") or ref.startswith(new):
            return ref
        raise ValueError(f"cannot embed schema: unsupported $ref {ref!r} (only local '#/' pointers are handled)")

    if isinstance(node, list):
        return [_rewrite_refs(v, old, new) for v in node]
    if not isinstance(node, dict):
        return node
    out = {}
    for k, v in node.items():
        if k == "$ref" and isinstance(v, str):
            out[k] = rewrite(v)
        elif k == "discriminator" and isinstance(v, dict) and isinstance(v.get("mapping"), dict):
            out[k] = {**v, "mapping": {mk: rewrite(mv) if isinstance(mv, str) else mv for mk, mv in v["mapping"].items()}}
        else:
            out[k] = _rewrite_refs(v, old, new)
    return out


def embed(schema: dict, name: str) -> dict:
    """Make a standalone params schema safe to place at ``#/$defs/<name>`` of a parent.

    Conditionals carry their own ``$defs`` referenced as ``#/$defs/When_...``;
    once spliced into the request schema those would resolve against the parent
    document, so point them at the embedded copy instead. ``unembed`` reverses it.
    """
    return _rewrite_refs(schema, "#/", f"#/$defs/{name}/")


def unembed(schema: dict, name: str) -> dict:
    return _rewrite_refs(schema, f"#/$defs/{name}/", "#/")


def flatten_params(params: dict, prefix: str = "") -> dict:
    """``{"a": {"b": 1}}`` -> ``{"a|b": 1}``: the parameter paths Galaxy workflows connect by."""
    out: dict = {}
    for key, value in params.items():
        path = f"{prefix}{key}"
        if isinstance(value, dict):
            out.update(flatten_params(value, f"{path}|"))
        else:
            out[path] = value
    return out


def _allowed_values(subschema: dict, root: dict) -> list | None:
    """The literal values a schema admits, if it is a plain enumeration (one level of local $ref resolved)."""
    if "enum" in subschema:
        return list(subschema["enum"])
    alts = subschema.get("anyOf") or subschema.get("oneOf")
    if not alts:
        return None
    consts = []
    for alt in alts:
        if isinstance(alt, dict) and isinstance(alt.get("$ref"), str) and alt["$ref"].startswith("#/"):
            node: Any = root
            for part in alt["$ref"][2:].split("/"):
                node = node.get(part.replace("~1", "/").replace("~0", "~")) if isinstance(node, dict) else None
            alt = node
        if not isinstance(alt, dict) or "const" not in alt:
            return None
        consts.append(alt["const"])
    return consts


def _describe(err, root: dict) -> str:
    """One contributor-facing sentence for a jsonschema error."""
    import jsonschema

    schema = err.schema if isinstance(err.schema, dict) else {}
    disc = schema.get("discriminator") if err.validator in ("oneOf", "anyOf") else None
    if isinstance(disc, dict) and isinstance(disc.get("mapping"), dict) and isinstance(err.instance, dict):
        # A conditional: say which branch selector is wrong, or descend into the chosen branch.
        selector = disc.get("propertyName", "")
        chosen = err.instance.get(selector)
        if chosen not in disc["mapping"]:
            return f"{selector}: {chosen!r} is not one of {sorted(disc['mapping'])}"
        # Report only what went wrong in the branch the selector picked, not in the others.
        alts = schema.get("oneOf") or schema.get("anyOf") or []
        branch = next((i for i, a in enumerate(alts) if isinstance(a, dict) and a.get("$ref") == disc["mapping"][chosen]), None)
        in_branch = [e for e in (err.context or []) if branch is None or (e.relative_schema_path and e.relative_schema_path[0] == branch)]
        best = jsonschema.exceptions.best_match(in_branch)
        if best is not None:
            inner = "|".join(str(p) for p in best.relative_path)
            return f"{inner}: {_describe(best, root)}" if inner else _describe(best, root)
    allowed = _allowed_values(schema, root)
    if err.validator in ("anyOf", "oneOf", "enum", "const") and allowed is not None:
        return f"{err.instance!r} is not one of {allowed}"
    if err.validator == "additionalProperties":
        known = sorted(schema.get("properties", {}))
        return f"{err.message}; this tool's parameters here are {known}"
    return err.message


def validate_params(schema: dict, params: dict) -> list[str]:
    """Human-readable problems with ``params`` under ``schema`` (empty == ok).

    ``params`` are validated as written (nested, like the tool form); a flat
    ``a|b`` key is reported as such rather than silently accepted.
    """
    import jsonschema  # deferred: build.yml runs generate_build.py without it

    params = params or {}
    piped = _piped_keys(params)
    if piped:
        return [f"key {k!r}: write nested parameters as mappings ({_nested_hint(k)}), not with '|'" for k in piped]
    validator = jsonschema.Draft202012Validator(schema)
    problems = []
    for err in sorted(validator.iter_errors(params), key=lambda e: tuple(str(p) for p in e.absolute_path)):
        where = "|".join(str(p) for p in err.absolute_path)
        msg = _describe(err, schema)
        problems.append(f"{where}: {msg}" if where else msg)
    return problems


def _piped_keys(params: dict) -> list[str]:
    """Keys (at any depth) that use the flat ``a|b`` spelling."""
    out = []
    for k, v in params.items():
        if "|" in str(k):
            out.append(str(k))
        if isinstance(v, dict):
            out.extend(_piped_keys(v))
    return sorted(out)


def _nested_hint(flat_key: str) -> str:
    parts = flat_key.split("|")
    return "".join(f"{p}: {{" for p in parts[:-1]) + f"{parts[-1]}: ..." + "}" * (len(parts) - 1)


class SchemaSource:
    """Resolve GUID -> contributor params schema: committed ``$defs`` first, then the Tool Shed.

    Raises SchemaUnavailable when neither can answer. Results are cached per
    process, so linting many requests for the same tool costs one fetch.
    """

    def __init__(self, committed: Optional[Path] = COMMITTED_SCHEMA, tool_shed: str = TOOL_SHED, fetch: bool = True):
        self.tool_shed = tool_shed
        self.fetch = fetch
        self.committed = committed
        self._cache: dict[str, dict] = {}
        self._defs: dict[str, Any] = {}
        if committed and committed.is_file():
            self._defs = json.loads(committed.read_text()).get("$defs", {})

    def __call__(self, guid: str) -> dict:
        if guid in self._cache:
            return self._cache[guid]
        name = def_name(guid)
        if name in self._defs:
            schema = unembed(self._defs[name], name)
        elif self.fetch:
            schema = contributor_schema(fetch_request_schema(guid, self.tool_shed))
        else:
            where = f"in {self.committed}" if self.committed else "(no committed schema file given)"
            raise SchemaUnavailable(
                f"no committed schema for {guid} {where} and fetching is disabled - "
                f"run scripts/generate_schema.py to add it"
            )
        self._cache[guid] = schema
        return schema


def main(argv: Optional[list[str]] = None) -> int:
    """Print the contributor params schema for a GUID (what the lint checks ``params`` against)."""
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        print("usage: tool_schemas.py <tool GUID>", file=sys.stderr)
        return 2
    try:
        print(json.dumps(contributor_schema(fetch_request_schema(argv[0])), indent=2))
    except (SchemaUnavailable, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
