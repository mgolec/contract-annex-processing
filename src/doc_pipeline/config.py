"""Pipeline configuration — loads pipeline.toml + .env."""

from __future__ import annotations

import logging
import os
from datetime import date
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field, field_validator

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib  # type: ignore[no-redef]

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


class GeneralConfig(BaseModel):
    company_name: str = "Procudo d.o.o."
    company_oib: str = "78127426216"
    company_address: str = "Fallerovo šetalište 22, 10000 Zagreb"
    company_director: str = "Bruno Bardić"
    default_location: str = "Zagreb"


class PathsConfig(BaseModel):
    source: str = "./contracts"
    working_dir: str = "./data"
    output_dir: str = "./output"
    template: str = "./templates/default/aneks_template.docx"


class ExtractionConfig(BaseModel):
    model: str = "claude-sonnet-4-5-20250929"
    use_batch_api: bool = True
    confidence_threshold: str = "medium"

    @field_validator('confidence_threshold', mode='before')
    @classmethod
    def validate_confidence(cls, v):
        valid = {"low", "medium", "high"}
        if v not in valid:
            raise ValueError(f"confidence_threshold must be one of {valid}, got '{v}'")
        return v


class CurrencyConfig(BaseModel):
    hrk_to_eur_rate: float = 7.53450
    default_currency: str = "EUR"


class GenerationConfig(BaseModel):
    default_effective_date: str = "2026-03-01"
    vat_note: str = "Sve cijene su izražene bez PDV-a."

    @field_validator('default_effective_date', mode='before')
    @classmethod
    def validate_date(cls, v):
        if isinstance(v, str):
            try:
                date.fromisoformat(v)
            except ValueError:
                raise ValueError(f"default_effective_date must be YYYY-MM-DD format, got '{v}'")
        return v


class PipelineConfig(BaseModel):
    """Top-level configuration assembled from pipeline.toml + .env."""

    general: GeneralConfig = Field(default_factory=GeneralConfig)
    paths: PathsConfig = Field(default_factory=PathsConfig)
    extraction: ExtractionConfig = Field(default_factory=ExtractionConfig)
    currency: CurrencyConfig = Field(default_factory=CurrencyConfig)
    generation: GenerationConfig = Field(default_factory=GenerationConfig)
    anthropic_api_key: str = ""

    @property
    def project_root(self) -> Path:
        return _PROJECT_ROOT

    @property
    def source_path(self) -> Path:
        return self._resolve(self.paths.source)

    @property
    def working_path(self) -> Path:
        return self._resolve(self.paths.working_dir)

    @property
    def data_source_path(self) -> Path:
        return self.working_path / "source"

    @property
    def output_path(self) -> Path:
        return self._resolve(self.paths.output_dir)

    @property
    def inventory_path(self) -> Path:
        return self.working_path / "inventory.json"

    @property
    def converted_path(self) -> Path:
        return self.working_path / "converted"

    @property
    def extractions_path(self) -> Path:
        return self.working_path / "extractions"

    @property
    def spreadsheet_path(self) -> Path:
        return self.output_path / "control_spreadsheet.xlsx"

    @property
    def annexes_output_path(self) -> Path:
        return self.output_path / "annexes"

    @property
    def template_path(self) -> Path:
        return self._resolve(self.paths.template)

    def validate_for_extraction(self) -> list[str]:
        """Validate config is ready for extraction phase. Returns list of error messages."""
        errors = []
        if not self.anthropic_api_key:
            errors.append("ANTHROPIC_API_KEY is not set (check .env file)")
        elif not self.anthropic_api_key.startswith(("sk-ant-", "sk-")):
            errors.append(
                f"ANTHROPIC_API_KEY has unexpected format "
                f"(starts with '{self.anthropic_api_key[:6]}...')"
            )
        return errors

    def _resolve(self, p: str) -> Path:
        path = Path(p)
        if path.is_absolute():
            return path
        return self.project_root / path


def load_config() -> PipelineConfig:
    """Load configuration from pipeline.toml and .env."""
    toml_path = _PROJECT_ROOT / "pipeline.toml"
    env_path = _PROJECT_ROOT / ".env"

    # Load TOML
    data: dict = {}
    if toml_path.exists():
        with open(toml_path, "rb") as f:
            data = tomllib.load(f)
        logger.debug("Loaded config from %s", toml_path)
    else:
        logger.warning("Config file not found: %s, using defaults", toml_path)

    # Load .env for API key
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key and env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            if key.strip() == "ANTHROPIC_API_KEY":
                val = val.strip()
                # Strip surrounding quotes (common in .env files)
                if (val.startswith('"') and val.endswith('"')) or \
                   (val.startswith("'") and val.endswith("'")):
                    val = val[1:-1]
                api_key = val
                break

    data["anthropic_api_key"] = api_key
    return PipelineConfig.model_validate(data)
