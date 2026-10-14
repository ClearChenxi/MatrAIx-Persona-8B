# Pre-campaign household coupon prediction

Treat the persona as a household, not an individual member. The household is
confirmed to receive the entire coupon bundle. Use only the observed
pre-campaign household behavior and the supplied campaign facts. Predict the
probability that the household redeems at least one valid coupon belonging to
this campaign during its complete valid period.

Unknown attributes and coupon terms remain unknown. Do not invent age,
income, psychology, motive, face value, creative, minimum spend, future
purchases or observed redemption. Do not predict profit or causal uplift.

Return the platform JSON envelope with exactly one answer: questionId
`p_redeem`, value a decimal string between 0 and 1 inclusive. No rationale
or confidence. This is a model-declared, uncalibrated probability.
