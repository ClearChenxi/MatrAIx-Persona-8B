"""Six synthetic calls via the unchanged official Agent and a logged local adapter."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import ipaddress
import json
import os
import platform
import socket
import subprocess
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from importlib.metadata import version
from pathlib import Path

from validation import CallBudget, check_messages, probability

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
TASK = ROOT / "application/tasks/survey_thesis-e0a-synthetic"
OLLAMA = "http://127.0.0.1:11434"


def now():
    return datetime.now(timezone.utc).isoformat()


def encode(value):
    return json.dumps(value, ensure_ascii=False, indent=2).encode()


def save(path, value):
    path.write_bytes(encode(value))


def sha(data):
    return hashlib.sha256(data).hexdigest()


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def local_network_guard(event, args):
    if event == "socket.getaddrinfo":
        host = args[0]
    elif event == "socket.connect" and args[0].family in (
        socket.AF_INET,
        socket.AF_INET6,
    ):
        host = args[1][0]
    else:
        return
    if host == "localhost":
        return
    try:
        allowed = ipaddress.ip_address(host).is_loopback
    except ValueError:
        allowed = False
    if not allowed:
        raise RuntimeError("E0a prohibits non-loopback networking")


def bootstrap():
    os.environ["PYTHON_DOTENV_DISABLED"] = "1"
    os.environ["LITELLM_MODE"] = "PRODUCTION"
    os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
    os.environ["LITELLM_TELEMETRY"] = "False"
    for key in list(os.environ):
        if key.lower().endswith("_proxy"):
            del os.environ[key]
    sys.addaudithook(local_network_guard)
    sys.path.insert(0, str(ROOT / "src"))
    from matraix.launch_env import required_pythonpath_entries

    sys.path[:0] = required_pythonpath_entries(ROOT)


def native(path, payload=None):
    request = urllib.request.Request(
        OLLAMA + path,
        data=encode(payload) if payload is not None else None,
        headers={"Content-Type": "application/json"},
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        response = opener.open(request, timeout=150)
    except urllib.error.HTTPError as exc:
        response = exc
    with response:
        return response.status, dict(response.headers), response.read()


def metadata(config):
    result = {}
    for name, path, payload in [
        ("version", "/api/version", None),
        ("tags", "/api/tags", None),
        ("show", "/api/show", {"model": config["model"]}),
    ]:
        status, headers, body = native(path, payload)
        if status != 200:
            raise RuntimeError(f"Ollama metadata {name}: HTTP {status}")
        result[name] = {
            "headers": headers,
            "raw": body.decode(),
            "data": json.loads(body),
        }
    models = [
        m for m in result["tags"]["data"]["models"] if m["name"] == config["model"]
    ]
    if len(models) != 1:
        raise RuntimeError("configured local model is unavailable")
    result["model_digest"] = models[0]["digest"]
    return result


def preflight(config, destination, task=TASK):
    import yaml
    from matraix.agents.persona.loader import load_persona
    from matraix.agents.persona.templating import (
        PERSONA_SYSTEM_TEMPLATE,
        render_persona_template,
        resolve_persona_template,
    )
    from playground.inprocess.survey_eval import build_survey_task_prompt
    from playground.survey_task_content import load_survey_task_content_for_task_path

    content = load_survey_task_content_for_task_path(str(task), repo_root=ROOT)
    prompt = build_survey_task_prompt(instrument=content.instrument)
    for text in [
        content.instruction_markdown.strip(),
        content.context_markdown.strip(),
    ]:
        if not text or text not in prompt:
            raise RuntimeError("task lookup silently fell back")
    prepared = []
    for profile in config["profiles"]:
        payload = {
            "persona_id": profile["id"],
            "version": "1.0",
            "display_name": "this household",
            "behavior": profile["behavior"],
            "system_prompt": "Use only these household behavior facts and the supplied campaign facts. Unknown psychological and demographic attributes must remain unknown. Do not invent a household member.",
        }
        path = destination / (profile["id"] + ".yaml")
        path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
        persona = load_persona(path)
        system = render_persona_template(
            resolve_persona_template(persona, None, PERSONA_SYSTEM_TEMPLATE), persona
        )
        if (
            profile["id"] in system
            or persona.demographics
            or persona.psychology
            or persona.dimensions
        ):
            raise RuntimeError("unexpected identity or unobserved attributes in prompt")
        for key, value in profile["behavior"].items():
            if f"{key.replace('_', ' ').title()}: {value}" not in system:
                raise RuntimeError("profile facts missing from rendered prompt")
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ]
        prepared.append((profile["id"], path, messages))
    return prepared


class Adapter(HTTPServer):
    def __init__(self, config):
        super().__init__(("127.0.0.1", 0), Handler)
        self.config = config
        self.budget = CallBudget(config["maximum_generations"])
        self.active = None


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        server = self.server
        trial, messages = server.active
        raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        # Each incoming request, including a denied SDK retry, remains auditable.
        attempt = len(list(trial.glob("official_request_*.json"))) + 1
        (trial / f"official_request_{attempt}.json").write_bytes(raw)
        save(
            trial / f"official_headers_{attempt}.json",
            {k: v for k, v in self.headers.items() if k.lower() != "authorization"},
        )
        record = {"started_at": now(), "adapter_id_is_synthetic": True}
        start = time.monotonic()
        try:
            request = json.loads(raw)
            if (
                self.path != "/v1/chat/completions"
                or request.get("model") != server.config["model"]
            ):
                raise ValueError("unexpected official API path or model")
            if request.get("response_format") != {"type": "json_object"}:
                raise ValueError("JSON mode required")
            check_messages(request.get("messages"), messages)
            if request.get("temperature") != 0.7:
                raise ValueError(
                    "unexpected upstream temperature; adapter mapping must be reviewed"
                )
            server.budget.claim(str(trial))
            payload = {
                "model": server.config["model"],
                "messages": request["messages"],
                "stream": False,
                "format": "json",
                "think": server.config["think"],
                "options": server.config["options"],
                "keep_alive": "5m",
            }
            save(trial / "ollama_request.json", payload)
            status, headers, body = native("/api/chat", payload)
            (trial / "ollama_response.raw.json").write_bytes(body)
            save(trial / "ollama_response_headers.json", headers)
            record["ollama_http_status"] = status
            if status != 200:
                raise RuntimeError(f"Ollama HTTP {status}")
            result = json.loads(body)
            if (
                result.get("model") != server.config["model"]
                or result.get("done") is not True
                or result.get("done_reason") != "stop"
            ):
                raise ValueError("model identity mismatch or incomplete generation")
            content = result["message"]["content"]
            record["p_redeem"] = probability(
                json.loads(content), server.config.get("instrument_id", "thesis_e0a_synthetic_v1")
            )
            output = {
                "id": "e0a-local-adapter-" + trial.name,
                "object": "chat.completion",
                "created": int(time.time()),
                "model": result["model"],
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": content},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": result.get("prompt_eval_count", 0),
                    "completion_tokens": result.get("eval_count", 0),
                    "total_tokens": result.get("prompt_eval_count", 0)
                    + result.get("eval_count", 0),
                },
            }
            response = encode(output)
            (trial / "adapter_response.raw.json").write_bytes(response)
            record["status"] = "passed"
            code = 200
        except Exception as exc:
            record.update(
                status="failed", error=str(exc), traceback=traceback.format_exc()
            )
            response = encode(
                {"error": {"message": str(exc), "type": "e0a_acceptance_error"}}
            )
            code = 400  # Nonretryable; no silent additional model calls.
        record.update(finished_at=now(), elapsed_seconds=time.monotonic() - start)
        save(trial / f"call_{attempt}.json", record)
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response)))
        self.end_headers()
        self.wfile.write(response)


async def run_trials(config, prepared, destination, server, task=TASK):
    from harbor.environments.host import HostEnvironment
    from harbor.models.agent.context import AgentContext
    from harbor.models.task.config import EnvironmentConfig
    from harbor.models.trial.paths import TrialPaths
    from matraix.agents.persona.json_survey import PersonaJsonSurvey

    rows = []
    for profile_id, persona_path, messages in prepared:
        for repetition in range(1, config["repetitions"] + 1):
            trial = destination / f"{profile_id}_r{repetition}"
            paths = TrialPaths(trial)
            paths.mkdir()
            save(trial / "expected_messages.json", messages)
            server.active = (trial, messages)
            environment = HostEnvironment(
                environment_dir=ROOT
                / "environment/task-environments/application/shared-survey-form",
                environment_name=config.get("environment_name", "e0a-synthetic"),
                session_id=trial.name,
                trial_paths=paths,
                task_env_config=EnvironmentConfig(
                    definition="application/shared-survey-form"
                ),
                tests_dir=task / "tests",
            )
            agent = PersonaJsonSurvey(
                logs_dir=paths.agent_dir,
                persona_path=str(persona_path),
                survey_task_path=str(task),
                model_name="openai/" + config["model"],
            )
            context = AgentContext()
            await environment.start(force_build=False)
            try:
                await agent.setup(environment)
                await agent.run("", environment, context)
                output_path = environment.resolve_container_path(
                    "/app/output/survey_result.json"
                )
                output = json.loads(output_path.read_text())
                record = json.loads((trial / "call_1.json").read_text())
                if (
                    len(output["answers"]) != 1
                    or output["answers"][0]["questionId"] != "p_redeem"
                    or float(output["answers"][0]["value"]) != record["p_redeem"]
                ):
                    raise ValueError("official output differs from accepted raw answer")
                save(trial / "agent_context.json", context.model_dump())
                rows.append(
                    {
                        "profile": profile_id,
                        "repetition": repetition,
                        "p_redeem": record["p_redeem"],
                        "status": "passed",
                        "elapsed_seconds": record["elapsed_seconds"],
                        "message_sha256": sha(encode(messages)),
                    }
                )
                print(
                    f"{trial.name}: passed, p_redeem={record['p_redeem']}", flush=True
                )
            except Exception:
                (trial / "failure.txt").write_text(traceback.format_exc())
                raise
            finally:
                await environment.stop(delete=False)
    return rows


def hashes(destination):
    return {
        str(p.relative_to(destination)): sha(p.read_bytes())
        for p in sorted(destination.rglob("*"))
        if p.is_file() and p.name != "checksums.json"
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--preflight-only",
        action="store_true",
        help="No HTTP or model calls; strict task/persona rendering only",
    )
    args = parser.parse_args()
    config = json.loads((HERE / "config.json").read_text())
    bootstrap()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    destination = HERE / "runs" / stamp
    destination.mkdir(parents=True, exist_ok=False)
    prepared = preflight(config, destination)
    save(destination / "config.json", config)
    if args.preflight_only:
        print(
            f"Strict preflight passed for {len(prepared)} profiles, no HTTP/model calls. {destination}"
        )
        return
    if git("status", "--porcelain", "--untracked-files=all"):
        raise RuntimeError("commit the reviewed code before model calls")
    if (
        git("rev-parse", "thesis-upstream-3633d8d^{commit}")
        != config["official_commit"]
    ):
        raise RuntimeError("official baseline tag changed")
    changes = git("diff", "--name-only", config["official_commit"], "HEAD").splitlines()
    allowed = ("thesis/e0a/", "application/tasks/survey_thesis-e0a-synthetic/")
    if any(p != "THESIS_PROVENANCE.md" and not p.startswith(allowed) for p in changes):
        raise RuntimeError("official implementation differs from fixed baseline")
    before = metadata(config)
    save(destination / "ollama_before.json", before)
    manifest = {
        "status": "running",
        "started_at": now(),
        "code_commit": git("rev-parse", "HEAD"),
        "official_commit": config["official_commit"],
        "branch": git("branch", "--show-current"),
        "agent": "matraix.agents.persona.json_survey:PersonaJsonSurvey",
        "agent_version": "1.0.0",
        "invocation": "direct unchanged official Agent class + official HostEnvironment, not CLI job scheduler",
        "identity": "official open-source agent with thesis local Ollama adapter; not hosted official product or MatrAIx-Persona-8B weights",
        "backend_provider": "local Ollama",
        "protocol_alias": "openai/qwen3.5:9b (protocol routing only)",
        "adapter_version": "1.0",
        "official_temperature": 0.7,
        "effective_options": config["options"],
        "think": config["think"],
        "model_digest": before["model_digest"],
        "cohort": {
            "id": "thesis-e0a-synthetic-v1",
            "sampling": "three manually specified invented profiles, not representative sampling",
            "profiles": [p[0] for p in prepared],
        },
        "task_id": "thesis_e0a_synthetic_v1",
        "python": sys.version,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "dependencies": {
            name: version(name)
            for name in [
                "matraix",
                "openai",
                "httpx",
                "pydantic",
                "litellm",
                "jinja2",
                "pyyaml",
            ]
        },
        "uv_lock_sha256": sha((ROOT / "uv.lock").read_bytes()),
        "external_api_cost": 0,
        "local_electricity_cost": "not measured",
        "limits": "No labels; no accuracy, calibration or full E0a acceptance claims",
        "tracked_input_hashes": {
            p: sha((ROOT / p).read_bytes())
            for p in git(
                "ls-files",
                "thesis/e0a",
                "application/tasks/survey_thesis-e0a-synthetic",
            ).splitlines()
        },
    }
    save(destination / "agent_manifest.json", manifest)
    server = Adapter(config)
    os.environ["OPENAI_API_KEY"] = "local-dummy-no-secret"
    os.environ["OPENAI_BASE_URL"] = f"http://127.0.0.1:{server.server_port}/v1"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        rows = asyncio.run(run_trials(config, prepared, destination, server))
        after = metadata(config)
        save(destination / "ollama_after.json", after)
        if (
            before["model_digest"] != after["model_digest"]
            or before["version"]["data"] != after["version"]["data"]
        ):
            raise RuntimeError("Ollama model/runtime changed during run")
        if server.budget.count != 6 or len(rows) != 6:
            raise RuntimeError("expected exactly six generations")
        pairs = []
        for profile in config["profiles"]:
            pair = [r for r in rows if r["profile"] == profile["id"]]
            if pair[0]["message_sha256"] != pair[1]["message_sha256"]:
                raise RuntimeError("repetition input changed")
            pairs.append(
                {
                    "profile": profile["id"],
                    "absolute_difference": abs(
                        pair[0]["p_redeem"] - pair[1]["p_redeem"]
                    ),
                }
            )
        save(
            destination / "summary.json",
            {
                "status": "passed",
                "actual_generations": server.budget.count,
                "rows": rows,
                "repetition_comparison": pairs,
                "accuracy_evaluated": False,
            },
        )
        manifest["status"] = "passed"
    except Exception:
        manifest["status"] = "failed"
        (destination / "failure.txt").write_text(traceback.format_exc())
        raise
    finally:
        server.shutdown()
        server.server_close()
        manifest.update(finished_at=now(), actual_generations=server.budget.count)
        save(destination / "agent_manifest.json", manifest)
        save(destination / "checksums.json", hashes(destination))
        print(f"Run artifacts: {destination}", flush=True)


if __name__ == "__main__":
    main()
