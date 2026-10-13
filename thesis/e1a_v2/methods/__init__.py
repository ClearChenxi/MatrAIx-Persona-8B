"""E1a baseline methods (N0, N1, B1, B2, B3).

These modules were MOVED FROM thesis/method_dev/methods/ on 2026-10-13.
They implement only traditional tabular / rule-based baselines and contain
no direct LLM calls. They are safe to reuse as-is in E1a_v2; see each
module's top header for the import-provenance statement.

LLM methods (P1, L0, L1, L2, P2, P3) for E1a and E1b will be added under
this directory but MUST go through MatrAIx PersonaJsonSurvey + HostEnvironment,
matching the integration pattern documented in thesis/e0a/run.py. Do NOT
re-introduce raw urllib.request calls to Ollama.
"""
from . import baselines, baseline_logistic, baseline_catboost, io_utils

__all__ = ["baselines", "baseline_logistic", "baseline_catboost", "io_utils"]
