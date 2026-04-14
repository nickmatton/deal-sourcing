#!/usr/bin/env python3
"""Deal Sourcing Pipeline CLI.

Run the full pipeline or individual stages against Claude-researched data.

Usage:
    python -m src.cli run --sector "healthcare IT" --count 5
    python -m src.cli run --sector "business services" --geography US --count 10
    python -m src.cli run --thesis theses/healthcare-it-rollup.yaml --count 8
"""

import argparse
import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from src.alpha_detection.scorer import AlphaScorer
from src.common.config import get_settings
from src.common.dataset import DatasetAccumulator
from src.ingestion.enrichment import enrich_companies
from src.common.logging import (
    PipelineStage,
    configure_logging,
    log_pipeline_run,
    log_stage,
    log_step,
)
from src.common.schemas.ingestion import CompanyNormalized
from src.common.schemas.underwriting import LBOAssumptions
from src.entity_resolution.engine import EntityResolutionEngine
from src.ingestion.connectors.claude_research import ClaudeResearchConnector
from src.ingestion.normalizers.company import normalize_company
from src.thesis_matching.hard_filter import apply_hard_filters, filter_universe
from src.thesis_matching.thesis_schema import InvestmentThesis, ThesisStore
from src.underwriting.monte_carlo import MonteCarloSimulator
from src.valuation.engine import ShadowValuationEngine
from src.valuation.margin_estimator import MarginEstimator

try:
    from src.thesis_matching.semantic_matcher import SemanticMatcher, build_company_description
    _HAS_SEMANTIC = True
except ImportError:
    _HAS_SEMANTIC = False


async def run_pipeline(
    sector: str | None = None,
    geography: str = "US",
    count: int = 10,
    thesis_path: str | None = None,
    revenue_min: float = 5_000_000,
    revenue_max: float = 100_000_000,
    log_level: str = "INFO",
    model: str = "sonnet",
) -> None:
    configure_logging(log_level=log_level)

    with log_pipeline_run(
        "deal_sourcing",
        sector=sector,
        geography=geography,
        target_count=count,
    ) as plog:

        # --- Load thesis if provided ---
        thesis: InvestmentThesis | None = None
        if thesis_path:
            with log_step("load_thesis", plog, path=thesis_path) as tlog:
                store = ThesisStore(Path(thesis_path).parent)
                thesis = store.get(Path(thesis_path).stem)
                if thesis is None:
                    all_theses = store.all()
                    if all_theses:
                        thesis = all_theses[0]
                if thesis:
                    tlog.info("loaded", thesis_id=thesis.id, sectors=thesis.sector)
                    if not sector:
                        sector = thesis.sector[0] if thesis.sector else None
                    revenue_min = thesis.revenue_range[0]
                    revenue_max = thesis.revenue_range[1]

        # =============================================
        # STAGE 0A: Data Ingestion via Claude Research
        # =============================================
        with log_stage(PipelineStage.INGESTION, source="claude_research") as slog:
            connector = ClaudeResearchConnector(model=model)

            with log_step("fetch_companies", slog) as clog:
                raw_companies = await connector.fetch_companies(
                    sector=sector,
                    geography=geography,
                    count=count,
                    revenue_range=(revenue_min, revenue_max),
                )
                clog.info("received", companies=len(raw_companies))

            with log_step("fetch_transactions", slog) as txlog:
                transactions = await connector.fetch_transactions(
                    sector=sector,
                    geography=geography,
                    count=max(count, 5),
                )
                txlog.info("received", transactions=len(transactions))

        # =============================================
        # STAGE 0A+: Free-Data Enrichment
        # =============================================
        with log_stage(PipelineStage.INGESTION, source="free_data_enrichment") as slog:
            settings = get_settings()
            enrichment_results = await enrich_companies(
                raw_companies,
                fmp_api_key=settings.fmp_api_key,
                edgar_identity=settings.edgar_identity,
            )
            enriched_count = sum(1 for r in enrichment_results if r.fields_updated)
            slog.info(
                "enrichment_complete",
                total=len(raw_companies),
                enriched=enriched_count,
                tickers_found=sum(1 for r in enrichment_results if r.ticker),
            )
            for r in enrichment_results:
                if r.fields_updated:
                    slog.debug(
                        "enriched",
                        company=r.company_name,
                        ticker=r.ticker,
                        fields=r.fields_updated,
                    )

        # =============================================
        # STAGE 0B: Entity Resolution
        # =============================================
        with log_stage(PipelineStage.ENTITY_RESOLUTION, input_count=len(raw_companies)) as slog:
            er_engine = EntityResolutionEngine()
            normalized: list[CompanyNormalized] = []

            for raw in raw_companies:
                entity_id = er_engine.resolve(raw)
                norm = normalize_company(raw, entity_id)
                normalized.append(norm)

            slog.info("resolved", entities=len(normalized))

        # =============================================
        # STAGE 2: Thesis Matching (hard filter + semantic)
        # =============================================
        semantic_scores: dict[str, float] = {}
        if thesis:
            with log_stage(PipelineStage.THESIS_MATCHING, thesis_id=thesis.id) as slog:
                passing, rejected = filter_universe(normalized, thesis)
                for company, gaps in rejected:
                    slog.debug("rejected", company=company.name, gaps=gaps)
                slog.info(
                    "filtered",
                    passing=len(passing),
                    rejected=len(rejected),
                )
                targets = passing

                # Semantic similarity scoring on passing companies
                if _HAS_SEMANTIC and targets:
                    with log_step("semantic_matching", slog) as sem_log:
                        try:
                            matcher = SemanticMatcher()
                            descriptions = [build_company_description(c) for c in targets]
                            ranked = matcher.rank_companies(
                                thesis_description=thesis.description,
                                company_descriptions=descriptions,
                                company_ids=[c.entity_id for c in targets],
                            )
                            for eid, score in ranked:
                                semantic_scores[eid] = score
                            sem_log.info(
                                "semantic_scored",
                                count=len(ranked),
                                top_score=round(ranked[0][1], 3) if ranked else 0,
                            )
                        except Exception as e:
                            sem_log.warning("semantic_matching_failed", error=str(e))
        else:
            targets = normalized

        if not targets:
            plog.warning("no_targets_after_filtering")
            return

        # =============================================
        # STAGE 3A: Shadow Valuation
        # =============================================
        valuations = []
        with log_stage(PipelineStage.VALUATION, target_count=len(targets)) as slog:
            val_engine = ShadowValuationEngine(illiquidity_discount=0.20)

            for company in targets:
                dummy_features = np.zeros(12)
                known_ebitda = company.estimated_ebitda_usd
                known_revenue = company.estimated_revenue_usd

                # Guard: skip or estimate EBITDA if negative/missing
                if known_ebitda is not None and known_ebitda <= 0:
                    if known_revenue and known_revenue > 0:
                        known_ebitda = known_revenue * 0.15  # Default 15% margin
                        slog.info(
                            "ebitda_estimated",
                            company=company.name,
                            reason="negative_ebitda",
                            estimated_margin=0.15,
                        )
                    else:
                        slog.warning("valuation_skipped", company=company.name, reason="no positive EBITDA or revenue")
                        continue

                try:
                    val = val_engine.value_company(
                        entity_id=company.entity_id,
                        company_name=company.name,
                        revenue_features=dummy_features,
                        margin_features=np.zeros(8),
                        multiple_features=np.zeros(10),
                        known_revenue=known_revenue,
                        known_ebitda=known_ebitda,
                        comparable_transactions=transactions,
                        company_sector=company.industry_primary,
                    )
                    valuations.append((company, val))
                except ValueError as e:
                    slog.warning("valuation_skipped", company=company.name, reason=str(e))

            slog.info("valued", count=len(valuations))

        # =============================================
        # STAGE 3B: Alpha Detection
        # =============================================
        alpha_results = {}
        with log_stage(PipelineStage.ALPHA_DETECTION, target_count=len(valuations)) as slog:
            alpha_scorer = AlphaScorer()
            for company, val in valuations:
                alpha = alpha_scorer.score(company, val, transactions)
                alpha_results[company.entity_id] = alpha
            slog.info(
                "alpha_scored",
                total=len(alpha_results),
                high_alpha=sum(1 for a in alpha_results.values() if a.alpha_score > 0.3),
                efficiently_priced=sum(1 for a in alpha_results.values() if a.efficiently_priced),
            )

        # =============================================
        # STAGE 5: Rapid Underwriting (Monte Carlo)
        # =============================================
        underwriting_results = []
        with log_stage(PipelineStage.UNDERWRITING, target_count=len(valuations)) as slog:
            simulator = MonteCarloSimulator()

            for company, val in valuations:
                if val.estimated_ebitda is None or val.estimated_ebitda <= 0:
                    slog.debug("skipped_no_ebitda", company=company.name)
                    continue

                ebitda = val.estimated_ebitda
                multiple = val.implied_ev_ebitda_multiple or 8.0

                assumptions = LBOAssumptions(
                    entry_ebitda_mean=ebitda,
                    entry_ebitda_std=ebitda * 0.15,
                    entry_multiple_low=multiple * 0.8,
                    entry_multiple_mode=multiple,
                    entry_multiple_high=multiple * 1.2,
                    revenue_growth_mean=0.08,
                    revenue_growth_std=0.04,
                    exit_multiple_bear=multiple * 0.85,
                    exit_multiple_base=multiple,
                    exit_multiple_bull=multiple * 1.15,
                    num_simulations=10_000,
                )

                result = simulator.simulate(
                    entity_id=company.entity_id,
                    company_name=company.name,
                    assumptions=assumptions,
                )
                underwriting_results.append((company, val, result))

            slog.info(
                "underwritten",
                total=len(underwriting_results),
                priority=sum(1 for _, _, r in underwriting_results if r.screening_decision == "priority"),
                pursue=sum(1 for _, _, r in underwriting_results if r.screening_decision == "pursue"),
                rejected=sum(1 for _, _, r in underwriting_results if r.screening_decision == "auto_reject"),
            )

        # =============================================
        # PERSIST TO DATASET
        # =============================================
        ds = DatasetAccumulator("data/live")
        ds.save_companies(normalized)
        ds.save_transactions(transactions)
        ds.save_valuations([val for _, val in valuations])
        ds.save_alpha_scores(list(alpha_results.values()))
        ds.save_underwriting([uw for _, _, uw in underwriting_results])
        ds.save_enrichment_log(enrichment_results)
        ds.save_pipeline_run(
            command="run",
            sector=sector,
            geography=geography,
            thesis=thesis.id if thesis else None,
            companies_researched=len(raw_companies),
            companies_enriched=enriched_count,
            transactions=len(transactions),
            targets_after_filter=len(targets),
            valuations=len(valuations),
            underwriting_results=len(underwriting_results),
        )

        # =============================================
        # RESULTS SUMMARY
        # =============================================
        plog.info("pipeline_results", total_targets=len(underwriting_results))

        # Sort by IRR p50 descending
        underwriting_results.sort(key=lambda x: x[2].irr_distribution.p50, reverse=True)

        print("\n" + "=" * 90)
        print("  DEAL SOURCING PIPELINE RESULTS")
        print("=" * 90)
        print(f"  Sector: {sector or 'all'}  |  Geography: {geography}  |  Thesis: {thesis.id if thesis else 'none'}")
        print(f"  Companies researched: {len(raw_companies)}  |  Transactions found: {len(transactions)}")
        print(f"  Enriched from public data: {enriched_count}/{len(raw_companies)}")
        if thesis:
            print(f"  Passed thesis filter: {len(targets)}/{len(normalized)}")
        print("=" * 90)

        for company, val, uw in underwriting_results:
            decision_marker = {
                "priority": " *** PRIORITY ***",
                "pursue": " -> PURSUE",
                "auto_reject": " x REJECT",
            }.get(uw.screening_decision, "")

            alpha = alpha_results.get(company.entity_id)

            print(f"\n  {company.name}{decision_marker}")
            print(f"  {'─' * 60}")
            print(f"  Industry:    {company.industry_primary or 'N/A'}")
            print(f"  Location:    {company.hq_city or '?'}, {company.hq_state or '?'}, {company.hq_country}")
            print(f"  Employees:   {company.employee_count or 'N/A'}")
            print(f"  Ownership:   {company.ownership_type.value}")
            if val.estimated_revenue:
                print(f"  Revenue:     ${val.estimated_revenue:,.0f}")
            if val.estimated_ebitda:
                print(f"  EBITDA:      ${val.estimated_ebitda:,.0f}  ({val.estimated_ebitda / val.estimated_revenue:.0%} margin)" if val.estimated_revenue else f"  EBITDA:      ${val.estimated_ebitda:,.0f}")
            print(f"  EV estimate: ${val.ev_point_estimate:,.0f}  (range: ${val.ev_range_80ci[0]:,.0f} - ${val.ev_range_80ci[1]:,.0f})")
            print(f"  Multiple:    {val.implied_ev_ebitda_multiple:.1f}x EBITDA" if val.implied_ev_ebitda_multiple else "")
            sem_score = semantic_scores.get(company.entity_id)
            if sem_score is not None:
                print(f"  Thesis fit:  {sem_score:.0%} semantic match")
            if alpha:
                priced_label = "EFFICIENTLY PRICED" if alpha.efficiently_priced else "ALPHA DETECTED"
                print(f"  Alpha:       {alpha.alpha_score:.2f}  ({priced_label})")
                for sig in alpha.alpha_signals[:3]:
                    print(f"               - {sig.signal_type}: {sig.description[:80]}")
            print(f"  IRR (P50):   {uw.irr_distribution.p50:.1%}  |  MOIC (P50): {uw.moic_distribution.p50:.2f}x")
            print(f"  P(IRR>20%):  {uw.p_irr_gt_20:.0%}  |  P(IRR>25%): {uw.p_irr_gt_25:.0%}")
            print(f"  Downside:    {uw.downside_irr:.1%} IRR  |  Break-even: {uw.break_even_multiple:.1f}x exit")
            print(f"  Bid range:   ${uw.recommended_bid_range[0]:,.0f} - ${uw.recommended_bid_range[1]:,.0f}")
            print(f"  Decision:    {uw.screening_decision.upper()}")

        print("\n" + "=" * 90)

        # Transaction comps summary
        if transactions:
            print("\n  COMPARABLE TRANSACTIONS")
            print("  " + "─" * 60)
            for tx in transactions[:8]:
                ev_str = f"${tx.enterprise_value:,.0f}" if tx.enterprise_value else "undisclosed"
                mult_str = f"{tx.ev_ebitda_multiple:.1f}x" if tx.ev_ebitda_multiple else "N/A"
                print(f"  {tx.target_name:30s}  EV: {ev_str:>15s}  {mult_str:>6s}  {tx.deal_date}")
            print()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="AI-powered PE deal sourcing pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m src.cli run --sector "healthcare IT" --count 5
  python -m src.cli run --thesis theses/healthcare-it-rollup.yaml
  python -m src.cli run --sector "business services" --geography US --count 10 --log-level DEBUG
        """,
    )

    sub = parser.add_subparsers(dest="command")
    run_parser = sub.add_parser("run", help="Run the deal sourcing pipeline")
    run_parser.add_argument("--sector", type=str, help="Target sector (e.g. 'healthcare IT')")
    run_parser.add_argument("--geography", type=str, default="US", help="Target geography (default: US)")
    run_parser.add_argument("--count", type=int, default=10, help="Number of companies to research (default: 10)")
    run_parser.add_argument("--thesis", type=str, help="Path to thesis YAML file")
    run_parser.add_argument("--revenue-min", type=float, default=5_000_000, help="Min revenue filter (default: 5M)")
    run_parser.add_argument("--revenue-max", type=float, default=100_000_000, help="Max revenue filter (default: 100M)")
    run_parser.add_argument("--log-level", type=str, default="INFO", choices=["DEBUG", "INFO", "WARNING"], help="Log level")
    run_parser.add_argument("--model", type=str, default="sonnet", help="Claude model to use (default: sonnet)")

    args = parser.parse_args()

    if args.command == "run":
        asyncio.run(run_pipeline(
            sector=args.sector,
            geography=args.geography,
            count=args.count,
            thesis_path=args.thesis,
            revenue_min=args.revenue_min,
            revenue_max=args.revenue_max,
            log_level=args.log_level,
            model=args.model,
        ))
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
