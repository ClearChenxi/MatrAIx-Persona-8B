#!/usr/bin/env python3
"""Read release metadata and Parquet footers only; never decode persona rows.

Requires pyarrow==23.0.1 in addition to the acquisition prerequisites.
SQLite is opened read-only and immutable; only quick_check and schema are read.
"""
import datetime
import hashlib
import json
import platform
import sqlite3
import sys

import pyarrow
import pyarrow.parquet as parquet

import acquire_persona as acquisition


def main():
    here = acquisition.HERE
    root = acquisition.ROOT
    dest = acquisition.DEST
    artifacts = acquisition.ARTIFACTS
    review = json.loads((artifacts / "structure_review.json").read_text())
    if review.get("passed") is not True:
        raise RuntimeError("Passing structure review required")
    for name in ("inspect_release.py", "acquire_persona.py"):
        digest = hashlib.sha256((here / name).read_bytes()).hexdigest()
        if review["reviewed_files"].get(name) != digest:
            raise RuntimeError(f"Reviewed code changed: {name}")
        relative = str((here / name).relative_to(root))
        acquisition.git("ls-files", "--error-unmatch", relative)
        acquisition.git("diff", "--exit-code", "HEAD", "--", relative)
    commit = acquisition.git("rev-parse", "HEAD")
    if commit != acquisition.git("rev-parse", "@{upstream}"):
        raise RuntimeError("Push code before running")
    if pyarrow.__version__ != "23.0.1":
        raise RuntimeError("Expected pyarrow 23.0.1; review environment change first")
    completed = []
    expected = {entry[0]: entry for entry in acquisition.FILES}
    for path in sorted(artifacts.glob("acquisition_*.json")):
        result = json.loads(path.read_text())
        if (result.get("status") == "complete" and
                result.get("revision") == acquisition.REVISION and
                result.get("destination") == str(dest)):
            entries = result.get("files", [])
            if (len(entries) == len(expected) and
                    {item["path"] for item in entries} == set(expected) and
                    all(item["upstream_digest"] == expected[item["path"]][2]
                        and item["bytes"] == expected[item["path"]][1] for item in entries)):
                completed.append(path)
    if not completed:
        raise RuntimeError("A complete byte-verified acquisition must precede inspection")
    for name, size, digest in acquisition.FILES:
        path = acquisition.safe_path(dest, name)
        if path.stat().st_size != size:
            raise RuntimeError(f"Size changed since acquisition: {name}")
        if len(digest) == 40:
            acquisition.verify(path, (name, size, digest))
    manifest = json.loads((dest / "manifest.json").read_text())
    index = json.loads((dest / "indexes/manifest.json").read_text())
    codebook = json.loads((dest / "persona_codes.schema.json").read_text())
    dimensions = codebook["columns"]
    ids = [column["id"] for column in dimensions]
    failures = []
    if len(ids) != 1290 or len(set(ids)) != len(ids):
        failures.append("Dimension count or uniqueness mismatch")
    observed = []
    common_schema = None
    index_shards = {item["path"]: item for item in index["shards"]}
    offset = 0
    for item in manifest["files"]:
        name = item["path"]
        if name not in expected or not name.startswith("data/"):
            raise RuntimeError("Unexpected shard in manifest")
        footer = parquet.ParquetFile(acquisition.safe_path(dest, name))
        rows = footer.metadata.num_rows
        columns = [{"name": field.name, "type": str(field.type),
                    "nullable": field.nullable} for field in footer.schema_arrow]
        if common_schema is None:
            common_schema = columns
        elif columns != common_schema:
            failures.append(f"Inconsistent schema: {name}")
        if rows != item["rows"]:
            failures.append(f"Manifest row count mismatch: {name}")
        mapped = index_shards.get(name, {})
        if mapped.get("rowStart") != offset or mapped.get("rowCount") != rows:
            failures.append(f"Index offset mismatch: {name}")
        observed.append({"path": name, "rows": rows,
                         "row_groups": footer.metadata.num_row_groups})
        offset += rows
    if set(index_shards) != {item["path"] for item in observed}:
        failures.append("Index shard membership mismatch")
    if offset != manifest["rows"] or offset != index["rows"]:
        failures.append("Total row count mismatch")
    db = acquisition.safe_path(dest, "indexes/postings.sqlite")
    connection = sqlite3.connect(db.as_uri() + "?mode=ro&immutable=1", uri=True)
    try:
        connection.execute("PRAGMA query_only=ON")
        quick_check = [row[0] for row in connection.execute("PRAGMA quick_check(1)")]
        tables = [{"name": row[0], "type": row[1], "definition": row[2]}
                  for row in connection.execute(
                      "SELECT name,type,sql FROM sqlite_master ORDER BY type,name")]
    finally:
        connection.close()
    if quick_check != ["ok"]:
        failures.append("SQLite quick_check failed")
    report = {
        "checked_at": acquisition.now(), "code_commit": commit,
        "revision": acquisition.REVISION, "acquisition_report": str(completed[-1]),
        "python": platform.python_version(), "pyarrow": pyarrow.__version__,
        "sqlite": sqlite3.sqlite_version, "status": "pass" if not failures else "fail",
        "rows_from_parquet_footers": offset, "shards": observed,
        "columns": common_schema, "codebook_dimensions": len(ids),
        "max_codebook_values": max(len(column["values"]) for column in dimensions),
        "sqlite_quick_check": quick_check, "sqlite_schema": tables,
        "index_rows_and_offsets_match": not any("Index" in item or "Total" in item for item in failures),
        "failures": failures,
        "not_evaluated": ["per-source actual row counts", "attribute missingness",
                          "grounding quality", "population coverage", "index posting correctness",
                          "individual attributes", "marketing outcomes", "H8 admission"]}
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    output = artifacts / f"structure_{stamp}.json"
    acquisition.save_report(output, report)
    print(json.dumps({"status": report["status"], "rows": offset,
                      "dimensions": len(ids), "report": str(output), "failures": failures}))
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
