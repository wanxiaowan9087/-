# Apifox scenario handoff

`scenario-manifest-v1.json` is the reviewable source manifest for the offline
Apifox export. It maps the required black-box scenarios to OpenAPI operation IDs
and assertions; it is not a replacement API contract or an executable CLI suite.

To produce the required suite, import `docs/contracts/openapi-v1.yaml` into the
Apifox project, implement and review the nine scenarios in the manifest, then
open each scenario's **Continuous Integration** action and export its Apifox CLI
data. Save the reviewed combined export as
`tests/apifox/scenarios/offline-suite.json`; retain the same IDs and assertions.
Generate CLI, JSON, and JUnit reports in `artifacts/apifox/`; do not add tokens
or private variables to the repository.

The current machine has Apifox CLI 2.2.8 available as
`C:\Users\唐世均\AppData\Roaming\npm\apifox.cmd`. The executable suite is still
absent, so `quality_gate.py apifox` correctly remains blocked until the reviewed
export is added. The export must contain no access token, private environment,
or personal project URL.
