# Synthetic household coupon prediction

This is a technical integration check using invented household records, not a real survey respondent or a measured outcome. Treat the persona as a household unit, not an individual member. Predict before the campaign starts, using only the supplied historical behavior and campaign facts.

Output the probability that this confirmed recipient household redeems at least one valid coupon from this campaign during its complete valid period. Do not predict purchase, profit, or confidence in your answer. Do not invent age, income, personality, psychology, motives, coupon face value, future purchases, or actual redemption outcomes. Missing facts remain unknown.

Return only the platform-derived JSON envelope with exactly one answer: questionId `p_redeem`, value a decimal STRING between 0 and 1 inclusive, for example `"0.0"`. Do not add rationale or confidence. The value is an uncalibrated model estimate; there are no labels in this check.
