# E1b restart: official Agent workflow and controls

This is the current method-development directory. P1 invokes the unmodified
open-source `PersonaJsonSurvey` through `HostEnvironment`, with the E0a-tested
local Ollama adapter. The generated Survey task contains the actual frozen
activity facts for the campaign being predicted. Its template explicitly
rejects execution without a generated fact card. Household behavior comes
from all 56 frozen X/S feature fields after removing `row_id` and separating
activity S from household X. Identifiers and labels are never rendered.

After the first R100 technical gate failed, this fork changed only the
`render_behavior` Jinja whitespace in the bundled Persona template so each
observed household field has its own line. The `PersonaJsonSurvey` agent and
`HostEnvironment` execution path remain unchanged. `verify_rendering.py`
checks all original D20/R100 inputs without model calls: P1 and L2 messages
remain identical, and comparison with the original messages permits only
newline changes. This is a local template patch, not an unmodified upstream
configuration; the full D20/R100 gate must be rerun before any T prediction.

`pilot.py` deterministically selects 20 D boundary records and 100 R records
stratified over four historical validation campaigns and the pre-frozen
behavior groups. Without `--execute`, it only displays the selection hash and
per-campaign counts. Its scope is now user-confirmed; execution still requires
the exact code and configuration commit to be pushed. Each actual call retains the native
Ollama response, official request, rendered messages and final official
Survey result. Up to two technical retries and the frozen D rate fallback are
recorded separately.

The direct L0/L1/L2 call primitive, same-activity P2 donor matching and P3
RFM field reduction are implemented and covered by synthetic tests. L2 uses
exactly the same model-visible messages as P1; the only difference is the
Agent execution path. Therefore a deterministic equal-score result cannot be
relabelled as an Agent workflow advantage. `condition_pilot.py` prepares D/R
comparisons for all six conditions; execution is locked pending P1's D/R
technical gate. `subsample.py` defines the label-free paired P4 selector;
the user-confirmed cap is 20 final-evaluation records per T activity, selected
before model execution using X/S-derived groups and deterministic hashes.
`p4_plan.py` records the exact paired 194-row final-evaluation subset, its
per-activity counts and hash without reading T outcomes or calling a model.
Historical calibration, the formal
T runner and prediction contract remain to be implemented. No T prediction,
new timelock seal or final outcome evaluation has occurred.
