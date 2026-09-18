from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

from harbor.environments.modal import (
    _DIRECT_IMAGE_CACHE,
    _MODAL_SANDBOX_CREATE_RATE,
    _direct_image_cache_key,
    _throttle_sandbox_create,
)


def test_direct_image_cache_key_is_shared_across_trials(tmp_path: Path) -> None:
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text("FROM python:3.12-slim\n", encoding="utf-8")
    first = _direct_image_cache_key(
        dockerfile_path=dockerfile,
        context_dir=tmp_path,
        image_name=None,
        force_build=False,
    )
    second = _direct_image_cache_key(
        dockerfile_path=dockerfile,
        context_dir=tmp_path,
        image_name=None,
        force_build=False,
    )
    assert first == second
    assert "force=0" in first
    registry = _direct_image_cache_key(
        dockerfile_path=None,
        context_dir=None,
        image_name="ghcr.io/example/web:latest",
        force_build=False,
    )
    assert registry == "reg:ghcr.io/example/web:latest"


@pytest.mark.asyncio
async def test_throttle_sandbox_create_caps_burst() -> None:
    import harbor.environments.modal as modal_mod

    modal_mod._CREATE_RATE_LOCK = asyncio.Lock()
    modal_mod._CREATE_RATE_NEXT = 0.0
    _DIRECT_IMAGE_CACHE.clear()
    started = time.monotonic()
    await asyncio.gather(*[_throttle_sandbox_create() for _ in range(3)])
    elapsed = time.monotonic() - started
    min_elapsed = 2.0 / _MODAL_SANDBOX_CREATE_RATE
    assert elapsed >= min_elapsed * 0.8
