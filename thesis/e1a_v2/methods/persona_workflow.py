# ---------------------------------------------------------------------------
# E1a_v2 / E1b_v2 — P1 Persona Agent + E1b condition workflow.
#
# Builds on the proven e0a integration: a local Adapter HTTP server stands in
# for the official hosted OpenAI endpoint, while the official PersonaJsonSurvey
# + HostEnvironment run unchanged. The Adapter forwards each chat-completions
# request to the local Ollama daemon (qwen3.5:9b) and returns the response in
# the OpenAI wire format expected by PersonaJsonSurvey.
#
# Per the execution manual §17 and the freeze manifest, no raw urllib call is
# permitted. Every LLM condition (P1 + L0/L1/L2/P2/P3) MUST go through this
# file's `run_persona(...)` entry point.
#
# Per ISOLATION.md this module does NOT import from archive/method_dev/.
# ---------------------------------------------------------------------------
"""Persona Agent workflow for E1a_v2 P1 and E1b_v2 conditions.

Architecture (lifted from thesis/e0a/run.py, hardened for thesis mainline):

  PersonaJsonSurvey  ──>  openai/qwen3.5:9b  ──>  Adapter  ──>  Ollama
                              (HTTP Proxy)        (HTTP srv)       (qwen3.5:9b)

Identity (per E0a / E1 freeze manifest):
  - agent: matraix.agents.persona.persona_json_survey:PersonaJsonSurvey (1.0.0)
  - environment: harbor.environments.host:HostEnvironment (unchanged official)
  - backend: local Ollama 0.33.x, model qwen3.5:9b digest 6488c96fa5fa
  - protocol_alias: openai/qwen3.5:9b (routing only)
  - survey task: application/tasks/survey_thesis-e0a-synthetic
                  (E0a-validated; reused for E1 with thesis_e0a_synthetic_v1
                  instrument ID)

Determinism: temperature 0, fixed seed 20261012, identical qwen3.5:9b digest.
Per REVIEW_FIXES §8.4 this is a "deterministic path": each main sample is
generated exactly once. Repeated calls with identical config are not
additional independent samples.

Public entry points:
  - run_one(persona_yaml_path, task_path, destination) -> Record
  - run_method(method_id, ...) -> list[Record]
  - methods constants: P1, L0, L1, L2, P2, P3  (E1b uses the same workflow)
"""
from __future__ import annotations

import asyncio
import csv
import hashlib
import json
import os
import socket
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request
from collections.abc import Iterable
from dataclasses import dataclass, field, asdict
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path


# ---------------------------------------------------------------------------
# Bootstrap (per e0a/run.py): set up PYTHONPATH so the official PersonaJsonSurvey,
# HostEnvironment, and playground survey task loader resolve correctly.
# ---------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parents[3]  # thesis/e1a_v2/methods/persona_workflow.py
sys.path.insert(0, str(ROOT / "src"))
from matraix.launch_env import required_pythonpath_entries  # noqa: E402
sys.path[:0] = required_pythonpath_entries(ROOT)


# ---------------------------------------------------------------------------
# Paths and config
# ---------------------------------------------------------------------------

TASK = ROOT / "application/tasks/survey_thesis-e0a-synthetic"
ENV_DIR = ROOT / "environment/task-environments/application/shared-survey-form"

# L1 (structured X/S) uses a dedicated template so its rendered system prompt
# differs from L2/P1/P2 (per 手册 §17: "L1 与 L2 的任务事件、输出 schema、模型版本
# 和生成参数必须与 P1 一致" but the prompt SURFACE must differ).
# Per REVIEW_FIXES §8.3: if L2 and P1 render identical messages, they MUST be
# merged; that same rule binds L1 vs L2 since the structured-vs-narrative
# distinction is the whole point of the L1 / L2 mechanism comparison.
TEMPLATES = ROOT / "environment/agents/matraix/agents/persona/templates"
L1_TEMPLATE = TEMPLATES / "persona_system_structured.md.j2"

MODEL = "qwen3.5:9b"
MODEL_DIGEST_KNOWN = "6488c96fa5fa"  # e0a + D-R pilot, also §19 historical reference
SEED = 20261012
TEMPERATURE = 0
OLLAMA = "http://127.0.0.1:11434"
INSTRUMENT_ID = "thesis_e0a_synthetic_v1"  # reuse e0a instrument, per survey_task_path

# Per e0a: same options for thesis (validated against qwen3.5:9b).
# num_predict=256 is required for qwen3.5:9b under the e0a survey task template:
# the full survey envelope (instrument + answers[0].value) is ~85 tokens when the
# schema is rendered faithfully. e0a historical num_predict=64 was below this and
# would be cut at "length"; we raise to 256 here.
OLLAMA_OPTIONS = {"num_ctx": 8192, "num_predict": 256}


# ---------------------------------------------------------------------------
# Adapter: HTTP server that translates official OpenAI-style chat completions
# into Ollama /api/chat calls. Per REVIEW_FIXES §5 / §8.4, this is the only
# allowed bridge between PersonaJsonSurvey and the local model.
# ---------------------------------------------------------------------------


class _AdapterRequest:
    """Tracks one in-flight PersonaJsonSurvey request against the budget."""

    def __init__(self, expected_messages: list[dict], model: str):
        self.expected_messages = expected_messages
        self.model = model
        self.payload: dict | None = None
        self.response_payload: dict | None = None
        self.error: str | None = None


class _AdapterHTTPServer(HTTPServer):
    """Single-shot server: only one outstanding request at a time."""

    def __init__(self, addr, handler):
        super().__init__(addr, handler)
        self.active: _AdapterRequest | None = None
        self.budget_lock = threading.Lock()
        self.budget_count = 0

    def claim_budget(self) -> None:
        with self.budget_lock:
            self.budget_count += 1


def _ollama_post(path: str, payload: dict, timeout: float = 150.0) -> tuple[int, bytes]:
    """Direct POST to local Ollama. Distinct from any official API path."""
    request = urllib.request.Request(
        OLLAMA + path,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=timeout) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def _ollama_get(path: str, timeout: float = 30.0) -> tuple[int, bytes]:
    """GET helper for Ollama metadata endpoints. /api/tags and /api/version are GET."""
    request = urllib.request.Request(OLLAMA + path)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=timeout) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def _ollama_metadata() -> dict:
    """Snapshot Ollama version + model digest before/after the run."""
    snapshot = {}
    # /api/version and /api/tags are GET; /api/show accepts POST.
    status, body = _ollama_get("/api/version")
    snapshot["version"] = {"status": status, "data": json.loads(body) if status == 200 else None}
    status, body = _ollama_get("/api/tags")
    snapshot["tags"] = {"status": status, "data": json.loads(body) if status == 200 else None}
    status, body = _ollama_post("/api/show", {"model": MODEL}, timeout=30.0)
    snapshot["show"] = {"status": status, "data": json.loads(body) if status == 200 else None}
    # Ollama /api/tags returns {"models": [...]} directly.
    tags_body = snapshot["tags"]["data"] or {}
    models = tags_body.get("models", [])
    matches = [m for m in models if m.get("name") == MODEL]
    if len(matches) != 1:
        raise RuntimeError(
            f"Ollama model {MODEL} unavailable: found {len(matches)} matching models"
        )
    snapshot["model_digest"] = matches[0]["digest"]
    return snapshot


def _check_messages(actual: list[dict], expected: list[dict]) -> None:
    """Verify the official PersonaJsonSurvey client fed us the prepared messages."""
    if actual is None or len(actual) != len(expected):
        raise ValueError(
            f"message count mismatch: actual={len(actual) if actual else 0}, "
            f"expected={len(expected)}"
        )
    for a, e in zip(actual, expected):
        if a.get("role") != e.get("role") or a.get("content") != e.get("content"):
            raise ValueError("message content differs from prepared messages")


class _AdapterHandler(BaseHTTPRequestHandler):
    """One HTTP handler translating /v1/chat/completions -> Ollama /api/chat."""

    def log_message(self, *_args):
        return  # suppress per-request stderr

    def do_POST(self):
        server: _AdapterHTTPServer = self.server
        active = server.active
        if active is None:
            self._respond(503, {"error": "no active persona request"})
            return

        raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        try:
            request = json.loads(raw)
        except Exception as exc:
            self._respond(400, {"error": f"invalid JSON: {exc}"})
            return

        try:
            if self.path != "/v1/chat/completions":
                raise ValueError(f"unexpected path: {self.path}")
            # Accept the litellm-style "openai/<model>" protocol prefix that
            # PersonaJsonSurvey emits, as well as the bare model id that direct
            # OpenAI SDK calls (or our _run_direct_llm) would emit.
            req_model = request.get("model", "")
            if req_model not in (active.model, f"openai/{active.model}"):
                raise ValueError(f"model mismatch: {req_model!r} != {active.model!r}")
            if request.get("response_format") != {"type": "json_object"}:
                raise ValueError("JSON mode (response_format=json_object) is required")
            _check_messages(request.get("messages"), active.expected_messages)
            server.claim_budget()
            ollama_payload = {
                "model": active.model,
                "messages": request["messages"],
                "stream": False,
                "format": "json",
                "think": False,
                "options": {**OLLAMA_OPTIONS, "temperature": TEMPERATURE, "seed": SEED},
                "keep_alive": "5m",
            }
            status, body = _ollama_post("/api/chat", ollama_payload)
            active.payload = ollama_payload
            if status != 200:
                raise RuntimeError(f"Ollama HTTP {status}: {body[:200]!r}")
            response = json.loads(body)
            content = response["message"]["content"]
            # Validate that Ollama returned a parseable JSON object. Schema-level
            # validation (e.g. p_redeem present, value is a decimal string) is
            # done downstream by PersonaJsonSurvey + coerce_json +
            # _normalize_answers via the survey task's instrument. We do NOT
            # try to extract p_redeem here because the survey envelope uses
            # {answers: [{questionId, value}]} and extracting p_redeem directly
            # would break the official Agent flow.
            try:
                parsed = json.loads(content)
                if not isinstance(parsed, dict):
                    raise ValueError(f"Ollama returned non-object JSON: {parsed!r}")
            except Exception as exc:
                raise ValueError(f"invalid JSON content from model: {exc}")
            active.response_payload = {
                "id": f"adapter-{int(time.time())}",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": response["model"],
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": content},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": response.get("prompt_eval_count", 0),
                    "completion_tokens": response.get("eval_count", 0),
                    "total_tokens": (response.get("prompt_eval_count", 0)
                                     + response.get("eval_count", 0)),
                },
            }
            self._respond(200, active.response_payload)
        except Exception as exc:
            active.error = traceback.format_exc()
            self._respond(400, {"error": str(exc)})

    def _respond(self, code: int, body: dict):
        encoded = json.dumps(body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


# ---------------------------------------------------------------------------
# Persona YAML builder + bootstrap
# ---------------------------------------------------------------------------

BEHAVIOR_KEYS_FULL = [
    "history_window_days",
    "recency_days",
    "basket_count",
    "shopping_day_count",
    "positive_line_count",
    "return_line_count",
    "sales_total",
    "average_basket_sales",
    "retail_discount_basket_share",
    "coupon_discount_basket_share",
    "prior_completed_campaign_count",
    "prior_redeemed_campaign_count",
    "prior_redemption_rate",
    "top_commodity_1", "top_commodity_2", "top_commodity_3",
    "top_commodity_1_share", "top_commodity_2_share", "top_commodity_3_share",
    "other_commodity_share",
    "campaign_type", "campaign_valid_days",
    "campaign_unique_coupon_count", "campaign_unique_product_count",
    "campaign_department_count", "campaign_commodity_count",
    "campaign_brand_type_count",
    "target_product_sales_share", "target_commodity_sales_share",
    "target_product_recency_days", "no_target_product_purchase_flag",
]
BEHAVIOR_KEYS_MINIMAL = ["recency_days", "basket_count", "sales_total"]

PERSONA_SYSTEM_PROMPT = (
    "Use only these household behavior facts and the supplied campaign facts. "
    "Unknown psychological and demographic attributes must remain unknown. "
    "Do not invent a household member."
)


def _row_to_behavior(row: dict, keys: Iterable[str]) -> dict:
    """Project a frozen feature row into a persona behavior dict.

    The Agent only sees facts present in the input; unknown fields are not
    invented (per E0a / E1 freeze manifest).
    """
    behavior = {}
    for k in keys:
        v = row.get(k, "")
        if v == "":
            continue
        behavior[k] = v
    return behavior


def build_persona_yaml(row: dict, destination: Path, condition: str) -> tuple[Path, dict]:
    """Write one persona YAML file per (row, condition). Returns (path, behavior).

    The behavior dict is returned alongside the path so callers can audit the
    rendered messages without re-parsing the YAML.
    """
    if condition == "P3":
        behavior = _row_to_behavior(row, BEHAVIOR_KEYS_MINIMAL)
    elif condition == "L0":
        # Only S (campaign facts), no X (household behavior). Per manual §17.
        s_only = [
            "campaign_type", "campaign_valid_days",
            "campaign_unique_coupon_count", "campaign_unique_product_count",
            "campaign_department_count", "campaign_commodity_count",
            "campaign_brand_type_count",
        ]
        behavior = _row_to_behavior(row, s_only)
    elif condition == "L1":
        # Same X/S facts as P1 but in flat structured form (no narrative).
        # We project the same behavior keys as P1; the Agent template will
        # decide whether to render them as narrative or structured text.
        behavior = _row_to_behavior(row, BEHAVIOR_KEYS_FULL)
    elif condition in ("L2", "P1", "P2"):
        behavior = _row_to_behavior(row, BEHAVIOR_KEYS_FULL)
    else:
        raise ValueError(f"unknown condition: {condition}")

    payload = {
        "persona_id": row["row_id"],
        "version": "1.0",
        "display_name": "this household",
        "behavior": behavior,
        "system_prompt": PERSONA_SYSTEM_PROMPT,
    }
    out = destination / f"{row['row_id']}_{condition}.yaml"
    try:
        import yaml
        out.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    except ImportError:
        # Fallback: minimal hand-written YAML for the keys we control.
        lines = [f"persona_id: {row['row_id']}", "version: '1.0'",
                 "display_name: this household", "behavior:"]
        for k, v in behavior.items():
            lines.append(f"  {k}: {json.dumps(v)}")
        lines.append(f"system_prompt: {json.dumps(PERSONA_SYSTEM_PROMPT)}")
        out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out, behavior


# ---------------------------------------------------------------------------
# run_one: runs PersonaJsonSurvey once on a (persona_yaml, task) pair.
# ---------------------------------------------------------------------------


@dataclass
class Record:
    """One PersonaJsonSurvey invocation result, used for E1a/E1b sealing."""
    row_id: str
    campaign: int
    condition: str
    p_redeem: float | None
    status: str
    fallback: bool
    elapsed_seconds: float
    config_sha256: str
    message_sha256: str
    raw_response: str | None = None
    error: str | None = None
    parsed_payload: dict | None = field(default=None, repr=False)


async def _run_persona_once(
    persona_yaml_path: Path,
    task_path: Path,
    destination: Path,
    expected_messages: list[dict],
    adapter: _AdapterHTTPServer,
    row_id: str,
    condition: str,
    persona_template_path: Path | None = None,
) -> Record:
    """One async invocation. Returns Record with messages_sha256 captured."""
    from harbor.environments.host import HostEnvironment
    from harbor.models.agent.context import AgentContext
    from harbor.models.task.config import EnvironmentConfig
    from harbor.models.trial.paths import TrialPaths
    from matraix.agents.persona.json_survey import PersonaJsonSurvey

    trial = destination / f"{row_id}_{condition}"
    paths = TrialPaths(trial)
    paths.mkdir()

    msg_sha = hashlib.sha256(
        json.dumps(expected_messages, sort_keys=True).encode("utf-8")
    ).hexdigest()

    # Persist expected messages for the L2/P1 message-merge audit (§17).
    (trial / "expected_messages.json").write_text(
        json.dumps(expected_messages, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    request = _AdapterRequest(expected_messages=expected_messages, model=MODEL)
    adapter.active = request

    started = time.monotonic()
    environment = HostEnvironment(
        environment_dir=ENV_DIR,
        environment_name=f"thesis-e1a-{condition}",
        session_id=trial.name,
        trial_paths=paths,
        task_env_config=EnvironmentConfig(definition="application/shared-survey-form"),
        tests_dir=task_path / "tests",
    )
    # L1 uses a custom structured template (see persona_system_structured.md.j2).
    # All other conditions use the default PERSONA_SYSTEM_TEMPLATE so they share
    # the same baseline rendering surface; differences come from Agent workflow /
    # behavior dict content / target row, not template choice.
    agent = PersonaJsonSurvey(
        logs_dir=paths.agent_dir,
        persona_path=str(persona_yaml_path),
        survey_task_path=str(task_path),
        persona_template_path=str(persona_template_path) if persona_template_path else None,
        model_name="openai/" + MODEL,
    )
    context = AgentContext()

    config_sha = hashlib.sha256(
        f"{condition}|{MODEL}|{SEED}|{TEMPERATURE}|{json.dumps(OLLAMA_OPTIONS, sort_keys=True)}".encode()
    ).hexdigest()

    try:
        await environment.start(force_build=False)
        await agent.setup(environment)
        await agent.run("", environment, context)
        output_path = environment.resolve_container_path("/app/output/survey_result.json")
        output = json.loads(output_path.read_text())
        answers = output.get("answers", [])
        if len(answers) != 1 or answers[0].get("questionId") != "p_redeem":
            raise ValueError(f"unexpected output envelope: {output!r}")
        p_redeem = float(answers[0]["value"])
        elapsed = time.monotonic() - started
        return Record(
            row_id=row_id,
            campaign=int(json.loads((trial / "call_1.json").read_text())["campaign"])
                if (trial / "call_1.json").exists() else -1,
            condition=condition,
            p_redeem=p_redeem,
            status="passed",
            fallback=False,
            elapsed_seconds=elapsed,
            config_sha256=config_sha,
            message_sha256=msg_sha,
            raw_response=request.payload and json.dumps(request.response_payload),
        )
    except Exception as exc:
        elapsed = time.monotonic() - started
        (trial / "failure.txt").write_text(traceback.format_exc(), encoding="utf-8")
        return Record(
            row_id=row_id,
            campaign=-1,
            condition=condition,
            p_redeem=None,
            status="failed",
            fallback=True,
            elapsed_seconds=elapsed,
            config_sha256=config_sha,
            message_sha256=msg_sha,
            error=str(exc),
        )
    finally:
        adapter.active = None
        try:
            await environment.stop(delete=False)
        except Exception:
            pass


# Methods that go through PersonaJsonSurvey + HostEnvironment (full Agent).
# Per 手册 §17: P1/P2/P3 are the only conditions with the Agent workflow.
PERSONA_AGENT_METHODS = {"P1", "P2", "P3"}
# Methods that go through direct LLM (no Agent workflow).
DIRECT_LLM_METHODS = {"L0", "L1", "L2"}


def run_method(
    method_id: str,
    rows: list[dict],
    destination: Path,
    *,
    on_event=None,
) -> list[Record]:
    """Run one E1a/E1b method on a list of rows. Used by stage-4 dev pilots
    (D/R only — T prediction is in run_T_predictions.py).

    Per 手册 §17 + REVIEW_FIXES §8.3:
      - L0/L1/L2 are "direct LLM" controls (no Agent workflow). They DO NOT
        invoke PersonaJsonSurvey; they send a hand-constructed (system, user)
        pair to the Adapter and parse the JSON response directly.
      - P1/P2/P3 are the full Persona Agent. They invoke PersonaJsonSurvey +
        HostEnvironment and read survey_result.json.

    This separation is what makes the L1-L0 / L2-L1 / P1-L2 / P1-P2 / P1-P3
    mechanism comparisons interpretable. L2 and P1 receive the same facts
    and same precision (§17), so their rendered messages can be identical —
    if so the §8.3 message-equality rule applies and the E1b report must
    say so explicitly.
    """
    if method_id not in PERSONA_AGENT_METHODS | DIRECT_LLM_METHODS:
        raise ValueError(
            f"unknown method_id {method_id!r}; expected one of "
            f"{sorted(PERSONA_AGENT_METHODS | DIRECT_LLM_METHODS)}"
        )

    from matraix.agents.persona.loader import load_persona
    from matraix.agents.persona.templating import (
        PERSONA_SYSTEM_TEMPLATE,
        render_persona_template,
        resolve_persona_template,
    )
    from playground.inprocess.survey_eval import build_survey_task_prompt
    from playground.survey_task_content import load_survey_task_content_for_task_path

    destination.mkdir(parents=True, exist_ok=True)
    personas_dir = destination / "personas"
    personas_dir.mkdir(exist_ok=True)

    # Pre-compute the survey task prompt (used by L2 directly; L0/L2/L1 share
    # the same user prompt as the survey task would render).
    content = load_survey_task_content_for_task_path(str(TASK), repo_root=ROOT)
    survey_prompt = build_survey_task_prompt(instrument=content.instrument)

    # Snapshot Ollama before run.
    before_meta = _ollama_metadata()
    (destination / "ollama_before.json").write_text(
        json.dumps(before_meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # Build adapter.
    server = _AdapterHTTPServer(("127.0.0.1", 0), _AdapterHandler)
    port = server.server_port
    os.environ["OPENAI_API_KEY"] = "local-dummy-no-secret"
    os.environ["OPENAI_BASE_URL"] = f"http://127.0.0.1:{port}/v1"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    records: list[Record] = []
    try:
        for row in rows:
            yaml_path, behavior = build_persona_yaml(row, personas_dir, method_id)

            if method_id in DIRECT_LLM_METHODS:
                # Construct messages directly. No PersonaJsonSurvey involvement.
                # This is the "ordinary LLM" comparison surface per §17.
                rec = _run_direct_llm(
                    method_id=method_id,
                    row=row,
                    behavior=behavior,
                    survey_prompt=survey_prompt,
                    destination=destination,
                    adapter=server,
                    personas_dir=personas_dir,
                )
            else:
                # Persona Agent path. Render messages via PersonaJsonSurvey's
                # template so P1/P2/P3 share the same rendering surface as the
                # full Agent invocation that will hit the same Ollama backend.
                persona = load_persona(yaml_path)
                template_name = PERSONA_SYSTEM_TEMPLATE
                system = render_persona_template(
                    resolve_persona_template(persona, None, template_name),
                    persona,
                )
                # Strict-content check (per e0a): every supplied behavior value
                # must appear in the rendered prompt.
                for k, v in behavior.items():
                    token = f"{k.replace('_', ' ').title()}: {v}"
                    if token not in system and str(v) not in system:
                        raise RuntimeError(
                            f"profile fact {k}={v} missing from rendered prompt"
                        )
                messages = [
                    {"role": "system", "content": system},
                    {"role": "user", "content": survey_prompt},
                ]
                rec = asyncio.run(_run_persona_once(
                    persona_yaml_path=yaml_path,
                    task_path=TASK,
                    destination=destination,
                    expected_messages=messages,
                    adapter=server,
                    row_id=row["row_id"],
                    condition=method_id,
                ))
            records.append(rec)
            if on_event:
                on_event(rec)
    finally:
        server.shutdown()
        server.server_close()
        after_meta = _ollama_metadata()
        (destination / "ollama_after.json").write_text(
            json.dumps(after_meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        if before_meta.get("model_digest") != after_meta.get("model_digest"):
            raise RuntimeError("Ollama model digest changed during run")
        # Save run manifest
        manifest = {
            "method": method_id,
            "model": MODEL,
            "seed": SEED,
            "temperature": TEMPERATURE,
            "options": OLLAMA_OPTIONS,
            "instrument": INSTRUMENT_ID,
            "task_path": str(TASK.relative_to(ROOT)),
            "env_dir": str(ENV_DIR.relative_to(ROOT)),
            "n_records": len(records),
            "passed": sum(1 for r in records if r.status == "passed"),
            "failed": sum(1 for r in records if r.status == "failed"),
        }
        (destination / "run_manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return records


# ---------------------------------------------------------------------------
# Fallback policy (per 手册 §19: D/R-frozen overall rate, not arbitrary).
# ---------------------------------------------------------------------------


def fallback_rate(D_labels: dict[str, int]) -> float:
    """Return D-frozen overall redemption rate. Used when the Agent fails."""
    if not D_labels:
        return 0.0
    return sum(D_labels.values()) / len(D_labels)


# ---------------------------------------------------------------------------
# Direct LLM path: L0 / L1 / L2 (§17). No Agent, no HostEnvironment.
# ---------------------------------------------------------------------------


# Per-condition system prompts. L0 has no household facts; L1 lists X/S as
# field-value pairs; L2 renders X/S through the persona narrative template
# (same surface as P1, but no Agent workflow). All three share the same user
# prompt (the survey task prompt) so the only deliberate differences are the
# system-prompt surface.
DIRECT_LLM_SYSTEM_PROMPTS = {
    "L0": (
        "You are a retail forecasting model. Use only the supplied campaign "
        "facts (no household facts). Predict the redemption probability for "
        "an unknown household."
    ),
    "L1": (
        "You are a retail forecasting model. Read the structured household "
        "behavior and campaign facts as neutral field-value pairs. Do not "
        "invent narrative, motive, psychology, age, income, or individual "
        "member. Predict the redemption probability."
    ),
    "L2": (
        "You are this household. Read the household behavior and campaign "
        "facts as your own profile. Predict your redemption probability for "
        "this campaign. Do not invent unknown attributes."
    ),
}


def _build_structured_block(behavior: dict) -> str:
    """Render an X/S dict as a structured field-value block (used by L1)."""
    lines = ["## Household behavior (structured)"]
    for key in _iter_behavior_keys(behavior):
        lines.append(f"- {key.replace('_', ' ')}: {behavior[key]}")
    return "\n".join(lines)


def _build_narrative_block(behavior: dict) -> str:
    """Render an X/S dict as a flat narrative system prompt (used by L2).

    Per 手册 §17: "L2 接收与 P1 完全相同事实、字段顺序和数值精度的 Persona 文本".
    We iterate behavior in BEHAVIOR_KEYS_FULL order so the field order matches
    the PersonaJsonSurvey path; only the surrounding system-prompt template
    differs. §8.3 still requires that we report any visible diff in the final
    write-up.
    """
    lines = ["You are this household.", "",
             "## Behavior"]
    for key in _iter_behavior_keys(behavior):
        lines.append(f"- {key.replace('_', ' ').title()}: {behavior[key]}")
    return "\n".join(lines)


def _build_s_block(behavior: dict) -> str:
    """Render an S-only behavior dict for L0 (campaign facts, no X).

    L0 receives ONLY S facts (§17) — no household behavior. We preserve the
    S-only key order declared in build_persona_yaml.
    """
    s_keys = [
        "campaign_type", "campaign_valid_days",
        "campaign_unique_coupon_count", "campaign_unique_product_count",
        "campaign_department_count", "campaign_commodity_count",
        "campaign_brand_type_count",
    ]
    lines = ["## Campaign facts"]
    for key in s_keys:
        if key in behavior:
            lines.append(f"- {key.replace('_', ' ').title()}: {behavior[key]}")
    return "\n".join(lines)


def _iter_behavior_keys(behavior: dict) -> list[str]:
    """Iterate behavior in BEHAVIOR_KEYS_FULL order, falling back to sorted."""
    full_order = [k for k in BEHAVIOR_KEYS_FULL if k in behavior]
    extras = sorted(k for k in behavior if k not in BEHAVIOR_KEYS_FULL)
    return full_order + extras


def _run_direct_llm(
    method_id: str,
    row: dict,
    behavior: dict,
    survey_prompt: str,
    destination: Path,
    adapter: _AdapterHTTPServer,
    personas_dir: Path,
) -> Record:
    """Run one row through the direct-LLM path (L0/L1/L2). No Persona Agent."""
    trial = destination / f"{row['row_id']}_{method_id}"
    trial.mkdir(parents=True, exist_ok=True)

    # Build the system prompt. L1 uses structured field-value rendering; L2 uses
    # narrative rendering; L0 only has S facts.
    if method_id == "L0":
        sys_body = _build_s_block(behavior)
    elif method_id == "L1":
        sys_body = _build_structured_block(behavior)
    elif method_id == "L2":
        sys_body = _build_narrative_block(behavior)
    else:
        raise ValueError(f"unexpected method_id in direct path: {method_id}")

    system_prompt = f"{DIRECT_LLM_SYSTEM_PROMPTS[method_id]}\n\n{sys_body}"
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": survey_prompt},
    ]

    msg_sha = hashlib.sha256(
        json.dumps(messages, sort_keys=True).encode("utf-8")
    ).hexdigest()
    (trial / "expected_messages.json").write_text(
        json.dumps(messages, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    config_sha = hashlib.sha256(
        f"{method_id}|{MODEL}|{SEED}|{TEMPERATURE}|{json.dumps(OLLAMA_OPTIONS, sort_keys=True)}".encode()
    ).hexdigest()

    request = _AdapterRequest(expected_messages=messages, model=MODEL)
    adapter.active = request
    started = time.monotonic()
    p_redeem: float | None = None
    raw_content: str | None = None
    status = "failed"
    error: str | None = None
    fallback = False
    try:
        # Drive the LLM via OpenAI SDK. This is the same protocol PersonaJsonSurvey
        # uses (openai/<model>); the Adapter intercepts /v1/chat/completions and
        # forwards to local Ollama. This keeps the L0/L1/L2 path identical to
        # the P1 path on the wire format, just without the Persona Agent wrapper.
        from openai import OpenAI
        client = OpenAI(
            api_key=os.environ["OPENAI_API_KEY"],
            base_url=os.environ["OPENAI_BASE_URL"],
            timeout=120.0,
        )
        completion = client.chat.completions.create(
            model=f"openai/{MODEL}",
            response_format={"type": "json_object"},
            messages=messages,
        )
        raw_content = completion.choices[0].message.content
        if raw_content is None:
            raise RuntimeError("OpenAI client returned null content")
        parsed = json.loads(raw_content)
        answers = parsed.get("answers", [])
        if not answers:
            raise ValueError(f"missing answers: {parsed!r}")
        v = answers[0].get("value", "")
        try:
            p_redeem = float(v)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"could not coerce value to float: {v!r} ({exc})")
        if not (0.0 <= p_redeem <= 1.0):
            raise ValueError(f"p_redeem out of [0,1]: {p_redeem}")
        # Record the Ollama request/response captured by the handler.
        if request.payload is not None:
            (trial / "ollama_request.json").write_text(
                json.dumps(request.payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        if request.response_payload is not None:
            (trial / "adapter_response.raw.json").write_bytes(
                json.dumps(request.response_payload).encode("utf-8")
            )
        status = "passed"
        fallback = False
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        if request.error:
            error += f" | adapter_error: {request.error[:500]}"
    finally:
        adapter.active = None

    elapsed = time.monotonic() - started
    # Persist raw response for audit (if any).
    if raw_content is not None:
        (trial / "raw_response.json").write_text(
            json.dumps({"content": raw_content}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    if status != "passed" and error:
        (trial / "failure.txt").write_text(error, encoding="utf-8")

    # Per e0a/run.py the trial directory holds a `call_1.json` with the chosen
    # call summary; mimic that for consistency.
    (trial / "call_1.json").write_text(
        json.dumps(
            {
                "campaign": -1,
                "p_redeem": p_redeem,
                "elapsed_seconds": elapsed,
                "condition": method_id,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    return Record(
        row_id=row["row_id"],
        campaign=int(row.get("campaign", -1)),
        condition=method_id,
        p_redeem=p_redeem,
        status=status,
        fallback=fallback,
        elapsed_seconds=elapsed,
        config_sha256=config_sha,
        message_sha256=msg_sha,
        raw_response=raw_content,
        error=error,
    )


def apply_fallback(
    records: list[Record],
    D_labels: dict[str, int],
) -> list[Record]:
    """Replace None / failed p_redeem with the frozen fallback rate.

    Per 手册 §19: failure is reported, not silently coerced to 0 or non-redeem.
    Records that previously had fallback=True now carry the frozen rate in
    p_redeem. The original failure trace stays in `error`.
    """
    rate = fallback_rate(D_labels)
    for r in records:
        if r.status != "passed" and r.p_redeem is None:
            r.p_redeem = float(rate)
            r.fallback = True
    return records


# ---------------------------------------------------------------------------
# Persistence: write a jsonl prediction file matching prediction-contract.json
# ---------------------------------------------------------------------------


def write_predictions_jsonl(records: list[Record], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for r in records:
            payload = {
                "row_id": r.row_id,
                "campaign": r.campaign,
                "method_id": r.condition,
                "p_redeem": r.p_redeem,
                "fallback": r.fallback,
                "elapsed_seconds": round(r.elapsed_seconds, 3),
                "config_sha256": r.config_sha256,
                "message_sha256": r.message_sha256,
                "status": r.status,
            }
            if r.error:
                payload["error"] = r.error[:500]
            f.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")