#!/usr/bin/env python3
"""Bloomberg-style deal sourcing terminal.

Usage:
    python -m src.terminal --sector "healthcare IT" --count 5
    python -m src.terminal --thesis theses/healthcare-it-rollup.yaml
    python -m src.terminal --sector "business services" --geography US --count 10
"""

import argparse
import sys

from src.terminal.app import BloombergTerminal


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Bloomberg-style deal sourcing terminal",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m src.terminal --sector "healthcare IT" --count 5
  python -m src.terminal --thesis theses/healthcare-it-rollup.yaml
  python -m src.terminal --sector "business services" --geography US --count 10
        """,
    )
    parser.add_argument("--sector", type=str, help="Target sector (e.g. 'healthcare IT')")
    parser.add_argument("--geography", type=str, default="US", help="Target geography (default: US)")
    parser.add_argument("--count", type=int, default=10, help="Number of companies to research (default: 10)")
    parser.add_argument("--thesis", type=str, help="Path to thesis YAML file")
    parser.add_argument("--revenue-min", type=float, default=5_000_000, help="Min revenue filter (default: 5M)")
    parser.add_argument("--revenue-max", type=float, default=100_000_000, help="Max revenue filter (default: 100M)")
    parser.add_argument("--model", type=str, default="sonnet", help="Claude model to use (default: sonnet)")

    args = parser.parse_args()

    terminal = BloombergTerminal(
        sector=args.sector,
        geography=args.geography,
        count=args.count,
        thesis_path=args.thesis,
        revenue_min=args.revenue_min,
        revenue_max=args.revenue_max,
        model=args.model,
    )
    terminal.run()


if __name__ == "__main__":
    main()
