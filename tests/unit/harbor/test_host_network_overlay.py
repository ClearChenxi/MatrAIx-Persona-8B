from __future__ import annotations

from pathlib import Path

from harbor.environments.docker.host_network import (
    HOST_NETWORK_ENV,
    build_host_network_overlay,
    host_network_requested,
)
from harbor.environments.docker.docker import DockerEnvironment
from harbor.models.task.config import EnvironmentConfig, NetworkMode, NetworkPolicy
from harbor.models.trial.paths import TrialPaths


def test_host_network_requested_explicit_and_modal_markers() -> None:
    assert host_network_requested({HOST_NETWORK_ENV: "1"})
    assert not host_network_requested({HOST_NETWORK_ENV: "0", "MODAL_TASK_ID": "t"})
    assert host_network_requested({"MODAL_TASK_ID": "t"})
    assert not host_network_requested({})


def test_build_host_network_overlay_covers_named_services(tmp_path: Path) -> None:
    compose = tmp_path / "docker-compose.yaml"
    compose.write_text(
        "services:\n"
        "  main:\n"
        "    build: .\n"
        "  redis:\n"
        "    image: redis:7\n",
        encoding="utf-8",
    )
    overlay = build_host_network_overlay(tmp_path, use_prebuilt=True)
    assert overlay.count("network_mode: host") == 2
    assert "redis:127.0.0.1" in overlay
    assert "main:127.0.0.1" in overlay


def test_docker_environment_writes_host_overlay_when_requested(
    tmp_path: Path, monkeypatch
) -> None:
    env_dir = tmp_path / "environment"
    env_dir.mkdir()
    (env_dir / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
    monkeypatch.setenv(HOST_NETWORK_ENV, "1")
    env = DockerEnvironment(
        environment_dir=env_dir,
        environment_name="notion-plan-comparison",
        session_id="notion-plan-comparison__abc",
        trial_paths=TrialPaths(trial_dir=tmp_path / "trial"),
        task_env_config=EnvironmentConfig(docker_image="matraix-prebuilt:test"),
        network_policy=NetworkPolicy(network_mode=NetworkMode.PUBLIC),
    )
    path = env._write_host_network_compose_file()
    assert path is not None
    text = path.read_text(encoding="utf-8")
    assert "network_mode: host" in text
    env._host_network_compose_path = path
    assert path in env._docker_compose_paths


def test_docker_environment_skips_host_overlay_when_network_disabled(
    tmp_path: Path, monkeypatch
) -> None:
    env_dir = tmp_path / "environment"
    env_dir.mkdir()
    (env_dir / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
    monkeypatch.setenv(HOST_NETWORK_ENV, "1")
    env = DockerEnvironment(
        environment_dir=env_dir,
        environment_name="offline",
        session_id="offline__abc",
        trial_paths=TrialPaths(trial_dir=tmp_path / "trial"),
        task_env_config=EnvironmentConfig(docker_image="matraix-prebuilt:test"),
        network_policy=NetworkPolicy(network_mode=NetworkMode.NO_NETWORK),
    )
    assert env._write_host_network_compose_file() is None
