# Phone Authentication and Agreement Consent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace username authentication with phone-and-password authentication backed by Aliyun number verification, versioned legal consent, secure audit logging, and password recovery.

**Architecture:** FastAPI owns validation, registration transactions, consent evidence, encrypted phone lookup, rate limits, and security audits. An injectable Dypnsapi adapter sends and checks codes. Vue consumes the OpenAPI DTOs and presents registration, login, reset, and legal-document flows.

**Tech Stack:** Vue 3, TypeScript, FastAPI, Pydantic v2, SQLAlchemy async, Alembic, PostgreSQL, Redis, Aliyun SDK, pytest, HTTPX, Apifox/ASGI scenarios.

## Global Constraints

- All account-owned data and queries use authenticated `user_id`; clients cannot choose another identity.
- Registration requires mainland phone, nickname, strong password, valid SMS verification, and two independent consents.
- Login accepts only phone and password; `xiaow` migrates to `15884119087` and uses this flow.
- Use `Dypnsapi.SendSmsVerifyCode` and `CheckSmsVerifyCode`; never generate, persist, log, or expose codes.
- Credentials come only from runtime configuration; never commit, return, or log keys, secrets, codes, plaintext phones, or provider payloads.
- Store encrypted phone plus a separate HMAC lookup digest; do not return ciphertext.
- Use the existing Envelope, request ID, Pydantic validation, OpenAPI contract, and no duplicate endpoint semantics.
- Minimum SMS limits: one per 60 seconds, five per hour, ten per day per phone, plus IP and anonymous-session limits.
- Security and SMS audit logs retain at least 180 days. Ordinary diagnostics retain 14 days; messages retain 90 days.
- Provider, Redis, or database failure fails closed.
- Do not stage, modify, revert, or commit unrelated existing worktree changes.

---

## Task 1: Freeze HTTP contract and Pydantic models

**Files:**
- Modify: `docs/contracts/openapi-v1.yaml`
- Modify: `backend/app/schemas/resources.py`
- Test: `backend/tests/test_contract_models.py`, `backend/tests/test_openapi_drift.py`

**Produces:** `SendSmsCodeRequest`, `PhoneRegisterRequest`, `PhoneLoginRequest`, `PasswordResetRequest`, `LegalDocument`, and operations for SMS send, phone register/login/reset, and two legal-document reads.

- [ ] Write failing tests asserting both consent fields are required and old username-only payloads are rejected.
- [ ] Run `pytest backend/tests/test_contract_models.py backend/tests/test_openapi_drift.py -q`; expect failure because phone models/routes do not exist.
- [ ] Add exact fields: phone, nickname, password, verification code, purpose, agreement versions, and literal true consent flags. Add standard 400/401/409/422/429/500/503 responses.
- [ ] Run the focused tests; expect pass.
- [ ] Commit `feat: define phone authentication contract`.

## Task 2: Add phone protection and configuration

**Files:**
- Create: `backend/app/application/phone_crypto.py`
- Modify: `backend/app/core/config.py`
- Test: `backend/tests/test_phone_crypto.py`, `backend/tests/test_config.py`

**Produces:** `normalize_mainland_phone`, `PhoneProtector.encrypt/decrypt/lookup_digest/redact`, and required production settings.

- [ ] Write tests proving E.164 normalization, reversible authenticated encryption, non-reversible lookup digest, and production startup rejection when keys are absent/default.
- [ ] Run focused tests; expect failure.
- [ ] Implement authenticated encryption with the project’s maintained crypto dependency, separate encryption and HMAC keys, key versioning, and masked output such as `158****9087`.
- [ ] Run focused tests; expect pass.
- [ ] Commit `feat: protect phone identity data`.

## Task 3: Add PostgreSQL models, migration, legal seed, and xiaow migration

**Files:**
- Modify: `backend/app/adapters/sql/models.py`
- Create: `backend/migrations/versions/20260821_0010_phone_auth_and_consents.py`
- Create: `backend/app/application/legal.py`
- Test: `backend/tests/test_sql_adapter.py`, `backend/tests/test_legal_consents.py`

**Produces:** phone columns on users; `agreement_versions`, `user_agreement_consents`, `sms_verification_audits`, and `security_audit_logs`; seeded version `2026.08.21`; migrated admin phone.

- [ ] Write tests for unique phone digest, two seeded document hashes, immutable consent evidence, and `xiaow` role preservation.
- [ ] Run focused tests; expect failure.
- [ ] Implement nullable migration columns, unique digest index, append-only audit models, and explicit migration failure if encryption keys are absent.
- [ ] Seed the approved agreement and privacy-policy text and SHA-256 hashes; never seed plaintext phone.
- [ ] Run migration-focused tests; expect pass.
- [ ] Commit `feat: persist phone identity and legal consent`.

## Task 4: Implement Aliyun Dypnsapi and Redis limits

**Files:**
- Create: `backend/app/application/sms_verification.py`
- Create: `backend/app/adapters/sms/aliyun.py`
- Create: `backend/app/adapters/sms/fake.py`
- Modify: `backend/app/bootstrap.py`
- Test: `backend/tests/test_sms_verification.py`

**Produces:** provider port `send(phone,purpose)`, `check(phone,purpose,code)`, rate-limit service, and deterministic fake.

- [ ] Write tests for success, invalid/expired code, provider timeout, fail-closed behavior, and phone/IP/session limits.
- [ ] Run focused tests; expect failure.
- [ ] Call `SendSmsVerifyCode` and `CheckSmsVerifyCode` with server-fixed sign/template. Persist only redacted phone, purpose, provider request ID, result, request ID, and time.
- [ ] Map provider failure to retryable platform errors; never retry blindly on timeout.
- [ ] Run focused tests; expect pass.
- [ ] Commit `feat: add Aliyun SMS verification adapter`.

## Task 5: Convert identity service and SQL/memory stores

**Files:**
- Modify: `backend/app/application/identity.py`
- Modify: `backend/app/adapters/auth/sql.py`
- Modify: `backend/app/adapters/auth/memory.py`
- Test: `backend/tests/test_identity.py`, `backend/tests/test_phone_auth.py`

**Produces:** `register_phone`, `login_phone`, `reset_password`, phone lookup, atomic consent insert, and token revocation via `tokens_revoked_after`.

- [ ] Write tests for successful registration, duplicate phone, missing consent, login, reset, old-token rejection, and migrated `xiaow`.
- [ ] Run focused tests; expect failure because identity methods use username.
- [ ] Implement registration transaction: check provider verification, digest uniqueness, password policy, both current legal versions, user insert, consent rows, and audit row atomically.
- [ ] Implement reset transaction: verify provider code, update hash and token revocation timestamp atomically.
- [ ] Run focused tests; expect pass.
- [ ] Commit `feat: authenticate users by verified phone`.

## Task 6: Expose routes and security auditing

**Files:**
- Modify: `backend/app/api/v1/routes.py`, `backend/app/api/dependencies.py`, `backend/app/main.py`
- Create or modify: `backend/app/application/security_audit.py`
- Test: `backend/tests/test_api.py`, `backend/tests/test_api_scenarios.py`

**Produces:** `POST /auth/sms-codes`, phone-based `/auth/register` and `/auth/login`, `POST /auth/password-resets`, and legal-document GET routes.

- [ ] Write ASGI tests for registration gating, SMS rate limits, login, reset, legal reads, no account enumeration, and provider failure.
- [ ] Run API tests; expect failure because existing routes accept username.
- [ ] Add routes using standard Envelope/error models. Restrict SMS purposes to register/reset; keep admin binding as controlled migration only.
- [ ] Audit login, SMS, reset, token revocation, limit hits, admin changes, and security failures without sensitive fields.
- [ ] Run API and scenario tests; expect pass.
- [ ] Commit `feat: expose phone authentication API`.

## Task 7: Add 180-day retention cleanup

**Files:**
- Modify: `backend/app/adapters/sql/repository.py` and the existing cleanup/lifecycle owner
- Test: `backend/tests/test_security_audit_retention.py`

**Produces:** append-only audit writes and bounded cleanup that preserves rows at the 180-day boundary.

- [ ] Write boundary tests for security/SMS audits at 179, 180, and 181 days and ordinary logs at 14 days.
- [ ] Run tests; expect failure.
- [ ] Implement batch cleanup that deletes only older-than-cutoff rows, records counts/failures, and never deletes current active snapshots or legal consent evidence.
- [ ] Run tests; expect pass.
- [ ] Commit `feat: retain authentication security audits`.

## Task 8: Implement Vue registration, login, reset, and legal pages

**Files:**
- Modify: `frontend/src/api/contracts.ts`, `frontend/src/api/client.ts`, `frontend/src/App.vue`, `frontend/src/styles.css`
- Test: existing frontend component/client test location

**Produces:** phone-only login, registration fields, resend countdown, two unchecked agreement boxes, reset-password flow, and accessible legal document views.

- [ ] Write tests that block submit unless both agreements are checked, show resend countdown, and never send username/provider fields.
- [ ] Run frontend tests; expect failure because current form uses username.
- [ ] Implement typed calls, Chinese error messages, server-driven countdown, keyboard-accessible checkboxes and legal page/dialog with version/date.
- [ ] Run `npm --prefix frontend test -- --run; npm --prefix frontend run build`; expect pass.
- [ ] Commit `feat: add phone registration and consent UI`.

## Task 9: Migration, integration, and quality gate

**Files:**
- Modify only: `README.agent_robot.md` for non-secret operations
- Test: all prior tests plus quality-gate scenarios

- [ ] Configure server variables without committing values: `ALIBABA_CLOUD_ACCESS_KEY_ID`, `ALIBABA_CLOUD_ACCESS_KEY_SECRET`, `APP_ALIYUN_SMS_SIGN_NAME`, `APP_ALIYUN_SMS_TEMPLATE_CODE`, phone encryption/HMAC keys.
- [ ] Run `alembic upgrade head` in a clean PostgreSQL environment and verify no plaintext phone or secret appears in logs.
- [ ] Run backend, frontend, contract, security, and ASGI suites; then run `python scripts/quality_gate.py all --integration-sha <40-character-sha>`.
- [ ] Verify registration, login, reset, legal version evidence, xiaow migration, two-user isolation, 180-day security retention, and fail-closed provider errors.
- [ ] Commit only safe operator documentation; do not stage unrelated files.

## Self-review

- Coverage: contract, crypto, migration, legal, provider, identity, routes, Vue, retention, migration, and final QA are each assigned.
- No placeholders: interfaces, commands, limits, errors, and commit boundaries are explicit.
- Type consistency: later tasks consume the schemas and service ports produced by earlier tasks; all routes use the standard envelope.

