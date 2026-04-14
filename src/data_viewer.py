#!/usr/bin/env python3
"""Data viewer — explore data from all connected sources.

Usage:
    python -m src.data_viewer yfinance AAPL MSFT CRM SNOW
    python -m src.data_viewer edgar AAPL CRM
    python -m src.data_viewer fmp AAPL CRM
    python -m src.data_viewer compare AAPL CRM SNOW PLTR
    python -m src.data_viewer sectors
"""

import argparse
import asyncio
import os
import sys


def _fmt_dollar(val: float | None, in_millions: bool = True) -> str:
    if val is None:
        return "—"
    if in_millions:
        return f"${val / 1e6:,.0f}M"
    return f"${val:,.0f}"


def _fmt_pct(val: float | None) -> str:
    if val is None:
        return "—"
    return f"{val:.1%}"


def _fmt_mult(val: float | None) -> str:
    if val is None:
        return "—"
    return f"{val:.1f}x"


def _print_table(headers: list[str], rows: list[list[str]], col_widths: list[int] | None = None) -> None:
    if not col_widths:
        col_widths = [max(len(h), max((len(str(r[i])) for r in rows), default=0)) + 2
                      for i, h in enumerate(headers)]

    header_line = "".join(h.ljust(w) for h, w in zip(headers, col_widths))
    print(f"  {header_line}")
    print(f"  {'─' * sum(col_widths)}")
    for row in rows:
        line = "".join(str(c).ljust(w) for c, w in zip(row, col_widths))
        print(f"  {line}")


def cmd_yfinance(tickers: list[str]) -> None:
    from src.ingestion.connectors.yfinance_connector import YFinanceConnector
    connector = YFinanceConnector()

    print(f"\n  YAHOO FINANCE — {len(tickers)} ticker(s)")
    print(f"  {'=' * 70}\n")

    for ticker in tickers:
        data = connector.get_company_profile(ticker)

        if data.get("error"):
            print(f"  {ticker}: ERROR — {data['error']}\n")
            continue

        print(f"  {data.get('company_name', ticker)}  ({ticker})")
        print(f"  {'-' * 60}")
        print(f"  Sector:        {data.get('sector', '—')}")
        print(f"  Industry:      {data.get('industry', '—')}")
        print(f"  Location:      {data.get('city', '—')}, {data.get('state', '—')}, {data.get('country', '—')}")
        print(f"  Employees:     {data.get('employee_count', '—'):,}" if data.get('employee_count') else "  Employees:     —")
        print(f"  Website:       {data.get('website', '—')}")
        print()
        print(f"  Revenue:       {_fmt_dollar(data.get('revenue'))}")
        print(f"  EBITDA:        {_fmt_dollar(data.get('ebitda'))}  ({_fmt_pct(data.get('ebitda_margin'))} margin)")
        print(f"  Rev Growth:    {_fmt_pct(data.get('revenue_growth'))}")
        print(f"  Gross Margin:  {_fmt_pct(data.get('gross_margin'))}")
        print(f"  Op Margin:     {_fmt_pct(data.get('operating_margin'))}")
        print()
        print(f"  Market Cap:    {_fmt_dollar(data.get('market_cap'))}")
        print(f"  EV:            {_fmt_dollar(data.get('enterprise_value'))}")
        print(f"  EV/EBITDA:     {_fmt_mult(data.get('ev_ebitda'))}")
        print(f"  EV/Revenue:    {_fmt_mult(data.get('ev_revenue'))}")
        print(f"  P/E (trail):   {_fmt_mult(data.get('trailing_pe'))}")
        print(f"  Beta:          {data.get('beta', '—')}")
        print()

        desc = data.get("description", "")
        if desc:
            print(f"  {desc[:200]}{'...' if len(desc) > 200 else ''}")
            print()


def cmd_edgar(tickers: list[str]) -> None:
    from src.common.config import get_settings
    from src.ingestion.connectors.sec_edgar import SECEdgarConnector
    identity = os.environ.get("EDGAR_IDENTITY") or get_settings().edgar_identity
    connector = SECEdgarConnector(identity=identity)

    print(f"\n  SEC EDGAR (10-K Filings) — {len(tickers)} ticker(s)")
    print(f"  {'=' * 70}\n")

    for ticker in tickers:
        data = connector.get_company_financials(ticker)

        if data.get("error"):
            print(f"  {ticker}: ERROR — {data['error']}\n")
            continue

        print(f"  {data.get('company_name', ticker)}  ({ticker})  CIK: {data.get('cik', '—')}")
        print(f"  Latest filing: {data.get('filing_date', '—')}")
        print(f"  {'-' * 60}")

        fiscal_years = data.get("fiscal_years", [])
        if not fiscal_years:
            print("  No financial data extracted\n")
            continue

        headers = ["Period", "Revenue", "EBITDA", "EBITDA Margin", "Op Income", "Op Margin", "D&A"]
        rows = []
        for fy in fiscal_years:
            rows.append([
                fy.get("period", "—"),
                _fmt_dollar(fy.get("revenue")),
                _fmt_dollar(fy.get("ebitda")),
                _fmt_pct(fy.get("ebitda_margin")),
                _fmt_dollar(fy.get("operating_income")),
                _fmt_pct(fy.get("operating_margin")),
                _fmt_dollar(fy.get("depreciation_amortization")),
            ])

        _print_table(headers, rows, [22, 14, 14, 14, 14, 12, 12])

        if data.get("revenue_growth") is not None:
            print(f"\n  YoY Revenue Growth: {_fmt_pct(data['revenue_growth'])}")
        print()


def _get_fmp_key() -> str | None:
    from src.common.config import get_settings
    key = os.environ.get("FMP_API_KEY") or get_settings().fmp_api_key
    if not key:
        print("\n  ERROR: FMP_API_KEY not set.")
        print("  Get a free key at: https://site.financialmodelingprep.com/developer")
        print("  Then add to .env:  FMP_API_KEY=your_key\n")
        return None
    return key


def _print_fmp_profile(data: dict, ticker: str) -> None:
    print(f"  {data.get('company_name', ticker)}  ({ticker})")
    print(f"  {'-' * 60}")
    print(f"  Sector:        {data.get('sector', '—')}")
    print(f"  Industry:      {data.get('industry', '—')}")
    print(f"  Country:       {data.get('country', '—')}")
    emp = data.get("employee_count")
    print(f"  Employees:     {int(emp):,}" if emp else "  Employees:     —")
    print(f"  Period:        {data.get('period', '—')} (FY {data.get('fiscal_year', '—')})")
    print()
    print(f"  Revenue:       {_fmt_dollar(data.get('revenue'))}")
    print(f"  Gross Profit:  {_fmt_dollar(data.get('gross_profit'))}  ({_fmt_pct(data.get('gross_margin'))} margin)")
    print(f"  EBITDA:        {_fmt_dollar(data.get('ebitda'))}  ({_fmt_pct(data.get('ebitda_margin'))} margin)")
    print(f"  Op Income:     {_fmt_dollar(data.get('operating_income'))}  ({_fmt_pct(data.get('operating_margin'))} margin)")
    print(f"  Net Income:    {_fmt_dollar(data.get('net_income'))}  ({_fmt_pct(data.get('net_margin'))} margin)")
    print()
    print(f"  Market Cap:    {_fmt_dollar(data.get('market_cap'))}")
    print(f"  EV:            {_fmt_dollar(data.get('enterprise_value'))}")
    print(f"  EV/EBITDA:     {_fmt_mult(data.get('ev_ebitda'))}")
    print(f"  P/E:           {_fmt_mult(data.get('pe_ratio'))}")
    print(f"  ROE:           {_fmt_pct(data.get('roe'))}")
    print(f"  D/E:           {_fmt_mult(data.get('debt_to_equity'))}")
    print()


def cmd_fmp_financials(tickers: list[str]) -> None:
    from src.ingestion.connectors.fmp import FMPConnector

    api_key = _get_fmp_key()
    if not api_key:
        return

    connector = FMPConnector(api_key)

    async def _fetch_all() -> list[tuple[str, dict]]:
        results = []
        for ticker in tickers:
            data = await connector.fetch_financials(ticker)
            results.append((ticker, data))
        await connector.close()
        return results

    print(f"\n  FMP — Company Financials ({len(tickers)} tickers)")
    print(f"  {'=' * 70}\n")

    for ticker, data in asyncio.run(_fetch_all()):
        _print_fmp_profile(data, ticker)


def cmd_compare(tickers: list[str]) -> None:
    """Side-by-side comparison from yfinance (fastest for multi-ticker)."""
    from src.ingestion.connectors.yfinance_connector import YFinanceConnector
    connector = YFinanceConnector()

    print(f"\n  COMPANY COMPARISON — {len(tickers)} companies")
    print(f"  {'=' * 70}\n")

    profiles = connector.get_bulk_profiles(tickers)

    headers = ["Metric"] + [p.get("ticker", "?") for p in profiles]
    col_w = [20] + [16] * len(profiles)

    rows = []

    def _row(label: str, key: str, fmt_fn=None) -> list[str]:
        vals = [label]
        for p in profiles:
            v = p.get(key)
            if fmt_fn:
                vals.append(fmt_fn(v))
            elif v is not None:
                vals.append(str(v))
            else:
                vals.append("—")
        return vals

    rows.append(_row("Company", "company_name"))
    rows.append(_row("Sector", "sector"))
    rows.append(_row("Industry", "industry"))
    rows.append(_row("Employees", "employee_count", lambda v: f"{v:,}" if v else "—"))
    rows.append([""] + ["─" * 14] * len(profiles))
    rows.append(_row("Revenue", "revenue", _fmt_dollar))
    rows.append(_row("EBITDA", "ebitda", _fmt_dollar))
    rows.append(_row("EBITDA Margin", "ebitda_margin", _fmt_pct))
    rows.append(_row("Rev Growth", "revenue_growth", _fmt_pct))
    rows.append(_row("Gross Margin", "gross_margin", _fmt_pct))
    rows.append(_row("Op Margin", "operating_margin", _fmt_pct))
    rows.append([""] + ["─" * 14] * len(profiles))
    rows.append(_row("Market Cap", "market_cap", _fmt_dollar))
    rows.append(_row("EV", "enterprise_value", _fmt_dollar))
    rows.append(_row("EV/EBITDA", "ev_ebitda", _fmt_mult))
    rows.append(_row("EV/Revenue", "ev_revenue", _fmt_mult))
    rows.append(_row("P/E", "trailing_pe", _fmt_mult))
    rows.append(_row("Beta", "beta", lambda v: f"{v:.2f}" if v else "—"))

    _print_table(headers, rows, col_w)
    print()


def cmd_sectors() -> None:
    """Show sector-level multiples from yfinance using representative tickers."""
    from src.ingestion.connectors.yfinance_connector import YFinanceConnector
    connector = YFinanceConnector()

    sector_tickers = {
        "Software": ["CRM", "NOW", "ADBE", "WDAY", "SNOW"],
        "Healthcare IT": ["VEEV", "HIMS", "DOCS", "CERT"],
        "Business Services": ["ADP", "PAYX", "WEX", "PAYC"],
        "Industrials": ["HON", "GE", "MMM", "CAT"],
        "Consumer": ["NKE", "SBUX", "MCD", "PG"],
        "Financials": ["JPM", "GS", "MS", "BLK"],
    }

    print(f"\n  SECTOR MULTIPLES (from yfinance)")
    print(f"  {'=' * 70}\n")

    headers = ["Sector", "Tickers", "Median EV/EBITDA", "Median EV/Rev", "Median Margin"]
    rows = []

    for sector, tickers in sector_tickers.items():
        ev_ebitdas = []
        ev_revs = []
        margins = []

        for t in tickers:
            try:
                p = connector.get_company_profile(t)
                if p.get("ev_ebitda") and p["ev_ebitda"] > 0:
                    ev_ebitdas.append(p["ev_ebitda"])
                if p.get("ev_revenue") and p["ev_revenue"] > 0:
                    ev_revs.append(p["ev_revenue"])
                if p.get("ebitda_margin") and p["ebitda_margin"] > 0:
                    margins.append(p["ebitda_margin"])
            except Exception:
                continue

        import numpy as np
        med_ebitda = f"{np.median(ev_ebitdas):.1f}x" if ev_ebitdas else "—"
        med_rev = f"{np.median(ev_revs):.1f}x" if ev_revs else "—"
        med_margin = f"{np.median(margins):.1%}" if margins else "—"

        rows.append([sector, ", ".join(tickers), med_ebitda, med_rev, med_margin])
        print(f"  {sector}: fetched {len(ev_ebitdas)}/{len(tickers)} tickers")

    print()
    _print_table(headers, rows, [18, 30, 18, 16, 16])
    print()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Explore data from connected sources",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m src.data_viewer yfinance AAPL MSFT CRM
  python -m src.data_viewer edgar AAPL CRM
  python -m src.data_viewer fmp AAPL CRM             (requires FMP_API_KEY in .env)
  python -m src.data_viewer compare AAPL CRM SNOW PLTR
  python -m src.data_viewer sectors
        """,
    )
    sub = parser.add_subparsers(dest="command")

    yf_parser = sub.add_parser("yfinance", help="Fetch company profiles from Yahoo Finance")
    yf_parser.add_argument("tickers", nargs="+", help="Stock ticker symbols")

    edgar_parser = sub.add_parser("edgar", help="Fetch 10-K financials from SEC EDGAR")
    edgar_parser.add_argument("tickers", nargs="+", help="Stock ticker symbols")

    fmp_parser = sub.add_parser("fmp", help="Fetch financials from Financial Modeling Prep")
    fmp_parser.add_argument("tickers", nargs="+", help="Stock ticker symbols")

    cmp_parser = sub.add_parser("compare", help="Side-by-side company comparison")
    cmp_parser.add_argument("tickers", nargs="+", help="Stock ticker symbols (2-6 recommended)")

    sub.add_parser("sectors", help="Sector-level multiples from representative tickers")

    args = parser.parse_args()

    if args.command == "yfinance":
        cmd_yfinance(args.tickers)
    elif args.command == "edgar":
        cmd_edgar(args.tickers)
    elif args.command == "fmp":
        cmd_fmp_financials(args.tickers)
    elif args.command == "compare":
        cmd_compare(args.tickers)
    elif args.command == "sectors":
        cmd_sectors()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
