#!/usr/bin/env python3
"""Download six approved KDD98 files; do not extract or decode observations.

Python standard library and curl only. Set PERSONA_DOWNLOAD_PROXY if needed.
Size checks use official HTTP headers observed 2026-10-15. SHA-256 is recorded
locally, not presented as a publisher checksum. No validation targets fetched.
"""
import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import zipfile

import acquire_persona as common

HERE = Path(__file__).resolve().parent
DEST = HERE / "local_artifacts/kdd98"
BASE = "https://kdd.ics.uci.edu/databases/kddcup98/epsilon_mirror/"
FILES = (("readme", 4134), ("cup98doc.txt", 62956), ("cup98dic.txt", 56987),
         ("valtargt.readme", 1096), ("cup98lrn.zip", 36468735), ("cup98val.zip", 36763018))


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def main():
    review = json.loads((HERE / "local_artifacts/kdd98_download_review.json").read_text())
    if review.get("passed") is not True:
        raise RuntimeError("Passing review required")
    for name in ("download_kdd98.py", "acquire_persona.py"):
        if sha256(HERE / name) != review["reviewed_files"].get(name):
            raise RuntimeError("Reviewed code changed")
        relative = str((HERE / name).relative_to(common.ROOT))
        common.git("ls-files", "--error-unmatch", relative)
        common.git("diff", "--exit-code", "HEAD", "--", relative)
    commit = common.git("rev-parse", "HEAD")
    if commit != common.git("rev-parse", "@{upstream}"):
        raise RuntimeError("Backup code before execution")
    common.safe_path(common.ROOT, str(DEST.relative_to(common.ROOT)))
    DEST.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(DEST).free < sum(size for _, size in FILES) + 100000000:
        raise RuntimeError("Insufficient free space")
    receipt = {"started_at": common.now(), "code_commit": commit, "files": [],
               "scope": "download only; ZIP directory inspection without extraction",
               "excluded": ["valtargt.txt", "compressed .Z duplicates"],
               "publisher_checksum": "not supplied; locally computed SHA-256 recorded",
               "status": "running"}
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    report = DEST / f"download_receipt_{stamp}.json"
    common.save_report(report, receipt)
    try:
        for name, size in FILES:
            path = common.safe_path(DEST, name)
            part = common.safe_path(DEST, name + ".part")
            if not path.exists():
                command = ["curl", "--silent", "--show-error", "--fail", "--location",
                           "--proto", "=https", "--proto-redir", "=https",
                           "--connect-timeout", "20", "--max-time", "600",
                           "--retry", "3", "--retry-all-errors", "--retry-delay", "2",
                           "--max-filesize", str(size), "--output", str(part)]
                proxy = os.environ.get("PERSONA_DOWNLOAD_PROXY")
                if proxy:
                    command.extend(["--proxy", proxy])
                process = subprocess.run(command + [BASE + name], capture_output=True)
                if process.returncode:
                    raise RuntimeError(f"Download failed for {name}; curl exit={process.returncode}")
                if part.stat().st_size != size:
                    raise RuntimeError(f"Official size mismatch: {name}")
                part.replace(path)
            if path.stat().st_size != size:
                raise RuntimeError(f"Existing file size mismatch: {name}")
            item = {"filename": name, "bytes": size, "sha256": sha256(path), "url": BASE + name}
            if name.endswith(".zip"):
                with zipfile.ZipFile(path) as archive:
                    # Central directory only: no decompression, CRC or row reads.
                    item["zip_members"] = [{"name": entry.filename, "bytes": entry.file_size,
                                             "crc32": f"{entry.CRC:08x}"} for entry in archive.infolist()]
            receipt["files"].append(item)
            common.save_report(report, receipt)
            print(json.dumps({"event": "downloaded", "file": name, "bytes": size}), flush=True)
        receipt["status"] = "complete"
    except Exception as error:
        receipt["status"] = "failed"
        receipt["error"] = str(error)
        raise
    finally:
        receipt["finished_at"] = common.now()
        common.save_report(report, receipt)
    print(json.dumps({"event": "complete", "destination": str(DEST),
                      "total_bytes": sum(size for _, size in FILES), "receipt": str(report)}), flush=True)


if __name__ == "__main__":
    main()
