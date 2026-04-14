#!/usr/bin/env python3
"""Bulk data ingestion CLI — builds ML training datasets from free sources.

Commands:
  python -m src.bulk_ingest edgar-ma --start 2023-01-01 --max 500
  python -m src.bulk_ingest form-d --start 2024-01-01 --max 200
  python -m src.bulk_ingest public-financials --tickers-file tickers.txt
  python -m src.bulk_ingest public-financials --sector technology --count 100
  python -m src.bulk_ingest stats
"""

import argparse
import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

import structlog

from src.common.config import get_settings
from src.common.dataset import DatasetAccumulator

logger = structlog.get_logger("bulk_ingest")


async def ingest_edgar_ma(
    start_date: str,
    end_date: str | None,
    max_results: int,
    data_dir: str,
) -> None:
    """Pull M&A transactions from EDGAR 8-K filings."""
    from src.ingestion.connectors.edgar_private import EdgarPrivateConnector

    ds = DatasetAccumulator(data_dir)
    connector = EdgarPrivateConnector()

    print(f"\n  Searching EDGAR 8-K filings for M&A deals since {start_date}...")
    try:
        deals = await connector.search_ma_filings(
            start_date=start_date,
            end_date=end_date,
            max_results=max_results,
        )
        print(f"  Found {len(deals)} deals with extractable data")

        transactions = connector.deals_to_transactions(deals)
        total = ds.save_transactions(transactions)

        # Also save raw deal data for richer training features
        ds.save_pipeline_run(
            command="edgar-ma",
            start_date=start_date,
            end_date=end_date or "now",
            deals_found=len(deals),
            transactions_saved=len(transactions),
            total_transactions=total,
        )

        print(f"  Saved {len(transactions)} transactions (total in dataset: {total})")

        # Show sample
        if deals:
            print(f"\n  {'TARGET':<30} {'ACQUIRER':<25} {'EV':>15} {'DATE':>12}")
            print(f"  {'─' * 85}")
            for d in deals[:15]:
                ev_str = f"${d.enterprise_value:,.0f}" if d.enterprise_value else "undisclosed"
                target = (d.target_name or "Unknown")[:30]
                print(f"  {target:<30} {d.acquirer_name[:25]:<25} {ev_str:>15} {d.filing_date:>12}")

    finally:
        await connector.close()


async def ingest_form_d(
    start_date: str,
    end_date: str | None,
    max_results: int,
    data_dir: str,
) -> None:
    """Pull private placement data from SEC Form D filings."""
    from src.ingestion.connectors.edgar_private import EdgarPrivateConnector

    ds = DatasetAccumulator(data_dir)
    connector = EdgarPrivateConnector()

    print(f"\n  Searching Form D filings since {start_date}...")
    try:
        offerings = await connector.search_form_d(
            start_date=start_date,
            end_date=end_date,
            max_results=max_results,
        )
        print(f"  Found {len(offerings)} private placements")

        total_offerings = ds.save_form_d(offerings)
        companies = connector.offerings_to_companies(offerings)
        total_companies = ds.save_companies(companies)

        ds.save_pipeline_run(
            command="form-d",
            start_date=start_date,
            end_date=end_date or "now",
            offerings_found=len(offerings),
            companies_saved=len(companies),
            total_offerings=total_offerings,
        )

        print(f"  Saved {len(offerings)} offerings, {len(companies)} companies")

        if offerings:
            print(f"\n  {'ISSUER':<35} {'AMOUNT SOLD':>15} {'REVENUE RANGE':<20} {'DATE':>12}")
            print(f"  {'─' * 85}")
            for o in offerings[:15]:
                amt = f"${o.total_amount_sold:,.0f}" if o.total_amount_sold else "N/A"
                rev = o.revenue_range or "N/A"
                print(f"  {o.issuer_name[:35]:<35} {amt:>15} {rev[:20]:<20} {o.filing_date:>12}")

    finally:
        await connector.close()


async def ingest_public_financials(
    tickers: list[str],
    data_dir: str,
) -> None:
    """Pull financial data for public companies from free sources."""
    from src.common.schemas.ingestion import CompanyRaw, OwnershipType, TransactionRecord
    from src.ingestion.connectors.yfinance_connector import YFinanceConnector

    ds = DatasetAccumulator(data_dir)
    yf = YFinanceConnector()
    settings = get_settings()

    # Optionally use EDGAR and FMP
    edgar_connector = None
    fmp_connector = None
    try:
        from src.ingestion.connectors.sec_edgar import SECEdgarConnector
        edgar_connector = SECEdgarConnector(identity=settings.edgar_identity)
    except Exception:
        pass

    if settings.fmp_api_key:
        try:
            from src.ingestion.connectors.fmp import FMPConnector
            fmp_connector = FMPConnector(api_key=settings.fmp_api_key)
        except Exception:
            pass

    print(f"\n  Fetching financials for {len(tickers)} tickers...")
    companies: list[CompanyRaw] = []
    skipped = 0

    for i, ticker in enumerate(tickers):
        print(f"  [{i + 1}/{len(tickers)}] {ticker}...", end=" ", flush=True)

        # Primary: yfinance
        yf_data = yf.get_company_profile(ticker)
        if yf_data.get("error"):
            print("skip (not found)")
            skipped += 1
            continue

        revenue = yf_data.get("revenue")
        ebitda = yf_data.get("ebitda")

        # Supplement with EDGAR if available
        if edgar_connector:
            try:
                edgar_data = edgar_connector.get_company_financials(ticker)
                if not edgar_data.get("error"):
                    revenue = edgar_data.get("revenue") or revenue
                    ebitda = edgar_data.get("ebitda") or ebitda
            except Exception:
                pass

        # Supplement with FMP if available
        if fmp_connector:
            try:
                fmp_data = await fmp_connector.fetch_financials(ticker)
                if not fmp_data.get("error"):
                    revenue = fmp_data.get("revenue") or revenue
                    ebitda = fmp_data.get("ebitda") or ebitda
            except Exception:
                pass

        company = CompanyRaw(
            source="bulk_ingest",
            source_id=f"ticker-{ticker}",
            name=yf_data.get("company_name") or ticker,
            domain=yf_data.get("website"),
            description=yf_data.get("description"),
            industry=yf_data.get("industry"),
            hq_city=yf_data.get("city"),
            hq_state=yf_data.get("state"),
            hq_country=yf_data.get("country"),
            employee_count=yf_data.get("employee_count"),
            estimated_revenue=float(revenue) if revenue else None,
            estimated_ebitda=float(ebitda) if ebitda else None,
            ownership_type=OwnershipType.PUBLIC,
        )
        companies.append(company)

        rev_str = f"${revenue / 1e6:.0f}M" if revenue else "N/A"
        print(f"ok ({rev_str} rev)")

    # Normalize and save
    from src.entity_resolution.engine import EntityResolutionEngine
    from src.ingestion.normalizers.company import normalize_company

    er = EntityResolutionEngine()
    normalized = [normalize_company(c, er.resolve(c)) for c in companies]
    total = ds.save_companies(normalized)

    ds.save_pipeline_run(
        command="public-financials",
        tickers_requested=len(tickers),
        companies_saved=len(normalized),
        skipped=skipped,
        total_companies=total,
    )

    if fmp_connector:
        await fmp_connector.close()

    print(f"\n  Saved {len(normalized)} companies (total in dataset: {total})")


async def ingest_public_by_sector(
    sector: str,
    count: int,
    data_dir: str,
) -> None:
    """Discover tickers in a sector via yfinance and ingest their financials."""
    try:
        import yfinance as yf
    except ImportError:
        print("  ERROR: yfinance not installed")
        return

    print(f"\n  Discovering {sector} tickers...")

    # Use yfinance screener to find tickers in a sector
    tickers: list[str] = []
    try:
        results = yf.Search(sector)
        for q in results.quotes[:count]:
            sym = q.get("symbol", "")
            if sym and "." not in sym and len(sym) <= 5:
                tickers.append(sym)
    except Exception as e:
        print(f"  Search failed: {e}")

    if not tickers:
        print("  No tickers found for sector. Try --tickers-file instead.")
        return

    print(f"  Found {len(tickers)} tickers: {', '.join(tickers[:10])}{'...' if len(tickers) > 10 else ''}")
    await ingest_public_financials(tickers, data_dir)


def show_stats(data_dir: str) -> None:
    """Display dataset statistics."""
    ds = DatasetAccumulator(data_dir)
    stats = ds.stats()

    if not stats:
        print("\n  No dataset files found. Run an ingest command first.")
        return

    print("\n  DATASET STATISTICS")
    print(f"  {'─' * 45}")
    print(f"  Directory: {ds.root.resolve()}")
    print()
    total_rows = 0
    for table, count in stats.items():
        print(f"  {table:<30} {count:>8,} rows")
        total_rows += count
    print(f"  {'─' * 45}")
    print(f"  {'Total':<30} {total_rows:>8,} rows")

    # Show recent pipeline runs
    runs = ds.load("pipeline_runs")
    if not runs.empty:
        print(f"\n  RECENT PIPELINE RUNS")
        print(f"  {'─' * 45}")
        for _, row in runs.tail(5).iterrows():
            ts = row.get("timestamp", "?")[:19]
            cmd = row.get("command", "?")
            print(f"  {ts}  {cmd}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Bulk data ingestion for ML training datasets",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m src.bulk_ingest edgar-ma --start 2023-01-01 --max 500
  python -m src.bulk_ingest form-d --start 2024-01-01 --max 200
  python -m src.bulk_ingest public-financials --tickers AAPL MSFT GOOG CRM
  python -m src.bulk_ingest public-financials --tickers-file tickers.txt
  python -m src.bulk_ingest public-financials --sector technology --count 50
  python -m src.bulk_ingest stats
        """,
    )
    parser.add_argument(
        "--data-dir", type=str, default="data/bulk",
        help="Directory for dataset storage (default: data/bulk/)",
    )

    sub = parser.add_subparsers(dest="command")

    # edgar-ma
    ma = sub.add_parser("edgar-ma", help="Ingest M&A deals from EDGAR 8-K filings")
    ma.add_argument("--start", type=str, default="2023-01-01", help="Start date (YYYY-MM-DD)")
    ma.add_argument("--end", type=str, default=None, help="End date (default: today)")
    ma.add_argument("--max", type=int, default=200, help="Max filings to process")

    # form-d
    fd = sub.add_parser("form-d", help="Ingest private placements from Form D filings")
    fd.add_argument("--start", type=str, default="2024-01-01", help="Start date")
    fd.add_argument("--end", type=str, default=None, help="End date")
    fd.add_argument("--max", type=int, default=200, help="Max filings to process")

    # public-financials
    pf = sub.add_parser("public-financials", help="Ingest public company financials")
    pf.add_argument("--tickers", nargs="+", help="Ticker symbols")
    pf.add_argument("--tickers-file", type=str, help="File with one ticker per line")
    pf.add_argument("--sector", type=str, help="Discover tickers by sector")
    pf.add_argument("--count", type=int, default=50, help="Max tickers for sector search")

    # stats
    sub.add_parser("stats", help="Show dataset statistics")

    args = parser.parse_args()

    if args.command == "edgar-ma":
        asyncio.run(ingest_edgar_ma(args.start, args.end, args.max, args.data_dir))
    elif args.command == "form-d":
        asyncio.run(ingest_form_d(args.start, args.end, args.max, args.data_dir))
    elif args.command == "public-financials":
        tickers = []
        if args.tickers:
            tickers = args.tickers
        elif args.tickers_file:
            tickers = Path(args.tickers_file).read_text().strip().splitlines()
            tickers = [t.strip().upper() for t in tickers if t.strip()]
        elif args.sector:
            asyncio.run(ingest_public_by_sector(args.sector, args.count, args.data_dir))
            return
        else:
            print("  Provide --tickers, --tickers-file, or --sector")
            return
        asyncio.run(ingest_public_financials(tickers, args.data_dir))
    elif args.command == "stats":
        show_stats(args.data_dir)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
