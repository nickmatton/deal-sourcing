"""Feature extraction for the deal signal detection model."""

from dataclasses import dataclass

import numpy as np


@dataclass
class SignalFeatures:
    """Feature vector for sell probability prediction."""

    # Ownership / succession
    founder_age: float = np.nan
    owner_tenure_years: float = np.nan
    has_succession_plan: float = 0.0  # 0 or 1
    ownership_pe_backed: float = 0.0
    pe_hold_duration_years: float = np.nan

    # Growth trajectory
    employee_growth_30d: float = np.nan
    employee_growth_90d: float = np.nan
    revenue_growth_yoy: float = np.nan
    web_traffic_growth_90d: float = np.nan
    review_volume_growth_90d: float = np.nan

    # Financial signals
    estimated_revenue: float = np.nan
    estimated_ebitda_margin: float = np.nan
    debt_maturity_months: float = np.nan
    funding_recency_months: float = np.nan

    # Advisor / strategic signals
    advisor_engagement_detected: float = 0.0
    corp_dev_hire_detected: float = 0.0
    leadership_change_6m: float = 0.0
    external_ceo_appointed: float = 0.0

    # Sector signals
    sector_ma_activity_12m: float = np.nan  # count of sector deals in 12 months
    sector_median_ev_ebitda: float = np.nan

    # Behavioral
    website_redesign_detected: float = 0.0
    tech_stack_migration: float = 0.0
    branding_change_detected: float = 0.0

    def to_array(self) -> np.ndarray:
        return np.array([
            self.founder_age,
            self.owner_tenure_years,
            self.has_succession_plan,
            self.ownership_pe_backed,
            self.pe_hold_duration_years,
            self.employee_growth_30d,
            self.employee_growth_90d,
            self.revenue_growth_yoy,
            self.web_traffic_growth_90d,
            self.review_volume_growth_90d,
            self.estimated_revenue,
            self.estimated_ebitda_margin,
            self.debt_maturity_months,
            self.funding_recency_months,
            self.advisor_engagement_detected,
            self.corp_dev_hire_detected,
            self.leadership_change_6m,
            self.external_ceo_appointed,
            self.sector_ma_activity_12m,
            self.sector_median_ev_ebitda,
            self.website_redesign_detected,
            self.tech_stack_migration,
            self.branding_change_detected,
        ], dtype=np.float64)

    @staticmethod
    def feature_names() -> list[str]:
        return [
            "founder_age",
            "owner_tenure_years",
            "has_succession_plan",
            "ownership_pe_backed",
            "pe_hold_duration_years",
            "employee_growth_30d",
            "employee_growth_90d",
            "revenue_growth_yoy",
            "web_traffic_growth_90d",
            "review_volume_growth_90d",
            "estimated_revenue",
            "estimated_ebitda_margin",
            "debt_maturity_months",
            "funding_recency_months",
            "advisor_engagement_detected",
            "corp_dev_hire_detected",
            "leadership_change_6m",
            "external_ceo_appointed",
            "sector_ma_activity_12m",
            "sector_median_ev_ebitda",
            "website_redesign_detected",
            "tech_stack_migration",
            "branding_change_detected",
        ]
