# QA contract scenario assets

These assets freeze QA assertions independently from a particular implementation.
They are derived from `docs/contracts/openapi-v1.yaml` version `1.0.0` and its
companion semantics document. They are intentionally data-first so the final
integration can bind them to pytest, Playwright, and an offline Apifox export
without changing the acceptance criteria.

| Asset | Binding target |
| --- | --- |
| `contract-matrix.json` | API contract/integration tests |
| `sse-sequences.json` | Fake-model SSE integration tests |
| `state-and-rag-gates.json` | agent, RAG, and frontend tests |

The final integration must execute all blocking cases, record their test IDs in
`artifacts/quality/acceptance-report.json`, and may add cases but may not weaken
or remove these assertions.
