"""Frozen E1a N0/N1/B1 reference methods and campaign-level metrics."""

from __future__ import annotations

import hashlib
from collections import defaultdict

from .data import Split


S_NUMERIC = (
    "campaign_valid_days", "campaign_unique_coupon_count",
    "campaign_unique_product_count", "campaign_department_count",
    "campaign_commodity_count", "campaign_brand_type_count",
    "campaign_top_commodity_1_weight", "campaign_top_commodity_2_weight",
    "campaign_top_commodity_3_weight", "campaign_top_commodity_4_weight",
    "campaign_top_commodity_5_weight", "campaign_top_commodity_6_weight",
    "campaign_top_commodity_7_weight", "campaign_top_commodity_8_weight",
    "campaign_other_commodity_weight", "campaign_unknown_taxonomy_flag",
)


def n0_rate(development: Split) -> float:
    if development.labels is None or not development.labels:
        raise ValueError("N0 requires permitted D labels")
    return sum(development.labels.values()) / len(development.labels)


def n1_features(row: dict[str, str]) -> tuple[float, ...]:
    """Only pre-campaign activity facts S; never row identity or household X."""
    return (float(row["campaign_type"] == "TypeC"),) + tuple(
        float(row.get(name, "") or 0) for name in S_NUMERIC
    )


def fit_n1(development: Split):
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    if development.labels is None or set(development.labels.values()) != {0, 1}:
        raise ValueError("N1 requires two-class D labels")
    model = make_pipeline(StandardScaler(), LogisticRegression(C=1.0, max_iter=2000))
    model.fit(
        [n1_features(row) for row in development.features],
        [development.labels[row["row_id"]] for row in development.features],
    )
    return model


def predict_n1(model, split: Split) -> list[float]:
    scores = model.predict_proba([n1_features(row) for row in split.features])[:, 1]
    by_campaign: dict[int, float] = {}
    for row, score in zip(split.features, scores):
        campaign = int(split.links[row["row_id"]]["campaign"])
        if campaign in by_campaign and abs(by_campaign[campaign] - float(score)) > 1e-12:
            raise ValueError("N1 produced household variation within one campaign")
        by_campaign[campaign] = float(score)
    return [float(score) for score in scores]


def _b1_priority(row: dict[str, str]) -> tuple[float, ...]:
    redeemed = int(row["prior_redeemed_campaign_count"] or 0)
    rate = float(row["prior_redemption_rate"] or 0)
    return (
        float(redeemed > 0), rate,
        -float(row["recency_days"]), float(row["basket_count"]),
        float(row["sales_total"]),
    )


def predict_b1(split: Split) -> list[float]:
    """Rank within each campaign; fixed hash resolves only exact priority ties."""
    groups: dict[int, list[int]] = defaultdict(list)
    for index, row in enumerate(split.features):
        groups[int(split.links[row["row_id"]]["campaign"])].append(index)
    output = [0.0] * len(split.features)
    for campaign, indices in groups.items():
        def ranking(index: int):
            row = split.features[index]
            household = split.links[row["row_id"]]["household_key"]
            tie = hashlib.sha256(f"{household}|{campaign}".encode()).hexdigest()
            return tuple(-v for v in _b1_priority(row)) + (tie,)

        order = sorted(indices, key=ranking)
        for rank, index in enumerate(order):
            output[index] = (len(order) - rank) / len(order)
    return output


def campaign_macro_ap(split: Split, scores: list[float]) -> dict:
    from sklearn.metrics import average_precision_score

    if split.labels is None or len(scores) != len(split.features):
        raise ValueError("AP requires aligned permitted historical labels")
    groups: dict[int, list[int]] = defaultdict(list)
    for index, row in enumerate(split.features):
        groups[int(split.links[row["row_id"]]["campaign"])].append(index)
    activity = {}
    for campaign, indices in sorted(groups.items()):
        labels = [split.labels[split.features[i]["row_id"]] for i in indices]
        activity[str(campaign)] = (
            float(average_precision_score(labels, [scores[i] for i in indices]))
            if sum(labels) else None
        )
    valid = [value for value in activity.values() if value is not None]
    return {"per_campaign": activity, "macro_ap": sum(valid) / len(valid) if valid else None,
            "n_campaigns_used": len(valid)}
