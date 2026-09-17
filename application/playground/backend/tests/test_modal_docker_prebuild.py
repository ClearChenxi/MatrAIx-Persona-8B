"""Tests for Modal Docker image prebuild / Volume cache."""

from __future__ import annotations

from pathlib import Path

from backend.service.modal_docker_prebuild import (
    DockerCli,
    DockerPrebuildError,
    bake_cached_images_into_worker,
    cached_archive_path,
    docker_context_digest,
    harbor_environment_type,
    harbor_main_image_name,
    job_task_dir,
    patch_task_toml_docker_image,
    prebuilt_image_tag,
    prepare_docker_environment_for_job,
    task_short_name,
)


class _FakeDocker(DockerCli):
    def __init__(self) -> None:
        self.images: set[str] = set()
        self.built: list[tuple[str, str]] = []
        self.loaded: list[str] = []
        self.saved: list[tuple[str, str]] = []

    def image_exists(self, tag: str) -> bool:
        return tag in self.images

    def build(self, context: Path, tag: str) -> None:
        self.built.append((str(context), tag))
        self.images.add(tag)

    def tag(self, source: str, dest: str) -> None:
        if source not in self.images:
            raise RuntimeError("missing {}".format(source))
        self.images.add(dest)

    def save(self, tag: str, dest: Path) -> None:
        dest.write_bytes(b"tar:" + tag.encode("utf-8"))
        self.saved.append((tag, str(dest)))

    def load(self, archive: Path) -> None:
        payload = archive.read_bytes().decode("utf-8")
        tag = payload.split("tar:", 1)[-1]
        self.images.add(tag)
        self.loaded.append(str(archive))


def _web_repo(tmp_path: Path) -> tuple[Path, Path]:
    env = tmp_path / "environment" / "task-environments" / "application" / "shared-web-playwright"
    env.mkdir(parents=True)
    (env / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
    task = tmp_path / "application" / "tasks" / "example-web"
    task.mkdir(parents=True)
    (task / "task.toml").write_text(
        '[task]\nname = "application/playwright-quote-choice"\n'
        '[environment]\ndefinition = "application/shared-web-playwright"\n',
        encoding="utf-8",
    )
    return tmp_path, task


def test_digest_ignores_readme_when_dockerignore_allowlists_dockerfile(
    tmp_path: Path,
) -> None:
    env = tmp_path / "env"
    env.mkdir()
    (env / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
    (env / ".dockerignore").write_text("*\n!Dockerfile\n", encoding="utf-8")
    first = docker_context_digest(env)
    (env / "README.md").write_text("noise\n", encoding="utf-8")
    assert docker_context_digest(env) == first
    (env / "Dockerfile").write_text("FROM debian\n", encoding="utf-8")
    assert docker_context_digest(env) != first


def test_prepare_builds_once_then_loads_from_cache(tmp_path: Path) -> None:
    repo, task = _web_repo(tmp_path)
    yaml_text = (
        "environment:\n  type: docker\n"
        "tasks:\n  - path: application/tasks/example-web\n"
    )
    cache = tmp_path / "cache"
    docker = _FakeDocker()
    tag = prepare_docker_environment_for_job(
        yaml_text,
        repo_root=repo,
        cache_dir=cache,
        docker=docker,
        start_daemon=False,
    )
    assert tag == prebuilt_image_tag(
        docker_context_digest(
            repo / "environment" / "task-environments" / "application" / "shared-web-playwright"
        )
    )
    assert docker.built
    harbor = harbor_main_image_name(task_short_name(task))
    assert harbor in docker.images
    assert tag in docker.images
    toml = (task / "task.toml").read_text(encoding="utf-8")
    assert 'docker_image = "{}"'.format(tag) in toml

    docker.images.clear()
    docker.built.clear()
    again = prepare_docker_environment_for_job(
        yaml_text,
        repo_root=repo,
        cache_dir=cache,
        docker=docker,
        start_daemon=False,
    )
    assert again == tag
    assert not docker.built
    assert docker.loaded
    assert harbor in docker.images


def test_prepare_loads_cache_when_volume_is_read_only(tmp_path: Path) -> None:
    repo, task = _web_repo(tmp_path)
    yaml_text = (
        "environment:\n  type: docker\n"
        "tasks:\n  - path: application/tasks/example-web\n"
    )
    cache = tmp_path / "cache"
    docker = _FakeDocker()
    tag = prepare_docker_environment_for_job(
        yaml_text,
        repo_root=repo,
        cache_dir=cache,
        docker=docker,
        start_daemon=False,
    )
    docker.images.clear()
    docker.built.clear()
    docker.loaded.clear()
    cache.chmod(0o555)
    try:
        again = prepare_docker_environment_for_job(
            yaml_text,
            repo_root=repo,
            cache_dir=cache,
            docker=docker,
            start_daemon=False,
        )
    finally:
        cache.chmod(0o755)
    assert again == tag
    assert not docker.built
    assert docker.loaded
    assert harbor_main_image_name(task_short_name(task)) in docker.images


def test_prepare_refuses_build_when_read_only_cache_missing(tmp_path: Path) -> None:
    repo, _task = _web_repo(tmp_path)
    yaml_text = (
        "environment:\n  type: docker\n"
        "tasks:\n  - path: application/tasks/example-web\n"
    )
    cache = tmp_path / "cache"
    cache.mkdir()
    cache.chmod(0o555)
    docker = _FakeDocker()
    try:
        try:
            prepare_docker_environment_for_job(
                yaml_text,
                repo_root=repo,
                cache_dir=cache,
                docker=docker,
                start_daemon=False,
            )
        except DockerPrebuildError as exc:
            assert "missing docker image cache" in str(exc)
        else:
            raise AssertionError("expected DockerPrebuildError")
    finally:
        cache.chmod(0o755)
    assert not docker.built


def test_prepare_skips_save_when_volume_rejects_write(tmp_path: Path) -> None:
    repo, task = _web_repo(tmp_path)
    yaml_text = (
        "environment:\n  type: docker\n"
        "tasks:\n  - path: application/tasks/example-web\n"
    )
    cache = tmp_path / "cache"
    cache.mkdir()

    class _SaveFailDocker(_FakeDocker):
        def save(self, tag: str, dest: Path) -> None:
            raise OSError(30, "Read-only file system", str(dest))

    docker = _SaveFailDocker()
    tag = prepare_docker_environment_for_job(
        yaml_text,
        repo_root=repo,
        cache_dir=cache,
        docker=docker,
        start_daemon=False,
    )
    assert tag
    assert docker.built
    assert harbor_main_image_name(task_short_name(task)) in docker.images


def test_host_jobs_skip_prebuild(tmp_path: Path) -> None:
    assert harbor_environment_type("environment:\n  type: host\n") == "host"
    assert (
        prepare_docker_environment_for_job(
            "environment:\n  type: host\n",
            repo_root=tmp_path,
            start_daemon=False,
        )
        is None
    )


def test_docker_jobs_use_sandbox_not_function() -> None:
    from backend.service.modal_host_job import (
        modal_function_name_for_config,
        modal_uses_docker_sandbox,
    )

    assert modal_uses_docker_sandbox("environment:\n  type: docker\n")
    assert not modal_uses_docker_sandbox("environment:\n  type: host\n")
    assert (
        modal_function_name_for_config("environment:\n  type: host\n")
        == "harbor_host_job"
    )


def test_job_task_dir_and_patch(tmp_path: Path) -> None:
    repo, task = _web_repo(tmp_path)
    found = job_task_dir(
        "tasks:\n  - path: application/tasks/example-web\n",
        repo_root=repo,
    )
    assert found == task
    patch_task_toml_docker_image(task / "task.toml", "matraix-prebuilt:abc")
    assert 'docker_image = "matraix-prebuilt:abc"' in (task / "task.toml").read_text(
        encoding="utf-8"
    )


def test_bake_cached_images_copies_and_loads(tmp_path: Path) -> None:
    volume = tmp_path / "volume"
    volume.mkdir()
    (volume / "4e38f22cdc9d7994.tar").write_bytes(b"tar:matraix-prebuilt:4e38f22cdc9d7994")
    baked = tmp_path / "baked"
    docker = _FakeDocker()
    loaded = bake_cached_images_into_worker(
        volume,
        baked_root=baked,
        docker=docker,
        start_daemon=False,
    )
    assert loaded == ["4e38f22cdc9d7994.tar"]
    assert (baked / "4e38f22cdc9d7994.tar").is_file()
    assert docker.loaded == [str(baked / "4e38f22cdc9d7994.tar")]
    assert "matraix-prebuilt:4e38f22cdc9d7994" in docker.images


def test_bake_cached_images_noop_when_empty(tmp_path: Path) -> None:
    docker = _FakeDocker()
    assert bake_cached_images_into_worker(
        tmp_path / "missing",
        baked_root=tmp_path / "baked",
        docker=docker,
        start_daemon=False,
    ) == []
    assert docker.loaded == []


def test_cached_archive_prefers_baked_then_volume(
    tmp_path: Path, monkeypatch
) -> None:
    baked = tmp_path / "baked"
    volume = tmp_path / "volume"
    baked.mkdir()
    volume.mkdir()
    (baked / "abc.tar").write_bytes(b"tar")
    (volume / "abc.tar").write_bytes(b"old")
    (volume / "def.tar").write_bytes(b"volume-only")
    monkeypatch.setenv("MATRIX_DOCKER_BAKED_CACHE", str(baked))
    monkeypatch.setenv("MATRIX_DOCKER_IMAGE_CACHE", str(volume))
    assert cached_archive_path("abc") == baked / "abc.tar"
    assert cached_archive_path("def") == volume / "def.tar"
    assert cached_archive_path("missing") is None


def test_prepare_loads_baked_archive_when_volume_lacks_digest(
    tmp_path: Path, monkeypatch
) -> None:
    repo, task = _web_repo(tmp_path)
    yaml_text = (
        "environment:\n  type: docker\n"
        "tasks:\n  - path: application/tasks/example-web\n"
    )
    env_dir = (
        repo / "environment" / "task-environments" / "application" / "shared-web-playwright"
    )
    digest = docker_context_digest(env_dir)
    tag = prebuilt_image_tag(digest)
    baked = tmp_path / "baked"
    volume = tmp_path / "volume"
    baked.mkdir()
    volume.mkdir()
    (baked / "{}.tar".format(digest)).write_bytes(b"tar:" + tag.encode("utf-8"))
    monkeypatch.setenv("MATRIX_DOCKER_BAKED_CACHE", str(baked))
    monkeypatch.setenv("MATRIX_DOCKER_IMAGE_CACHE", str(volume))
    docker = _FakeDocker()
    got = prepare_docker_environment_for_job(
        yaml_text,
        repo_root=repo,
        docker=docker,
        start_daemon=False,
    )
    assert got == tag
    assert not docker.built
    assert docker.loaded == [str(baked / "{}.tar".format(digest))]
    assert harbor_main_image_name(task_short_name(task)) in docker.images
