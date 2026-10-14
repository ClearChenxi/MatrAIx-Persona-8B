# E1b restart: official Agent workflow and controls

This is the current method-development directory. P1 invokes the unmodified
open-source `PersonaJsonSurvey` through `HostEnvironment`, with the E0a-tested
local Ollama adapter. The generated Survey task contains the actual frozen
activity facts for the campaign being predicted. Its template explicitly
rejects execution without a generated fact card. Household behavior comes
from all 56 frozen X/S feature fields after removing `row_id` and separating
activity S from household X. Identifiers and labels are never rendered.

`pilot.py` deterministically selects 20 D boundary records and 100 R records
stratified over four historical validation campaigns and the pre-frozen
behavior groups. Without `--execute`, it only displays the selection hash and
per-campaign counts. Its execution gate currently remains pending until the
pilot scope is confirmed and pushed. Each actual call retains the native
Ollama response, official request, rendered messages and final official
Survey result. Up to two technical retries and the frozen D rate fallback are
recorded separately.

The direct L0/L1/L2 call primitive, same-activity P2 donor matching and P3
RFM field reduction are implemented and covered by synthetic tests. L2 uses
exactly the same model-visible messages as P1; the only difference is the
Agent execution path. Therefore a deterministic equal-score result cannot be
relabelled as an Agent workflow advantage. The full condition runner, P4
frozen subset, historical calibration and final prediction contract remain
to be implemented. No T prediction, new timelock seal or final outcome
evaluation has occurred.
