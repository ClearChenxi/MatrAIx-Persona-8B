"""Run web/linux Harbor jobs in a Modal Sandbox with Docker.

Playground starts the sandbox, persists its id in ``cloud_run.json``, and
reattaches after an API restart.

Local ``jobs/`` landing is a handshake while the sandbox is still alive:
the worker stages a gzip archive, the orchestrator copies it (at most 8 at
once), then the worker is allowed to exit. Modal ``exec`` after the
entrypoint finishes is not usable. The jobs Volume stays read-only and is
not the landing path.
"""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from backend.service.modal_host_job import (
    MODAL_DOCKER_IMAGES_VOLUME_NAME,
    MODAL_HOST_APP_NAME,
    MODAL_JOBS_VOLUME_NAME,
    MODAL_REMOTE_REPO,
    ModalHostJobError,
    ModalHostJobRequest,
    ModalHostJobResult,
    apply_worker_live_status,
    attach_nested_docker_host_network,
    collect_live_status,
    collect_local_repo_files,
    collect_orchestrator_secret_env,
    encode_modal_sandbox_call_id,
    execute_host_harbor_job,
    job_dir_has_trials,
    materialize_repo_files,
    LivePublishState,
    merge_job_artifacts,
    modal_credentials_configured,
    modal_sandbox_object_id,
    modal_volume_job_name,
    pack_job_dir,
    pull_live_progress,
    publish_job_to_volume,
)
from backend.service.modal_volume_io import (
    batch_upload_payloads,
    local_artifact_pull_slot,
    sandbox_payload_bytes,
    volume_write_is_retryable,
)

SANDBOX_LIVE_PATH = "/tmp/matraix-live.json"
SANDBOX_ARTIFACT_PATH = "/tmp/matraix-artifacts.tgz"
SANDBOX_READY_PATH = "/tmp/matraix-ready.json"
SANDBOX_PULLED_PATH = "/tmp/matraix-pulled"
SANDBOX_RESULT_FILENAME = "_sandbox_result.json"
SANDBOX_PAYLOAD_ENV = "MATRIX_SANDBOX_PAYLOAD"
SANDBOX_CREATE_CONCURRENCY_ENV = "MATRIX_MODAL_SANDBOX_CREATE_CONCURRENCY"
SANDBOX_CREATE_RATE_ENV = "MATRIX_MODAL_SANDBOX_CREATE_RATE"
SANDBOX_ARTIFACT_ACK_ENV = "MATRIX_MODAL_ARTIFACT_ACK_SEC"
_SANDBOX_TIMEOUT_SEC = 2 * 60 * 60
_DEFAULT_SANDBOX_CREATE_CONCURRENCY = 8
_DEFAULT_SANDBOX_CREATE_RATE = 5.0
_DEFAULT_ARTIFACT_ACK_SEC = 2700.0
_MAX_INLINE_TAR_BYTES = 40 * 1024 * 1024
_GZIP_MAGIC = b"\x1f\x8b"
_LIVE_PATH = Path(SANDBOX_LIVE_PATH)
_create_rate_lock = threading.Lock()
_create_rate_next = 0.0
_shared_runtime: dict[str, Any] | None = None


def sandbox_jobs_volume_root() -> Path:
    return Path("/modal-jobs")


def sandbox_create_concurrency() -> int:
    """How many Modal ``Sandbox.create`` calls to run at once.

    Team-plan input concurrency (5000) is not the bottleneck. Modal caps
    ``Sandbox.create`` at 5/s; pair this with ``sandbox_create_rate``.
    After ``Image.build`` / ``Image.from_id``, each create is a ~0.2s RPC.
    """
    raw = (os.environ.get(SANDBOX_CREATE_CONCURRENCY_ENV) or "").strip()
    try:
        value = int(raw) if raw else _DEFAULT_SANDBOX_CREATE_CONCURRENCY
    except ValueError:
        value = _DEFAULT_SANDBOX_CREATE_CONCURRENCY
    return max(1, min(value, 256))


def sandbox_create_rate() -> float:
    """Modal ``Sandbox.create`` API rate limit, in calls per second."""
    raw = (os.environ.get(SANDBOX_CREATE_RATE_ENV) or "").strip()
    try:
        value = float(raw) if raw else _DEFAULT_SANDBOX_CREATE_RATE
    except ValueError:
        value = _DEFAULT_SANDBOX_CREATE_RATE
    return max(0.5, min(value, 50.0))


def wait_for_sandbox_create_slot() -> None:
    """Pace ``Sandbox.create`` so we do not trip Modal's 5/s rate limit."""
    global _create_rate_next
    interval = 1.0 / sandbox_create_rate()
    with _create_rate_lock:
        now = time.monotonic()
        wait = _create_rate_next - now
        if wait > 0:
            time.sleep(wait)
            now = time.monotonic()
        _create_rate_next = now + interval


def reuse_built_sandbox_image(modal: Any, app: Any, image: Any) -> Any:
    """Build the worker image once, then pass ``Image.from_id`` into creates."""
    build = getattr(image, "build", None)
    if not callable(build) or app is None:
        return image
    try:
        print("modal sandbox image.build (once per process)", flush=True)
        built = build(app)
        print(
            "modal sandbox image ready {}".format(
                getattr(built, "object_id", None) or ""
            ),
            flush=True,
        )
    except Exception:
        return image
    image_id = getattr(built, "object_id", None) or getattr(image, "object_id", None)
    from_id = getattr(getattr(modal, "Image", None), "from_id", None)
    if not image_id or not callable(from_id):
        return built if built is not None else image
    try:
        return from_id(str(image_id))
    except Exception:
        return built if built is not None else image


def sandbox_payload_remote_path(job_name: str, shard_key: str = "") -> str:
    return "_payloads/{}.json".format(modal_volume_job_name(job_name, shard_key))


def write_sandbox_live_status(job_dir: Path, dest: Path | None = None) -> dict[str, str]:
    status = collect_live_status(job_dir) if job_dir.is_dir() else {}
    target = dest if dest is not None else _LIVE_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(status, sort_keys=True) + "\n", encoding="utf-8")
    return status


def artifact_ack_timeout_sec() -> float:
    """How long the worker waits for the orchestrator to copy the staged tar.

    ``0`` skips the wait (tests). Default covers 1024 shards / 8 concurrent
    pulls with headroom.
    """
    raw = (os.environ.get(SANDBOX_ARTIFACT_ACK_ENV) or "").strip()
    try:
        value = float(raw) if raw else _DEFAULT_ARTIFACT_ACK_SEC
    except ValueError:
        value = _DEFAULT_ARTIFACT_ACK_SEC
    return max(0.0, value)


def stage_sandbox_artifact(
    job_dir: Path, result: ModalHostJobResult
) -> dict[str, Any]:
    """Write the Harbor tree to a gzip file the orchestrator can ``cat`` while alive."""
    payload: dict[str, Any] = {
        "ready": True,
        "exitCode": int(result.exit_code),
        "error": result.error,
        "bytes": 0,
        "sha256": "",
        "hasJobDir": job_dir.is_dir(),
        "hasTrials": job_dir_has_trials(job_dir) if job_dir.is_dir() else False,
    }
    if job_dir.is_dir():
        blob = pack_job_dir(job_dir)
        Path(SANDBOX_ARTIFACT_PATH).write_bytes(blob)
        payload["bytes"] = len(blob)
        payload["sha256"] = hashlib.sha256(blob).hexdigest()
    Path(SANDBOX_READY_PATH).write_text(json.dumps(payload) + "\n", encoding="utf-8")
    return payload


def wait_for_artifact_pull_ack() -> bool:
    timeout = artifact_ack_timeout_sec()
    if timeout <= 0:
        return True
    deadline = time.monotonic() + timeout
    pulled = Path(SANDBOX_PULLED_PATH)
    while time.monotonic() < deadline:
        if pulled.is_file():
            return True
        time.sleep(1)
    print("sandbox artifact pull ack timed out", file=sys.stderr, flush=True)
    return False


def parse_sandbox_ready(raw: bytes | str) -> dict[str, Any] | None:
    text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else raw
    try:
        loaded = json.loads(text)
    except Exception:
        return None
    if not isinstance(loaded, dict) or loaded.get("ready") is not True:
        return None
    return loaded


def read_sandbox_ready(sandbox: Any) -> dict[str, Any] | None:
    return parse_sandbox_ready(sandbox_exec_bytes(sandbox, "cat", SANDBOX_READY_PATH))


def ack_sandbox_artifact_pull(sandbox: Any) -> None:
    sandbox_exec_bytes(sandbox, "touch", SANDBOX_PULLED_PATH)


def pull_sandbox_staged_archive(
    sandbox: Any,
    *,
    request: ModalHostJobRequest,
    dest: Path,
    ready: dict[str, Any] | None = None,
) -> bytes:
    """Copy the worker-staged gzip onto the API host. Sandbox must still be running."""
    raw = sandbox_exec_bytes(sandbox, "cat", SANDBOX_ARTIFACT_PATH, text=False)
    expected = int(ready.get("bytes") or 0) if ready else 0
    digest = str(ready.get("sha256") or "") if ready else ""
    if raw and raw[:2] == _GZIP_MAGIC:
        if expected and len(raw) != expected:
            return b""
        if digest and hashlib.sha256(raw).hexdigest() != digest:
            return b""
        dest.mkdir(parents=True, exist_ok=True)
        merge_job_artifacts(raw, jobs_dir=dest.parent, job_name=request.job_name)
        return raw
    if expected > 0:
        return b""
    return pull_sandbox_job_archive(sandbox, request=request, dest=dest)


def finalize_local_sandbox_result(
    *,
    dest: Path | None,
    exit_code: int,
    archive: bytes,
    error: str | None,
    job_name: str,
) -> ModalHostJobResult:
    """Never report success unless local ``jobs/<job>/`` has trial results."""
    has_trials = dest is not None and job_dir_has_trials(dest)
    if dest is not None and not has_trials and exit_code == 0:
        exit_code = 1
        error = error or (
            "Modal Sandbox finished without local jobs/{}/ trial results".format(job_name)
        )
    artifact = archive if archive and len(archive) <= _MAX_INLINE_TAR_BYTES else None
    if has_trials:
        artifact = None
    return ModalHostJobResult(
        exit_code=exit_code,
        artifact_tar=artifact,
        error=None if exit_code == 0 else error,
        volume_path=None,
    )


def terminate_sandbox(sandbox: Any) -> None:
    method = getattr(sandbox, "terminate", None)
    if not callable(method):
        return
    try:
        method()
    except Exception:
        pass


def read_sandbox_stdio(sandbox: Any, *, limit: int = 4000) -> str:
    """Best-effort stdout/stderr after the sandbox entrypoint has finished."""
    chunks: list[str] = []
    for name in ("stderr", "stdout"):
        stream = getattr(sandbox, name, None)
        if stream is None:
            continue
        read = getattr(stream, "read", None)
        if not callable(read):
            continue
        try:
            data = read()
        except Exception:
            continue
        if isinstance(data, bytes):
            text = data.decode("utf-8", errors="replace")
        else:
            text = str(data or "")
        text = text.strip()
        if text:
            chunks.append("{}:\n{}".format(name, text[-limit:]))
    return "\n".join(chunks)


def load_sandbox_payload(path: Path | None = None) -> dict[str, Any]:
    raw_path = path or Path(
        (os.environ.get(SANDBOX_PAYLOAD_ENV) or "").strip() or "/modal-jobs/_payloads/job.json"
    )
    last_error = "payload not found"
    for _ in range(20):
        try:
            loaded = json.loads(raw_path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc)
            time.sleep(0.5)
            continue
        if isinstance(loaded, dict):
            return loaded
        last_error = "payload is not a JSON object"
        break
    raise ModalHostJobError(
        "sandbox worker could not read {} ({})".format(raw_path, last_error)
    )


def run_sandbox_worker(payload: dict[str, Any] | None = None) -> int:
    """Harbor + image cache inside the sandbox. Called as ``python -m``."""
    from backend.service.modal_docker_prebuild import ensure_docker_daemon

    ensure_docker_daemon()
    loaded = payload if payload is not None else load_sandbox_payload()
    materialize_repo_files(MODAL_REMOTE_REPO, dict(loaded.get("repoFiles") or {}))
    request = ModalHostJobRequest(
        job_name=str(loaded.get("jobName") or ""),
        config_yaml=str(loaded.get("configYaml") or ""),
        repo_root=MODAL_REMOTE_REPO,
        jobs_dir="jobs",
        env=dict(loaded.get("env") or {}),
        secret_env={},
        shard_key=str(loaded.get("shardKey") or "").strip(),
    )
    volume_name = modal_volume_job_name(request.job_name, request.shard_key)
    volume_root = sandbox_jobs_volume_root()
    live = LivePublishState()
    docker_volume = _mounted_volume(
        MODAL_DOCKER_IMAGES_VOLUME_NAME, "MATRIX_MODAL_DOCKER_IMAGES_VOLUME"
    )
    if docker_volume is not None:
        try:
            docker_volume.reload()
        except Exception:
            pass

    def _live_publish(job_dir: Path) -> None:
        write_sandbox_live_status(job_dir)
        live.tick(
            job_dir,
            volume_root=volume_root,
            job_name=volume_name,
            status_key=volume_name,
            persist_volume=False,
        )

    result = execute_host_harbor_job(
        request,
        command_runner=_run_command,
        work_root=Path(MODAL_REMOTE_REPO),
        progress=_live_publish,
    )
    if result.error:
        print(result.error, file=sys.stderr, flush=True)
    job_dir = Path(MODAL_REMOTE_REPO) / "jobs" / request.job_name
    write_sandbox_live_status(job_dir)
    staged = stage_sandbox_artifact(job_dir, result)
    print(
        "sandbox artifact staged bytes={} trials={} waiting for local pull".format(
            staged.get("bytes") or 0, staged.get("hasTrials")
        ),
        flush=True,
    )
    wait_for_artifact_pull_ack()
    volume_path = None
    if job_dir.is_dir():
        try:
            publish_job_to_volume(job_dir, volume_root=volume_root, job_name=volume_name)
            volume_path = volume_name
        except OSError:
            pass
    try:
        _write_sandbox_result(volume_root / volume_name, result, volume_path)
    except OSError:
        pass
    live.tick(
        job_dir,
        volume_root=volume_root,
        job_name=volume_name,
        status_key=volume_name,
        force=True,
        persist_volume=False,
    )
    if result.error and result.exit_code == 0:
        return 1
    return int(result.exit_code)


def wait_sandbox_with_live_pull(
    sandbox: Any,
    *,
    request: ModalHostJobRequest,
    volume: Any | None,
    poll_sec: float = 5.0,
) -> int:
    dest = _live_dest(request)
    del volume
    volume_job_name = modal_volume_job_name(request.job_name, request.shard_key)
    ready: dict[str, Any] | None = None
    pulled = False
    sleep_for = poll_sec if poll_sec > 0 else 0.5
    while True:
        if dest is not None and not pulled:
            if ready is None:
                ready = read_sandbox_ready(sandbox)
                if ready is None:
                    pull_sandbox_live(
                        sandbox,
                        dest=dest,
                        volume=None,
                        volume_job_name=volume_job_name,
                    )
            if ready is not None:
                if int(ready.get("bytes") or 0) <= 0:
                    ack_sandbox_artifact_pull(sandbox)
                    pulled = True
                else:
                    with local_artifact_pull_slot():
                        archive = pull_sandbox_staged_archive(
                            sandbox,
                            request=request,
                            dest=dest,
                            ready=ready,
                        )
                    if archive:
                        ack_sandbox_artifact_pull(sandbox)
                        pulled = True
                        print(
                            "modal sandbox artifact landed shard={} bytes={}".format(
                                request.shard_key or request.job_name,
                                len(archive),
                            ),
                            flush=True,
                        )
                    else:
                        ready = None
        code = sandbox_returncode(sandbox)
        if code is not None:
            return int(code)
        time.sleep(sleep_for)


def pull_sandbox_live(
    sandbox: Any,
    *,
    dest: Path,
    volume: Any | None,
    volume_job_name: str,
) -> None:
    raw = sandbox_exec_bytes(sandbox, "cat", SANDBOX_LIVE_PATH)
    if raw:
        apply_worker_live_status(dest, raw)
    if volume is not None:
        pull_live_progress(volume=volume, volume_job_name=volume_job_name, dest=dest)


def pull_sandbox_job_archive(
    sandbox: Any,
    *,
    request: ModalHostJobRequest,
    dest: Path,
) -> bytes:
    """Tar the remote Harbor tree while the sandbox is still running."""
    remote = "{}/jobs/{}".format(MODAL_REMOTE_REPO, request.job_name)
    quoted = shlex.quote(remote)
    raw = sandbox_exec_bytes(
        sandbox,
        "bash",
        "-lc",
        "test -d {} && tar -C {} -czf - .".format(quoted, quoted),
        text=False,
    )
    if not raw or raw[:2] != _GZIP_MAGIC:
        return b""
    dest.mkdir(parents=True, exist_ok=True)
    merge_job_artifacts(raw, jobs_dir=dest.parent, job_name=request.job_name)
    return raw


def sandbox_returncode(sandbox: Any) -> int | None:
    poll = getattr(sandbox, "poll", None)
    if callable(poll):
        try:
            code = poll()
        except Exception:
            return None
        if code is not None:
            return int(code)
        return None
    code = getattr(sandbox, "returncode", None)
    if code is None:
        return None
    return int(code)


def sandbox_exec_bytes(
    sandbox: Any,
    *command: str,
    text: bool | None = None,
) -> bytes:
    execute = getattr(sandbox, "exec", None)
    if not callable(execute):
        return b""
    kwargs: dict[str, Any] = {}
    if text is not None:
        kwargs["text"] = text
    try:
        proc = execute(*command, **kwargs)
    except TypeError:
        try:
            proc = execute(*command)
        except Exception:
            return b""
    except Exception:
        return b""
    wait = getattr(proc, "wait", None)
    if callable(wait):
        try:
            wait()
        except Exception:
            return b""
    stdout = getattr(proc, "stdout", None)
    if stdout is None:
        return b""
    read = getattr(stdout, "read", None)
    if callable(read):
        try:
            data = read()
        except Exception:
            return b""
        if isinstance(data, (bytes, bytearray)):
            return bytes(data)
        if text is False:
            return b""
        return str(data or "").encode("utf-8")
    if isinstance(stdout, (bytes, bytearray)):
        return bytes(stdout)
    return str(stdout).encode("utf-8")


def read_sandbox_result(volume: Any, volume_job_name: str) -> dict[str, Any] | None:
    path = "{}/{}".format(volume_job_name.rstrip("/"), SANDBOX_RESULT_FILENAME)
    try:
        volume.reload()
    except Exception:
        pass
    try:
        raw = b"".join(volume.read_file(path))
        loaded = json.loads(raw.decode("utf-8"))
    except Exception:
        return None
    return loaded if isinstance(loaded, dict) else None


class SdkModalSandboxJobRunner:
    """Create a Docker-capable Modal Sandbox and wait until Harbor finishes."""

    def __init__(self) -> None:
        self._runtime_cache: dict[str, Any] | None = None

    def spawn(self, request: ModalHostJobRequest) -> tuple[str, Any]:
        return self.spawn_many([request])[0]

    def warm_docker_cache(self, *, task_path: str) -> str:
        """Build the Harbor task image once and save it onto the docker Volume."""
        runtime = self._runtime(
            ModalHostJobRequest(
                job_name="_docker-warmup",
                config_yaml="environment:\n  type: docker\n",
                repo_root=".",
                jobs_dir="jobs",
                env={},
            )
        )
        env = dict(runtime.get("worker_env") or {})
        env["MATRIX_DOCKER_IMAGE_CACHE"] = "/modal-docker-images"
        env["MATRIX_DOCKER_WARMUP_TASK"] = task_path
        wait_for_sandbox_create_slot()
        sandbox = runtime["modal"].Sandbox.create(
            "bash",
            "-lc",
            "cd {} && python -m backend.service.modal_sandbox_job --warm-docker".format(
                MODAL_REMOTE_REPO
            ),
            app=runtime["app"],
            image=runtime["image"],
            timeout=60 * 60,
            cpu=2,
            memory=8192,
            volumes={"/modal-docker-images": runtime["docker_volume"]},
            secrets=runtime["secrets"],
            env=env,
            experimental_options={"enable_docker": True},
        )
        object_id = str(getattr(sandbox, "object_id", "") or "")
        print("docker warmup sandbox {}".format(object_id), flush=True)
        code = sandbox.wait()
        try:
            out = sandbox.stdout.read() or ""
            err = sandbox.stderr.read() or ""
        except Exception:
            out = ""
            err = ""
        if isinstance(out, bytes):
            out = out.decode("utf-8", "replace")
        if isinstance(err, bytes):
            err = err.decode("utf-8", "replace")
        if out:
            print(out, end="" if out.endswith("\n") else "\n", flush=True)
        if err:
            print(err, end="" if err.endswith("\n") else "\n", file=sys.stderr, flush=True)
        if "docker warmup ok" not in out:
            raise ModalHostJobError(
                "docker cache warmup exited {} ({})".format(
                    code, (err or out)[-1500:]
                )
            )
        return object_id

    def spawn_many(self, requests: list[ModalHostJobRequest]) -> list[tuple[str, Any]]:
        if not requests:
            return []
        runtime = self._runtime(requests[0])
        self._upload_payloads(runtime["jobs_volume"], requests)

        def _spawn_one(request: ModalHostJobRequest) -> tuple[str, Any]:
            sandbox = self._create_sandbox(runtime, request)
            object_id = str(
                getattr(sandbox, "object_id", None)
                or getattr(sandbox, "sandbox_id", None)
                or ""
            )
            if not object_id:
                raise ModalHostJobError("Modal Sandbox did not return an id")
            return encode_modal_sandbox_call_id(object_id), sandbox

        workers = min(sandbox_create_concurrency(), len(requests))
        print(
            "modal sandbox spawn: {} shards, {} parallel creates, max {:.0f}/s".format(
                len(requests), workers, sandbox_create_rate()
            ),
            flush=True,
        )
        if workers <= 1:
            return [_spawn_one(request) for request in requests]
        results: list[tuple[str, Any] | None] = [None] * len(requests)
        done = 0
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(_spawn_one, request): index
                for index, request in enumerate(requests)
            }
            for future in as_completed(futures):
                index = futures[future]
                results[index] = future.result()
                done += 1
                if done == 1 or done == len(requests) or done % 32 == 0:
                    print(
                        "modal sandbox created {}/{}".format(done, len(requests)),
                        flush=True,
                    )
        out: list[tuple[str, Any]] = []
        for item in results:
            if item is None:
                raise ModalHostJobError("Modal Sandbox spawn returned no result")
            out.append(item)
        return out

    def wait(
        self,
        request: ModalHostJobRequest,
        *,
        call: Any | None = None,
        call_id: str = "",
    ) -> ModalHostJobResult:
        sandbox = call
        object_id = modal_sandbox_object_id(call_id)
        if sandbox is None and object_id:
            try:
                sandbox = self._runtime(request)["modal"].Sandbox.from_id(object_id)
            except Exception as exc:  # noqa: BLE001
                sandbox = None
                from_id_error = exc
            else:
                from_id_error = None
        else:
            from_id_error = None
        volume = self._jobs_volume()
        volume_job_name = modal_volume_job_name(request.job_name, request.shard_key)
        dest = _live_dest(request)
        exit_code: int | None = None
        if sandbox is not None:
            try:
                exit_code = wait_sandbox_with_live_pull(
                    sandbox,
                    request=request,
                    volume=None,
                    poll_sec=_poll_sec(),
                )
            except Exception as exc:  # noqa: BLE001
                raise ModalHostJobError(
                    "Modal Sandbox {} is not reachable ({})".format(
                        object_id or call_id, exc
                    )
                ) from exc
        elif from_id_error is not None and volume is None:
            raise ModalHostJobError(
                "Modal Sandbox {} is not reachable ({})".format(object_id, from_id_error)
            ) from from_id_error
        archive = b""
        stored = read_sandbox_result(volume, volume_job_name) if volume is not None else None
        if exit_code is None and stored and stored.get("exitCode") is not None:
            exit_code = int(stored["exitCode"])
        if exit_code is None:
            exit_code = 1
        error = None
        if stored and stored.get("error"):
            error = str(stored["error"])
        elif exit_code != 0:
            error = "Modal Sandbox exited with code {}".format(exit_code)
        if sandbox is not None and exit_code != 0:
            stdio = read_sandbox_stdio(sandbox)
            if stdio:
                error = "{}\n{}".format(error or "Modal Sandbox failed", stdio)
                print(stdio, file=sys.stderr, flush=True)
        if dest is not None and job_dir_has_trials(dest):
            archive = b""
        return finalize_local_sandbox_result(
            dest=dest,
            exit_code=exit_code,
            archive=archive,
            error=error,
            job_name=request.job_name,
        )

    def run(self, request: ModalHostJobRequest) -> ModalHostJobResult:
        call_id, sandbox = self.spawn(request)
        return self.wait(request, call=sandbox, call_id=call_id)

    def _runtime(self, request: ModalHostJobRequest) -> dict[str, Any]:
        del request
        global _shared_runtime
        if self._runtime_cache is not None:
            return self._runtime_cache
        if _shared_runtime is not None:
            self._runtime_cache = _shared_runtime
            return _shared_runtime
        if not modal_credentials_configured():
            raise ModalHostJobError(
                "computeFamily=modal requires Modal credentials "
                "(MODAL_TOKEN_ID and MODAL_TOKEN_SECRET, or ~/.modal.toml)"
            )
        try:
            import modal
        except ImportError as exc:
            raise ModalHostJobError(
                "computeFamily=modal requires the Modal SDK (pip install 'matraix[modal]')"
            ) from exc
        try:
            from backend.service import modal_host_app as host_app
        except Exception as exc:  # noqa: BLE001
            raise ModalHostJobError(
                "Modal sandbox image is not available ({})".format(exc)
            ) from exc
        try:
            app = modal.App.lookup(MODAL_HOST_APP_NAME, create_if_missing=True)
        except Exception as exc:  # noqa: BLE001
            raise ModalHostJobError(
                "Modal app {} is not reachable. "
                "Deploy application/playground/backend/service/modal_host_app.py "
                "({}).".format(MODAL_HOST_APP_NAME, exc)
            ) from exc
        runtime = {
            "modal": modal,
            "app": app,
            "image": reuse_built_sandbox_image(modal, app, host_app.docker_image),
            "jobs_volume": host_app.volume,
            "docker_volume": host_app.docker_images_volume,
            "secrets": list(getattr(host_app, "_secrets", None) or []),
            "worker_env": dict(getattr(host_app, "_WORKER_ENV", None) or {}),
        }
        self._runtime_cache = runtime
        _shared_runtime = runtime
        return runtime

    def _upload_payloads(self, volume: Any, requests: list[ModalHostJobRequest]) -> None:
        payloads = []
        for request in requests:
            config_yaml = attach_nested_docker_host_network(request.config_yaml)
            payloads.append(
                (
                    sandbox_payload_remote_path(request.job_name, request.shard_key),
                    sandbox_payload_bytes(
                        job_name=request.job_name,
                        config_yaml=config_yaml,
                        env=request.env,
                        shard_key=request.shard_key,
                        repo_files=collect_local_repo_files(
                            config_yaml, request.repo_root
                        ),
                    ),
                )
            )
        try:
            print(
                "modal sandbox payload upload: {} files".format(len(payloads)),
                flush=True,
            )
            batch_upload_payloads(volume, payloads)
            print("modal sandbox payload upload done", flush=True)
        except Exception as exc:  # noqa: BLE001
            raise ModalHostJobError(
                "could not upload sandbox payloads ({})".format(exc)
            ) from exc

    def _upload_payload(self, volume: Any, remote_path: str, request: ModalHostJobRequest) -> None:
        del remote_path
        self._upload_payloads(volume, [request])

    def _sandbox_env(self, runtime: dict[str, Any], request: ModalHostJobRequest) -> dict[str, str]:
        env = dict(request.env)
        env.update(request.secret_env or collect_orchestrator_secret_env())
        # Local orchestrator PYTHONPATH / MATRIX_REPO_ROOT must not clobber the
        # image paths — otherwise `python -m backend.service.modal_sandbox_job`
        # fails with ModuleNotFoundError.
        env.update(runtime.get("worker_env") or {})
        env["MATRIX_DOCKER_IMAGE_CACHE"] = "/modal-docker-images"
        env["MATRIX_DOCKER_HOST_NETWORK"] = "1"
        env[SANDBOX_PAYLOAD_ENV] = "/modal-jobs/{}".format(
            sandbox_payload_remote_path(request.job_name, request.shard_key)
        )
        return env

    def _create_sandbox(self, runtime: dict[str, Any], request: ModalHostJobRequest) -> Any:
        env = self._sandbox_env(runtime, request)
        jobs_volume = _readonly_volume(runtime["jobs_volume"])
        docker_volume = _readonly_volume(runtime["docker_volume"])
        last_error: Exception | None = None
        for attempt in range(1, 8):
            try:
                wait_for_sandbox_create_slot()
                create = runtime["modal"].Sandbox.create
                kwargs = {
                    "app": runtime["app"],
                    "image": runtime["image"],
                    "timeout": _SANDBOX_TIMEOUT_SEC,
                    "cpu": 2,
                    "memory": 8192,
                    "volumes": {
                        "/modal-jobs": jobs_volume,
                        "/modal-docker-images": docker_volume,
                    },
                    "secrets": runtime["secrets"],
                    "env": env,
                    "experimental_options": {"enable_docker": True},
                    "tags": {
                        "matraix-job": request.job_name,
                        "matraix-shard": request.shard_key or "default",
                    },
                }
                try:
                    return create(
                        "bash",
                        "-lc",
                        "cd {} && python -m backend.service.modal_sandbox_job".format(
                            MODAL_REMOTE_REPO
                        ),
                        **kwargs,
                    )
                except TypeError:
                    kwargs.pop("tags", None)
                    return create(
                        "bash",
                        "-lc",
                        "cd {} && python -m backend.service.modal_sandbox_job".format(
                            MODAL_REMOTE_REPO
                        ),
                        **kwargs,
                    )
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                text = str(exc).lower()
                retryable = (
                    volume_write_is_retryable(exc)
                    or "rate" in text
                    or "quota" in text
                    or "429" in text
                )
                if not retryable or attempt >= 7:
                    break
                time.sleep(min(2**attempt, 20))
        raise ModalHostJobError(
            "Modal Sandbox for {} could not be created. "
            "Deploy application/playground/backend/service/modal_host_app.py "
            "({}).".format(MODAL_HOST_APP_NAME, last_error)
        ) from last_error

    def _jobs_volume(self) -> Any | None:
        try:
            import modal
        except ImportError:
            return None
        try:
            return modal.Volume.from_name(
                os.environ.get("MATRIX_MODAL_JOBS_VOLUME") or MODAL_JOBS_VOLUME_NAME
            )
        except Exception:
            return None


def _readonly_volume(volume: Any) -> Any:
    method = getattr(volume, "with_mount_options", None)
    if not callable(method):
        return volume
    try:
        return method(read_only=True)
    except Exception:
        return volume


def _live_dest(request: ModalHostJobRequest) -> Path | None:
    root = (request.live_jobs_dir or "").strip()
    if not root:
        return None
    dest = Path(root) / request.job_name
    dest.mkdir(parents=True, exist_ok=True)
    return dest


def _poll_sec() -> float:
    raw = (os.environ.get("MATRIX_MODAL_LIVE_PULL_SEC") or "").strip()
    try:
        value = float(raw) if raw else 5.0
    except ValueError:
        value = 5.0
    if value <= 0:
        return 5.0
    return max(value, 0.5)


def _run_command(command: list[str], *, cwd: str, env: dict[str, str]) -> int:
    completed = subprocess.run(command, cwd=cwd, env=env, check=False)
    return int(completed.returncode)


def _mounted_volume(default_name: str, env_name: str) -> Any | None:
    try:
        import modal
    except ImportError:
        return None
    try:
        return modal.Volume.from_name(os.environ.get(env_name) or default_name)
    except Exception:
        return None


def _commit_volume(volume: Any | None) -> None:
    if volume is None:
        return
    try:
        volume.commit()
    except Exception:
        pass


def _write_sandbox_result(
    dest: Path,
    result: ModalHostJobResult,
    volume_path: str | None,
) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    payload = {
        "exitCode": int(result.exit_code),
        "error": result.error,
        "volumePath": volume_path,
    }
    (dest / SANDBOX_RESULT_FILENAME).write_text(
        json.dumps(payload) + "\n", encoding="utf-8"
    )


def run_docker_warmup() -> int:
    """Build and save the Harbor task image onto the writable docker Volume."""
    from backend.service.modal_docker_prebuild import (
        docker_cache_dir,
        prepare_docker_environment_for_job,
    )

    task = (os.environ.get("MATRIX_DOCKER_WARMUP_TASK") or "").strip() or (
        "application/tasks/web_notion-plan-comparison"
    )
    yaml_text = "environment:\n  type: docker\ntasks:\n  - path: {}\n".format(task)
    print("docker warmup task {}".format(task), flush=True)
    try:
        tag = prepare_docker_environment_for_job(
            yaml_text,
            repo_root=Path(MODAL_REMOTE_REPO),
        )
    except Exception as exc:  # noqa: BLE001
        print("docker warmup failed: {}".format(exc), file=sys.stderr, flush=True)
        return 1
    cache = docker_cache_dir()
    print("docker warmup ok {}".format(tag), flush=True)
    try:
        for path in sorted(cache.iterdir()):
            size = path.stat().st_size if path.is_file() else 0
            print("cache {} {}".format(path.name, size), flush=True)
    except OSError as exc:
        print("cache list failed {}".format(exc), file=sys.stderr, flush=True)
    return 0 if tag else 1


def main() -> int:
    if "--warm-docker" in sys.argv:
        return run_docker_warmup()
    try:
        return run_sandbox_worker()
    except Exception as exc:  # noqa: BLE001
        volume_name = (os.environ.get("MATRIX_SANDBOX_VOLUME_NAME") or "").strip()
        if volume_name:
            try:
                _write_sandbox_result(
                    Path("/modal-jobs") / volume_name,
                    ModalHostJobResult(exit_code=1, error=str(exc)),
                    volume_name,
                )
                _commit_volume(
                    _mounted_volume(MODAL_JOBS_VOLUME_NAME, "MATRIX_MODAL_JOBS_VOLUME")
                )
            except Exception:
                pass
        raise


if __name__ == "__main__":
    raise SystemExit(main())
