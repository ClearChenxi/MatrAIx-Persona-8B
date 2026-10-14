#!/usr/bin/env python3
"""Inventory legacy categories for 14 approved fields; draft, never apply, maps.

Only source and sparse overrides are read. Unknown free-text values are suppressed;
visible categories must come from public schema/crosswalk literals or age-band syntax.
No external outcomes, target matching, individual output, or downloaded-code execution.
"""
import ast
import collections
import datetime
import hashlib
import json
from pathlib import Path
import platform
import re
import unicodedata

import pyarrow as pa
import pyarrow.parquet as pq

import acquire_persona as common
import audit_persona_availability as availability

HERE = Path(__file__).resolve().parent
SCHEMA_REFERENCES = (
    "persona/schema/dimensions.json",
    "persona/curation/existing_data/wiki_collab/collab_kit/sample/dimensions.json",
)
CROSSWALKS = (
    "persona/curation/existing_data/scripts/crosswalks/gss.py",
    "persona/curation/existing_data/scripts/crosswalks/prism.py",
    "persona/human_extraction/scripts/render_prism_profiles.py",
)
SOURCE_CODE = CROSSWALKS + (
    "persona/curation/existing_data/scripts/crosswalk_engine.py",
    "persona/post_process/unified_dataset/schema.py",
    "persona/post_process/unified_dataset/materialize.py",
)


def normalize(value):
    text = unicodedata.normalize("NFKC", str(value)).strip().casefold()
    text = text.translate(str.maketrans({"–": "-", "—": "-", "−": "-", "’": "'", "‘": "'"}))
    text = re.sub(r"\s*-\s*", "-", text)
    text = re.sub(r"(?<=[a-z])-(?=[a-z])", " ", text)
    return " ".join(text.split())


def age_band_token(value):
    text = normalize(value)
    older = re.fullmatch(r"(\d{1,3})\s*(?:years?\s*)?(?:or older|and older|and over)", text)
    if older and 0 <= int(older[1]) <= 130:
        return older[1] + "+"
    text = re.sub(r"\s+years?(?:\s+old)?$", "", text)
    younger = re.fullmatch(r"(?:under|below|less than)\s*(\d{1,3})", text)
    if younger and 0 < int(younger[1]) <= 130:
        return "<" + younger[1]
    match = re.fullmatch(r"(\d{1,3})-(\d{1,3})", text)
    if match and 0 <= int(match[1]) <= int(match[2]) <= 130:
        return text
    match = re.fullmatch(r"(\d{1,3})\+", text)
    if match and 0 <= int(match[1]) <= 130:
        return text
    return None


def categorize(field, value, current, visible):
    """Return a category-level proposal, not a transformed persona value."""
    if value == "" or not str(value).strip():
        return {"kind": "blank", "value": "[blank]", "target": None}
    if availability.unavailable(value):
        return {"kind": "explicit_unavailable", "value": availability_marker(value), "target": None}
    if value in current:
        return {"kind": "already_current", "value": value, "target": value}
    candidates = [label for label in current if normalize(label) == normalize(value)]
    if len(candidates) == 1:
        # A recognized category after punctuation normalization is safe to display.
        return {"kind": "format_alias_candidate", "value": value, "target": candidates[0]}
    if field == "age_bracket":
        token = age_band_token(value)
        if token:
            candidates = [label for label in current if age_band_token(label) == token]
            if len(candidates) == 1:
                return {"kind": "age_label_alias_candidate", "value": value, "target": candidates[0]}
            if token == "65+":
                return {"kind": "coarse_union_requires_confirmation", "value": value,
                        "target": None, "possible_common_category": "65+",
                        "current_members": ["65-74", "75-84", "85+"]}
            if token == "<18":
                return {"kind": "coarse_union_requires_confirmation", "value": value,
                        "target": None, "possible_common_category": "Under 18",
                        "current_members": ["Under 5", "5-12", "13-17"]}
            return {"kind": "age_band_requires_review", "value": value, "target": None}
    if normalize(value) in visible:
        return {"kind": "public_category_requires_review", "value": value, "target": None}
    return {"kind": "unlisted_value_suppressed", "value": "[unlisted value suppressed]", "target": None}


def availability_marker(value):
    return " ".join(str(value).strip().casefold().replace("_", " ").split())


def literal_crosswalk_maps(path):
    """Inspect literal dictionaries with AST; do not import the source module."""
    tree = ast.parse(path.read_text())
    output = {}
    for statement in tree.body:
        if not isinstance(statement, ast.Assign) or not any(
                isinstance(name, ast.Name) and name.id == "CROSSWALK" for name in statement.targets):
            continue
        if not isinstance(statement.value, ast.Dict):
            continue
        for key, node in zip(statement.value.keys, statement.value.values):
            if not isinstance(key, ast.Constant) or key.value not in availability.FIELDS:
                continue
            mapping = None
            if isinstance(node, ast.Tuple) and len(node.elts) == 2:
                mapping = node.elts[1]
            elif isinstance(node, ast.Dict):
                for subkey, subvalue in zip(node.keys, node.values):
                    if isinstance(subkey, ast.Constant) and subkey.value == "map":
                        mapping = subvalue
            if isinstance(mapping, ast.Dict):
                try:
                    output[key.value] = ast.literal_eval(mapping)
                except (ValueError, TypeError):
                    continue
    return output


def visible_categories(codebook):
    allowed = {field: {normalize(value) for value in codebook[field]} for field in availability.FIELDS}
    for relative in SCHEMA_REFERENCES:
        data = json.loads((common.ROOT / relative).read_text())
        dimensions = data if isinstance(data, list) else data["dimensions"]
        for item in dimensions:
            if item["id"] in allowed:
                allowed[item["id"]].update(normalize(value) for value in item["values"])
    for relative in CROSSWALKS:
        for field, mapping in literal_crosswalk_maps(common.ROOT / relative).items():
            for key, value in mapping.items():
                if isinstance(key, str):
                    allowed[field].add(normalize(key))
                if isinstance(value, str):
                    allowed[field].add(normalize(value))
    return allowed


def preflight():
    review = json.loads((HERE / "local_artifacts/mapping_review.json").read_text())
    if review.get("passed") is not True or review.get("apply_mapping") is not False:
        raise RuntimeError("Passing draft-only review required")
    if pa.__version__ != "23.0.1" or availability.np.__version__ != "2.4.4":
        raise RuntimeError("Expected pyarrow 23.0.1 and numpy 2.4.4")
    for name in ("draft_field_mapping.py", "test_field_mapping.py", "audit_persona_availability.py", "acquire_persona.py"):
        if hashlib.sha256((HERE / name).read_bytes()).hexdigest() != review["reviewed_files"].get(name):
            raise RuntimeError("Reviewed code changed")
        relative = str((HERE / name).relative_to(common.ROOT))
        common.git("ls-files", "--error-unmatch", relative)
        common.git("diff", "--exit-code", "HEAD", "--", relative)
    head = common.git("rev-parse", "HEAD")
    if head != common.git("rev-parse", "@{upstream}"):
        raise RuntimeError("Push code before execution")
    for relative in dict.fromkeys(SCHEMA_REFERENCES + SOURCE_CODE):
        common.git("ls-files", "--error-unmatch", relative)
        common.git("diff", "--exit-code", "HEAD", "--", relative)
    return head


def main():
    commit = preflight()
    entries = {item[0]: item for item in common.FILES}
    for name in ("manifest.json", "persona_codes.schema.json"):
        common.verify(common.safe_path(common.DEST, name), entries[name])
    manifest = json.loads((common.DEST / "manifest.json").read_text())
    schema = json.loads((common.DEST / "persona_codes.schema.json").read_text())
    positions = {item["id"]: index for index, item in enumerate(schema["columns"])}
    codebook = {item["id"]: item["values"] for item in schema["columns"] if item["id"] in availability.FIELDS}
    visible = visible_categories(codebook)
    indices = [positions[field] for field in availability.FIELDS]
    field_lookup = {positions[field]: field for field in availability.FIELDS}
    counters = {source: {field: collections.Counter() for field in availability.FIELDS} for source in availability.SOURCES}
    source_rows = collections.Counter()
    for item in manifest["files"]:
        path = common.safe_path(common.DEST, item["path"])
        common.verify(path, entries[item["path"]])
        for batch in pq.ParquetFile(path).iter_batches(batch_size=2048, columns=["source", "attribute_overrides"], use_threads=False):
            sources = batch.column("source").to_pylist()
            if any(source not in availability.SOURCES for source in sources):
                raise RuntimeError("Unexpected source")
            source_rows.update(sources)
            selected, parents = availability.selected_structs(batch.column("attribute_overrides"), indices)
            final_overrides = {}
            for parent, override in zip(parents, selected.to_pylist()):
                if override["value"] is not None:
                    final_overrides[(int(parent), override["field_index"])] = override["value"]
            for (parent, index), value in final_overrides.items():
                field = field_lookup[index]
                proposal = categorize(field, value, codebook[field], visible[field])
                counters[sources[parent]][field][json.dumps(proposal, ensure_ascii=False, sort_keys=True)] += 1
        print(json.dumps({"event": "shard_complete", "rows": sum(source_rows.values())}), flush=True)
    if dict(source_rows) != manifest["sources"]:
        raise RuntimeError("Source counts differ from frozen manifest")
    rows = []
    totals = {source: {} for source in availability.SOURCES}
    for source, fields in counters.items():
        for field, counts in fields.items():
            kinds = collections.Counter()
            for encoded, count in sorted(counts.items()):
                proposal = json.loads(encoded)
                kinds[proposal["kind"]] += count
                rows.append({"source": source, "field": field, "count": count, **proposal})
            totals[source][field] = dict(sorted(kinds.items()))
    snapshots = [{"path": relative, "sha256": hashlib.sha256((common.ROOT / relative).read_bytes()).hexdigest()}
                 for relative in dict.fromkeys(SCHEMA_REFERENCES + SOURCE_CODE)]
    report = {"checked_at": common.now(), "code_commit": commit, "release_revision": common.REVISION,
              "python": platform.python_version(), "pyarrow": pa.__version__, "numpy": availability.np.__version__,
              "fields": list(availability.FIELDS), "source_rows": dict(source_rows),
              "read_columns": ["source", "attribute_overrides"], "by_source_kind": totals,
              "category_aggregates": rows, "source_code_snapshots": snapshots,
              "mapping_applied": False, "target_population_matched": False,
              "note": "Draft proposals only. Formatting compatibility does not validate source semantics, dates or evidence.",
              "unlisted_value_policy": "Raw unknown/free-text values suppressed without hashes or personal identifiers."}
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    output = HERE / f"local_artifacts/mapping_draft_{stamp}.json"
    common.save_report(output, report)
    print(json.dumps({"event": "complete", "report": str(output), "mapping_applied": False}), flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(json.dumps({"event": "failed", "error_type": type(error).__name__, "details": "record values suppressed"}))
        raise SystemExit(1) from None
