"""Inventory protected plaintext copies before separately authorized removal."""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from audit import canonical_json, digest
from timelock_common import (
    find_matching_plaintext, physical_plaintext_commitments, plaintext_paths,
    plaintext_scan_roots,
)


def create_inventory(run_dir, seal_manifest_path, repository, output_path):
    if output_path.exists():
        raise ValueError("plaintext inventory output must not already exist")
    manifest = json.loads(seal_manifest_path.read_text(encoding="utf-8"))
    commitments = physical_plaintext_commitments(manifest)
    authoritative = plaintext_paths(run_dir)
    authoritative_records = []
    for object_id, path in authoritative.items():
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"authoritative plaintext is missing or unsafe: {object_id}")
        record = commitments[object_id]
        if path.stat().st_size != record["bytes"] or digest(path) != record["sha256"]:
            raise ValueError(f"authoritative plaintext differs from seal commitment: {object_id}")
        authoritative_records.append({
            "object_id": object_id,
            "path": str(path.resolve(strict=True)),
            "bytes": record["bytes"],
            "sha256": record["sha256"],
        })
    roots = plaintext_scan_roots(repository, run_dir)
    matches = find_matching_plaintext(roots, commitments)
    matched = {(record["object_id"], record["path"]) for record in matches}
    for record in authoritative_records:
        if (record["object_id"], record["path"]) not in matched:
            raise ValueError(f"authoritative plaintext was omitted from inventory: {record['object_id']}")
    inventory = {
        "schema_version": "1.0",
        "status": "plaintext_inventory_complete",
        "created_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "timelock_seal_manifest_sha256": digest(seal_manifest_path),
        "scan_roots": [str(path) for path in roots],
        "authoritative_plaintext": sorted(authoritative_records, key=lambda item: item["object_id"]),
        "matching_plaintext": matches,
    }
    output_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    output_path.write_text(canonical_json(inventory), encoding="utf-8")
    os.chmod(output_path, 0o600)
    return inventory


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_directory", type=Path)
    parser.add_argument("seal_manifest", type=Path)
    parser.add_argument("output_inventory", type=Path)
    parser.add_argument("--repository", type=Path, default=Path(__file__).resolve().parents[2])
    args = parser.parse_args()
    result = create_inventory(
        args.run_directory.resolve(strict=True), args.seal_manifest.resolve(strict=True),
        args.repository.resolve(strict=True), args.output_inventory.resolve(),
    )
    print(canonical_json(result), end="")


if __name__ == "__main__":
    main()
