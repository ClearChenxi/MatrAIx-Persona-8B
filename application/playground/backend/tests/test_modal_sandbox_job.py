"""Tests for Modal Sandbox web/linux workers."""

from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path

import pytest

from backend.service.modal_host_job import (
    ModalHostJobRequest,
    ModalHostJobResult,
    SdkModalDispatchRunner,
    encode_modal_sandbox_call_id,
    is_modal_sandbox_call_id,
    modal_sandbox_object_id,
    pack_job_dir,
    read_live_overlay,
)
from backend.service.modal_sandbox_job import (
    SANDBOX_RESULT_FILENAME,
    finalize_local_sandbox_result,
    parse_sandbox_ready,
    run_sandbox_worker,
    sandbox_payload_remote_path,
    wait_sandbox_with_live_pull,
    write_sandbox_live_status,
)


def _request(tmp_path: Path, config_yaml: str, live: bool = True) -> ModalHostJobRequest:
    return ModalHostJobRequest(
        job_name="demo-job",
        config_yaml=config_yaml,
        repo_root=str(tmp_path),
        jobs_dir="jobs",
        env={},
        shard_key="s0",
        live_jobs_dir=str(tmp_path / "jobs") if live else "",
    )


def test_sandbox_call_id_round_trip() -> None:
    encoded = encode_modal_sandbox_call_id("sb-abc")
    assert is_modal_sandbox_call_id(encoded)
    assert modal_sandbox_object_id(encoded) == "sb-abc"
    assert not is_modal_sandbox_call_id("fc-123")


def test_sandbox_payload_path_uses_shard() -> None:
    assert sandbox_payload_remote_path("demo-job", "s0") == "_payloads/demo-job/s0.json"


def test_write_sandbox_live_status(tmp_path: Path) -> None:
    job_dir = tmp_path / "jobs" / "demo-job"
    trial = job_dir / "web__a"
    trial.mkdir(parents=True)
    (trial / "config.json").write_text("{}\n", encoding="utf-8")
    dest = tmp_path / "live.json"
    status = write_sandbox_live_status(job_dir, dest=dest)
    assert status["web__a"] == "running"
    assert json.loads(dest.read_text(encoding="utf-8"))["web__a"] == "running"


def test_dispatch_runner_uses_sandbox_for_docker(monkeypatch, tmp_path: Path) -> None:
    spawned: list[str] = []

    class _FakeSandbox:
        def spawn(self, request):
            spawned.append("sandbox")
            return encode_modal_sandbox_call_id("sb-1"), object()

        def wait(self, request, **kwargs):
            return ModalHostJobResult(exit_code=0, volume_path="demo-job/s0")

    class _FakeHost:
        def spawn(self, request):
            spawned.append("host")
            return "fc-1", object()

        def wait(self, request, **kwargs):
            return ModalHostJobResult(exit_code=0)

    monkeypatch.setattr(
        "backend.service.modal_sandbox_job.SdkModalSandboxJobRunner",
        _FakeSandbox,
    )
    monkeypatch.setattr(
        "backend.service.modal_host_job.SdkModalHostJobRunner",
        _FakeHost,
    )
    runner = SdkModalDispatchRunner()
    docker = _request(tmp_path, "environment:\n  type: docker\n")
    call_id, _ = runner.spawn(docker)
    assert spawned == ["sandbox"]
    assert is_modal_sandbox_call_id(call_id)
    host = _request(tmp_path, "environment:\n  type: host\n")
    host_id, _ = runner.spawn(host)
    assert spawned == ["sandbox", "host"]
    assert host_id == "fc-1"


def test_wait_sandbox_applies_live_overlay(tmp_path: Path) -> None:
    dest = tmp_path / "jobs" / "demo-job"
    dest.mkdir(parents=True)

    class _Stdout:
        def __init__(self, payload: bytes) -> None:
            self._payload = payload

        def read(self) -> bytes:
            return self._payload

    class _Proc:
        def __init__(self, payload: bytes) -> None:
            self.stdout = _Stdout(payload)

        def wait(self) -> int:
            return 0

    class _Sandbox:
        def __init__(self) -> None:
            self._polls = 0

        def poll(self) -> int | None:
            self._polls += 1
            if self._polls < 2:
                return None
            return 0

        def exec(self, *command: str):
            del command
            return _Proc(b'{"web__a": "running"}\n')

    request = _request(tmp_path, "environment:\n  type: docker\n")
    code = wait_sandbox_with_live_pull(_Sandbox(), request=request, volume=None, poll_sec=0.01)
    assert code == 0
    assert read_live_overlay(dest)["web__a"] == "running"


def test_wait_sandbox_merges_finished_tree_into_local_jobs(tmp_path: Path) -> None:
    dest = tmp_path / "jobs" / "demo-job"
    dest.mkdir(parents=True)
    (dest / "compute.json").write_text('{"family": "modal"}\n', encoding="utf-8")
    remote = tmp_path / "matraix" / "jobs" / "demo-job"
    trial = remote / "web__a"
    trial.mkdir(parents=True)
    (trial / "result.json").write_text('{"ok": true}\n', encoding="utf-8")
    archive = pack_job_dir(remote)
    ready = {
        "ready": True,
        "exitCode": 0,
        "bytes": len(archive),
        "sha256": hashlib.sha256(archive).hexdigest(),
        "hasTrials": True,
    }

    class _Stdout:
        def __init__(self, payload: bytes) -> None:
            self._payload = payload

        def read(self) -> bytes:
            return self._payload

    class _Proc:
        def __init__(self, payload: bytes) -> None:
            self.stdout = _Stdout(payload)

        def wait(self) -> int:
            return 0

    class _Sandbox:
        def __init__(self) -> None:
            self.acked = False

        def poll(self) -> int | None:
            return 0 if self.acked else None

        def exec(self, *command: str, **kwargs):
            del kwargs
            joined = " ".join(command)
            if "matraix-ready.json" in joined:
                return _Proc(json.dumps(ready).encode("utf-8"))
            if "matraix-artifacts.tgz" in joined:
                return _Proc(archive)
            if "matraix-pulled" in joined:
                self.acked = True
                return _Proc(b"")
            return _Proc(b'{"web__a": "done"}\n')

    request = _request(tmp_path, "environment:\n  type: docker\n")
    code = wait_sandbox_with_live_pull(_Sandbox(), request=request, volume=None, poll_sec=0.01)
    assert code == 0
    assert (dest / "web__a" / "result.json").read_text(encoding="utf-8") == '{"ok": true}\n'
    assert '"family": "modal"' in (dest / "compute.json").read_text(encoding="utf-8")


def test_parse_sandbox_ready_ignores_live_status() -> None:
    assert parse_sandbox_ready(b'{"web__a": "running"}\n') is None
    loaded = parse_sandbox_ready(b'{"ready": true, "bytes": 12, "sha256": "abc"}\n')
    assert loaded is not None
    assert loaded["bytes"] == 12


def test_finalize_local_sandbox_result_fail_closed(tmp_path: Path) -> None:
    dest = tmp_path / "jobs" / "demo-job"
    dest.mkdir(parents=True)
    result = finalize_local_sandbox_result(
        dest=dest,
        exit_code=0,
        archive=b"",
        error=None,
        job_name="demo-job",
    )
    assert result.exit_code == 1
    assert result.volume_path is None
    assert "trial results" in (result.error or "")


def test_sandbox_wait_fail_closed_without_local_trials(tmp_path: Path, monkeypatch) -> None:
    from backend.service.modal_sandbox_job import SdkModalSandboxJobRunner

    class _Stdout:
        def read(self) -> bytes:
            return b""

    class _Proc:
        stdout = _Stdout()

        def wait(self) -> int:
            return 0

    class _Sandbox:
        def poll(self) -> int:
            return 0

        def exec(self, *command: str, **kwargs):
            del command, kwargs
            return _Proc()

    dest = tmp_path / "jobs" / "demo-job"
    dest.mkdir(parents=True)
    runner = SdkModalSandboxJobRunner()
    monkeypatch.setattr(runner, "_jobs_volume", lambda: None)
    result = runner.wait(
        _request(tmp_path, "environment:\n  type: docker\n"),
        call=_Sandbox(),
        call_id="sandbox:sb-1",
    )
    assert result.exit_code == 1
    assert result.volume_path is None
    assert "trial results" in (result.error or "")


def test_spawn_many_uploads_all_payloads_in_one_batch(monkeypatch, tmp_path: Path) -> None:
    from backend.service import modal_sandbox_job as msj
    from backend.service.modal_sandbox_job import SdkModalSandboxJobRunner

    monkeypatch.setenv("MATRIX_MODAL_SANDBOX_CREATE_RATE", "50")
    msj._create_rate_next = 0.0

    class _Batch:
        def __init__(self, volume: "_Volume") -> None:
            self.volume = volume
            self.files: list[str] = []

        def __enter__(self) -> "_Batch":
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:
            del exc_type, exc, tb
            self.volume.batches.append(list(self.files))
            return False

        def put_file(self, local: str, remote: str) -> None:
            del local
            self.files.append(remote)

    class _Volume:
        def __init__(self) -> None:
            self.batches: list[list[str]] = []

        def batch_upload(self, force: bool = False):
            del force
            return _Batch(self)

        def with_mount_options(self, **kwargs):
            del kwargs
            return self

    class _Sandbox:
        def __init__(self, object_id: str) -> None:
            self.object_id = object_id

    class _Modal:
        class Sandbox:
            created: list[str] = []
            _lock = threading.Lock()

            @classmethod
            def create(cls, *args, **kwargs):
                del args, kwargs
                with cls._lock:
                    name = "sb-{}".format(len(cls.created) + 1)
                    cls.created.append(name)
                return _Sandbox(name)

    volume = _Volume()
    monkeypatch.setattr(
        "backend.service.modal_sandbox_job.modal_credentials_configured",
        lambda: True,
    )
    runner = SdkModalSandboxJobRunner()
    monkeypatch.setattr(
        runner,
        "_runtime",
        lambda request: {
            "modal": _Modal,
            "app": object(),
            "image": object(),
            "jobs_volume": volume,
            "docker_volume": volume,
            "secrets": [],
            "worker_env": {},
        },
    )
    requests = [
        _request(tmp_path, "environment:\n  type: docker\n"),
        ModalHostJobRequest(
            job_name="demo-job",
            config_yaml="environment:\n  type: docker\n",
            repo_root=str(tmp_path),
            jobs_dir="jobs",
            env={},
            shard_key="s1",
            live_jobs_dir=str(tmp_path / "jobs"),
        ),
    ]
    spawned = runner.spawn_many(requests)
    assert len(spawned) == 2
    assert len(volume.batches) == 1
    assert volume.batches[0] == ["_payloads/demo-job/s0.json", "_payloads/demo-job/s1.json"]
    assert _Modal.Sandbox.created == ["sb-1", "sb-2"]


def test_sandbox_env_keeps_remote_pythonpath(tmp_path: Path) -> None:
    from backend.service.modal_sandbox_job import (
        SANDBOX_PAYLOAD_ENV,
        SdkModalSandboxJobRunner,
    )

    runner = SdkModalSandboxJobRunner()
    request = ModalHostJobRequest(
        job_name="demo-job",
        config_yaml="environment:\n  type: docker\n",
        repo_root=str(tmp_path),
        jobs_dir="jobs",
        env={
            "PYTHONPATH": "/Users/me/local/application/playground",
            "MATRIX_REPO_ROOT": "/Users/me/local",
        },
        shard_key="s0",
    )
    env = runner._sandbox_env(
        {
            "worker_env": {
                "PYTHONPATH": "/matraix/application/playground:/matraix",
                "MATRIX_REPO_ROOT": "/matraix",
            }
        },
        request,
    )
    assert env["PYTHONPATH"].startswith("/matraix/")
    assert env["MATRIX_REPO_ROOT"] == "/matraix"
    assert env["MATRIX_DOCKER_HOST_NETWORK"] == "1"
    assert env[SANDBOX_PAYLOAD_ENV] == "/modal-jobs/_payloads/demo-job/s0.json"


def test_reuse_built_sandbox_image_uses_from_id() -> None:
    from backend.service.modal_sandbox_job import reuse_built_sandbox_image

    class _Image:
        def __init__(self, object_id: str) -> None:
            self.object_id = object_id

        @staticmethod
        def from_id(image_id: str) -> "_Image":
            return _Image("from:" + image_id)

        def build(self, app: object) -> "_Image":
            del app
            return _Image("im-built")

    class _Modal:
        Image = _Image

    reused = reuse_built_sandbox_image(_Modal, object(), _Image("im-local"))
    assert reused.object_id == "from:im-built"


def test_sandbox_create_concurrency_env(monkeypatch) -> None:
    from backend.service.modal_sandbox_job import sandbox_create_concurrency

    monkeypatch.delenv("MATRIX_MODAL_SANDBOX_CREATE_CONCURRENCY", raising=False)
    assert sandbox_create_concurrency() == 8
    monkeypatch.setenv("MATRIX_MODAL_SANDBOX_CREATE_CONCURRENCY", "128")
    assert sandbox_create_concurrency() == 128


def test_sandbox_create_rate_spaces_calls(monkeypatch) -> None:
    from backend.service import modal_sandbox_job as msj

    monkeypatch.setenv("MATRIX_MODAL_SANDBOX_CREATE_RATE", "5")
    clock = {"t": 100.0}
    sleeps: list[float] = []
    monkeypatch.setattr(msj.time, "monotonic", lambda: clock["t"])

    def _sleep(sec: float) -> None:
        sleeps.append(sec)
        clock["t"] += sec

    monkeypatch.setattr(msj.time, "sleep", _sleep)
    msj._create_rate_next = 0.0
    msj.wait_for_sandbox_create_slot()
    msj.wait_for_sandbox_create_slot()
    assert sleeps == pytest.approx([0.2])


def test_run_sandbox_worker_writes_result(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "matraix"
    job_dir = repo / "jobs" / "demo-job"
    trial = job_dir / "web__a"
    trial.mkdir(parents=True)
    (trial / "result.json").write_text("{}\n", encoding="utf-8")
    volume_root = tmp_path / "modal-jobs"
    live_path = tmp_path / "live.json"
    monkeypatch.setenv("MATRIX_MODAL_ARTIFACT_ACK_SEC", "0")
    monkeypatch.setattr(
        "backend.service.modal_sandbox_job.MODAL_REMOTE_REPO",
        str(repo),
    )
    monkeypatch.setattr(
        "backend.service.modal_sandbox_job.sandbox_jobs_volume_root",
        lambda: volume_root,
    )
    monkeypatch.setattr(
        "backend.service.modal_sandbox_job._LIVE_PATH",
        live_path,
    )
    monkeypatch.setattr(
        "backend.service.modal_docker_prebuild.ensure_docker_daemon",
        lambda: None,
    )
    monkeypatch.setattr(
        "backend.service.modal_docker_prebuild.prepare_docker_environment_for_job",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "backend.service.modal_sandbox_job._mounted_volume",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "backend.service.modal_sandbox_job._commit_volume",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "backend.service.modal_sandbox_job._run_command",
        lambda command, *, cwd, env: 0,
    )
    payload = {
        "jobName": "demo-job",
        "configYaml": "environment:\n  type: docker\njob_name: demo-job\n",
        "env": {},
        "shardKey": "s0",
        "repoFiles": {"persona/p.yaml": "persona_id: p\n"},
    }
    code = run_sandbox_worker(payload)
    assert code == 0
    assert (repo / "persona" / "p.yaml").read_text(encoding="utf-8") == "persona_id: p\n"
    stored = json.loads(
        (volume_root / "demo-job" / "s0" / SANDBOX_RESULT_FILENAME).read_text(
            encoding="utf-8"
        )
    )
    assert stored["exitCode"] == 0
    assert (volume_root / "demo-job" / "s0" / "web__a" / "result.json").is_file()
    assert json.loads(live_path.read_text(encoding="utf-8"))["web__a"] == "done"
