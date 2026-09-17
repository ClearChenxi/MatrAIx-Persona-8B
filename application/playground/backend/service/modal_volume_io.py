"""Serialized Modal Volume writes for large web/linux cohorts.

One ``batch_upload`` is one Volume layer. Many shards each calling
``batch_upload`` / ``commit`` is what blows past Modal's layer cap.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import uuid
from collections.abc import Callable, Iterable, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

MODAL_VOLUME_UPLOAD_LOCK_DICT = "matraix-jobs-upload-lock"
_SKIP_REMOTE_FILES = frozenset({"result.json", "lock.json", "config.json", "compute.json"})
_RETRYABLE_MARKERS = (
    "too many layers",
    "rate limit",
    "429",
    "workerreconnect",
    "connection reset",
    "temporarily unavailable",
)
_upload_gate: Callable[[Callable[[], Any]], Any] | None = None
_local_upload_lock = threading.Lock()
_pull_sema: threading.Semaphore | None = None
_pull_sema_n: int | None = None
_pull_sema_lock = threading.Lock()


def set_volume_upload_gate(gate: Callable[[Callable[[], Any]], Any] | None) -> None:
    """Tests replace the distributed lock with a local callable."""
    global _upload_gate
    _upload_gate = gate


def local_pull_concurrency() -> int:
    raw = (os.environ.get("MATRIX_MODAL_LOCAL_PULL_CONCURRENCY") or "").strip()
    try:
        value = int(raw) if raw else 8
    except ValueError:
        value = 8
    return max(1, value)


@contextmanager
def local_artifact_pull_slot() -> Iterator[None]:
    """Bound how many sandbox tars copy to this machine at once."""
    n = local_pull_concurrency()
    global _pull_sema, _pull_sema_n
    with _pull_sema_lock:
        if _pull_sema is None or _pull_sema_n != n:
            _pull_sema = threading.Semaphore(n)
            _pull_sema_n = n
        sema = _pull_sema
    sema.acquire()
    try:
        yield
    finally:
        sema.release()


def volume_write_is_retryable(exc: BaseException) -> bool:
    text = str(exc).lower()
    return any(marker in text for marker in _RETRYABLE_MARKERS)


def sandbox_payload_bytes(
    *,
    job_name: str,
    config_yaml: str,
    env: dict[str, str],
    shard_key: str = "",
    repo_files: dict[str, str] | None = None,
) -> bytes:
    payload = {
        "jobName": job_name,
        "configYaml": config_yaml,
        "env": env,
        "shardKey": shard_key,
    }
    if repo_files:
        payload["repoFiles"] = repo_files
    return json.dumps(payload).encode("utf-8")


def job_dir_upload_items(job_dir: Path, remote_prefix: str) -> list[tuple[Path, str]]:
    """Map a Harbor job tree onto Volume paths. Skip shard-level rollup files."""
    prefix = remote_prefix.strip("/")
    items: list[tuple[Path, str]] = []
    if not job_dir.is_dir():
        return items
    for path in sorted(job_dir.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(job_dir).as_posix()
        if Path(rel).name in _SKIP_REMOTE_FILES and "/" not in rel:
            continue
        remote = "{}/{}".format(prefix, rel) if prefix else rel
        items.append((path, remote))
    return items


def batch_upload_files(
    volume: Any,
    items: Sequence[tuple[Path | str, str]],
    *,
    force: bool = True,
) -> int:
    """Put every file in ``items`` in a single ``batch_upload`` (one layer)."""
    if not items:
        return 0
    with volume.batch_upload(force=force) as batch:
        for local, remote in items:
            batch.put_file(str(local), str(remote).lstrip("/"))
    return len(items)


def batch_upload_files_retry(
    volume: Any,
    items: Sequence[tuple[Path | str, str]],
    *,
    force: bool = True,
    attempts: int = 8,
) -> int:
    last: BaseException | None = None
    for attempt in range(1, max(1, attempts) + 1):
        try:
            return batch_upload_files(volume, items, force=force)
        except Exception as exc:  # noqa: BLE001
            last = exc
            if not volume_write_is_retryable(exc) or attempt >= attempts:
                raise
            time.sleep(min(2**attempt, 30))
    if last is not None:
        raise last
    return 0


def run_serialized_volume_write(action: Callable[[], Any]) -> Any:
    """One Volume writer at a time (Modal Dict lock, or a test gate)."""
    gate = _upload_gate
    if gate is not None:
        return gate(action)
    try:
        from backend.service.modal_host_job import modal_credentials_configured

        if modal_credentials_configured():
            return _modal_dict_volume_lock(action)
    except Exception:
        pass
    with _local_upload_lock:
        return action()


def batch_upload_payloads(
    volume: Any,
    payloads: Sequence[tuple[str, bytes]],
    *,
    force: bool = True,
) -> int:
    """Upload many small JSON payloads in one ``batch_upload``."""
    if not payloads:
        return 0
    tmp = Path(tempfile.mkdtemp(prefix="matraix-modal-payloads-"))
    items: list[tuple[Path, str]] = []
    try:
        for index, (remote_path, blob) in enumerate(payloads):
            local = tmp / "{:04d}.json".format(index)
            local.write_bytes(blob)
            items.append((local, remote_path.lstrip("/")))
        return run_serialized_volume_write(
            lambda: batch_upload_files_retry(volume, items, force=force)
        )
    finally:
        for local, _remote in items:
            try:
                local.unlink()
            except OSError:
                pass
        try:
            tmp.rmdir()
        except OSError:
            pass


def batch_upload_job_dir(
    volume: Any,
    job_dir: Path,
    remote_prefix: str,
    *,
    extra_files: Iterable[tuple[Path, str]] = (),
    force: bool = True,
) -> int:
    items = job_dir_upload_items(job_dir, remote_prefix)
    items.extend(extra_files)
    if not items:
        return 0
    return run_serialized_volume_write(
        lambda: batch_upload_files_retry(volume, items, force=force)
    )


def _modal_dict_volume_lock(action: Callable[[], Any]) -> Any:
    import modal

    store = modal.Dict.from_name(MODAL_VOLUME_UPLOAD_LOCK_DICT, create_if_missing=True)
    token = uuid.uuid4().hex
    timeout = _lock_timeout_sec()
    deadline = time.time() + timeout
    while time.time() < deadline:
        owner = _dict_get(store, "owner")
        stale = False
        if isinstance(owner, dict):
            held_at = float(owner.get("at") or 0)
            stale = held_at > 0 and (time.time() - held_at) > _lock_stale_sec()
        if owner is None or stale:
            store["owner"] = {"token": token, "at": time.time()}
            time.sleep(0.25)
            current = _dict_get(store, "owner")
            if isinstance(current, dict) and current.get("token") == token:
                try:
                    return action()
                finally:
                    current = _dict_get(store, "owner")
                    if isinstance(current, dict) and current.get("token") == token:
                        try:
                            del store["owner"]
                        except Exception:
                            store["owner"] = None
        time.sleep(1.5)
    raise TimeoutError("timed out waiting for Modal Volume upload lock")


def _dict_get(store: Any, key: str) -> Any:
    try:
        getter = getattr(store, "get", None)
        if callable(getter):
            return getter(key)
        return store[key]
    except KeyError:
        return None
    except Exception:
        return None


def _lock_timeout_sec() -> float:
    raw = (os.environ.get("MATRIX_MODAL_VOLUME_LOCK_SEC") or "").strip()
    try:
        value = float(raw) if raw else 900.0
    except ValueError:
        value = 900.0
    return max(30.0, value)


def _lock_stale_sec() -> float:
    raw = (os.environ.get("MATRIX_MODAL_VOLUME_LOCK_STALE_SEC") or "").strip()
    try:
        value = float(raw) if raw else 180.0
    except ValueError:
        value = 180.0
    return max(30.0, value)
