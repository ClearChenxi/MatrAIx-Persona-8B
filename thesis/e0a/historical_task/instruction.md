# Historical household coupon interface check

Treat the persona as one household, never an individual member. Predict the probability that this confirmed recipient household redeems at least one valid campaign coupon during the entire campaign period. Use only the supplied observed behavior and campaign facts. All behavior precedes the prediction cutoff.

Do not invent demographics, psychology, motivations, coupon amounts, future purchases or actual outcomes. Unknown values remain unknown. This is a technical interface check without outcome labels. Do not predict purchase, profit or confidence in your answer.

Return only the platform-derived JSON envelope with exactly one answer, questionId `p_redeem`, value a decimal STRING in [0,1], such as "0.0". Do not add rationale or confidence. This value is an uncalibrated model estimate.
