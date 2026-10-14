"""Reviewable historical E0a execution; defaults to proposal display, zero calls."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import sys
import threading
import traceback
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path

import history
import run
from verify import verify


def check_code():
    branch = "thesis-integration-3633d8d"
    if run.git("branch", "--show-current") != branch:
        raise RuntimeError("historical checks belong on the integration branch")
    if run.git("status", "--porcelain", "--untracked-files=all"):
        raise RuntimeError("review, commit and back up code before real preparation/execution")
    if run.git("rev-parse", "HEAD") != run.git("rev-parse", "origin/" + branch):
        raise RuntimeError("current commit is not recorded as backed up to origin")
    baseline = "3633d8dab149a9482a71b024418a49ae828cc941"
    if run.git("rev-parse", "thesis-upstream-3633d8d^{commit}") != baseline:
        raise RuntimeError("official fixed baseline moved")
    allowed = ("thesis/e0a/", "application/tasks/survey_thesis-e0a-synthetic/")
    changes = run.git("diff", "--name-only", baseline, "HEAD").splitlines()
    if any(p != "THESIS_PROVENANCE.md" and not p.startswith(allowed) for p in changes):
        raise RuntimeError("official source differs from fixed baseline")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, default=history.HERE / "history-plan.json")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--prepare-only", action="store_true", help="Approved real inputs and official prompt rendering; zero HTTP/model calls")
    modes.add_argument("--execute", action="store_true", help="Requires recorded confirmation; maximum sixty local calls")
    args = parser.parse_args()
    plan = json.loads(args.plan.read_text())
    history.validate_plan(plan, require_approval=args.prepare_only or args.execute)
    if not (args.prepare_only or args.execute):
        print(f"Plan display only. No source data read or model calls. Status: {plan['status']}.")
        return
    pinned = json.loads((history.HERE / "config.json").read_text())
    for key in ("official_commit", "model", "options", "think"):
        if plan[key] != pinned[key]:
            raise ValueError("historical model/runtime configuration differs from reviewed synthetic configuration")
    check_code()
    run.bootstrap()
    destination = history.HERE / "runs" / ("history-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ"))
    destination.mkdir(parents=True, exist_ok=False)
    manifest = {"status": "preparing", "started_at": run.now(), "code_commit": run.git("rev-parse", "HEAD"),
                "official_commit": plan["official_commit"], "branch": run.git("branch", "--show-current"),
                "approval_reference": plan["approval_reference"], "plan_sha256": history.digest(args.plan),
                "actual_generations": 0, "identity": "MatrAIx-compatible domain persona agent, official open-source Survey Agent, local Qwen backend",
                "cohort": {"id": f"thesis-e0a-history-{plan['campaign']}-20x3-v1", "sampling": plan["sampling"], "seed": plan["sampling_seed"], "sample_size": plan["sample_size"]},
                "repetitions": plan["repetitions"], "maximum_generations": plan["maximum_generations"],
                "task_id": plan["instrument_id"], "agent": "matraix.agents.persona.json_survey:PersonaJsonSurvey",
                "agent_version": "1.0.0", "invocation": "direct official Agent + HostEnvironment; no CLI scheduler/reward verifier",
                "backend_provider": "local Ollama", "protocol_alias": "openai/" + plan["model"],
                "official_temperature": 0.7, "effective_options": plan["options"], "think": plan["think"],
                "python": sys.version, "platform": platform.platform(), "machine": platform.machine(),
                "external_api_cost": 0, "local_electricity_cost": "not measured",
                "limits": "No labels, accuracy, calibration, representative cohort or official hosted product conclusions"}
    server = None
    try:
        config, task = history.prepare(plan, destination)
        with history.task_binding(task):
            prepared = run.preflight(config, destination, task=task)
        manifest["task_lookup_binding"] = "temporary in-memory questionnaire ID mapping to generated run task; restored after rendering/execution"
        run.save(destination / "preflight_messages.json", {p[0]: p[2] for p in prepared})
        manifest["dependencies"] = {name: version(name) for name in ("matraix", "openai", "httpx", "pydantic", "litellm", "jinja2", "pyyaml")}
        manifest["uv_lock_sha256"] = history.digest(run.ROOT / "uv.lock")
        manifest["tracked_input_hashes"] = {p: history.digest(run.ROOT / p) for p in run.git("ls-files", "thesis/e0a", "application/tasks/survey_thesis-e0a-synthetic").splitlines()}
        manifest["prepared_input_hashes"] = run.hashes(destination)
        if args.prepare_only:
            manifest["status"] = "prepared_only_no_model_calls"
            print(f"Prepared and rendered {len(prepared)} inputs. No HTTP/model calls. {destination}")
            return
        before = run.metadata(config)
        run.save(destination / "ollama_before.json", before)
        if before["model_digest"] != plan["expected_model_digest"] or before["version"]["data"]["version"] != plan["expected_ollama_version"]:
            raise RuntimeError("model or Ollama version changed; review before generation")
        manifest["model_digest"] = before["model_digest"]
        manifest["status"] = "running"
        run.save(destination / "agent_manifest.json", manifest)
        server = run.Adapter(config)
        os.environ["OPENAI_API_KEY"] = "local-dummy-no-secret"
        os.environ["OPENAI_BASE_URL"] = f"http://127.0.0.1:{server.server_port}/v1"
        threading.Thread(target=server.serve_forever, daemon=True).start()
        with history.task_binding(task):
            rows = asyncio.run(run.run_trials(config, prepared, destination, server, task=task))
        after = run.metadata(config)
        run.save(destination / "ollama_after.json", after)
        if before["model_digest"] != after["model_digest"] or before["version"]["data"] != after["version"]["data"]:
            raise RuntimeError("model/runtime changed during execution")
        if server.budget.count != plan["maximum_generations"] or len(rows) != plan["maximum_generations"]:
            raise RuntimeError("expected exactly the approved number of accepted generations")
        pairs = repetition_summary(config, rows)
        run.save(destination / "summary.json", {"status": "passed", "actual_generations": server.budget.count,
                 "rows": rows, "repetition_comparison": pairs, "accuracy_evaluated": False})
        manifest["status"] = "passed"
    except Exception:
        manifest["status"] = "failed"
        (destination / "failure.txt").write_text(traceback.format_exc())
        raise
    finally:
        if server:
            server.shutdown()
            server.server_close()
            manifest["actual_generations"] = server.budget.count
        manifest["finished_at"] = run.now()
        run.save(destination / "agent_manifest.json", manifest)
        run.save(destination / "checksums.json", run.hashes(destination))
        print(f"Local evidence: {destination}", flush=True)
    print(json.dumps(verify(destination), ensure_ascii=False, indent=2))


def repetition_summary(config, rows):
    comparisons = []
    for profile in config["profiles"]:
        repeated = [r for r in rows if r["profile"] == profile["id"]]
        if (len(repeated) != config["repetitions"]
                or {r["repetition"] for r in repeated} != set(range(1, config["repetitions"] + 1))
                or len({r["message_sha256"] for r in repeated}) != 1):
            raise RuntimeError("repetition inputs or identities differ")
        values = [r["p_redeem"] for r in sorted(repeated, key=lambda r: r["repetition"])]
        comparisons.append({"profile": profile["id"], "values": values,
                            "mean": sum(values) / len(values), "range": max(values) - min(values)})
    return comparisons


if __name__ == "__main__":
    main()
