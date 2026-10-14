"""Create and immediately restore-check two independent timelock package replicas."""

from __future__ import annotations

import argparse
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

from audit import canonical_json, digest


def filesystem_device_id(path):
    candidate = path
    while not candidate.exists():
        if candidate.parent == candidate:
            raise ValueError(f"cannot resolve filesystem device for replica destination: {path}")
        candidate = candidate.parent
    return candidate.stat().st_dev


def create_replicas(
    seal_manifest_path, destinations, location_ids, output_manifest, device_resolver=filesystem_device_id,
):
    if len(destinations) < 2 or len(destinations) != len(location_ids):
        raise ValueError("two or more destinations and matching storage location IDs are required")
    if len(set(location_ids)) != len(location_ids) or any(not value.strip() for value in location_ids):
        raise ValueError("storage location IDs must be nonempty and distinct")
    resolved = [path.resolve() for path in destinations]
    if len(set(resolved)) != len(resolved):
        raise ValueError("replica destinations must resolve to distinct directories")
    if any(destination.exists() for destination in resolved):
        raise ValueError("replica destinations must not already exist")
    device_ids = [device_resolver(destination) for destination in resolved]
    if len(set(device_ids)) != len(device_ids):
        raise ValueError("replica destinations must be on distinct filesystem devices")
    seal = json.loads(seal_manifest_path.read_text(encoding="utf-8"))
    files = {seal_manifest_path.name: digest(seal_manifest_path)}
    files.update({record["ciphertext_file"]: record["ciphertext_sha256"] for record in seal["objects"].values()})
    source_dir = seal_manifest_path.parent.resolve()
    for name, checksum in files.items():
        source = (source_dir / name).resolve(strict=True)
        if source.parent != source_dir or digest(source) != checksum:
            raise ValueError(f"source timelock package differs from seal manifest: {name}")
    replicas = []
    for index, (destination, location_id, device_id) in enumerate(
        zip(resolved, location_ids, device_ids), start=1,
    ):
        destination.mkdir(mode=0o700, parents=True)
        if device_resolver(destination) != device_id:
            raise ValueError("replica destination filesystem device changed during creation")
        for name, checksum in files.items():
            source = (source_dir / name).resolve(strict=True)
            target = destination / name
            shutil.copyfile(source, target)
            os.chmod(target, 0o600)
            if digest(target) != checksum:
                raise ValueError(f"replica write verification failed: {name}")
        replicas.append({
            "replica_id": f"replica-{index}",
            "storage_location_id": location_id,
            "filesystem_device_id": device_id,
            "directory": str(destination),
            "verified_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "restoration_test_passed": True,
            "files": files,
        })
    manifest = {
        "schema_version": "2.0",
        "status": "two_replicas_verified",
        "timelock_seal_manifest_sha256": digest(seal_manifest_path),
        "replicas": replicas,
    }
    if output_manifest.exists():
        raise ValueError("replica manifest output must not already exist")
    output_manifest.write_text(canonical_json(manifest), encoding="utf-8")
    os.chmod(output_manifest, 0o600)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("seal_manifest", type=Path)
    parser.add_argument("output_manifest", type=Path)
    parser.add_argument("--replica", nargs=2, action="append", metavar=("DIRECTORY", "LOCATION_ID"), required=True)
    args = parser.parse_args()
    destinations = [Path(item[0]) for item in args.replica]
    locations = [item[1] for item in args.replica]
    result = create_replicas(
        args.seal_manifest.resolve(strict=True), destinations, locations, args.output_manifest.resolve(),
    )
    print(canonical_json(result), end="")


if __name__ == "__main__":
    main()
