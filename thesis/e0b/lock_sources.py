"""Verify registered source hashes and set the source files to read-only mode."""

from __future__ import annotations

import argparse
import json
import os
import stat
from pathlib import Path

from audit import canonical_json, digest


def mode(path):
    return stat.S_IMODE(path.stat(follow_symlinks=False).st_mode)


def registered_sources(run_dir):
    source_manifest = json.loads((run_dir / "public/source_manifest.json").read_text(encoding="utf-8"))
    dataset_source = json.loads((run_dir / "public/dataset_source.json").read_text(encoding="utf-8"))
    records = []
    for name, record in sorted(source_manifest.items()):
        records.append((name, Path(record["local_path"]), record["sha256"]))
    records.append(("original_archive", Path(dataset_source["local_archive"]), dataset_source["archive_sha256"]))
    return records


def lock_sources(run_dir, execute=False):
    records = registered_sources(run_dir)
    if not execute:
        return {
            "status": "plan_only",
            "registered_objects": len(records),
            "source_bytes_read_for_hashing": False,
            "permissions_changed": False,
        }
    for name, path, expected_sha256 in records:
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"registered source is missing, not regular, or a symlink: {name}")
        if digest(path) != expected_sha256:
            raise ValueError(f"registered source hash changed before read-only hardening: {name}")
    for _, path, _ in records:
        os.chmod(path, 0o444, follow_symlinks=False)
    changed = [name for name, path, _ in records if mode(path) != 0o444]
    if changed:
        raise ValueError(f"registered source did not become 0o444: {', '.join(changed)}")
    return {
        "status": "source_permissions_hardened",
        "registered_objects": len(records),
        "csv_files": len(records) - 1,
        "archive_files": 1,
        "verified_mode": "0o444",
        "content_hashes_unchanged": True,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_directory", type=Path)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    print(canonical_json(lock_sources(args.run_directory.resolve(strict=True), execute=args.execute)), end="")


if __name__ == "__main__":
    main()
