[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

# This script only resets deterministic fixtures in the local Docker database.
# It never sends data to Apifox or GitHub and requires the APIFOX-001 session
# created in the local integration environment to exist first.
$projectRoot = Split-Path -Parent $PSScriptRoot
$docker = 'D:\DockerDesktop\resources\bin\docker.exe'
if (-not (Test-Path -LiteralPath $docker)) {
    throw "Docker CLI not found at $docker. Start Docker Desktop and try again."
}

$sql = @'
BEGIN;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM users WHERE username = 'apifox_tester') THEN
        RAISE EXCEPTION 'The local user apifox_tester does not exist. Register it in Apifox first.';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM sessions WHERE id = 'd9548dfb-8d11-4f48-91a2-22e907960602'
    ) THEN
        RAISE EXCEPTION 'The APIFOX-001 fixture session does not exist. Run APIFOX-001 once first.';
    END IF;
END $$;

UPDATE users SET role = 'reviewer' WHERE username = 'apifox_tester';

UPDATE runs
SET status = 'completed',
    ended_at = COALESCE(ended_at, NOW()),
    cancellation_requested_at = NULL
WHERE id = '68cc363d-120f-458e-bc5b-80178f6cd11a';

INSERT INTO sessions (id, owner_id, title, created_at, updated_at, last_message_at)
VALUES (
    '33333333-3333-4333-8333-333333333333',
    'foreign-owner-005',
    'Private foreign fixture',
    NOW(), NOW(), NOW()
)
ON CONFLICT (id) DO UPDATE
SET owner_id = EXCLUDED.owner_id, title = EXCLUDED.title, updated_at = NOW(), last_message_at = NOW();

INSERT INTO messages (
    id, session_id, owner_id, role, status, content, reply_to_message_id, run_id,
    citations, created_at, updated_at
)
SELECT
    '55555555-5555-4555-8555-555555555555', id, owner_id, 'assistant', 'completed',
    'Apifox feedback fixture message', NULL, NULL, '[]', NOW(), NOW()
FROM sessions
WHERE id = 'd9548dfb-8d11-4f48-91a2-22e907960602'
ON CONFLICT (id) DO UPDATE
SET status = 'completed', content = 'Apifox feedback fixture message', updated_at = NOW();

DELETE FROM feedback
WHERE message_id = '55555555-5555-4555-8555-555555555555';

INSERT INTO messages (
    id, session_id, owner_id, role, status, content, reply_to_message_id, run_id,
    citations, created_at, updated_at
)
SELECT
    '66666666-6666-4666-8666-666666666666', id, owner_id, 'user', 'completed',
    'Apifox review fixture input', NULL, NULL, '[]', NOW(), NOW()
FROM sessions
WHERE id = 'd9548dfb-8d11-4f48-91a2-22e907960602'
ON CONFLICT (id) DO UPDATE
SET status = 'completed', content = 'Apifox review fixture input', updated_at = NOW();

INSERT INTO messages (
    id, session_id, owner_id, role, status, content, reply_to_message_id, run_id,
    citations, created_at, updated_at
)
SELECT
    '77777777-7777-4777-8777-777777777777', id, owner_id, 'assistant', 'needs_review',
    'Apifox review fixture candidate', '66666666-6666-4666-8666-666666666666',
    '88888888-8888-4888-8888-888888888888', '[]', NOW(), NOW()
FROM sessions
WHERE id = 'd9548dfb-8d11-4f48-91a2-22e907960602'
ON CONFLICT (id) DO UPDATE
SET status = 'needs_review', content = 'Apifox review fixture candidate', updated_at = NOW();

INSERT INTO runs (
    id, session_id, owner_id, user_message_id, assistant_message_id, status, attempt,
    retry_of_user_message_id, model, retrieval_strategy, confidence_threshold,
    cancellation_requested_at, started_at, ended_at, steps, citations
)
SELECT
    '88888888-8888-4888-8888-888888888888', id, owner_id,
    '66666666-6666-4666-8666-666666666666', '77777777-7777-4777-8777-777777777777',
    'needs_review', 1, NULL, 'apifox-fixture', 'fixture', 0.5, NULL, NOW(), NULL, '[]', '[]'
FROM sessions
WHERE id = 'd9548dfb-8d11-4f48-91a2-22e907960602'
ON CONFLICT (id) DO UPDATE
SET status = 'needs_review', attempt = 1, ended_at = NULL, cancellation_requested_at = NULL,
    steps = '[]', citations = '[]';

DELETE FROM review_audits WHERE review_id = '22222222-2222-4222-8222-222222222222';
INSERT INTO reviews (
    id, run_id, session_id, owner_id, user_message_id, candidate_content, reason_codes,
    confidence, status, version, created_at, decided_at
)
SELECT
    '22222222-2222-4222-8222-222222222222',
    '88888888-8888-4888-8888-888888888888', id, owner_id,
    '66666666-6666-4666-8666-666666666666', 'Apifox review fixture candidate',
    json_build_array('low_confidence'), 0.5, 'pending', 1, NOW(), NULL
FROM sessions
WHERE id = 'd9548dfb-8d11-4f48-91a2-22e907960602'
ON CONFLICT (id) DO UPDATE
SET run_id = EXCLUDED.run_id, session_id = EXCLUDED.session_id, owner_id = EXCLUDED.owner_id,
    user_message_id = EXCLUDED.user_message_id, candidate_content = EXCLUDED.candidate_content,
    reason_codes = EXCLUDED.reason_codes, confidence = EXCLUDED.confidence, status = 'pending',
    version = 1, created_at = NOW(), decided_at = NULL;

INSERT INTO memories (
    id, owner_id, memory_type, content, status, confidence, source_message_id,
    corrected_from_version, version, created_at, updated_at, deleted_at
)
SELECT
    '11111111-1111-4111-8111-111111111111', owner_id, 'preference',
    'Apifox local integration memory', 'active', 0.9,
    '55555555-5555-4555-8555-555555555555', NULL, 1, NOW(), NOW(), NULL
FROM sessions
WHERE id = 'd9548dfb-8d11-4f48-91a2-22e907960602'
ON CONFLICT (id) DO UPDATE
SET owner_id = EXCLUDED.owner_id, memory_type = EXCLUDED.memory_type, content = EXCLUDED.content,
    status = 'active', confidence = EXCLUDED.confidence,
    source_message_id = EXCLUDED.source_message_id, corrected_from_version = NULL,
    version = 1, updated_at = NOW(), deleted_at = NULL;

COMMIT;
'@

Push-Location $projectRoot
try {
    & $docker compose exec -T postgres psql -U agent -d agent -v ON_ERROR_STOP=1 -c $sql
    if ($LASTEXITCODE -ne 0) {
        throw "Fixture reset failed with exit code $LASTEXITCODE."
    }
}
finally {
    Pop-Location
}

Write-Host 'Local Apifox fixtures were reset successfully.'
