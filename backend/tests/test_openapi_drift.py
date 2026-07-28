from __future__ import annotations

import re
from pathlib import Path

from backend.app.adapters.memory.repository import MemoryPlatformRepository
from backend.app.core.config import Settings
from backend.app.main import create_app


def _frozen_response_statuses() -> dict[tuple[str, str], set[str]]:
    contract = (
        Path(__file__).resolve().parents[2] / "docs" / "contracts" / "openapi-v1.yaml"
    )
    result: dict[tuple[str, str], set[str]] = {}
    current_path = ""
    current_method = ""
    in_responses = False
    for line in contract.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        indent = len(line) - len(line.lstrip())
        if indent == 2 and stripped.startswith("/") and stripped.endswith(":"):
            current_path = stripped[:-1]
            in_responses = False
        elif indent == 4 and stripped in {"get:", "post:", "patch:", "delete:"}:
            current_method = stripped[:-1]
            in_responses = False
        elif indent == 6 and stripped == "responses:":
            in_responses = True
            result[(current_method, current_path)] = set()
        elif in_responses and indent == 8:
            match = re.fullmatch(r'"([0-9]{3})":', stripped)
            if match:
                result[(current_method, current_path)].add(match.group(1))
        elif in_responses and indent <= 6 and stripped:
            in_responses = False
    return result


def test_runtime_openapi_matches_frozen_paths_and_response_statuses() -> None:
    app = create_app(
        Settings(
            environment="test",
            database_url="sqlite+aiosqlite:///./openapi-test.db",
            redis_url=None,
            demo_auth_enabled=True,
        ),
        repository=MemoryPlatformRepository(),
    )
    runtime = app.openapi()
    actual = {
        (method, path): set(operation["responses"])
        for path, path_item in runtime["paths"].items()
        for method, operation in path_item.items()
        if method in {"get", "post", "patch", "delete"}
    }
    assert actual == _frozen_response_statuses()

    stream_content = runtime["paths"]["/chat/stream"]["post"]["responses"]["200"]["content"]
    assert set(stream_content) == {"text/event-stream"}
    schema = stream_content["text/event-stream"]["schema"]
    assert schema["discriminator"]["propertyName"] == "event_type"

