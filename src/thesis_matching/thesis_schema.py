"""Investment thesis encoding and management."""

from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class InvestmentThesis(BaseModel):
    """Structured encoding of a PE investment thesis."""

    id: str
    description: str
    sector: list[str] = Field(default_factory=list)
    revenue_range: tuple[float, float]  # (min, max) USD
    ebitda_margin_floor: float = 0.0
    geography: list[str] = Field(default_factory=lambda: ["US"])
    ownership_preference: list[str] = Field(default_factory=list)
    growth_floor: float = 0.0
    must_have: list[str] = Field(default_factory=list)
    nice_to_have: list[str] = Field(default_factory=list)
    anti_patterns: list[str] = Field(default_factory=list)
    deal_type: str = "platform"  # platform, add-on, growth, buyout
    active: bool = True


class ThesisStore:
    """Loads and manages investment theses from YAML definitions."""

    def __init__(self, thesis_dir: Path | None = None) -> None:
        self._theses: dict[str, InvestmentThesis] = {}
        if thesis_dir and thesis_dir.exists():
            self._load_from_dir(thesis_dir)

    def _load_from_dir(self, thesis_dir: Path) -> None:
        for path in thesis_dir.glob("*.yaml"):
            with open(path) as f:
                data = yaml.safe_load(f)
            if "thesis" in data:
                data = data["thesis"]
            thesis = InvestmentThesis.model_validate(data)
            self._theses[thesis.id] = thesis

    def add(self, thesis: InvestmentThesis) -> None:
        self._theses[thesis.id] = thesis

    def get(self, thesis_id: str) -> InvestmentThesis | None:
        return self._theses.get(thesis_id)

    def list_active(self) -> list[InvestmentThesis]:
        return [t for t in self._theses.values() if t.active]

    def all(self) -> list[InvestmentThesis]:
        return list(self._theses.values())
