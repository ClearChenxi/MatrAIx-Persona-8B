"""Tests for serialized Modal Volume uploads."""

from __future__ import annotations

from pathlib import Path

from backend.service.modal_volume_io import (
    batch_upload_files_retry,
    batch_upload_payloads,
    job_dir_upload_items,
    sandbox_payload_bytes,
    set_volume_upload_gate,
    volume_write_is_retryable,
)


class _Batch:
    def __init__(self, volume: "_Volume") -> None:
        self.volume = volume
        self.files: list[tuple[str, str]] = []

    def __enter__(self) -> "_Batch":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        del exc_type, tb
        if exc is None:
            self.volume.batches.append(list(self.files))
        return False

    def put_file(self, local: str, remote: str) -> None:
        self.files.append((local, remote))


class _Volume:
    def __init__(self, *, fail_times: int = 0) -> None:
        self.batches: list[list[tuple[str, str]]] = []
        self.fail_times = fail_times
        self.attempts = 0

    def batch_upload(self, force: bool = False):
        del force
        self.attempts += 1
        if self.attempts <= self.fail_times:
            raise RuntimeError("too many layers in volume. Please wait and retry.")
        return _Batch(self)


def test_volume_write_is_retryable() -> None:
    assert volume_write_is_retryable(RuntimeError("too many layers in volume"))
    assert volume_write_is_retryable(RuntimeError("Rate limit exceeded"))
    assert not volume_write_is_retryable(RuntimeError("file not found"))


def test_batch_upload_payloads_uses_one_batch() -> None:
    volume = _Volume()
    count = batch_upload_payloads(
        volume,
        [
            ("_payloads/job/s0.json", b'{"shardKey":"s0"}'),
            ("_payloads/job/s1.json", b'{"shardKey":"s1"}'),
        ],
    )
    assert count == 2
    assert len(volume.batches) == 1
    remotes = [remote for _local, remote in volume.batches[0]]
    assert remotes == ["_payloads/job/s0.json", "_payloads/job/s1.json"]


def test_batch_upload_retries_too_many_layers() -> None:
    volume = _Volume(fail_times=2)
    items = [(Path(__file__), "remote/a.py")]
    assert batch_upload_files_retry(volume, items, attempts=4) == 1
    assert volume.attempts == 3
    assert len(volume.batches) == 1


def test_job_dir_upload_items_skips_shard_rollup(tmp_path: Path) -> None:
    job = tmp_path / "job"
    trial = job / "web__a"
    trial.mkdir(parents=True)
    (trial / "result.json").write_text("{}\n", encoding="utf-8")
    (job / "result.json").write_text('{"shard": true}\n', encoding="utf-8")
    (job / "compute.json").write_text("{}\n", encoding="utf-8")
    items = job_dir_upload_items(job, "job/s0")
    remotes = [remote for _path, remote in items]
    assert "job/s0/web__a/result.json" in remotes
    assert "job/s0/result.json" not in remotes
    assert "job/s0/compute.json" not in remotes


def test_sandbox_payload_bytes_includes_shard() -> None:
    blob = sandbox_payload_bytes(
        job_name="demo",
        config_yaml="job_name: demo\n",
        env={"A": "1"},
        shard_key="s3",
        repo_files={"persona/p.yaml": "id: p\n"},
    )
    assert b'"shardKey": "s3"' in blob
    assert b'"jobName": "demo"' in blob
    assert b'"repoFiles"' in blob
    assert b"persona/p.yaml" in blob


def test_upload_gate_is_used(monkeypatch) -> None:
    del monkeypatch
    seen: list[str] = []

    def _gate(action):
        seen.append("lock")
        return action()

    set_volume_upload_gate(_gate)
    try:
        volume = _Volume()
        batch_upload_payloads(volume, [("_payloads/a.json", b"{}")])
        assert seen == ["lock"]
    finally:
        set_volume_upload_gate(None)
