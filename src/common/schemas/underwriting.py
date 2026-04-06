from pydantic import BaseModel, Field


class LBOAssumptions(BaseModel):
    """Input assumptions for Monte Carlo IRR simulation."""

    entry_ebitda_mean: float
    entry_ebitda_std: float
    entry_multiple_low: float
    entry_multiple_mode: float
    entry_multiple_high: float
    revenue_growth_mean: float
    revenue_growth_std: float
    margin_improvement_low: float = 0.0
    margin_improvement_mode: float = 0.02
    margin_improvement_high: float = 0.05
    debt_equity_low: float = 0.50
    debt_equity_high: float = 0.70
    hold_periods: list[int] = Field(default_factory=lambda: [3, 4, 5, 6, 7])
    exit_multiple_bear: float | None = None
    exit_multiple_base: float | None = None
    exit_multiple_bull: float | None = None
    interest_rate: float = 0.08
    num_simulations: int = 10_000


class IRRDistribution(BaseModel):
    p10: float
    p25: float
    p50: float
    p75: float
    p90: float
    mean: float
    std: float


class Sensitivity(BaseModel):
    parameter: str
    base_irr: float
    low_irr: float
    high_irr: float
    impact: float  # absolute change in IRR


class UnderwritingResult(BaseModel):
    """Output of Stage 5: Rapid Underwriting."""

    entity_id: str
    company_name: str
    irr_distribution: IRRDistribution
    moic_distribution: IRRDistribution
    p_irr_gt_20: float  # probability IRR > 20%
    p_irr_gt_25: float  # probability IRR > 25%
    downside_irr: float  # P10 IRR
    key_sensitivities: list[Sensitivity] = Field(default_factory=list)
    break_even_multiple: float  # exit multiple for 1x equity return
    recommended_bid_range: tuple[float, float]  # (low, high) USD
    structure_suggestion: str | None = None
    walkaway_price: float | None = None
    screening_decision: str  # "auto_reject", "pursue", "priority"
    simulated_at: str  # ISO 8601


class ICMemo(BaseModel):
    """Output of Stage 6: IC Preparation."""

    entity_id: str
    company_name: str
    thesis_id: str
    sections: dict = Field(default_factory=dict)
    # Keys: investment_thesis, company_overview, financial_summary,
    #        valuation_analysis, key_risks, value_creation_plan
    source_references: list[dict] = Field(default_factory=list)
    generated_at: str  # ISO 8601
    reviewed: bool = False
