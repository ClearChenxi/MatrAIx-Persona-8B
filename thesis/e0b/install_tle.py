"""Build the pinned official tle module into a local, untracked tool directory."""

from __future__ import annotations

import argparse
import os
import subprocess
from pathlib import Path

from audit import canonical_json
from timelock_common import inspect_tle_binary


def install(destination):
    if destination.exists():
        raise ValueError("tle destination must not already exist")
    destination.mkdir(mode=0o700, parents=True)
    environment = os.environ.copy()
    environment["GOBIN"] = str(destination)
    environment["GOFLAGS"] = "-mod=readonly"
    completed = subprocess.run(
        ["go", "install", "github.com/drand/tlock/cmd/tle@v1.2.0"],
        check=False, capture_output=True, text=True, env=environment,
    )
    if completed.returncode != 0:
        raise ValueError(f"pinned tle build failed: {completed.stderr.strip()}")
    binary = destination / "tle"
    metadata = inspect_tle_binary(binary)
    metadata["go_version"] = subprocess.run(
        ["go", "version"], check=True, capture_output=True, text=True,
    ).stdout.strip()
    provenance = destination / "provenance.json"
    provenance.write_text(canonical_json(metadata), encoding="utf-8")
    os.chmod(provenance, 0o600)
    return metadata


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    print(canonical_json(install(args.destination.resolve())), end="")


if __name__ == "__main__":
    main()
