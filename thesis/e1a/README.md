# E1a historical method development

This directory is the independent restart implementation of the E1a
traditional methods. It uses only the newly verified E1 input bundle pinned
in `config.json`. The loader checks source hashes, keeps household and
campaign IDs in a separate link table, and has no T-label reader.

`run_development.py` fits N1, B2, and B3 on D; N0 uses the D rate; B1 is a
fixed within-campaign ordering rule. B2 and B3 use the manual's finite grids
and the early-D versus late-D temporal holdout for selection. Vocabulary and
preprocessing for candidate selection are fitted on early D only. Final
models are fitted on all D. R is measured separately and will later provide
the same predeclared calibration opportunity to B2, B3, and P1. No R label
enters the final B2/B3 fit. D/R predictions and model files remain local
under `runs/`; only aggregate method records and checksums are published.

The model-specific packages are pinned in `requirements.txt` and installed
into the branch's independent `.venv` from the local cache. Upstream
MatrAIx source files are not modified.

Status: code and synthetic tests are in preparation. There is no T prediction
or final evaluation in this directory. The parent experiment and conclusion
limits are fixed by `../experiment-blueprint/`.
