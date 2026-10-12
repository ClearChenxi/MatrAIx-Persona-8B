"""R 上 P1 (v2 已冻结 prompt) 区分度 + 稳定性检查。

Per 手册 步骤 19:
- R 上固定约 100 条家庭—活动，按活动和冻结行为群体分层
- 合法率 >= 99%
- 合法分数不全相同
- >= 20 条的 R 活动中至少 3 个不同合法分数
- 任一单一分数不得占 90% 以上

不读 T 标签，不进入 T 预测。
"""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from io_utils import load_split  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]  # matraix repo root
DATA = ROOT / "thesis/e1_data/runs/e1-data-20261011T042328.304949Z/private"

# Frozen prompt v2 (selected by D macro_AP = 0.1485)
PROMPT_V2 = (
    "Using only the observed household facts and campaign facts, estimate the chance of "
    "at least one valid coupon redemption during this campaign. Treat unknown information "
    "as unknown and do not invent facts. Return only {\"p_redeem\": number}, where number "
    "is finite and between 0 and 1."
)
MODEL = "qwen3.5:9b"
OLLAMA = "http://127.0.0.1:11434"

# Hash-based sampling: 100 records, stratified by campaign & behavior group tier
TARGET_N = 100
SEED = "20261012-r-v2-pilot-v1"


def _digest(s: str) -> str:
    import hashlib
    return hashlib.sha256(s.encode()).hexdigest()


def select_r_sample(R_feats: list[dict], R_links: dict, n: int = TARGET_N) -> list[dict]:
    """Stratified sample by campaign + behavior group tier (low/mid/high).
    Falls back to round-robin if a stratum is too small.

    Note: prior_redeemed_campaign_count lives on features, not links.
    """
    by_stratum: dict[tuple[int, int], list[dict]] = defaultdict(list)
    for r in R_feats:
        c = int(R_links[r["row_id"]]["campaign"])
        prior_redeemed_str = r.get("prior_redeemed_campaign_count", "0") or "0"
        try:
            prior_redeemed = int(prior_redeemed_str)
        except ValueError:
            prior_redeemed = 0
        # 3-tier bucket
        if prior_redeemed == 0:
            tier = 0
        elif prior_redeemed <= 1:
            tier = 1
        else:
            tier = 2
        by_stratum[(c, tier)].append(r)

    for k in by_stratum:
        by_stratum[k].sort(key=lambda r: _digest(f"{SEED}:{r['row_id']}"))

    strata = sorted(by_stratum.keys())
    selected: list[dict] = []
    indices = {k: 0 for k in strata}
    while len(selected) < n and any(indices[k] < len(by_stratum[k]) for k in strata):
        for k in strata:
            if indices[k] < len(by_stratum[k]):
                selected.append(by_stratum[k][indices[k]])
                indices[k] += 1
                if len(selected) >= n:
                    break
    return selected


def _call_ollama(payload_json: str) -> tuple[float | None, dict]:
    """Call Ollama, parse p_redeem from JSON. Returns (score, info)."""
    request = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": "Return only the requested JSON. Do not invent facts."},
            {"role": "user", "content": payload_json + "\n\n" + PROMPT_V2},
        ],
        "stream": False,
        "format": "json",
        "think": False,
        "options": {"temperature": 0, "seed": 20261012, "num_ctx": 8192, "num_predict": 64},
    }
    started = time.monotonic()
    try:
        req = urllib.request.Request(
            f"{OLLAMA}/api/chat",
            data=json.dumps(request).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read().decode()
        elapsed = time.monotonic() - started
        resp = json.loads(raw)
        content = resp.get("message", {}).get("content", "{}")
        try:
            obj = json.loads(content)
            score = float(obj.get("p_redeem", ""))
            if not (0.0 <= score <= 1.0):
                raise ValueError(f"out-of-range p_redeem={score}")
            return score, {"status": "valid", "elapsed": elapsed, "raw": content}
        except (json.JSONDecodeError, ValueError, TypeError) as e:
            return None, {"status": "invalid", "elapsed": elapsed, "raw": content, "err": str(e)}
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return None, {"status": "error", "elapsed": time.monotonic() - started, "err": str(e)}


def main():
    R_feats, _, R_links = load_split("R")
    sample = select_r_sample(R_feats, R_links, n=TARGET_N)
    print(f"R total: {len(R_feats)}, sampled: {len(sample)}")

    # Load payloads from index (index has line_number, row_id columns)
    import csv as _csv
    index_path = DATA / "model_payload_index_R.csv"
    payloads_path = DATA / "model_payloads_R.jsonl"
    with open(index_path, newline="") as f:
        reader = _csv.DictReader(f)
        rows = [r["row_id"] for r in reader]
    payloads = [json.loads(l) for l in open(payloads_path)]
    payload_by_id = {rows[i]: payloads[i] for i in range(len(rows))}

    results = []
    t0 = time.monotonic()
    for i, r in enumerate(sample, 1):
        rid = r["row_id"]
        payload = payload_by_id.get(rid)
        if payload is None:
            print(f"[{i}] missing payload for {rid}, skipping", flush=True)
            continue
        payload_json = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        score, info = _call_ollama(payload_json)
        results.append({
            "row_id": rid,
            "campaign": int(R_links[rid]["campaign"]),
            "p_redeem": score,
            "elapsed_seconds": round(info["elapsed"], 2),
            "status": info["status"],
        })
        if i % 10 == 0 or i == len(sample):
            print(f"[{i}/{len(sample)}] valid={sum(1 for x in results if x['status']=='valid')}, "
                  f"elapsed={time.monotonic()-t0:.0f}s", flush=True)

    # === Distinguishability / Stability checks ===
    valid = [r for r in results if r["status"] == "valid"]
    by_camp = defaultdict(list)
    for r in valid:
        by_camp[r["campaign"]].append(r["p_redeem"])

    summary = {
        "method": "P1-v2-on-R",
        "n_total": len(results),
        "n_valid": len(valid),
        "valid_rate": len(valid) / len(results) if results else 0,
        "by_campaign": {str(c): {"n": len(vs), "distinct": len(set(vs)),
                                  "top_value_share": Counter(vs).most_common(1)[0][1] / len(vs)
                                                   if vs else 0}
                        for c, vs in sorted(by_camp.items())},
        "distinct_total": len(set(r["p_redeem"] for r in valid)),
        "max_single_value_share": Counter(r["p_redeem"] for r in valid).most_common(1)[0][1] / len(valid)
                                  if valid else 0,
        "thresholds_pass": {},
    }
    # Threshold checks per 步骤 19
    summary["thresholds_pass"]["valid_rate_>=0.99"] = summary["valid_rate"] >= 0.99
    summary["thresholds_pass"]["not_all_same"] = summary["distinct_total"] >= 2
    big_camps = [c for c, v in by_camp.items() if len(v) >= 20]
    summary["thresholds_pass"]["all_big_camps_have_>=3_distinct"] = all(
        len(set(by_camp[c])) >= 3 for c in big_camps
    )
    summary["thresholds_pass"]["max_single_share_<=0.90"] = summary["max_single_value_share"] <= 0.90
    summary["all_pass"] = all(summary["thresholds_pass"].values())

    out_path = Path(__file__).resolve().parent / "r_v2_pilot_results.json"
    out_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    print("\n=== Summary ===")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"\nWrote {out_path}")
    print(f"ALL PASS: {summary['all_pass']}")


if __name__ == "__main__":
    main()
