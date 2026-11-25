"""Interchangeable, restricted CLI execution; no provider controls scientific decisions."""

from __future__ import annotations

import json
import os
import re
import subprocess
import uuid
from typing import Protocol

from .agents import (
    SCHEMAS,
    AuthenticationError,
    CLIAdapter,
    Codex,
    fits,
    prompt_prefix,
    validate_output,
)
from .config import PROVIDERS, ROLES, Config
from .storage import VaultError, atomic, inside


class LLMRuntime(Protocol):
    provider: str
    last_metadata: dict

    def run(self, role: str, payload: dict) -> dict: ...
    def authenticate(self) -> None: ...
    def probe(self) -> dict: ...
    def identity(self, role: str) -> dict: ...


def claude_schema(value):
    """Transport only: Anthropic omits string-length constraints; Python still enforces them."""
    if isinstance(value, dict):
        return {k: claude_schema(v) for k, v in value.items() if k != "minLength"}
    if isinstance(value, list):
        return [claude_schema(v) for v in value]
    return value


class ClaudeCode(CLIAdapter):
    provider = "claude"
    label = "Claude Code"
    npm_entry = "@anthropic-ai/claude-code/cli.js"
    required_flags = (
        "--print",
        "--output-format",
        "--json-schema",
        "--tools",
        "--disallowedTools",
        "--strict-mcp-config",
        "--mcp-config",
        "--setting-sources",
        "--settings",
        "--safe-mode",
        "--restricted",
        "--no-session-persistence",
        "--permission-mode",
        "--disable-slash-commands",
        "--system-prompt-file",
    )

    def environment(self) -> dict:
        # Preserve externally managed credentials and platform PATH; never log environment values.
        return {
            **os.environ,
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
            "DISABLE_AUTOUPDATER": "1",
        }

    def authenticate(self) -> None:
        try:
            result = subprocess.run(
                [*self.command_prefix(), "auth", "status"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=15,
                cwd=self.config.root,
                env=self.environment(),
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise AuthenticationError(f"Cannot check Claude Code authentication: {exc}") from exc
        if result.returncode:
            raise AuthenticationError(
                "Claude Code authentication unavailable; run claude auth login in this environment"
            )

    def _run(self, role: str, payload: dict) -> dict:
        if not fits(self.config, role, payload, self.provider):
            raise VaultError(f"Claude Code {role} exceeds input/output budget; no truncation")
        staging = inside(self.config.root, f".pipeline/staging/{uuid.uuid4().hex}")
        staging.mkdir(parents=True)
        self.last_metadata["staging"] = staging.relative_to(self.config.root).as_posix()
        # Instructions stay on disk to avoid Windows argument-length limits; data goes to stdin.
        atomic(staging / "instructions.txt", prompt_prefix(self.config, role))
        atomic(staging / "input.txt", json.dumps(payload, ensure_ascii=False))
        atomic(staging / "schema.json", json.dumps(SCHEMAS[role]))
        command = [
            *self.command_prefix(),
            "-p",
            "--output-format",
            "json",
            "--json-schema",
            json.dumps(claude_schema(SCHEMAS[role])),
            "--tools",
            "",
            "--disallowedTools",
            "mcp__*",
            "--strict-mcp-config",
            "--mcp-config",
            '{"mcpServers":{}}',
            "--setting-sources",
            "",
            "--settings",
            '{"disableAllHooks":true}',
            "--safe-mode",
            "--restricted",
            "--disable-slash-commands",
            "--permission-mode",
            "dontAsk",
            "--no-session-persistence",
            "--system-prompt-file",
            str(staging / "instructions.txt"),
        ]
        settings = self.settings(role)
        if settings.get("model"):
            command.extend(["--model", settings["model"]])
        try:
            process = subprocess.run(
                command,
                input=json.dumps(payload, ensure_ascii=False),
                capture_output=True,
                text=True,
                encoding="utf-8",
                cwd=staging,
                timeout=settings.get("timeout_seconds", 600),
                env=self.environment(),
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise VaultError(f"Claude Code {role} execution failed: {exc}") from exc
        atomic(staging / "stderr.txt", process.stderr)
        atomic(staging / "response.json", process.stdout)
        try:
            envelope = json.loads(process.stdout)
        except ValueError as exc:
            if process.returncode:
                self.failure(process.returncode, process.stderr + "\n" + process.stdout)
            raise VaultError(f"Claude Code {role} returned invalid JSON") from exc
        if not isinstance(envelope, dict):
            raise VaultError("Claude Code response must be a JSON result object")
        self.last_metadata["usage"] = envelope.get("usage")
        self.last_metadata["total_cost_usd"] = envelope.get("total_cost_usd")
        # Keep only inference identity/usage, not auth records or arbitrary response fields.
        if isinstance(envelope.get("modelUsage"), dict):
            self.last_metadata["model_usage"] = envelope["modelUsage"]
            self.last_metadata["resolved_models"] = sorted(envelope["modelUsage"])
        if (
            process.returncode
            or envelope.get("is_error")
            or envelope.get("subtype", "success") != "success"
        ):
            self.failure(
                process.returncode, process.stderr + "\n" + str(envelope.get("result", ""))
            )
        if "structured_output" in envelope:
            result = envelope["structured_output"]
        else:
            # Older supported builds return JSON text under result. Refusals and prose fail closed.
            try:
                result = json.loads(envelope.get("result", ""))
            except (TypeError, ValueError) as exc:
                raise VaultError("Claude Code returned no valid structured output") from exc
        atomic(staging / "output.json", json.dumps(result, ensure_ascii=False))
        return validate_output(role, result)

    @staticmethod
    def failure(code, detail):
        if re.search(
            r"unauthori[sz]ed|\b401\b|not logged in|authentication|token.{0,30}expired|invalid api key",
            detail,
            re.I,
        ):
            raise AuthenticationError(
                "Claude Code authentication failed; run claude auth login in this environment"
            )
        raise VaultError(
            f"Claude Code execution failed (exit {code}); inspect staged response.json and stderr.txt"
        )


class LLMRunner:
    """Route roles through one execution interface, preserving the existing run -> dict API."""

    def __init__(self, config: Config):
        self.config = config
        self.adapters: dict[str, LLMRuntime] = {
            "codex": Codex(config),
            "claude": ClaudeCode(config),
        }
        self.last_metadata = {}

    def identity(self, role, provider=None):
        selected, _ = self.config.runtime(role, provider)
        return self.adapters[selected].identity(role)

    def run(self, role, payload):
        return self.run_provider(role, payload, self.config.runtime(role)[0])

    def run_provider(self, role, payload, provider):
        adapter = self.adapters[provider]
        try:
            return adapter.run(role, payload)
        finally:
            self.last_metadata = dict(adapter.last_metadata)

    def authenticate(self, roles=ROLES):
        for provider in sorted({self.config.runtime(role)[0] for role in roles}):
            adapter = self.adapters[provider]
            try:
                adapter.probe()
                adapter.authenticate()
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise VaultError(f"{provider} CLI preflight failed: {exc}") from exc

    def diagnostics(self):
        required = {self.config.runtime(role)[0] for role in ROLES}
        evaluation = self.config.data.get("llm", {}).get("evaluation", {})
        if evaluation.get("enabled"):
            required.add(evaluation["provider"])
        report = {}
        for provider in PROVIDERS:
            adapter = self.adapters[provider]
            record = {
                "required": provider in required,
                "available": False,
                "authentication": "not_checked",
            }
            try:
                record["command"] = adapter.command_prefix()
                record["available"] = True
                try:
                    record.update(adapter.probe())
                except (VaultError, OSError, subprocess.TimeoutExpired) as exc:
                    record["configuration_error"] = str(exc)
                    record["version"] = adapter.cli_version
                adapter.authenticate()
                record["authentication"] = "ok"
            except (VaultError, OSError, subprocess.TimeoutExpired) as exc:
                record["error"] = str(exc)
                if isinstance(exc, AuthenticationError):
                    record["authentication"] = "failed"
            report[provider] = record
        return report
