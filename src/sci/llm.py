"""Reasoning backend, via the local `claude` CLI in headless mode.

Deliberately no API key and no vendored SDK: the pipeline shells out to the
Claude Code CLI the machine is already authenticated for. That keeps a cron
job free of secrets on disk.

The model is used for exactly two jobs — proposing candidate claims, and
writing Italian prose. It is never the arbiter of whether a claim is true;
that decision belongs to deterministic code in `textutil` and `claims`.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from typing import Any

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


class LLMUnavailable(RuntimeError):
    """The CLI is missing, unauthenticated, or timed out."""


@dataclass
class LLM:
    model: str = "claude-sonnet-5"
    timeout: int = 180
    binary: str = "claude"

    @property
    def available(self) -> bool:
        return shutil.which(self.binary) is not None

    def complete(self, prompt: str, *, system: str | None = None) -> str:
        """Run one headless completion and return the text."""
        if not self.available:
            raise LLMUnavailable(
                f"`{self.binary}` not found on PATH; drafting stages are unavailable"
            )

        command = [self.binary, "-p", prompt, "--model", self.model]
        if system:
            command += ["--append-system-prompt", system]

        try:
            result = subprocess.run(
                command, capture_output=True, text=True,
                timeout=self.timeout, check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise LLMUnavailable(f"claude CLI timed out after {self.timeout}s") from exc

        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()[:400]
            raise LLMUnavailable(f"claude CLI exited {result.returncode}: {detail}")

        output = (result.stdout or "").strip()
        if not output:
            raise LLMUnavailable("claude CLI returned no output")
        return output

    def complete_json(self, prompt: str, *, system: str | None = None) -> Any:
        """Run a completion expected to return JSON, and parse it.

        Models wrap JSON in prose or fences often enough that tolerating both
        is worth more than a strict parser plus a retry loop.
        """
        raw = self.complete(prompt, system=system)
        return parse_json(raw)


def parse_json(raw: str) -> Any:
    """Extract a JSON value from model output, or raise ValueError."""
    text = raw.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    fenced = _FENCE.search(text)
    if fenced:
        try:
            return json.loads(fenced.group(1).strip())
        except json.JSONDecodeError:
            pass

    # Last resort: the outermost array or object in the text.
    for opener, closer in (("[", "]"), ("{", "}")):
        start, end = text.find(opener), text.rfind(closer)
        if 0 <= start < end:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                continue

    raise ValueError(f"no JSON found in model output: {text[:200]}")


def from_settings(settings: Any) -> LLM:
    """Build the backend described by pipeline.yaml."""
    return LLM(model=str(settings.pipeline.get("draft", "model", "claude-sonnet-5")))
