# Apifox scenario handoff

`scenarios/scenario-manifest-v1.json` is the reviewed mapping between the nine
named acceptance scenarios, frozen OpenAPI operation IDs, and expected outcomes.
It remains the source of truth for scenario intent; it is not an API contract.

On 2026-07-29, Apifox CLI 2.2.8 and the desktop client were both verified, but
the desktop **Export data run** action remained unavailable. The project export
format is not executable by the current CLI, so no token-bearing or fabricated
`offline-suite.json` is stored in this repository.

The authoritative offline gate is now
`backend/tests/test_api_scenarios.py`, run by:

```powershell
python scripts/quality_gate.py api-scenarios --integration-sha <HEAD_SHA>
```

It executes the same APIFOX-001 through APIFOX-009 flows against the FastAPI
ASGI boundary and writes JUnit output to `artifacts/quality/api-scenarios/`.
Apifox's online 27/27 report is retained as manual acceptance evidence only.
Never commit access tokens, private environment variables, personal URLs, or
vendor-exported files.
