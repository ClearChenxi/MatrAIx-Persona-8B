"""Host-network compose overlay for nested Docker without iptables/NAT.

Modal ``enable_docker`` sandboxes start ``dockerd --iptables=false``. Bridge
networks then have no MASQUERADE, so Harbor task containers cannot resolve or
reach public APIs. Share the sandbox network namespace instead.
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from pathlib import Path

HOST_NETWORK_ENV = "MATRIX_DOCKER_HOST_NETWORK"
_FALSE = frozenset({"0", "false", "no", "off"})
_TRUE = frozenset({"1", "true", "yes", "on"})
_MODAL_MARKERS = ("MODAL_TASK_ID", "MODAL_FUNCTION_CALL_ID", "MODAL_CLOUD_PROVIDER")

MAIN_HOST_NETWORK_COMPOSE = "services:\n  main:\n    network_mode: host\n"


def host_network_requested(env: Mapping[str, str] | None = None) -> bool:
    source = os.environ if env is None else env
    explicit = str(source.get(HOST_NETWORK_ENV) or "").strip().lower()
    if explicit in _FALSE:
        return False
    if explicit in _TRUE:
        return True
    return any(str(source.get(name) or "").strip() for name in _MODAL_MARKERS)


def build_host_network_overlay(
    environment_dir: Path,
    *,
    use_prebuilt: bool = False,
    extra_compose_paths: Sequence[Path] | None = None,
) -> str:
    """Generate a compose overlay that sets host networking on all services.

    Parses service names from the task's docker-compose.yaml and extra compose
    overlays so the overlay covers all services regardless of naming
    conventions. Only adds ``build.network: host`` for services that have a
    build context (not pure image-based services like redis).

    Each service gets ``extra_hosts`` entries mapping every *other* service
    name to ``127.0.0.1`` so Docker DNS hostnames (e.g. ``redis``) resolve
    under host networking.
    """
    import yaml

    compose_path = environment_dir / "docker-compose.yaml"
    services: dict[str, bool] = {}
    compose_paths = [compose_path, *(extra_compose_paths or [])]
    for path in compose_paths:
        if not path.exists():
            continue
        doc = yaml.safe_load(path.read_text())
        if doc and "services" in doc:
            for name, cfg in doc["services"].items():
                has_build = isinstance(cfg, dict) and "build" in cfg
                services[name] = services.get(name, False) or has_build

    if not services:
        services = {"main": True, "sidecar": True, "redis": False}

    if use_prebuilt:
        services.setdefault("main", False)
    else:
        services["main"] = True

    service_names = list(services.keys())
    lines = ["services:"]
    for svc, has_build in services.items():
        lines.append(f"  {svc}:")
        if has_build:
            lines.append("    build:")
            lines.append("      network: host")
        lines.append("    network_mode: host")
        others = [s for s in service_names if s != svc]
        if others:
            lines.append("    extra_hosts:")
            for other in others:
                lines.append(f'      - "{other}:127.0.0.1"')
    return "\n".join(lines) + "\n"
