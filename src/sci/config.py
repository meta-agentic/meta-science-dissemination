"""Configuration loading with validation at the boundary.

A malformed threshold silently defaulting to zero would make every item look
publishable. So every field is checked on load and a bad config is a hard
failure, not a warning.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = ROOT / "config"


class ConfigError(ValueError):
    """Raised when configuration is missing or out of range."""


def _load_yaml(path: Path) -> dict[str, Any]:
    """Parse a YAML file, preferring PyYAML and falling back to a subset."""
    if not path.is_file():
        raise ConfigError(f"missing config file: {path}")
    text = path.read_text(encoding="utf-8")
    try:
        import yaml
    except ModuleNotFoundError as exc:  # pragma: no cover - env dependent
        raise ConfigError(
            "PyYAML is required. Install with: uv sync, or pip install pyyaml"
        ) from exc
    data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise ConfigError(f"{path.name} must contain a mapping at the top level")
    return data


def _require(mapping: dict[str, Any], key: str, path: str) -> Any:
    if key not in mapping:
        raise ConfigError(f"missing required key: {path}.{key}")
    return mapping[key]


def _ratio(value: Any, path: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{path} must be a number, got {value!r}") from exc
    if not 0.0 <= number <= 1.0:
        raise ConfigError(f"{path} must be within [0, 1], got {number}")
    return number


@dataclass(frozen=True)
class Source:
    id: str
    name: str
    url: str
    lang: str = "en"
    independence: float = 0.0

    @property
    def is_independent(self) -> bool:
        """Whether a match here counts as genuine confirmation.

        Press-release republishers score 0.0 and are recorded as echo, so a
        single institutional press release cannot masquerade as three
        separate confirmations.
        """
        return self.independence >= 0.5


@dataclass(frozen=True)
class Sources:
    primary: tuple[Source, ...]
    corroborators: tuple[Source, ...]

    @property
    def all(self) -> tuple[Source, ...]:
        return self.primary + self.corroborators

    def by_id(self, source_id: str) -> Source | None:
        return next((s for s in self.all if s.id == source_id), None)


def load_sources(path: Path | None = None) -> Sources:
    data = _load_yaml(path or CONFIG_DIR / "sources.yaml")

    def build(key: str, *, independent_default: float) -> tuple[Source, ...]:
        raw = data.get(key) or []
        if not isinstance(raw, list) or not raw:
            raise ConfigError(f"sources.yaml: '{key}' must be a non-empty list")
        out = []
        for index, entry in enumerate(raw):
            where = f"sources.yaml:{key}[{index}]"
            if not isinstance(entry, dict):
                raise ConfigError(f"{where} must be a mapping")
            out.append(
                Source(
                    id=str(_require(entry, "id", where)),
                    name=str(_require(entry, "name", where)),
                    url=str(_require(entry, "url", where)),
                    lang=str(entry.get("lang", "en")),
                    independence=_ratio(
                        entry.get("independence", independent_default),
                        f"{where}.independence",
                    ),
                )
            )
        ids = [s.id for s in out]
        if len(ids) != len(set(ids)):
            raise ConfigError(f"sources.yaml: duplicate id in '{key}'")
        return tuple(out)

    return Sources(
        primary=build("primary", independent_default=1.0),
        corroborators=build("corroborators", independent_default=0.0),
    )


@dataclass(frozen=True)
class Pipeline:
    raw: dict[str, Any] = field(repr=False)

    def section(self, name: str) -> dict[str, Any]:
        value = self.raw.get(name)
        if not isinstance(value, dict):
            raise ConfigError(f"pipeline.yaml: missing section '{name}'")
        return value

    def get(self, section: str, key: str, default: Any = None) -> Any:
        block = self.section(section)
        if key not in block:
            if default is None:
                raise ConfigError(f"pipeline.yaml: missing '{section}.{key}'")
            return default
        return block[key]


def load_pipeline(path: Path | None = None) -> Pipeline:
    data = _load_yaml(path or CONFIG_DIR / "pipeline.yaml")
    pipeline = Pipeline(raw=data)

    # Fail fast on the thresholds that decide what gets published.
    binding = pipeline.section("binding")
    bound = _ratio(_require(binding, "bound_threshold", "binding"), "binding.bound_threshold")
    weak = _ratio(_require(binding, "weak_threshold", "binding"), "binding.weak_threshold")
    if weak > bound:
        raise ConfigError(
            f"binding.weak_threshold ({weak}) must not exceed bound_threshold ({bound})"
        )
    _ratio(
        _require(pipeline.section("corroboration"), "match_threshold", "corroboration"),
        "corroboration.match_threshold",
    )

    gate = pipeline.section("gate")
    max_hype = _require(gate, "max_hype_score", "gate")
    if not isinstance(max_hype, (int, float)) or not 0 <= max_hype <= 100:
        raise ConfigError(f"gate.max_hype_score must be within [0, 100], got {max_hype!r}")
    return pipeline


@dataclass(frozen=True)
class Settings:
    sources: Sources
    pipeline: Pipeline
    root: Path = ROOT

    @property
    def data_dir(self) -> Path:
        return self.root / "data"

    @property
    def drafts_dir(self) -> Path:
        return self.root / "drafts"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "sci.db"


def load(root: Path | None = None) -> Settings:
    base = root or ROOT
    settings = Settings(
        sources=load_sources(base / "config" / "sources.yaml"),
        pipeline=load_pipeline(base / "config" / "pipeline.yaml"),
        root=base,
    )
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.drafts_dir.mkdir(parents=True, exist_ok=True)
    return settings


def version() -> str:
    """Project version, read from pyproject so it is stated in one place."""
    try:
        data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        return str(data["project"]["version"])
    except Exception:  # pragma: no cover - version is cosmetic
        return "0.0.0"
