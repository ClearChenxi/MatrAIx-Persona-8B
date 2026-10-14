"""Outcome-blind, same-campaign whole-household Persona derangement for P2."""

from __future__ import annotations

import hashlib

import numpy as np
from scipy.optimize import linear_sum_assignment

from thesis.e1b.agent_workflow import partition_fields


GROUP_KEYS = ("frequency_group", "historical_redemption_group", "target_category_match_group")


def mismatch(
    rows: tuple[dict[str, str], ...], groups: dict[str, dict[str, str]],
    *, seed: int,
) -> tuple[tuple[dict[str, str], ...], list[dict]]:
    """Minimize same-group pairs while forbidding self-pairs.

    The donor's complete observed household X, including activity-matching
    features computed for the same campaign, is substituted. The original
    activity S and recipient row key stay intact. No Y value is inspected.
    """
    if len(rows) < 2:
        raise ValueError("P2 cannot derange an activity with fewer than two households")
    row_ids = [row["row_id"] for row in rows]
    if len(set(row_ids)) != len(row_ids) or set(row_ids) - set(groups):
        raise ValueError("P2 row/group alignment failed")
    activity_facts = [partition_fields(row)[1] for row in rows]
    if any(facts != activity_facts[0] for facts in activity_facts[1:]):
        raise ValueError("P2 households do not share the same activity facts")

    signatures = [tuple(groups[row_id][key] for key in GROUP_KEYS) for row_id in row_ids]
    count = len(rows)
    costs = np.empty((count, count), dtype=float)
    for recipient in range(count):
        for donor in range(count):
            if recipient == donor:
                costs[recipient, donor] = 1_000_000.0
                continue
            tie = hashlib.sha256(
                f"{seed}:{row_ids[recipient]}:{row_ids[donor]}".encode()
            ).digest()
            costs[recipient, donor] = (
                float(signatures[recipient] == signatures[donor])
                + int.from_bytes(tie[:4], "big") / (2**32) * 1e-6
            )
    recipient_indices, donor_indices = linear_sum_assignment(costs)
    if any(recipient == donor for recipient, donor in zip(recipient_indices, donor_indices)):
        raise RuntimeError("P2 derangement assigned a household to itself")

    assigned = dict(zip(recipient_indices.tolist(), donor_indices.tolist()))
    transformed, mapping = [], []
    for recipient, original in enumerate(rows):
        donor = rows[assigned[recipient]]
        donor_x, _ = partition_fields(donor)
        altered = {
            name: (original[name] if name == "row_id" or name == "campaign_type"
                   or name.startswith("campaign_") else donor[name])
            for name in original
        }
        if set(donor_x) != set(partition_fields(altered)[0]):
            raise RuntimeError("P2 lost a donor household field")
        transformed.append(altered)
        mapping.append({
            "recipient_row_id": original["row_id"],
            "donor_row_id": donor["row_id"],
            "different_behavior_group": signatures[recipient] != signatures[assigned[recipient]],
        })
    return tuple(transformed), mapping
