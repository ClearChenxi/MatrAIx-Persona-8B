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

The L0/L1/L2 direct-LLM controls, P2 wrong-household Persona, P3 reduced
Persona and P4 second-model subset are not yet implemented in this branch.
No T prediction, new timelock seal or final outcome evaluation has occurred.
