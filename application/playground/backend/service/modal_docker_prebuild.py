"""Prebuild and cache Harbor Docker images for Modal web/linux sandboxes.

Harbor tags each trial ``hb__{task.short_name}``, so two Playwright tasks with
the same Dockerfile still rebuild if the daemon is empty. Each sandbox starts
with an empty Docker graph. Hash the environment context once, persist
``docker save`` tarballs on a Volume, and retag ``hb__{short_name}`` so Harbor
skips ``compose build``.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import tomllib
from pathlib import Path
from typing import Callable

DOCKER_CACHE_ENV = "MATRIX_DOCKER_IMAGE_CACHE"
DEFAULT_DOCKER_CACHE_DIR = "/modal-docker-images"
BAKED_DOCKER_CACHE_DIR = Path("/opt/matraix-docker-cache")
PREBUILT_IMAGE_PREFIX = "matraix-prebuilt"
_SKIP_HASH_PARTS = frozenset({".git", "__pycache__", ".pytest_cache", "node_modules"})


class DockerPrebuildError(RuntimeError):
    """Docker was required for a Modal web/linux job but was not usable."""


def harbor_environment_type(config_yaml: str) -> str:
    try:
        import yaml
    except ImportError:
        return ""
    loaded = yaml.safe_load(config_yaml)
    if not isinstance(loaded, dict):
        return ""
    block = loaded.get("environment")
    if not isinstance(block, dict):
        return ""
    return str(block.get("type") or "").strip().lower()


def job_task_dir(config_yaml: str, *, repo_root: Path) -> Path | None:
    try:
        import yaml
    except ImportError:
        return None
    loaded = yaml.safe_load(config_yaml)
    if not isinstance(loaded, dict):
        return None
    tasks = loaded.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        return None
    first = tasks[0]
    rel = ""
    if isinstance(first, dict):
        rel = str(first.get("path") or "").strip()
    elif isinstance(first, str):
        rel = first.strip()
    if not rel:
        return None
    path = (repo_root / rel).resolve()
    return path if path.is_dir() else None


def docker_context_digest(environment_dir: Path) -> str:
    """Stable hash of Dockerfile + context (shared images reuse one digest)."""
    hasher = hashlib.sha256()
    keep = _dockerignore_keep(environment_dir)
    files = []
    for path in sorted(environment_dir.rglob("*")):
        if not path.is_file():
            continue
        if any(part in _SKIP_HASH_PARTS for part in path.parts):
            continue
        rel = path.relative_to(environment_dir).as_posix()
        if keep is not None and rel not in keep:
            continue
        files.append(path)
    if not files:
        raise DockerPrebuildError(
            "environment dir {} has no files to hash".format(environment_dir)
        )
    for path in files:
        rel = path.relative_to(environment_dir).as_posix().encode("utf-8")
        hasher.update(rel)
        hasher.update(b"\0")
        hasher.update(path.read_bytes())
        hasher.update(b"\0")
    return hasher.hexdigest()[:16]


def _dockerignore_keep(environment_dir: Path) -> set[str] | None:
    """If the ignore file is ``*`` plus ``!`` allow-rules, hash only those files."""
    path = environment_dir / ".dockerignore"
    if not path.is_file():
        return None
    lines = [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    if "*" not in lines:
        return None
    keep = {".dockerignore"}
    for line in lines:
        if line.startswith("!"):
            keep.add(line[1:].lstrip("/"))
    return keep


def sanitize_docker_image_name(name: str) -> str:
    cleaned = name.lower()
    if not re.match(r"^[a-z0-9]", cleaned):
        cleaned = "0" + cleaned
    return re.sub(r"[^a-z0-9._-]", "-", cleaned)


def harbor_main_image_name(short_name: str) -> str:
    return sanitize_docker_image_name("hb__{}".format(short_name))


def task_short_name(task_dir: Path) -> str:
    config_path = task_dir / "task.toml"
    name = task_dir.name
    if config_path.is_file():
        try:
            loaded = tomllib.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError):
            loaded = {}
        task = loaded.get("task") if isinstance(loaded, dict) else None
        raw = (task or {}).get("name") if isinstance(task, dict) else None
        if isinstance(raw, str) and raw.strip():
            name = raw.strip()
    parts = name.split("/")
    return parts[1] if len(parts) > 1 else parts[0]


def prebuilt_image_tag(digest: str) -> str:
    return "{}:{}".format(PREBUILT_IMAGE_PREFIX, digest)


def docker_cache_dir() -> Path:
    """Writable Volume/local cache used to save newly built images."""
    env = (os.environ.get(DOCKER_CACHE_ENV) or "").strip()
    if env:
        return Path(env)
    default = Path(DEFAULT_DOCKER_CACHE_DIR)
    if default.is_dir():
        return default
    return Path("/tmp/matraix-docker-images")


def _baked_cache_dir() -> Path:
    override = (os.environ.get("MATRIX_DOCKER_BAKED_CACHE") or "").strip()
    return Path(override) if override else BAKED_DOCKER_CACHE_DIR


def cached_archive_path(digest: str, cache_dir: Path | None = None) -> Path | None:
    """Find ``{digest}.tar`` in the baked image cache, then the Volume cache."""
    roots: list[Path] = []
    if cache_dir is not None:
        roots.append(Path(cache_dir))
    else:
        roots.append(_baked_cache_dir())
        roots.append(docker_cache_dir())
    seen: set[str] = set()
    name = "{}.tar".format(digest)
    for root in roots:
        key = str(root)
        if key in seen:
            continue
        seen.add(key)
        for archive in (root / name,):
            if _usable_archive(archive):
                return archive
        try:
            matches = sorted(root.glob(name))
        except OSError:
            matches = []
        for archive in matches:
            if _usable_archive(archive):
                return archive
    return None


def _usable_archive(archive: Path) -> bool:
    try:
        return archive.exists() and archive.stat().st_size > 0
    except OSError:
        return False


def bake_cached_images_into_worker(
    volume_root: Path,
    *,
    baked_root: Path | None = None,
    docker: "DockerCli | None" = None,
    start_daemon: bool = True,
) -> list[str]:
    """Copy Volume tars into the worker image and docker-load them.

    After this runs inside ``Image.run_function``, sandboxes start with the
    Playwright image already in the Docker graph (or at least a local tar, so
    they do not all ``docker load`` from the shared Volume).
    """
    baked = baked_root if baked_root is not None else BAKED_DOCKER_CACHE_DIR
    baked.mkdir(parents=True, exist_ok=True)
    if not volume_root.is_dir():
        print("bake docker cache: {} is missing".format(volume_root), flush=True)
        return []
    tars = sorted(
        path
        for path in volume_root.glob("*.tar")
        if path.is_file() and path.stat().st_size > 0
    )
    if not tars:
        print("bake docker cache: no *.tar under {}".format(volume_root), flush=True)
        return []
    copied: list[str] = []
    for archive in tars:
        dest = baked / archive.name
        if archive.resolve() != dest.resolve():
            shutil.copy2(archive, dest)
        copied.append(dest.name)
        print("baked docker tar {}".format(dest.name), flush=True)
    if start_daemon:
        try:
            ensure_docker_daemon()
        except DockerPrebuildError as exc:
            print(
                "bake docker cache: daemon unavailable ({}); tars copied only".format(exc),
                flush=True,
            )
            return copied
    cli = docker or DockerCli()
    for name in copied:
        dest = baked / name
        try:
            cli.load(dest)
            print("baked docker image {}".format(name), flush=True)
        except DockerPrebuildError as exc:
            print(
                "bake docker cache: load {} failed ({}); tar is still local".format(
                    name, exc
                ),
                flush=True,
            )
    return copied


def patch_task_toml_docker_image(toml_path: Path, image: str) -> None:
    """Point Harbor at a prebuilt tag so ``should_use_prebuilt_docker_image`` is true."""
    text = toml_path.read_text(encoding="utf-8") if toml_path.is_file() else ""
    line = 'docker_image = "{}"'.format(image)
    if re.search(r"^docker_image\s*=", text, flags=re.MULTILINE):
        text = re.sub(
            r"^docker_image\s*=\s*.*$",
            line,
            text,
            count=1,
            flags=re.MULTILINE,
        )
    elif re.search(r"^\[environment\]\s*$", text, flags=re.MULTILINE):
        text = re.sub(
            r"^\[environment\]\s*$",
            "[environment]\n{}".format(line),
            text,
            count=1,
            flags=re.MULTILINE,
        )
    else:
        text = text.rstrip() + "\n\n[environment]\n{}\n".format(line)
    toml_path.write_text(text, encoding="utf-8")


def ensure_docker_daemon(
    *,
    runner: Callable[..., subprocess.CompletedProcess[bytes]] | None = None,
) -> None:
    run = runner or _run
    if shutil_which("docker") is None and runner is None:
        raise DockerPrebuildError(
            "docker CLI is missing in the Modal worker image; "
            "redeploy application/playground/backend/service/modal_host_app.py"
        )
    if _docker_info_ok(run):
        return
    dockerd = shutil_which("dockerd")
    if dockerd is None and runner is None:
        raise DockerPrebuildError(
            "Docker daemon is not running and dockerd is not installed"
        )
    if dockerd is not None:
        subprocess.Popen(  # noqa: S603
            [
                dockerd,
                "--host=unix:///var/run/docker.sock",
                "--iptables=false",
                "--dns=8.8.8.8",
                "--dns=1.1.1.1",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    import time

    for _ in range(30):
        if _docker_info_ok(run):
            return
        time.sleep(1)
    raise DockerPrebuildError("Docker daemon did not become ready")


def prepare_docker_environment_for_job(
    config_yaml: str,
    *,
    repo_root: Path,
    cache_dir: Path | None = None,
    docker: "DockerCli | None" = None,
    start_daemon: bool = True,
) -> str | None:
    """Build or restore the task image. Returns the prebuilt tag, or None."""
    if harbor_environment_type(config_yaml) != "docker":
        return None
    task_dir = job_task_dir(config_yaml, repo_root=repo_root)
    if task_dir is None:
        return None
    from backend.service.task_environment import resolve_task_environment_dir

    environment_dir = resolve_task_environment_dir(task_dir)
    if not environment_dir.is_dir():
        raise DockerPrebuildError(
            "task {} has no environment directory".format(task_dir)
        )
    dockerfile = environment_dir / "Dockerfile"
    if not dockerfile.is_file():
        return None
    if start_daemon:
        ensure_docker_daemon()
    cli = docker or DockerCli()
    digest = docker_context_digest(environment_dir)
    cache_tag = prebuilt_image_tag(digest)
    harbor_tag = harbor_main_image_name(task_short_name(task_dir))
    cache_root = Path(cache_dir) if cache_dir is not None else docker_cache_dir()
    try:
        cache_root.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    archive = cached_archive_path(
        digest,
        cache_dir=cache_root if cache_dir is not None else None,
    )
    if archive is None and cache_dir is None:
        for extra in (
            _baked_cache_dir() / "{}.tar".format(digest),
            Path(DEFAULT_DOCKER_CACHE_DIR) / "{}.tar".format(digest),
        ):
            if _usable_archive(extra):
                archive = extra
                break
    write_archive = cache_root / "{}.tar".format(digest)
    # Volume is mounted read-only to avoid extra commits; flock lives on tmpfs.
    lock_path = Path("/tmp") / "matraix-docker-{}.lock".format(digest)
    with _file_lock(lock_path):
        if cli.image_exists(cache_tag):
            if not cli.image_exists(harbor_tag):
                cli.tag(cache_tag, harbor_tag)
        elif archive is not None:
            cli.load(archive)
            if not cli.image_exists(cache_tag):
                raise DockerPrebuildError(
                    "docker load of {} did not produce {}".format(archive, cache_tag)
                )
            cli.tag(cache_tag, harbor_tag)
        elif not _cache_writable(cache_root):
            raise DockerPrebuildError(
                "missing docker image cache {} — warm the Volume once "
                "before spawning many sandboxes".format(write_archive)
            )
        else:
            cli.build(environment_dir, cache_tag)
            cli.tag(cache_tag, harbor_tag)
            if _cache_writable(cache_root):
                try:
                    cli.save(cache_tag, write_archive)
                except (DockerPrebuildError, OSError) as exc:
                    print(
                        "docker save skipped for {}: {}".format(write_archive, exc),
                        flush=True,
                    )
    toml_path = task_dir / "task.toml"
    if toml_path.is_file():
        patch_task_toml_docker_image(toml_path, cache_tag)
    return cache_tag


class DockerCli:
    """Thin wrapper so tests can stub Harbor image cache operations."""

    def image_exists(self, tag: str) -> bool:
        completed = _run(
            ["docker", "image", "inspect", tag],
            check=False,
        )
        return completed.returncode == 0

    def build(self, context: Path, tag: str) -> None:
        completed = _run(
            ["docker", "build", "--network=host", "-t", tag, str(context)],
            check=False,
        )
        if completed.returncode != 0:
            detail = "\n".join(
                part for part in (_decode(completed.stderr), _decode(completed.stdout)) if part
            )
            raise DockerPrebuildError(
                "docker build failed for {}: {}".format(context, detail[-4000:])
            )

    def tag(self, source: str, dest: str) -> None:
        completed = _run(["docker", "tag", source, dest], check=False)
        if completed.returncode != 0:
            raise DockerPrebuildError(
                "docker tag {} -> {} failed".format(source, dest)
            )

    def save(self, tag: str, dest: Path) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        completed = _run(
            ["docker", "save", "-o", str(dest), tag],
            check=False,
        )
        if completed.returncode != 0:
            try:
                dest.unlink(missing_ok=True)
            except OSError:
                pass
            raise DockerPrebuildError("docker save {} failed".format(tag))

    def load(self, archive: Path) -> None:
        completed = _run(["docker", "load", "-i", str(archive)], check=False)
        if completed.returncode != 0:
            raise DockerPrebuildError("docker load {} failed".format(archive))


def shutil_which(name: str) -> str | None:
    import shutil

    return shutil.which(name)


def _docker_info_ok(run: Callable[..., subprocess.CompletedProcess[bytes]]) -> bool:
    completed = run(["docker", "info"], check=False)
    return completed.returncode == 0


def _run(
    command: list[str],
    *,
    check: bool = False,
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(  # noqa: S603
        command,
        check=check,
        capture_output=True,
    )


def _decode(blob: bytes | None) -> str:
    if not blob:
        return ""
    return blob.decode("utf-8", errors="replace").strip()


def _cache_writable(cache_root: Path) -> bool:
    """True only if we can actually create a file. Mounts can be RO with 0755."""
    try:
        cache_root.mkdir(parents=True, exist_ok=True)
        probe = cache_root / ".matraix-write-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def _file_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+", encoding="utf-8")
    try:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
    except OSError:
        pass
    return _Lock(handle)


class _Lock:
    def __init__(self, handle) -> None:
        self._handle = handle

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            import fcntl

            fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        self._handle.close()
