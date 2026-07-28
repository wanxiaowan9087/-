from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ARTIFACT_DIR = ROOT / "artifacts" / "quality"
COMMANDS = (
    "contract",
    "apifox",
    "backend",
    "agent",
    "rag",
    "frontend",
    "e2e",
    "compose",
    "security",
)


@dataclass(frozen=True)
class CommandResult:
    name: str
    command: str
    exit_code: int
    duration_seconds: float
    output: str


def _run(
    name: str,
    command: list[str],
    *,
    cwd: Path = ROOT,
    timeout_seconds: float = 90.0,
    env: dict[str, str] | None = None,
) -> CommandResult:
    started = time.monotonic()
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=timeout_seconds,
            env=env,
        )
        output = "\n".join(item for item in (completed.stdout, completed.stderr) if item)
        exit_code = completed.returncode
    except FileNotFoundError as error:
        output = f"required executable is unavailable: {error}"
        exit_code = 127
    except subprocess.TimeoutExpired as error:
        partial = error.stdout or ""
        if isinstance(partial, bytes):
            partial = partial.decode("utf-8", errors="replace")
        output = f"command timed out after {timeout_seconds:.0f} seconds\n{partial}"
        exit_code = 124
    return CommandResult(
        name=name,
        command=subprocess.list2cmdline(command),
        exit_code=exit_code,
        duration_seconds=round(time.monotonic() - started, 3),
        output=output[-20_000:],
    )


def _missing(name: str, detail: str) -> CommandResult:
    return CommandResult(name, detail, 127, 0.0, detail)


def _python(*args: str) -> list[str]:
    return [sys.executable, *args]


def _python_tool(environment_name: str, module: str, *args: str) -> list[str]:
    override = os.environ.get(environment_name)
    if override:
        return [override, *args]
    executable = shutil.which(module)
    if executable:
        return [executable, *args]
    return _python("-m", module, *args)


def _npm(*args: str) -> list[str]:
    executable = shutil.which("npm.cmd") or shutil.which("npm") or "npm"
    return [executable, *args]


def _docker() -> str | None:
    configured = os.environ.get("QUALITY_DOCKER_CLI")
    if configured and Path(configured).is_file():
        return configured
    discovered = shutil.which("docker.exe") or shutil.which("docker")
    if discovered:
        return discovered
    d_drive_default = Path("D:/DockerDesktop/resources/bin/docker.exe")
    if d_drive_default.is_file():
        return str(d_drive_default)
    return None


def _playwright_environment() -> dict[str, str]:
    environment = os.environ.copy()
    if "PLAYWRIGHT_BROWSERS_PATH" not in environment:
        d_drive_default = Path("D:/codex_store/playwright-browsers")
        if d_drive_default.is_dir():
            environment["PLAYWRIGHT_BROWSERS_PATH"] = str(d_drive_default)
    return environment


def _git_head() -> str:
    result = _run("git-head", ["git", "rev-parse", "HEAD"])
    if result.exit_code != 0:
        raise RuntimeError(result.output or "cannot resolve Git HEAD")
    return result.output.strip()


def _working_tree_is_clean() -> bool:
    result = _run("git-status", ["git", "status", "--porcelain"])
    return result.exit_code == 0 and not result.output.strip()


def run_contract(_: str) -> list[CommandResult]:
    return [
        _run(
            "contract",
            _python(
                "-m",
                "pytest",
                "backend/tests/test_contract_models.py",
                "backend/tests/test_openapi_drift.py",
                "tests/qa/test_assets.py",
            ),
        )
    ]


def run_apifox(_: str) -> list[CommandResult]:
    candidate = os.environ.get("APIFOX_CLI")
    discovered = shutil.which("apifox.cmd") or shutil.which("apifox")
    executable = Path(candidate) if candidate else Path(discovered or "")
    suite = ROOT / "tests" / "apifox" / "scenarios" / "offline-suite.json"
    if not executable.is_file():
        return [_missing("apifox", "APIFOX_CLI is not configured to an executable")]
    if not suite.is_file():
        return [_missing("apifox", f"reviewed executable scenario suite is missing: {suite}")]
    ARTIFACT_DIR.joinpath("apifox").mkdir(parents=True, exist_ok=True)
    return [
        _run(
            "apifox",
            [
                str(executable),
                "run",
                str(suite),
                "--reporters",
                "cli,json,junit",
                "--output",
                str(ARTIFACT_DIR / "apifox"),
            ],
        )
    ]


def run_backend(_: str) -> list[CommandResult]:
    return [
        _run("backend-lint", _python_tool("QUALITY_RUFF_BIN", "ruff", "check", "backend")),
        _run("backend-types", _python_tool("QUALITY_MYPY_BIN", "mypy", "backend/app")),
        _run(
            "backend-tests",
            _python(
                "-m", "pytest", "backend/tests/test_api.py", "backend/tests/test_sql_adapter.py"
            ),
        ),
    ]


def run_agent(_: str) -> list[CommandResult]:
    return [
        _run(
            "agent-tests",
            _python(
                "-m",
                "pytest",
                "backend/tests/agent",
                "backend/tests/test_runtime_platform_integration.py",
                "backend/tests/test_platform_memory_runtime.py",
            ),
        )
    ]


def run_rag(sha: str) -> list[CommandResult]:
    return [
        _run("rag-tests", _python("-m", "pytest", "backend/tests/rag")),
        _run(
            "rag-eval",
            _python(
                "-m",
                "evals.run_rag_eval",
                "--assert-gates",
                "--implementation-sha",
                sha,
            ),
        ),
    ]


def run_frontend(_: str) -> list[CommandResult]:
    return [
        _run("frontend-lint", _npm("run", "lint"), cwd=ROOT / "frontend"),
        _run("frontend-types", _npm("run", "typecheck"), cwd=ROOT / "frontend"),
        _run("frontend-tests", _npm("run", "test", "--", "--run"), cwd=ROOT / "frontend"),
        _run("frontend-build", _npm("run", "build"), cwd=ROOT / "frontend"),
    ]


def run_e2e(_: str) -> list[CommandResult]:
    executable = ROOT / "frontend" / "node_modules" / ".bin" / "playwright.cmd"
    config = ROOT / "frontend" / "playwright.config.ts"
    tests = ROOT / "frontend" / "tests" / "e2e"
    if not executable.is_file() or not tests.is_dir():
        return [_missing("e2e", "Playwright executable or reviewed e2e suite is missing")]
    docker = _docker()
    if not docker:
        return [_missing("e2e", "Docker CLI is unavailable for the real API test stack")]
    if os.environ.get("QUALITY_ALLOW_COMPOSE") != "1":
        return [
            _missing("e2e", "set QUALITY_ALLOW_COMPOSE=1 to allow test stack lifecycle execution")
        ]
    stack = _run(
        "e2e-stack",
        [
            docker,
            "compose",
            "-f",
            "docker-compose.yml",
            "-f",
            "docker-compose.e2e.yml",
            "up",
            "--build",
            "-d",
            "--wait",
            "--wait-timeout",
            "120",
        ],
        timeout_seconds=150.0,
    )
    if stack.exit_code != 0:
        return [stack]
    return [
        stack,
        _run(
            "e2e",
            [str(executable), "test", "--config", str(config)],
            cwd=ROOT / "frontend",
            env=_playwright_environment(),
        ),
    ]


def run_compose(_: str) -> list[CommandResult]:
    docker = _docker()
    if not docker:
        return [_missing("compose", "Docker CLI is unavailable")]
    if os.environ.get("QUALITY_ALLOW_COMPOSE") != "1":
        return [_missing("compose", "set QUALITY_ALLOW_COMPOSE=1 to allow lifecycle execution")]
    return [
        _run(
            "compose",
            [docker, "compose", "up", "--build", "--wait", "--wait-timeout", "120"],
        )
    ]


def run_security(_: str) -> list[CommandResult]:
    pip_audit = shutil.which("pip-audit")
    if not pip_audit:
        return [
            _missing(
                "security", "pip-audit is unavailable; dependency vulnerability gate cannot run"
            )
        ]
    return [
        _run(
            "security-python",
            [pip_audit, "-r", "requirements.lock"],
            timeout_seconds=45.0,
        ),
        _run(
            "security-node",
            _npm("audit", "--omit=dev", "--audit-level=high"),
            cwd=ROOT / "frontend",
        ),
    ]


RUNNERS: dict[str, Callable[[str], list[CommandResult]]] = {
    "contract": run_contract,
    "apifox": run_apifox,
    "backend": run_backend,
    "agent": run_agent,
    "rag": run_rag,
    "frontend": run_frontend,
    "e2e": run_e2e,
    "compose": run_compose,
    "security": run_security,
}


def _write_report(sha: str, results: list[CommandResult], started_at: datetime) -> None:
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    finished_at = datetime.now(UTC)
    report = {
        "integration_sha": sha,
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "environment": {
            "platform": platform.platform(),
            "python": sys.version,
            "node": _run("node-version", ["node", "--version"]).output.strip(),
            "docker": _run("docker-version", [_docker() or "docker", "--version"]).output.strip(),
        },
        "passed": all(result.exit_code == 0 for result in results),
        "results": [asdict(result) for result in results],
    }
    (ARTIFACT_DIR / "acceptance-report.json").write_text(
        json.dumps(report, ensure_ascii=True, indent=2), encoding="utf-8"
    )
    lines = [
        "# Acceptance report",
        "",
        f"- Integration SHA: `{sha}`",
        f"- Passed: `{report['passed']}`",
        f"- Started: `{report['started_at']}`",
        f"- Finished: `{report['finished_at']}`",
        "",
        "| Check | Exit code | Seconds | Command |",
        "| --- | ---: | ---: | --- |",
    ]
    lines.extend(
        (
            f"| {result.name} | {result.exit_code} | {result.duration_seconds:.3f} | "
            f"`{result.command}` |"
        )
        for result in results
    )
    (ARTIFACT_DIR / "acceptance-report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the project's authoritative quality gates.")
    parser.add_argument("command", choices=(*COMMANDS, "all"))
    parser.add_argument(
        "--integration-sha", required=True, help="exact 40-character Git SHA at HEAD"
    )
    args = parser.parse_args()
    actual_sha = _git_head()
    if args.integration_sha != actual_sha or len(actual_sha) != 40:
        parser.error("--integration-sha must exactly match the current 40-character HEAD SHA")
    if args.command == "all" and not _working_tree_is_clean():
        parser.error("the all gate requires a clean working tree at the integration SHA")
    selected = COMMANDS if args.command == "all" else (args.command,)
    started_at = datetime.now(UTC)
    results: list[CommandResult] = []
    for command in selected:
        results.extend(RUNNERS[command](actual_sha))
    _write_report(actual_sha, results, started_at)
    return 0 if all(result.exit_code == 0 for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
