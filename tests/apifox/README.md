# Apifox scenario handoff

`scenario-manifest-v1.json` is the reviewable source manifest for the offline
Apifox export. It maps the required black-box scenarios to OpenAPI operation IDs
and assertions; it is not a replacement API contract or an executable CLI suite.

When the CLI is provisioned, import `docs/contracts/openapi-v1.yaml`, export the
reviewed offline scenario suite under `tests/apifox/scenarios/`, and retain the
same IDs and assertions. Generate CLI, JSON, and JUnit reports in
`artifacts/apifox/`; do not add tokens or private variables to the repository.

CLI status at creation: only `D:\apifox\Apifox.exe` desktop client is available;
no CLI was installed or run.
