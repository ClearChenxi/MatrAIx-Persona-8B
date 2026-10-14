#!/usr/bin/env python3
"""Acquire an immutable public release without decoding any individual records.

Run from any directory: python3 acquire_persona.py
Optional proxy is read from PERSONA_DOWNLOAD_PROXY, never saved in artifacts.
Requires Python 3.10+, curl and Git. No third-party Python dependencies.
"""

import concurrent.futures
import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import time

REPOSITORY = "MatrAIx2026/MatrAIx_Persona_1M"
REVISION = "8b1073ab23d0c0ba0928386a041bac55e5365ddc"
BASE = f"https://huggingface.co/datasets/{REPOSITORY}/resolve/{REVISION}"
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
DEST = ROOT / "persona/datasets/matraix-persona-1m/release"
ARTIFACTS = HERE / "local_artifacts"
# (relative path, byte count, digest). Large files use LFS SHA-256;
# small files use the Git blob SHA-1 from the fixed-revision repository tree.
FILES = (
    ("README.md", 8758, "5ec3282dd27429518c3eb4b41cf384b4657bc2db"),
    ("RESULTS.md", 2562, "8e2a772b5500318cbe849bef2ff0105a25a04348"),
    ("audit.json", 14388, "91e8e102920a5c259d601c43afea09f75f77a99c"),
    ("calibration_targets.json", 1681, "3165426251ec792bd6274290073ca9fc749ace02"),
    ("manifest.json", 2692, "7033f890b80aa60252160421c2b866cda221f651"),
    ("persona_codes.schema.json", 325757, "ff54c7bcb78809f1115fd2d396adf84eaa5254d7"),
    ("indexes/manifest.json", 1574, "556b355b35b6d409d63fa1f202f9d35f1c97b2fa"),
    ("indexes/postings.sqlite", 2636185600, "a6e026c75057999dbfd8a50c276f725600c404aa2114865f29055cd0e546bd16"),
    ("data/persona-1m-0000.parquet", 1135877520, "99f7cff790a5dc7c83a1f4cdfc34bb09962ebf526fe645f7bf29965131a2dab2"),
    ("data/persona-1m-0001.parquet", 1003083203, "5677bb4338bd7093d71737fd047a5138a8ada7d95f744f1406ed3cc70c4de926"),
    ("data/persona-1m-0002.parquet", 1033556644, "845aa8588a6f0dc397a5d1923dea803a7ade905289762cbf6f6931dff59f9d27"),
    ("data/persona-1m-0003.parquet", 490983173, "b1b9177571e14bfbee867c1d430bfc30a0e16f121defe6718a9ca236fe391474"),
    ("data/persona-1m-0004.parquet", 212864655, "fc6f441f8d5a954f9aaec146b8cafc094740dfd553b17b066585aed6d5d481fa"),
    ("data/persona-1m-0005.parquet", 140200105, "27e7a517df0a579708a3b53ec3f6dd580168cf1820d9455637822bdbcee6f8b9"),
    ("data/persona-1m-0006.parquet", 37785027, "602cfb8baf198ca9cef778f5508340695e963dfcdb5e97fd2f0fc277cff39b83"),
    ("data/persona-1m-0007.parquet", 37790645, "e009eaf3c866fa3adc262fbda3410a75514227ea61383ef1be856ff6b2c5adbf"),
    ("data/persona-1m-0008.parquet", 37796239, "657b73c12b69090e35efbd60968308a619b49d0cb27d958b21cc27a55353c39d"),
    ("data/persona-1m-0009.parquet", 37792551, "a1f7453b5b10d8bc438f2bff2e92cae516e14215fc2afe766eed3f9d1e3b480c"),
)


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def git(*args):
    return subprocess.check_output(["git", "-C", str(ROOT), *args], text=True).strip()


def safe_path(root, relative):
    root = Path(root)
    path = root / relative
    if Path(relative).is_absolute() or ".." in Path(relative).parts:
        raise ValueError("Path must remain inside the chosen directory")
    for component in (path, *path.parents):
        if component.is_symlink():
            raise ValueError(f"Symlink is not permitted: {component}")
    return path


def verify(path, entry):
    name, size, expected = entry
    if path.stat().st_size != size:
        raise ValueError(f"Size mismatch: {name}")
    sha256 = hashlib.sha256()
    digest = hashlib.sha256() if len(expected) == 64 else hashlib.sha1()
    if len(expected) == 40:
        digest.update(f"blob {size}\0".encode())
    with path.open("rb") as handle:
        while block := handle.read(8 * 1024 * 1024):
            digest.update(block)
            sha256.update(block)
    if digest.hexdigest() != expected:
        raise ValueError(f"Checksum mismatch: {name}; file preserved for inspection")
    return {"path": name, "bytes": size, "sha256": sha256.hexdigest(),
            "upstream_digest": expected, "verified_at": now()}


def save_report(path, content):
    temporary = safe_path(path.parent, path.name + ".tmp")
    temporary.write_text(json.dumps(content, indent=2, ensure_ascii=False) + "\n")
    temporary.replace(path)


def preflight():
    if shutil.which("curl") is None:
        raise RuntimeError("curl is required")
    review = json.loads((ARTIFACTS / "acquisition_review.json").read_text())
    if review.get("passed") is not True or review.get("revision") != REVISION:
        raise RuntimeError("A passing review for this release is required")
    for name in ("acquire_persona.py", "test_acquire_persona.py", ".gitignore"):
        actual = hashlib.sha256((HERE / name).read_bytes()).hexdigest()
        if review["reviewed_files"].get(name) != actual:
            raise RuntimeError(f"Reviewed code changed: {name}")
        relative = str((HERE / name).relative_to(ROOT))
        git("ls-files", "--error-unmatch", relative)
        git("diff", "--exit-code", "HEAD", "--", relative)
    commit = git("rev-parse", "HEAD")
    if commit != git("rev-parse", "@{upstream}"):
        raise RuntimeError("Code commit must be pushed before execution")
    safe_path(ROOT, str(DEST.relative_to(ROOT)))
    safe_path(ROOT, str(ARTIFACTS.relative_to(ROOT)))
    DEST.mkdir(parents=True, exist_ok=True)
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    remaining = 0
    for entry in FILES:
        target = safe_path(DEST, entry[0])
        part = safe_path(DEST, entry[0] + ".part")
        if target.exists():
            verify(target, entry)
        else:
            present = part.stat().st_size if part.exists() else 0
            if present > entry[1]:
                raise ValueError(f"Oversized partial file: {entry[0]}")
            remaining += entry[1] - present
    if shutil.disk_usage(DEST).free < remaining + 2 * 1024**3:
        raise RuntimeError("Insufficient disk space including 2 GiB reserve")
    return commit


def acquire(entry):
    name, size, _ = entry
    target = safe_path(DEST, name)
    part = safe_path(DEST, name + ".part")
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        return verify(target, entry)
    if not part.exists() or part.stat().st_size < size:
        command = ["curl", "--silent", "--show-error", "--fail", "--location",
                   "--proto", "=https", "--proto-redir", "=https",
                   "--connect-timeout", "20", "--max-time", "7200",
                   "--speed-limit", "1024", "--speed-time", "60",
                   "--retry", "4", "--retry-all-errors", "--retry-delay", "3",
                   "--retry-max-time", "7200", "--max-filesize", str(size),
                   "--continue-at", "-", "--output", str(part)]
        proxy = os.environ.get("PERSONA_DOWNLOAD_PROXY")
        if proxy:
            command.extend(["--proxy", proxy])
        command.append(f"{BASE}/{name}?download=true")
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode:
            # Do not put proxy addresses, credentials or signed redirects in logs.
            raise RuntimeError(f"curl failed for {name}, exit={result.returncode}; partial retained")
    verified = verify(part, entry)
    part.replace(target)
    return verified


def main():
    commit = preflight()
    lock = safe_path(ARTIFACTS, "download.lock").open("a+")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    run_id = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    report_path = ARTIFACTS / f"acquisition_{run_id}.json"
    report = {"repository": REPOSITORY, "revision": REVISION, "code_commit": commit,
              "started_at": now(), "python": platform.python_version(),
              "platform": platform.platform(), "curl": subprocess.check_output(
                  ["curl", "--version"], text=True).splitlines()[0],
              "expected_files": len(FILES), "expected_bytes": sum(x[1] for x in FILES),
              "destination": str(DEST), "status": "running", "files": [], "errors": []}
    save_report(report_path, report)
    print(json.dumps({"event": "started", "expected_bytes": report["expected_bytes"],
                      "files": len(FILES), "code_commit": commit}), flush=True)
    # Verify small metadata first; three bounded workers then fetch the binary files.
    try:
        for entry in FILES[:7]:
            report["files"].append(acquire(entry))
            save_report(report_path, report)
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
            futures = {executor.submit(acquire, item): item for item in FILES[7:]}
            pending = set(futures)
            while pending:
                done, pending = concurrent.futures.wait(
                    pending, timeout=30, return_when=concurrent.futures.FIRST_COMPLETED)
                for future in done:
                    try:
                        item = future.result()
                        report["files"].append(item)
                        print(json.dumps({"event": "verified", "path": item["path"],
                                          "bytes": item["bytes"]}), flush=True)
                    except Exception as error:
                        report["errors"].append(str(error))
                present_bytes = 0
                for name, _, _ in FILES:
                    target = DEST / name
                    part = DEST / (name + ".part")
                    try:
                        present_bytes += (target if target.exists() else part).stat().st_size
                    except FileNotFoundError:
                        pass
                print(json.dumps({"event": "progress", "present_bytes": present_bytes,
                                  "verified_files": len(report["files"])}), flush=True)
                save_report(report_path, report)
        report["status"] = "complete" if len(report["files"]) == len(FILES) and not report["errors"] else "failed"
    except Exception as error:
        report["status"] = "failed"
        report["errors"].append(str(error))
    finally:
        report["finished_at"] = now()
        save_report(report_path, report)
        lock.close()
    print(json.dumps({"event": "finished", "status": report["status"],
                      "report": str(report_path), "errors": report["errors"]}), flush=True)
    return 0 if report["status"] == "complete" else 1


if __name__ == "__main__":
    sys.exit(main())
