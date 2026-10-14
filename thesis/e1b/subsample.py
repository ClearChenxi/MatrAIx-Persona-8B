"""Pre-label, X/S-only paired selection for the P4 second-model check."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict

from thesis.e1b.misalignment import GROUP_KEYS


def select_p4(
    rows: tuple[dict[str, str], ...], links: dict[str, dict[str, str]],
    groups: dict[str, dict[str, str]], *, per_campaign_cap: int, seed: int,
) -> tuple[str, ...]:
    if per_campaign_cap < 1:
        raise ValueError("P4 subset cap must be positive")
    if len({row["row_id"] for row in rows}) != len(rows):
        raise ValueError("P4 input has repeated row IDs")
    activities = defaultdict(list)
    for row in rows:
        row_id = row["row_id"]
        if row_id not in links or row_id not in groups:
            raise ValueError("P4 requires matched label-free links and groups")
        activities[int(links[row_id]["campaign"])].append(row_id)
    selected = []
    def xs_order(row: dict[str, str]) -> str:
        facts = {name: value for name, value in row.items() if name != "row_id"}
        encoded = json.dumps(facts, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(f"{seed}:{encoded}:{row['row_id']}".encode()).hexdigest()

    by_id = {row["row_id"]: row for row in rows}
    for campaign, row_ids in sorted(activities.items()):
        buckets = defaultdict(list)
        for row_id in row_ids:
            key = tuple(groups[row_id][name] for name in GROUP_KEYS)
            buckets[key].append(row_id)
        for key in buckets:
            buckets[key].sort(key=lambda row_id: xs_order(by_id[row_id]))
        keys = sorted(buckets, key=lambda key: hashlib.sha256(f"{seed}:{campaign}:{key}".encode()).hexdigest())
        chosen = []
        while len(chosen) < min(per_campaign_cap, len(row_ids)):
            for key in keys:
                if buckets[key] and len(chosen) < min(per_campaign_cap, len(row_ids)):
                    chosen.append(buckets[key].pop(0))
        selected.extend(chosen)
    return tuple(selected)
