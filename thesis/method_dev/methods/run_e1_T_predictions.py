"""Run all E1a + E1b T predictions on the final_evaluation pool (1,386 rows).

This script is the single entry point for the entire T-prediction suite.
It runs sequentially through every frozen method, writes each method's
predictions to a separate jsonl file, and supports resume from prior runs.

Per 手册 §8.5, only `final_evaluation` pool rows are predicted. T labels
remain sealed — this script must NEVER read `labels_T*`.
"""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from io_utils import load_split, top_departments_from_d, to_tabular  # noqa: E402

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

ROOT = Path("/Users/jiangchenxi/科研内容/代码仓库/MatrAIx-Persona-8B")
E1_RUN = ROOT / "thesis/e1_data/runs/e1-data-20261011T042328.304949Z"
T_OUT = ROOT / "thesis/method_dev/runs/e1a-e1b-T-20261012"
T_OUT.mkdir(parents=True, exist_ok=True)
PRIVATE_OUT = T_OUT / "private"
PRIVATE_OUT.mkdir(exist_ok=True)

# Frozen prompts (per e1a_freeze_manifest.md)
PROMPT_V2 = (
    "Using only the observed household facts and campaign facts, estimate the chance of "
    "at least one valid coupon redemption during this campaign. Treat unknown information "
    "as unknown and do not invent facts. Return only {\"p_redeem\": number}, where number "
    "is finite and between 0 and 1."
)
# E1b prompts per 手册 step 17 (must differ in input but share model + output schema)
PROMPT_L0_CAMPAIGN = (
    "Estimate the probability that some household redeems at least one valid coupon "
    "during this campaign. Use only the campaign facts provided. Return exactly one JSON "
    "object with a finite number from 0 to 1 under the key p_redeem."
)
PROMPT_L1_STRUCTURED = (
    "Given the household and campaign facts below in structured form, estimate the "
    "probability of at least one valid coupon redemption. Return only {\"p_redeem\": number}."
)
PROMPT_L2_PERSONA = (
    "You are simulating a specific household. Given the persona description and the "
    "campaign facts, estimate the probability of at least one valid coupon redemption. "
    "Return only {\"p_redeem\": number}."
)
PROMPT_P2_MISALIGNED = PROMPT_V2  # Same model + prompt; only X is misaligned
PROMPT_P3_MINIMAL = PROMPT_V2

MODEL = "qwen3.5:9b"
OLLAMA = "http://127.0.0.1:11434"
FALLBACK = 87 / 1410  # 0.06170212765957447
SEED = 20261012


def load_T_features():
    """Return (feats, links, link_map, payloads, payload_by_id) for T.
    Filters to final_evaluation rows.
    """
    feats_T, _, links_T = load_split("T")
    # filter to final_evaluation
    feats_eval = [r for r in feats_T if links_T[r["row_id"]]["e2_role"] == "final_evaluation"]
    print(f"T total: {len(feats_T)}, final_evaluation: {len(feats_eval)}")

    # load payloads
    import csv as _csv
    idx_path = E1_RUN / "private/model_payload_index_T.csv"
    pay_path = E1_RUN / "private/model_payloads_T.jsonl"
    with open(idx_path, newline="") as f:
        reader = _csv.DictReader(f)
        rows = [r["row_id"] for r in reader]
    payloads = [json.loads(l) for l in open(pay_path)]
    payload_by_id = {rows[i]: payloads[i] for i in range(len(rows))}
    return feats_eval, links_T, payload_by_id


def load_D_trained_baselines():
    """Train N0/N1/B1/B2/B3 on full D, return inference fns for T."""
    D_feats, D_labels, D_links = load_split("D")
    top_deps = top_departments_from_d(D_feats, n=20)

    # N0: global rate
    rate = sum(D_labels.values()) / len(D_labels)

    # N1: aggregate D to per-campaign, fit logistic on S-only
    def _campaign_key(r):
        return (
            f"{r.get('campaign_type','')}|{r.get('campaign_valid_days','')}|"
            f"{r.get('campaign_unique_coupon_count','')}|"
            f"{r.get('campaign_department_count','')}|"
            f"{r.get('campaign_brand_type_count','')}"
        )
    by_camp_y = defaultdict(list)
    by_camp_X = {}
    for r in D_feats:
        k = _campaign_key(r)
        if k not in by_camp_X:
            by_camp_X[k] = (r.get('campaign_type','')=='TypeC',
                              float(r.get('campaign_valid_days',0) or 0),
                              float(r.get('campaign_unique_coupon_count',0) or 0),
                              float(r.get('campaign_unique_product_count',0) or 0),
                              float(r.get('campaign_department_count',0) or 0),
                              float(r.get('campaign_commodity_count',0) or 0),
                              float(r.get('campaign_brand_type_count',0) or 0),
                              float(r.get('campaign_unknown_taxonomy_flag',0) or 0))
        by_camp_y[k].append(D_labels[r["row_id"]])
    keys = sorted(by_camp_X)
    Xn = [by_camp_X[k] for k in keys]
    yn = [1 if sum(by_camp_y[k]) > 0 else 0 for k in keys]
    from sklearn.linear_model import LogisticRegression as _LR
    if sum(yn) >= 2 and (len(yn) - sum(yn)) >= 2:
        m = _LR(C=1.0, max_iter=1000).fit(Xn, yn)
        camp_scores_N1 = {k: float(m.predict_proba([Xn[i]])[0, 1]) for i, k in enumerate(keys)}
    else:
        camp_scores_N1 = {k: (sum(by_camp_y[k]) / len(by_camp_y[k])) for k in keys}

    # B1: rank-based, no model
    # we'll compute at T-time

    # B2: logistic C=0.1 on full X/S
    X_d, _ = to_tabular(D_feats, top_deps)
    X_d = np.asarray(X_d)
    y_d = np.array([D_labels[r["row_id"]] for r in D_feats], dtype=np.int32)
    scaler = StandardScaler().fit(X_d)
    X_d_s = scaler.transform(X_d)
    b2 = LogisticRegression(C=0.1, max_iter=2000).fit(X_d_s, y_d)

    # B3: catboost depth=5, lr=0.1
    from catboost import CatBoostClassifier
    import pandas as pd
    NUM_MISSING = {"prior_redemption_rate"}
    base_num = [
        "history_window_days", "recency_days", "basket_count", "shopping_day_count",
        "positive_line_count", "return_line_count", "sales_total", "average_basket_sales",
        "zero_sales_total_flag", "retail_discount_basket_share", "coupon_discount_basket_share",
        "prior_completed_campaign_count", "prior_redeemed_campaign_count", "prior_redemption_rate",
        "no_prior_campaign_flag",
        "top_commodity_1_share", "top_commodity_2_share", "top_commodity_3_share", "other_commodity_share",
        "product_taxonomy_missing_flag",
        "campaign_valid_days", "campaign_unique_coupon_count",
        "campaign_unique_product_count", "campaign_department_count", "campaign_commodity_count",
        "campaign_brand_type_count",
        "campaign_top_commodity_1_weight", "campaign_top_commodity_2_weight",
        "campaign_top_commodity_3_weight", "campaign_top_commodity_4_weight",
        "campaign_top_commodity_5_weight", "campaign_top_commodity_6_weight",
        "campaign_top_commodity_7_weight", "campaign_top_commodity_8_weight",
        "campaign_other_commodity_weight", "campaign_unknown_taxonomy_flag",
        "target_product_sales_share", "target_commodity_sales_share",
        "target_product_recency_days", "no_target_product_purchase_flag",
    ]
    dep_cols = [f"dep_spend__{d}" for d in top_deps]
    num_cols = base_num + dep_cols
    cat_cols = ["campaign_type", "top_commodity_1", "top_commodity_2", "top_commodity_3"]
    def build_df(rows):
        data = {c: [] for c in num_cols + cat_cols}
        for r in rows:
            for c in base_num:
                v = r.get(c, "")
                if v == "":
                    data[c].append(np.nan)
                else:
                    try:
                        f = float(v)
                        if c in NUM_MISSING and (f != f):
                            data[c].append(np.nan)
                        else:
                            data[c].append(f)
                    except ValueError:
                        data[c].append(np.nan)
            dep = r.get("department_spend_shares_json", {}) or {}
            for d in top_deps:
                try:
                    data[f"dep_spend__{d}"].append(float(dep.get(d, 0.0)))
                except (ValueError, TypeError):
                    data[f"dep_spend__{d}"].append(0.0)
            for c in cat_cols:
                data[c].append(r.get(c, "") or "")
        df = pd.DataFrame(data)
        for c in num_cols:
            df[c] = df[c].fillna(-1.0)
        return df
    D_df = build_df(D_feats)
    b3 = CatBoostClassifier(iterations=1000, depth=5, learning_rate=0.1,
                            loss_function="Logloss", random_seed=SEED,
                            cat_features=cat_cols, verbose=False).fit(D_df, y_d)
    return {
        "N0_rate": rate,
        "N1_camp_scores": camp_scores_N1,
        "N1_key_fn": _campaign_key,
        "B1_train": (D_feats, D_links, D_labels),
        "B2_model": (b2, scaler, top_deps),
        "B3_model": (b3, build_df, cat_cols),
    }


def b1_score(row, link):
    prior_redeemed = int(link.get("prior_redeemed_campaign_count", "0") or 0)
    prior_rate = row.get("prior_redemption_rate", "")
    if prior_rate == "":
        prior_rate_v = -1.0
    else:
        try:
            prior_rate_v = float(prior_rate)
        except ValueError:
            prior_rate_v = -1.0
    recency = float(row.get("recency_days", 9999) or 9999)
    baskets = float(row.get("basket_count", 0) or 0)
    sales = float(row.get("sales_total", 0) or 0)
    return (prior_redeemed, prior_rate_v, -recency, baskets, sales)


def b1_rank_scores(eval_feats, links):
    """Compute B1 rank-based scores for eval rows."""
    import hashlib
    scored = []
    for r in eval_feats:
        link = links[r["row_id"]]
        score = b1_score(r, link)
        h = hashlib.sha256(f"{link.get('household_key','')}|{link.get('campaign','')}".encode()).digest()
        tie = int.from_bytes(h[:4], "big")
        scored.append((score, tie))
    n = len(scored)
    sorted_idx = sorted(range(n), key=lambda i: (-scored[i][0][0], -scored[i][0][1],
                                                  -scored[i][0][2], -scored[i][0][3],
                                                  -scored[i][0][4], scored[i][1]))
    rank_scores = [0.0] * n
    for rank, idx in enumerate(sorted_idx):
        rank_scores[idx] = (n - rank) / n
    return rank_scores


def b2_predict(b2_pack, eval_feats, top_deps):
    b2, scaler, _ = b2_pack
    X_t, _ = to_tabular(eval_feats, top_deps)
    X_t = np.asarray(X_t)
    return b2.predict_proba(scaler.transform(X_t))[:, 1].tolist()


def b3_predict(b3_pack, eval_feats):
    b3, build_df, cat_cols = b3_pack
    df = build_df(eval_feats)
    return b3.predict_proba(df)[:, 1].tolist()


def call_ollama(prompt: str, payload_json: str, retries: int = 2) -> tuple[float | None, dict]:
    request = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": "Return only the requested JSON. Do not invent facts."},
            {"role": "user", "content": payload_json + "\n\n" + prompt},
        ],
        "stream": False,
        "format": "json",
        "think": False,
        "options": {"temperature": 0, "seed": SEED, "num_ctx": 8192, "num_predict": 64},
    }
    started = time.monotonic()
    last_err = None
    for attempt in range(1, retries + 2):
        try:
            req = urllib.request.Request(
                f"{OLLAMA}/api/chat",
                data=json.dumps(request).encode(),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=60) as r:
                raw = r.read().decode()
            resp = json.loads(raw)
            content = resp.get("message", {}).get("content", "{}")
            elapsed = time.monotonic() - started
            try:
                obj = json.loads(content)
                score = float(obj.get("p_redeem", ""))
                if 0.0 <= score <= 1.0:
                    return score, {"status": "valid", "attempt": attempt, "elapsed": elapsed,
                                   "raw": content}
            except Exception as e:
                last_err = f"parse: {e}"
                continue
        except Exception as e:
            last_err = str(e)
            continue
    return None, {"status": "fallback", "attempts": retries + 1, "err": last_err}


def _f(v, default=0.0):
    try:
        return float(v)
    except (ValueError, TypeError):
        return default


def persona_text_for(r: dict, top_deps: list[str]) -> str:
    """Render row dict as a Persona-style text block. Used for E1b L2/P2/P3.
    For P3 (minimal), strip to R/F/M only.
    """
    prior_redeemed = int(_f(r.get("prior_redeemed_campaign_count", 0)))
    prior_rate = r.get("prior_redemption_rate", "") or "unknown"
    recency = _f(r.get("recency_days", "?"))
    baskets = _f(r.get("basket_count", 0))
    sales = _f(r.get("sales_total", 0))
    text = (
        f"Past 84 days: {baskets:.0f} shopping baskets, ${sales:.2f} total spend. "
        f"Most recent purchase: {recency:.0f} days ago. "
        f"Prior completed campaigns: {int(_f(r.get('prior_completed_campaign_count',0)))}, "
        f"redeemed: {prior_redeemed}, redemption rate: {prior_rate}. "
        f"Top commodities: {r.get('top_commodity_1','')}, {r.get('top_commodity_2','')}, {r.get('top_commodity_3','')}. "
        f"Discount basket share: {_f(r.get('retail_discount_basket_share',0)):.2f}, "
        f"coupon discount: {_f(r.get('coupon_discount_basket_share',0)):.2f}."
    )
    return text


def minimal_persona_text_for(r: dict) -> str:
    """P3 (minimal) persona: R/F/M only."""
    recency = _f(r.get("recency_days", "?"))
    baskets = _f(r.get("basket_count", 0))
    sales = _f(r.get("sales_total", 0))
    return (f"Past 84 days: {baskets:.0f} shopping baskets, ${sales:.2f} total spend. "
            f"Most recent purchase: {recency:.0f} days ago.")


def campaign_facts_text_for(r: dict) -> str:
    """L0/S-only campaign facts."""
    return (
        f"Campaign type: {r.get('campaign_type','')}, "
        f"valid days: {r.get('campaign_valid_days','')}, "
        f"unique coupons: {r.get('campaign_unique_coupon_count','')}, "
        f"departments: {r.get('campaign_department_count','')}, "
        f"top commodity weights: "
        f"{r.get('campaign_top_commodity_1_weight','')} / "
        f"{r.get('campaign_top_commodity_2_weight','')} / "
        f"{r.get('campaign_top_commodity_3_weight','')} / "
        f"{r.get('campaign_top_commodity_4_weight','')} / "
        f"{r.get('campaign_top_commodity_5_weight','')} / "
        f"{r.get('campaign_top_commodity_6_weight','')} / "
        f"{r.get('campaign_top_commodity_7_weight','')} / "
        f"{r.get('campaign_top_commodity_8_weight','')}."
    )


def structured_xs_text_for(r: dict) -> str:
    """L1 structured X/S facts (no persona narrative)."""
    fields = [
        ("R (days since last purchase)", r.get("recency_days", "?")),
        ("F (basket count)", r.get("basket_count", "?")),
        ("M (sales total)", r.get("sales_total", "?")),
        ("prior_redeemed_campaign_count", r.get("prior_redeemed_campaign_count", 0)),
        ("prior_redemption_rate", r.get("prior_redemption_rate", "unknown")),
        ("coupon_discount_basket_share", r.get("coupon_discount_basket_share", 0)),
        ("target_product_sales_share", r.get("target_product_sales_share", 0)),
        ("campaign_type", r.get("campaign_type", "")),
        ("campaign_valid_days", r.get("campaign_valid_days", "")),
    ]
    return "\n".join(f"  {k}: {v}" for k, v in fields)


def append_jsonl(path: Path, record: dict):
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        f.flush()


def load_existing(path: Path) -> set:
    if not path.exists():
        return set()
    done = set()
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            r = json.loads(line)
            done.add(r["row_id"])
        except Exception:
            pass
    return done


def main():
    print("=" * 60)
    print("E1a + E1b T predictions on final_evaluation pool")
    print("=" * 60)

    # Load T
    eval_feats, links, payload_by_id = load_T_features()
    eval_links = {r["row_id"]: links[r["row_id"]] for r in eval_feats}
    eval_ids = [r["row_id"] for r in eval_feats]

    # Train D baselines
    print("\n[1/3] Training baselines on D...")
    packs = load_D_trained_baselines()
    print(f"  N0 rate = {packs['N0_rate']:.4f}")
    print(f"  N1 campaign scores: {len(packs['N1_camp_scores'])} campaigns")
    print(f"  B2 trained on {len(packs['B2_model'][1].mean_)} features")
    print(f"  B3 trained, num_trees={packs['B3_model'][0].tree_count_}")

    # B1 ranking: needs to score eval feats using train feats' rules
    # Just compute B1 score per eval row directly using b1_score + hash tiebreak.
    # For consistency with D ranking, we'd build on train rows then rank T relative to D. Skip that,
    # use within-T rank — B1 still gives deterministic rank order within T.
    print("\n[2/3] Computing deterministic baselines on T (B1/N0/N1/B2/B3)...")
    # B1
    b1_out_path = PRIVATE_OUT / "e1a_B1_predictions.jsonl"
    done_b1 = load_existing(b1_out_path)
    b1_scores = b1_rank_scores(eval_feats, eval_links)
    open_mode = "a" if done_b1 else "w"
    with b1_out_path.open(open_mode, encoding="utf-8") as f:
        for i, r in enumerate(eval_feats):
            rid = r["row_id"]
            if rid in done_b1:
                continue
            rec = {"row_id": rid, "campaign": int(eval_links[rid]["campaign"]),
                   "method_id": "B1", "p_redeem": b1_scores[i], "fallback": False,
                   "elapsed_seconds": 0.0, "config_sha256": "b1-rule-v1"}
            f.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")
    print(f"  B1 wrote {len(eval_feats)} rows")

    # N0
    n0_out = PRIVATE_OUT / "e1a_N0_predictions.jsonl"
    with n0_out.open("w", encoding="utf-8") as f:
        for r in eval_feats:
            rec = {"row_id": r["row_id"], "campaign": int(eval_links[r["row_id"]]["campaign"]),
                   "method_id": "N0", "p_redeem": packs["N0_rate"], "fallback": False,
                   "elapsed_seconds": 0.0, "config_sha256": "n0-constant"}
            f.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")
    print(f"  N0 wrote {len(eval_feats)} rows")

    # N1
    n1_out = PRIVATE_OUT / "e1a_N1_predictions.jsonl"
    with n1_out.open("w", encoding="utf-8") as f:
        for r in eval_feats:
            k = packs["N1_key_fn"](r)
            rec = {"row_id": r["row_id"], "campaign": int(eval_links[r["row_id"]]["campaign"]),
                   "method_id": "N1", "p_redeem": packs["N1_camp_scores"].get(k, packs["N0_rate"]),
                   "fallback": False, "elapsed_seconds": 0.0, "config_sha256": "n1-campaign-logistic"}
            f.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")
    print(f"  N1 wrote {len(eval_feats)} rows")

    # B2
    b2_scores = b2_predict(packs["B2_model"], eval_feats, packs["B2_model"][2])
    b2_out = PRIVATE_OUT / "e1a_B2-Logistic_predictions.jsonl"
    with b2_out.open("w", encoding="utf-8") as f:
        for i, r in enumerate(eval_feats):
            rec = {"row_id": r["row_id"], "campaign": int(eval_links[r["row_id"]]["campaign"]),
                   "method_id": "B2-Logistic", "p_redeem": float(b2_scores[i]), "fallback": False,
                   "elapsed_seconds": 0.0, "config_sha256": "b2-logistic-c0.1-d-full"}
            f.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")
    print(f"  B2 wrote {len(eval_feats)} rows")

    # B3
    b3_scores = b3_predict(packs["B3_model"], eval_feats)
    b3_out = PRIVATE_OUT / "e1a_B3-CatBoost_predictions.jsonl"
    with b3_out.open("w", encoding="utf-8") as f:
        for i, r in enumerate(eval_feats):
            rec = {"row_id": r["row_id"], "campaign": int(eval_links[r["row_id"]]["campaign"]),
                   "method_id": "B3-CatBoost", "p_redeem": float(b3_scores[i]), "fallback": False,
                   "elapsed_seconds": 0.0, "config_sha256": "b3-catboost-d5-lr0.1-d-full"}
            f.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")
    print(f"  B3 wrote {len(eval_feats)} rows")

    # P1 + E1b methods (LLM calls)
    print("\n[3/3] Running P1 / L0 / L1 / L2 / P2 / P3 on T (Ollama calls)...")

    p1_out = PRIVATE_OUT / "e1a_P1-v2_predictions.jsonl"
    l0_out = PRIVATE_OUT / "e1b_L0_predictions.jsonl"
    l1_out = PRIVATE_OUT / "e1b_L1_predictions.jsonl"
    l2_out = PRIVATE_OUT / "e1b_L2_predictions.jsonl"
    p2_out = PRIVATE_OUT / "e1b_P2_predictions.jsonl"
    p3_out = PRIVATE_OUT / "e1b_P3_predictions.jsonl"

    done_p1 = load_existing(p1_out)
    done_l0 = load_existing(l0_out)
    done_l1 = load_existing(l1_out)
    done_l2 = load_existing(l2_out)
    done_p2 = load_existing(p2_out)
    done_p3 = load_existing(p3_out)
    print(f"  Resuming: P1={len(done_p1)}, L0={len(done_l0)}, L1={len(done_l1)}, "
          f"L2={len(done_l2)}, P2={len(done_p2)}, P3={len(done_p3)}")

    open_p1 = "a" if done_p1 else "w"
    open_l0 = "a" if done_l0 else "w"
    open_l1 = "a" if done_l1 else "w"
    open_l2 = "a" if done_l2 else "w"
    open_p2 = "a" if done_p2 else "w"
    open_p3 = "a" if done_p3 else "w"

    f_p1 = p1_out.open(open_p1, encoding="utf-8")
    f_l0 = l0_out.open(open_l0, encoding="utf-8")
    f_l1 = l1_out.open(open_l1, encoding="utf-8")
    f_l2 = l2_out.open(open_l2, encoding="utf-8")
    f_p2 = p2_out.open(open_p2, encoding="utf-8")
    f_p3 = p3_out.open(open_p3, encoding="utf-8")

    t0 = time.monotonic()
    n_calls = 0
    valid = 0
    fallback = 0
    for i, r in enumerate(eval_feats, 1):
        rid = r["row_id"]
        camp = int(eval_links[rid]["campaign"])
        payload = payload_by_id.get(rid)
        if payload is None:
            print(f"[{i}] missing payload for {rid}, skipping LLM calls")
            continue
        payload_str = json.dumps(payload, ensure_ascii=False, sort_keys=True)

        # E1a P1-v2: full persona + v2 prompt
        if rid not in done_p1:
            score, info = call_ollama(PROMPT_V2, payload_str)
            n_calls += 1
            rec = {"row_id": rid, "campaign": camp, "method_id": "P1-v2",
                   "p_redeem": score if score is not None else FALLBACK,
                   "fallback": score is None,
                   "elapsed_seconds": round(info.get("elapsed", 0), 2),
                   "config_sha256": "p1-v2-qwen3.5-9b",
                   "ollama_status": info.get("status")}
            f_p1.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")
            f_p1.flush()
            if score is not None: valid += 1
            else: fallback += 1

        # E1b L0: campaign-only (S facts only), no X, no agent
        if rid not in done_l0:
            s_facts = campaign_facts_text_for(r)
            score, info = call_ollama(PROMPT_L0_CAMPAIGN, s_facts)
            n_calls += 1
            rec = {"row_id": rid, "campaign": camp, "method_id": "L0",
                   "p_redeem": score if score is not None else FALLBACK,
                   "fallback": score is None,
                   "elapsed_seconds": round(info.get("elapsed", 0), 2),
                   "config_sha256": "l0-campaign-only",
                   "ollama_status": info.get("status")}
            f_l0.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")
            f_l0.flush()

        # E1b L1: structured X/S facts (no persona narrative)
        if rid not in done_l1:
            xs = structured_xs_text_for(r)
            score, info = call_ollama(PROMPT_L1_STRUCTURED, xs)
            n_calls += 1
            rec = {"row_id": rid, "campaign": camp, "method_id": "L1",
                   "p_redeem": score if score is not None else FALLBACK,
                   "fallback": score is None,
                   "elapsed_seconds": round(info.get("elapsed", 0), 2),
                   "config_sha256": "l1-structured-xs",
                   "ollama_status": info.get("status")}
            f_l1.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")
            f_l1.flush()

        # E1b L2: persona text + v2 prompt (no agent)
        if rid not in done_l2:
            persona = persona_text_for(r, packs["B2_model"][2])
            score, info = call_ollama(PROMPT_L2_PERSONA, persona)
            n_calls += 1
            rec = {"row_id": rid, "campaign": camp, "method_id": "L2",
                   "p_redeem": score if score is not None else FALLBACK,
                   "fallback": score is None,
                   "elapsed_seconds": round(info.get("elapsed", 0), 2),
                   "config_sha256": "l2-persona-text",
                   "ollama_status": info.get("status")}
            f_l2.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")
            f_l2.flush()

        # E1b P2: misaligned persona (use another household's X within same campaign)
        if rid not in done_p2:
            # Find another row in same campaign (different row_id) for misalignment
            other = next((rr for rr in eval_feats
                          if int(eval_links[rr["row_id"]]["campaign"]) == camp
                          and rr["row_id"] != rid), None)
            if other is not None:
                other_payload = payload_by_id.get(other["row_id"])
                payload_for_p2 = dict(other_payload) if other_payload else payload
            else:
                payload_for_p2 = payload
            p2_payload_str = json.dumps(payload_for_p2, ensure_ascii=False, sort_keys=True)
            score, info = call_ollama(PROMPT_P2_MISALIGNED, p2_payload_str)
            n_calls += 1
            rec = {"row_id": rid, "campaign": camp, "method_id": "P2",
                   "p_redeem": score if score is not None else FALLBACK,
                   "fallback": score is None,
                   "elapsed_seconds": round(info.get("elapsed", 0), 2),
                   "config_sha256": "p2-misaligned",
                   "ollama_status": info.get("status")}
            f_p2.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")
            f_p2.flush()

        # E1b P3: minimal persona (R/F/M only) + v2 prompt
        if rid not in done_p3:
            min_p = minimal_persona_text_for(r)
            score, info = call_ollama(PROMPT_P3_MINIMAL, min_p)
            n_calls += 1
            rec = {"row_id": rid, "campaign": camp, "method_id": "P3",
                   "p_redeem": score if score is not None else FALLBACK,
                   "fallback": score is None,
                   "elapsed_seconds": round(info.get("elapsed", 0), 2),
                   "config_sha256": "p3-minimal-rfm",
                   "ollama_status": info.get("status")}
            f_p3.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")
            f_p3.flush()

        if i % 20 == 0 or i == len(eval_feats):
            elapsed = time.monotonic() - t0
            print(f"[{i}/{len(eval_feats)}] calls={n_calls}, "
                  f"valid={valid}, fallback={fallback}, elapsed={elapsed:.0f}s", flush=True)

    f_p1.close(); f_l0.close(); f_l1.close(); f_l2.close(); f_p2.close(); f_p3.close()
    print("\nALL DONE")
    print(f"Total LLM calls: {n_calls}, valid: {valid}, fallback: {fallback}")
    print(f"Total wall time: {time.monotonic()-t0:.0f}s")
    print(f"\nOutput directory: {T_OUT}")


if __name__ == "__main__":
    main()
