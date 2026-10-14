#!/usr/bin/env python3
"""Fetch an approved replication package; inspect documents and DTA headers only.

Requires pandas==3.0.2, curl and Poppler (pdfinfo/pdftotext/pdftoppm).
Never invokes Stata or any downloaded program. No observation values are decoded.
"""
import datetime
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import re
import shutil
import stat
import subprocess
import zipfile

import pandas as pd

import acquire_persona as common

HERE = Path(__file__).resolve().parent
LOCAL = HERE / "local_artifacts/credit"
DOI = "10.7910/DVN/II4HDS"
DATASET_VERSION = "1.2"
# Sizes and MD5 are publisher metadata, queried from the dataset version API.
FILES = (
    ("advertising_content.zip", 2670403, 1227326, "613e34553b7635c831ac77905cab93a7"),
    ("readme.pdf", 2668660, 413507, "e9357381872cd1949548e4b1f057aa60"),
)


class TrackedBuffer(io.BytesIO):
    def __init__(self, payload):
        super().__init__(payload)
        self.max_read_end = 0

    def read(self, size=-1):
        if size < 0:
            raise RuntimeError("Unbounded metadata reads are forbidden")
        block = super().read(size)
        self.max_read_end = max(self.max_read_end, self.tell())
        return block

    def readinto(self, buffer):
        raise RuntimeError("Record-buffer reads are forbidden")


class NoRowsReader(pd.io.stata.StataReader):
    def read(self, *args, **kwargs):
        raise RuntimeError("Observation decoding is forbidden in metadata review")


def stata_metadata(payload):
    if pd.__version__ != "3.0.2":
        raise RuntimeError("Expected pandas 3.0.2; review a version change first")
    buffer = TrackedBuffer(payload)
    with NoRowsReader(buffer) as reader:
        labels = reader.variable_labels()
        data_start = reader._data_location
        if buffer.max_read_end > data_start or reader._lines_read != 0:
            raise RuntimeError("Reader crossed the observation boundary")
        return {"header_rows": reader._nobs, "header_variables": reader._nvar,
                "format_version": reader._format_version,
                "variables": [{"name": name, "label": labels[name], "format": fmt}
                              for name, fmt in zip(reader._varlist, reader._fmtlist)],
                "data_start_byte": data_start, "max_metadata_read_end": buffer.max_read_end,
                "observations_decoded": 0,
                "boundary_check": "metadata reads end at or before the record section"}


def preflight():
    review = json.loads((HERE / "local_artifacts/credit_review.json").read_text())
    if review.get("passed") is not True:
        raise RuntimeError("Passing review required")
    for name in ("inspect_credit_metadata.py", "test_credit_metadata.py", "acquire_persona.py"):
        actual = hashlib.sha256((HERE / name).read_bytes()).hexdigest()
        if review["reviewed_files"].get(name) != actual:
            raise RuntimeError(f"Reviewed file changed: {name}")
        relative = str((HERE / name).relative_to(common.ROOT))
        common.git("ls-files", "--error-unmatch", relative)
        common.git("diff", "--exit-code", "HEAD", "--", relative)
    head = common.git("rev-parse", "HEAD")
    if head != common.git("rev-parse", "@{upstream}"):
        raise RuntimeError("Push code before execution")
    if pd.__version__ != "3.0.2":
        raise RuntimeError("Expected pandas 3.0.2")
    for tool in ("curl", "pdfinfo", "pdftotext", "pdftoppm"):
        if shutil.which(tool) is None:
            raise RuntimeError(f"Required tool unavailable: {tool}")
    common.safe_path(common.ROOT, str(LOCAL.relative_to(common.ROOT)))
    LOCAL.mkdir(parents=True, exist_ok=True)
    return head


def acquire():
    receipt = []
    for name, identifier, size, md5 in FILES:
        target = common.safe_path(LOCAL, "raw/" + name)
        part = common.safe_path(LOCAL, "raw/" + name + ".part")
        target.parent.mkdir(parents=True, exist_ok=True)
        url = f"https://dataverse.harvard.edu/api/access/datafile/{identifier}"
        if not target.exists():
            command = ["curl", "--silent", "--show-error", "--fail", "--location",
                       "--proto", "=https", "--proto-redir", "=https",
                       "--connect-timeout", "20", "--max-time", "120",
                       "--max-filesize", str(size), "--retry", "2", "--retry-all-errors",
                       "--output", str(part)]
            proxy = os.environ.get("PERSONA_DOWNLOAD_PROXY")
            if proxy:
                command.extend(["--proxy", proxy])
            result = subprocess.run(command + [url], capture_output=True)
            if result.returncode:
                raise RuntimeError(f"Download failed: {name}; curl exit {result.returncode}")
            candidate = part
        else:
            candidate = target
        payload = candidate.read_bytes()
        if len(payload) != size or hashlib.md5(payload).hexdigest() != md5:
            raise RuntimeError(f"Publisher checksum or size mismatch: {name}")
        if candidate == part:
            part.replace(target)
        receipt.append({"path": name, "file_id": identifier, "bytes": size,
                        "publisher_md5": md5, "sha256": hashlib.sha256(payload).hexdigest(),
                        "url": url})
    return receipt


def inspect_archive():
    assets, metadata, members = [], [], []
    with zipfile.ZipFile(LOCAL / "raw/advertising_content.zip") as archive:
        items = archive.infolist()
        if len(items) > 100 or sum(item.file_size for item in items) > 40000000:
            raise RuntimeError("Unexpected archive size")
        if len({item.filename for item in items}) != len(items):
            raise RuntimeError("Repeated archive paths")
        for item in items:
            members.append({"name": item.filename, "bytes": item.file_size})
            if item.is_dir():
                continue
            suffix = Path(item.filename).suffix.lower()
            if suffix not in {".do", ".txt", ".dta"}:
                continue
            if stat.S_ISLNK(item.external_attr >> 16):
                raise RuntimeError("Archive symlink rejected")
            target = common.safe_path(LOCAL, "package/" + item.filename)
            payload = archive.read(item)
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists() and target.read_bytes() != payload:
                raise RuntimeError("Existing extracted file differs; preserve before change")
            target.write_bytes(payload)
            asset = {"path": str(target.relative_to(LOCAL)), "bytes": len(payload),
                     "sha256": hashlib.sha256(payload).hexdigest()}
            assets.append(asset)
            if suffix == ".dta":
                metadata.append({"path": asset["path"], **stata_metadata(payload)})
    return assets, metadata, members


def inspect_pdf():
    pdf = LOCAL / "raw/readme.pdf"
    info = subprocess.check_output(["pdfinfo", str(pdf)], text=True)
    match = re.search(r"^Pages:\s+(\d+)", info, re.MULTILINE)
    if not match or not 1 <= int(match.group(1)) <= 12:
        raise RuntimeError("Unexpected PDF page count; review before rendering")
    pages = int(match.group(1))
    folder = common.safe_path(LOCAL, "pdf_review")
    folder.mkdir(parents=True, exist_ok=True)
    subprocess.run(["pdftotext", "-layout", str(pdf), str(folder / "readme.txt")], check=True)
    subprocess.run(["pdftoppm", "-f", "1", "-l", str(pages), "-scale-to", "1400",
                    "-png", str(pdf), str(folder / "readme")], check=True)
    (folder / "pdfinfo.txt").write_text(info)
    return {"pages": pages, "text": "pdf_review/readme.txt",
            "rendered_pages": [str(path.relative_to(LOCAL)) for path in sorted(folder.glob("readme-*.png"))]}


def main():
    commit = preflight()
    receipt = acquire()
    assets, metadata, members = inspect_archive()
    pdf = inspect_pdf()
    report = {"checked_at": common.now(), "code_commit": commit, "dataset_doi": DOI,
              "dataset_version": DATASET_VERSION, "python": platform.python_version(),
              "pandas": pd.__version__, "files": receipt, "archive_members": members,
              "extracted_assets": assets, "stata_metadata": metadata, "pdf": pdf,
              "scope": "documents, static code and header metadata only",
              "observations_decoded": 0, "downloaded_code_executed": False,
              "status": "metadata_complete_not_dataset_admitted"}
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    path = LOCAL / f"metadata_{stamp}.json"
    common.save_report(path, report)
    print(json.dumps({"report": str(path), "header_dimensions": [
        {"rows": item["header_rows"], "variables": item["header_variables"]} for item in metadata],
                      "pdf_pages": pdf["pages"], "observations_decoded": 0}))


if __name__ == "__main__":
    main()
