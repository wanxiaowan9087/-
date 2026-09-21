# Local Evidence Selector Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Improve ranking quality and citation support with deterministic local evidence selection, without adding an external model call or changing the database schema.

**Architecture:** Keep DashScope Rerank as the primary signal, then apply a small local selector using model identity, query-term coverage, heading intent, and score gap. Use the same selector before generation and in the offline evaluator so the quality gate measures the evidence path actually used by the runtime.

**Tech Stack:** Python 3.11, pytest, existing RAG `SearchHit`/Markdown metadata, pgvector evaluation harness.

## Global Constraints

- Do not add an external model call or new dependency.
- Do not change OpenAPI, database migrations, or vector dimensions.
- Ordinary answers use at most 3–4 evidence chunks; collection questions retain all required product groups.
- No metric may be improved by deleting cases, weakening labels, or hiding unsupported citations.
- Do not print or commit API keys, evaluation artifacts, database files, or build output.

### Task 1: Lock the deterministic selector contract

**Files:**
- Modify: `backend/app/rag/evidence.py`
- Test: `backend/tests/rag/test_evidence_gate.py`

**Interfaces:**
- Produces `select_query_evidence_hits(query: str, hits: Sequence[SearchHit], *, limit: int = 3) -> tuple[SearchHit, ...]` for pre-generation evidence selection.
- Existing `select_citation_hits` remains the post-generation selector and keeps answer-aware behavior.

- [ ] **Step 1: Add failing tests** for empty-answer query selection, model-specific selection, and collection-query preservation.
- [ ] **Step 2: Run the focused tests** and verify the new selector contract fails before implementation.
- [ ] **Step 3: Implement the selector** by reusing the existing deterministic token coverage and score-gap rules; do not call a model.
- [ ] **Step 4: Run focused tests** and verify all selector cases pass.

### Task 2: Apply intent/model cohesion after Rerank

**Files:**
- Modify: `backend/app/rag/retrieval_planning.py`
- Modify: `backend/app/rag/retrieval.py`
- Test: `backend/tests/rag/test_retrieval_planning.py`

**Interfaces:**
- Produces `cohere_reranked_hits(query: str, hits: Sequence[SearchHit]) -> tuple[SearchHit, ...]`.
- The provider score remains dominant; local corrections only reorder clear model/section matches.

- [ ] **Step 1: Add a regression test** where an explicit model query must keep same-model, intent-matching sections ahead of another model.
- [ ] **Step 2: Run the regression test** and verify it fails on the pre-selector order.
- [ ] **Step 3: Implement the local cohesion sort** with model match, section markers, query coverage, and stable tie-breaking.
- [ ] **Step 4: Run retrieval tests** and verify no collection-query or recall-budget behavior changes.

### Task 3: Align runtime and evaluator citation paths

**Files:**
- Modify: `backend/app/agent/runtime.py`
- Modify: `evals/run_rag_eval.py`
- Modify: `evals/run_pgvector_dashscope_eval.py`
- Test: `backend/tests/rag/test_eval_citation_support.py`

**Interfaces:**
- Runtime preliminary policy citations use query-aware evidence; final citations still use generated-answer-aware selection.
- Evaluators use the same query-aware pre-generation selector and preserve exact support labels.

- [ ] **Step 1: Add a regression assertion** that irrelevant context chunks are not included when a query-specific evidence chunk is available.
- [ ] **Step 2: Run the focused evaluator tests** and verify the assertion fails before wiring.
- [ ] **Step 3: Wire the selector** into preliminary runtime citations and both evaluators.
- [ ] **Step 4: Run evaluator tests** and verify citation integrity remains 1.0 in fixed tests.

### Task 4: Offline quality gate before paid validation

**Files:**
- Modify: `docs/quality/ZENMOP-RAG-v3评测报告.md` only if offline results are produced.
- Create: `D:\codex_store\agent_robot_evals\v3-20260920-local-selector\` outside the repository.

**Interfaces:**
- Use the existing fixed-embedding evaluator; no DashScope calls.
- Compare Recall@5, MRR@10, nDCG@10, citation support precision, citation integrity, refusal, and injection defense against the previous baseline.

- [ ] **Step 1: Run the full local pytest suite.**
- [ ] **Step 2: Run the fixed-embedding RAG evaluator** and save the report outside the repository.
- [ ] **Step 3: Reject the solution** if Recall@5 drops by more than 0.03, citation integrity drops, or injection defense drops.
- [ ] **Step 4: Ask for user approval** before spending paid cloud quota only if offline results show a meaningful improvement and no safety regression.

### Task 5: Commit only verified source changes

**Files:**
- Modify: `docs/approvals/` only for an explicitly approved Git push.

- [ ] **Step 1: Run `git diff --check` and scan staged content for secrets.**
- [ ] **Step 2: Commit source/tests/docs without artifacts or reports.**
- [ ] **Step 3: Report the offline result and wait for explicit approval before any remote push.**
