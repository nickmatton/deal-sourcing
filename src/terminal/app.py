"""Bloomberg-style terminal application for the deal sourcing pipeline.

Full-screen curses UI with live pipeline progress, deal rankings table,
deal detail drill-down, transaction comps, and keyboard navigation.
"""

from __future__ import annotations

import asyncio
import curses
import locale
import os
import queue
import sys
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np

from src.common.schemas.ingestion import CompanyNormalized, TransactionRecord
from src.common.schemas.underwriting import LBOAssumptions, UnderwritingResult
from src.common.schemas.valuation import ShadowValuation
from src.entity_resolution.engine import EntityResolutionEngine
from src.ingestion.connectors.claude_research import ClaudeResearchConnector
from src.ingestion.normalizers.company import normalize_company
from src.thesis_matching.hard_filter import filter_universe
from src.thesis_matching.thesis_schema import InvestmentThesis, ThesisStore
from src.underwriting.monte_carlo import MonteCarloSimulator
from src.valuation.engine import ShadowValuationEngine

# ── Color Pairs ──────────────────────────────────────────────────────────────

C_TITLE = 1
C_GREEN = 2
C_RED = 3
C_AMBER = 4
C_CYAN = 5
C_SELECT = 6
C_STATUS = 7
C_DIM = 8

# ── Pipeline Stage Definitions ───────────────────────────────────────────────

STAGES = [
    ("ingestion", "INGEST"),
    ("entity_resolution", "RESOLVE"),
    ("thesis_matching", "THESIS"),
    ("valuation", "VALUE"),
    ("underwriting", "UNDERWRITE"),
]

SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

SORT_COLS = ["company", "revenue", "ebitda", "ev", "irr", "moic", "decision"]


@dataclass
class PipelineState:
    stage: str = "idle"
    stages_completed: set[str] = field(default_factory=set)
    stages_errored: set[str] = field(default_factory=set)
    raw_count: int = 0
    resolved_count: int = 0
    filtered_count: int = 0
    rejected_count: int = 0
    txn_count: int = 0
    val_count: int = 0
    uw_count: int = 0
    running: bool = False
    complete: bool = False
    error: str | None = None


class BloombergTerminal:
    """Full-screen Bloomberg-style terminal for the deal sourcing pipeline.

    Provides live pipeline progress, interactive deal rankings, drill-down
    detail views, transaction comps, and keyboard-driven navigation.
    """

    def __init__(
        self,
        sector: str | None = None,
        geography: str = "US",
        count: int = 10,
        thesis_path: str | None = None,
        revenue_min: float = 5_000_000,
        revenue_max: float = 100_000_000,
        model: str = "sonnet",
    ) -> None:
        self.sector = sector
        self.geography = geography
        self.count = count
        self.thesis_path = thesis_path
        self.revenue_min = revenue_min
        self.revenue_max = revenue_max
        self.model = model

        # Pipeline data
        self.thesis: InvestmentThesis | None = None
        self.companies: list[CompanyNormalized] = []
        self.transactions: list[TransactionRecord] = []
        self.valuations: list[tuple[CompanyNormalized, ShadowValuation]] = []
        self.results: list[tuple[CompanyNormalized, ShadowValuation, UnderwritingResult]] = []

        # Pipeline state
        self.ps = PipelineState()
        self.events: queue.Queue[tuple] = queue.Queue()

        # UI state
        self.view = "loading"
        self.sel = 0
        self.scroll = 0
        self.sort_idx = 4  # IRR by default
        self.sort_desc = True
        self.detail_scroll = 0
        self.tick = 0
        self.scr: curses.window | None = None

    # ═══════════════════════════════════════════════════════════════════════
    # Lifecycle
    # ═══════════════════════════════════════════════════════════════════════

    def run(self) -> None:
        locale.setlocale(locale.LC_ALL, "")
        saved_stderr = sys.stderr
        sys.stderr = open(os.devnull, "w")
        try:
            curses.wrapper(self._main)
        finally:
            sys.stderr.close()
            sys.stderr = saved_stderr

    def _main(self, scr: curses.window) -> None:
        self.scr = scr
        self._init_colors()
        curses.curs_set(0)
        scr.timeout(100)
        self._launch_pipeline()

        while True:
            self.tick += 1
            self._drain()
            self._draw()
            ch = scr.getch()
            if ch == curses.KEY_RESIZE:
                scr.clear()
                continue
            if self._input(ch):
                break

    def _init_colors(self) -> None:
        curses.start_color()
        curses.use_default_colors()
        curses.init_pair(C_TITLE, curses.COLOR_WHITE, curses.COLOR_BLUE)
        curses.init_pair(C_GREEN, curses.COLOR_GREEN, -1)
        curses.init_pair(C_RED, curses.COLOR_RED, -1)
        curses.init_pair(C_AMBER, curses.COLOR_YELLOW, -1)
        curses.init_pair(C_CYAN, curses.COLOR_CYAN, -1)
        curses.init_pair(C_SELECT, curses.COLOR_BLACK, curses.COLOR_GREEN)
        curses.init_pair(C_STATUS, curses.COLOR_BLACK, curses.COLOR_CYAN)
        curses.init_pair(C_DIM, curses.COLOR_WHITE, -1)

    # ═══════════════════════════════════════════════════════════════════════
    # Drawing Helpers
    # ═══════════════════════════════════════════════════════════════════════

    def _w(self, y: int, x: int, s: str, a: int = 0) -> None:
        """Write string at position, clipping to screen bounds."""
        h, w = self.scr.getmaxyx()
        if y < 0 or y >= h or x >= w:
            return
        try:
            self.scr.addnstr(y, x, s, w - x - 1, a)
        except curses.error:
            pass

    def _wl(self, y: int, s: str, a: int = 0) -> None:
        """Write full-width line padded with spaces."""
        _, w = self.scr.getmaxyx()
        try:
            self.scr.addnstr(y, 0, s.ljust(w), w - 1, a)
        except curses.error:
            pass

    def _hl(self, y: int, ch: str = "─", a: int = 0) -> None:
        """Draw horizontal line."""
        _, w = self.scr.getmaxyx()
        self._w(y, 0, ch * (w - 1), a)

    # ═══════════════════════════════════════════════════════════════════════
    # Event Queue Processing
    # ═══════════════════════════════════════════════════════════════════════

    def _drain(self) -> None:
        while True:
            try:
                ev = self.events.get_nowait()
            except queue.Empty:
                break
            self._on_event(ev)

    def _on_event(self, ev: tuple) -> None:
        k = ev[0]
        if k == "stage_start":
            self.ps.stage = ev[1]
        elif k == "stage_done":
            self.ps.stages_completed.add(ev[1])
        elif k == "stage_error":
            self.ps.stages_errored.add(ev[1])
        elif k == "thesis":
            self.thesis = ev[1]
        elif k == "raw":
            self.ps.raw_count = ev[1]
        elif k == "txn":
            self.transactions = ev[1]
            self.ps.txn_count = len(ev[1])
        elif k == "resolved":
            self.companies = ev[1]
            self.ps.resolved_count = len(ev[1])
        elif k == "filtered":
            self.companies = ev[1]
            self.ps.filtered_count = len(ev[1])
            self.ps.rejected_count = ev[2]
        elif k == "val":
            self.valuations.append((ev[1], ev[2]))
            self.ps.val_count = len(self.valuations)
        elif k == "uw":
            self.results.append((ev[1], ev[2], ev[3]))
            self.ps.uw_count = len(self.results)
        elif k == "done":
            self.ps.complete = True
            self.ps.running = False
            self._do_sort()
        elif k == "err":
            self.ps.error = ev[1]

    # ═══════════════════════════════════════════════════════════════════════
    # Pipeline Execution (background thread)
    # ═══════════════════════════════════════════════════════════════════════

    def _launch_pipeline(self) -> None:
        self.ps = PipelineState(running=True)
        self.companies.clear()
        self.transactions.clear()
        self.valuations.clear()
        self.results.clear()
        self.thesis = None
        self.view = "loading"
        self.sel = 0
        self.scroll = 0
        threading.Thread(target=self._pipeline_thread, daemon=True).start()

    def _pipeline_thread(self) -> None:
        try:
            asyncio.run(self._run_pipeline())
        except Exception as e:
            self.events.put(("err", str(e)))
            self.events.put(("done",))

    async def _run_pipeline(self) -> None:
        q = self.events
        sector = self.sector
        rmin, rmax = self.revenue_min, self.revenue_max

        # ── Load Thesis ──────────────────────────────────────────────────
        thesis: InvestmentThesis | None = None
        if self.thesis_path:
            try:
                store = ThesisStore(Path(self.thesis_path).parent)
                thesis = store.get(Path(self.thesis_path).stem)
                if not thesis:
                    all_t = store.all()
                    thesis = all_t[0] if all_t else None
                if thesis:
                    q.put(("thesis", thesis))
                    if not sector:
                        sector = thesis.sector[0] if thesis.sector else None
                    rmin, rmax = thesis.revenue_range
            except Exception as e:
                q.put(("err", f"Thesis load: {e}"))

        # ── Stage: Ingestion ─────────────────────────────────────────────
        q.put(("stage_start", "ingestion"))
        try:
            connector = ClaudeResearchConnector(model=self.model)
            raw = await connector.fetch_companies(
                sector=sector,
                geography=self.geography,
                count=self.count,
                revenue_range=(rmin, rmax),
            )
            q.put(("raw", len(raw)))

            txns = await connector.fetch_transactions(
                sector=sector,
                geography=self.geography,
                count=max(self.count, 5),
            )
            q.put(("txn", txns))
            q.put(("stage_done", "ingestion"))
        except Exception as e:
            q.put(("stage_error", "ingestion"))
            q.put(("err", f"Ingestion: {e}"))
            q.put(("done",))
            return

        # ── Stage: Entity Resolution ─────────────────────────────────────
        q.put(("stage_start", "entity_resolution"))
        try:
            er = EntityResolutionEngine()
            normalized = [normalize_company(r, er.resolve(r)) for r in raw]
            q.put(("resolved", normalized))
            q.put(("stage_done", "entity_resolution"))
        except Exception as e:
            q.put(("stage_error", "entity_resolution"))
            q.put(("err", f"Entity resolution: {e}"))
            q.put(("done",))
            return

        # ── Stage: Thesis Matching ───────────────────────────────────────
        targets = normalized
        if thesis:
            q.put(("stage_start", "thesis_matching"))
            try:
                passing, rejected = filter_universe(normalized, thesis)
                q.put(("filtered", passing, len(rejected)))
                targets = passing
                q.put(("stage_done", "thesis_matching"))
            except Exception as e:
                q.put(("stage_error", "thesis_matching"))
                q.put(("err", f"Thesis matching: {e}"))
                q.put(("filtered", normalized, 0))
                targets = normalized
        else:
            q.put(("stage_start", "thesis_matching"))
            q.put(("filtered", targets, 0))
            q.put(("stage_done", "thesis_matching"))

        if not targets:
            q.put(("err", "No companies passed filters"))
            q.put(("done",))
            return

        # ── Stage: Valuation ─────────────────────────────────────────────
        q.put(("stage_start", "valuation"))
        val_engine = ShadowValuationEngine(illiquidity_discount=0.20)
        val_pairs: list[tuple[CompanyNormalized, ShadowValuation]] = []

        for company in targets:
            try:
                known_ebitda = company.estimated_ebitda_usd
                known_revenue = company.estimated_revenue_usd

                if known_ebitda is not None and known_ebitda <= 0:
                    if known_revenue and known_revenue > 0:
                        known_ebitda = known_revenue * 0.15
                    else:
                        continue

                val = val_engine.value_company(
                    entity_id=company.entity_id,
                    company_name=company.name,
                    revenue_features=np.zeros(12),
                    margin_features=np.zeros(8),
                    multiple_features=np.zeros(10),
                    known_revenue=known_revenue,
                    known_ebitda=known_ebitda,
                )
                val_pairs.append((company, val))
                q.put(("val", company, val))
            except ValueError:
                continue

        q.put(("stage_done", "valuation"))

        # ── Stage: Underwriting ──────────────────────────────────────────
        q.put(("stage_start", "underwriting"))
        simulator = MonteCarloSimulator()

        for company, val in val_pairs:
            if not val.estimated_ebitda or val.estimated_ebitda <= 0:
                continue

            ebitda = val.estimated_ebitda
            mult = val.implied_ev_ebitda_multiple or 8.0

            assumptions = LBOAssumptions(
                entry_ebitda_mean=ebitda,
                entry_ebitda_std=ebitda * 0.15,
                entry_multiple_low=mult * 0.8,
                entry_multiple_mode=mult,
                entry_multiple_high=mult * 1.2,
                revenue_growth_mean=0.08,
                revenue_growth_std=0.04,
                exit_multiple_bear=mult * 0.85,
                exit_multiple_base=mult,
                exit_multiple_bull=mult * 1.15,
                num_simulations=10_000,
            )

            result = simulator.simulate(
                entity_id=company.entity_id,
                company_name=company.name,
                assumptions=assumptions,
            )
            q.put(("uw", company, val, result))

        q.put(("stage_done", "underwriting"))
        q.put(("done",))

    # ═══════════════════════════════════════════════════════════════════════
    # Sorting
    # ═══════════════════════════════════════════════════════════════════════

    def _do_sort(self) -> None:
        if not self.results:
            return

        def sort_key(item: tuple) -> float | str:
            c, v, u = item
            col = SORT_COLS[self.sort_idx]
            if col == "company":
                return c.name.lower()
            if col == "revenue":
                return v.estimated_revenue or 0
            if col == "ebitda":
                return v.estimated_ebitda or 0
            if col == "ev":
                return v.ev_point_estimate
            if col == "irr":
                return u.irr_distribution.p50
            if col == "moic":
                return u.moic_distribution.p50
            if col == "decision":
                return {"priority": 0, "pursue": 1, "auto_reject": 2}.get(
                    u.screening_decision, 3
                )
            return 0

        self.results.sort(key=sort_key, reverse=self.sort_desc)

    # ═══════════════════════════════════════════════════════════════════════
    # Input Handling
    # ═══════════════════════════════════════════════════════════════════════

    def _input(self, ch: int) -> bool:
        """Handle key input. Returns True to quit."""
        if ch in (ord("q"), ord("Q")):
            return True

        if self.view == "loading":
            if self.ps.complete and self.results and ch != -1:
                self.view = "dashboard"
            return False

        if self.view == "dashboard":
            self._input_dashboard(ch)
        elif self.view == "detail":
            self._input_detail(ch)
        elif self.view == "comps":
            if ch == 27 or ch in (ord("t"), ord("T")):
                self.view = "dashboard"
        elif self.view == "help":
            if ch == 27 or ch in (ord("h"), ord("H")):
                self.view = "dashboard"

        return False

    def _input_dashboard(self, ch: int) -> None:
        n = len(self.results)
        if ch == curses.KEY_UP and self.sel > 0:
            self.sel -= 1
        elif ch == curses.KEY_DOWN and self.sel < n - 1:
            self.sel += 1
        elif ch == curses.KEY_HOME:
            self.sel = 0
        elif ch == curses.KEY_END:
            self.sel = max(0, n - 1)
        elif ch in (10, 13, curses.KEY_ENTER) and n:
            self.view = "detail"
            self.detail_scroll = 0
        elif ch in (ord("s"), ord("S")):
            self.sort_idx = (self.sort_idx + 1) % len(SORT_COLS)
            self._do_sort()
        elif ch in (ord("d"), ord("D")):
            self.sort_desc = not self.sort_desc
            self._do_sort()
        elif ch in (ord("t"), ord("T")):
            self.view = "comps"
        elif ch in (ord("h"), ord("H")):
            self.view = "help"
        elif ch in (ord("r"), ord("R")) and not self.ps.running:
            self._launch_pipeline()

    def _input_detail(self, ch: int) -> None:
        n = len(self.results)
        if ch == 27:
            self.view = "dashboard"
        elif ch == curses.KEY_LEFT and self.sel > 0:
            self.sel -= 1
            self.detail_scroll = 0
        elif ch == curses.KEY_RIGHT and self.sel < n - 1:
            self.sel += 1
            self.detail_scroll = 0
        elif ch == curses.KEY_UP:
            self.detail_scroll = max(0, self.detail_scroll - 1)
        elif ch == curses.KEY_DOWN:
            self.detail_scroll += 1
        elif ch in (ord("t"), ord("T")):
            self.view = "comps"

    # ═══════════════════════════════════════════════════════════════════════
    # Drawing
    # ═══════════════════════════════════════════════════════════════════════

    def _draw(self) -> None:
        self.scr.erase()
        h, w = self.scr.getmaxyx()

        if h < 10 or w < 60:
            self._w(h // 2, 1, "Terminal too small (need 60x10+)", curses.color_pair(C_RED))
            self.scr.refresh()
            return

        self._draw_header()

        if self.view == "loading":
            self._draw_loading()
        elif self.view == "dashboard":
            self._draw_dashboard()
        elif self.view == "detail":
            self._draw_detail()
        elif self.view == "comps":
            self._draw_comps()
        elif self.view == "help":
            self._draw_help()

        self._draw_footer()
        self.scr.refresh()

    def _draw_header(self) -> None:
        _, w = self.scr.getmaxyx()
        now = datetime.now().strftime("%d-%b-%Y  %H:%M:%S")
        title = " DEAL SOURCING TERMINAL"
        pad = max(1, w - len(title) - len(now) - 2)
        self._wl(0, title + " " * pad + now + " ", curses.color_pair(C_TITLE) | curses.A_BOLD)

    def _draw_footer(self) -> None:
        h, _ = self.scr.getmaxyx()
        footers = {
            "loading": " Q:QUIT",
            "dashboard": " \u2191\u2193:NAV  ENTER:DETAIL  S:SORT  D:DIR  T:COMPS  R:RERUN  H:HELP  Q:QUIT",
            "detail": " ESC:BACK  \u2190\u2192:PREV/NEXT  \u2191\u2193:SCROLL  T:COMPS  Q:QUIT",
            "comps": " ESC:BACK  Q:QUIT",
            "help": " ESC:BACK  Q:QUIT",
        }
        self._wl(
            h - 1,
            footers.get(self.view, " Q:QUIT"),
            curses.color_pair(C_STATUS) | curses.A_BOLD,
        )

    # ── Loading View ─────────────────────────────────────────────────────

    def _draw_loading(self) -> None:
        h, w = self.scr.getmaxyx()
        y = 2
        ps = self.ps
        gc = curses.color_pair(C_GREEN)
        ga = curses.color_pair(C_AMBER)
        gr = curses.color_pair(C_RED)
        gd = curses.color_pair(C_DIM)

        self._w(y, 2, "PIPELINE STATUS", gc | curses.A_BOLD)
        y += 1
        self._hl(y, "\u2500", gd)
        y += 1

        info = f"Sector: {self.sector or 'all'}  |  Geo: {self.geography}  |  Count: {self.count}"
        if self.thesis_path:
            info += f"  |  Thesis: {Path(self.thesis_path).stem}"
        self._w(y, 2, info, gd)
        y += 2

        # Progress bar
        done = len(ps.stages_completed)
        pct = done / len(STAGES) if STAGES else 0
        bar_w = min(w - 18, 50)
        filled = int(bar_w * pct)
        bar = "\u2588" * filled + "\u2591" * (bar_w - filled)
        self._w(y, 2, f"[{bar}] {pct:.0%}", gc if pct >= 1.0 else ga)
        y += 2

        # Stage list with spinner
        spinner_ch = SPINNER[self.tick % len(SPINNER)]
        for stage_id, stage_label in STAGES:
            if stage_id in ps.stages_completed:
                icon, attr = "\u2713", gc
            elif stage_id in ps.stages_errored:
                icon, attr = "\u2717", gr
            elif ps.stage == stage_id:
                icon, attr = spinner_ch, ga | curses.A_BOLD
            else:
                icon, attr = "\u25CB", gd

            detail = ""
            if stage_id == "ingestion" and ps.raw_count:
                detail = f"{ps.raw_count} companies, {ps.txn_count} transactions"
            elif stage_id == "entity_resolution" and ps.resolved_count:
                detail = f"{ps.resolved_count} entities resolved"
            elif stage_id == "thesis_matching" and ps.filtered_count:
                detail = f"{ps.filtered_count} passed, {ps.rejected_count} rejected"
            elif stage_id == "valuation" and ps.val_count:
                detail = f"{ps.val_count} valued"
            elif stage_id == "underwriting" and ps.uw_count:
                detail = f"{ps.uw_count} underwritten"

            self._w(y, 3, icon, attr)
            self._w(y, 5, f"{stage_label:<14}", attr)
            if detail:
                self._w(y, 20, detail, gd)
            y += 1

        y += 1
        if ps.error:
            self._w(y, 2, f"ERROR: {ps.error}", gr | curses.A_BOLD)
            y += 1

        if ps.complete:
            y += 1
            if self.results:
                self._w(
                    y, 2,
                    f"Complete \u2014 {len(self.results)} deals ready. Press any key.",
                    gc | curses.A_BOLD,
                )
            else:
                self._w(y, 2, "Complete \u2014 no qualifying deals found.", ga)

    # ── Dashboard View ───────────────────────────────────────────────────

    def _draw_dashboard(self) -> None:
        h, w = self.scr.getmaxyx()
        y = 2
        ga = curses.color_pair(C_AMBER) | curses.A_BOLD
        gc = curses.color_pair(C_GREEN)
        gd = curses.color_pair(C_DIM)
        gs = curses.color_pair(C_SELECT) | curses.A_BOLD

        # Pipeline breadcrumb
        breadcrumb = ""
        for stage_id, stage_label in STAGES:
            if stage_id in self.ps.stages_completed:
                breadcrumb += f" \u2713 {stage_label}"
            else:
                breadcrumb += f" - {stage_label}"

        flow = (
            f"{self.ps.raw_count}\u2192{self.ps.resolved_count}"
            f"\u2192{self.ps.filtered_count}\u2192{self.ps.val_count}"
            f"\u2192{self.ps.uw_count}"
        )
        self._w(y, 1, breadcrumb, gc)
        self._w(y, max(1, w - len(flow) - 2), flow, curses.color_pair(C_CYAN))
        y += 1
        self._hl(y, "\u2550", gd)
        y += 1

        # Sort indicator + thesis
        sort_name = SORT_COLS[self.sort_idx].upper()
        sort_dir = "\u25BC" if self.sort_desc else "\u25B2"
        self._w(y, 1, f" Sort: {sort_name} {sort_dir}", gd)
        if self.thesis:
            self._w(y, 22, f"Thesis: {self.thesis.id}", curses.color_pair(C_CYAN))
        y += 1

        # Table header
        hdr = self._fmt_table_row(
            "#", "COMPANY", "SECTOR", "REV $M", "EBITDA", "EV $M",
            "MULT", "IRR P50", "MOIC", "DECISION",
        )
        self._wl(y, hdr, ga)
        y += 1
        self._hl(y, "\u2500", gd)
        y += 1

        # Table body
        table_height = h - y - 4
        if table_height < 1:
            table_height = 1

        # Scroll adjustment
        if self.sel < self.scroll:
            self.scroll = self.sel
        if self.sel >= self.scroll + table_height:
            self.scroll = self.sel - table_height + 1

        for i in range(self.scroll, min(self.scroll + table_height, len(self.results))):
            c, v, u = self.results[i]

            rev_s = f"{v.estimated_revenue / 1e6:.1f}" if v.estimated_revenue else "\u2014"
            eb_s = f"{v.estimated_ebitda / 1e6:.1f}" if v.estimated_ebitda else "\u2014"
            ev_s = f"{v.ev_point_estimate / 1e6:.1f}"
            ml_s = (
                f"{v.implied_ev_ebitda_multiple:.1f}x"
                if v.implied_ev_ebitda_multiple
                else "\u2014"
            )
            irr_s = f"{u.irr_distribution.p50:.1%}"
            moic_s = f"{u.moic_distribution.p50:.2f}x"
            dec_s = {
                "priority": "\u2605 PRIORITY",
                "pursue": "  PURSUE",
                "auto_reject": "\u2717 REJECT",
            }.get(u.screening_decision, u.screening_decision.upper())

            row = self._fmt_table_row(
                str(i + 1),
                c.name[:22],
                (c.industry_primary or "\u2014")[:14],
                rev_s, eb_s, ev_s, ml_s, irr_s, moic_s, dec_s,
            )

            if i == self.sel:
                attr = gs
            elif u.screening_decision == "priority":
                attr = gc
            elif u.screening_decision == "auto_reject":
                attr = curses.color_pair(C_RED)
            else:
                attr = gd

            self._wl(y, row, attr)
            y += 1

        # Summary bar
        y = h - 3
        self._hl(y, "\u2550", gd)
        y += 1

        n_pri = sum(1 for _, _, u in self.results if u.screening_decision == "priority")
        n_pur = sum(1 for _, _, u in self.results if u.screening_decision == "pursue")
        n_rej = sum(1 for _, _, u in self.results if u.screening_decision == "auto_reject")
        avg_irr = (
            float(np.mean([u.irr_distribution.p50 for _, _, u in self.results]))
            if self.results
            else 0
        )
        avg_moic = (
            float(np.mean([u.moic_distribution.p50 for _, _, u in self.results]))
            if self.results
            else 0
        )

        summary = (
            f" PRIORITY:{n_pri}  PURSUE:{n_pur}  REJECT:{n_rej}"
            f"  \u2502  AVG IRR:{avg_irr:.1%}  AVG MOIC:{avg_moic:.2f}x"
        )
        self._w(y, 0, summary, gc | curses.A_BOLD)

    @staticmethod
    def _fmt_table_row(
        num: str, co: str, sec: str, rev: str, eb: str,
        ev: str, ml: str, ir: str, mo: str, dc: str,
    ) -> str:
        return (
            f" {num:>3} {co:<22} {sec:<14} {rev:>8} {eb:>8}"
            f" {ev:>8} {ml:>6} {ir:>7} {mo:>6} {dc:>11}"
        )

    # ── Detail View ──────────────────────────────────────────────────────

    def _draw_detail(self) -> None:
        if not self.results or self.sel >= len(self.results):
            self.view = "dashboard"
            return

        h, w = self.scr.getmaxyx()
        c, v, u = self.results[self.sel]
        ga = curses.color_pair(C_AMBER) | curses.A_BOLD
        gc = curses.color_pair(C_GREEN) | curses.A_BOLD
        gr = curses.color_pair(C_RED) | curses.A_BOLD
        gd = curses.color_pair(C_DIM)
        gy = curses.color_pair(C_CYAN)
        mid = w // 2

        y = 2

        # Company header with decision badge
        dec = u.screening_decision
        badges = {
            "priority": ("\u2605\u2605\u2605 PRIORITY \u2605\u2605\u2605", gc),
            "pursue": ("\u2192 PURSUE", ga),
            "auto_reject": ("\u2717 REJECT", gr),
        }
        badge_text, badge_attr = badges.get(dec, (dec.upper(), gd))
        self._w(y, 1, f" {c.name.upper()}", gc)
        self._w(y, max(1, w - len(badge_text) - 2), badge_text, badge_attr)
        y += 1
        self._hl(y, "\u2550", gd)
        y += 1

        # Build content lines as (type, left_text, right_text)
        lines: list[tuple[str, ...]] = []

        # Company Overview + Financials
        lines.append(("hdr", "COMPANY OVERVIEW", "FINANCIALS"))
        lines.append((
            "kv",
            f"Industry:    {c.industry_primary or 'N/A'}",
            f"Revenue:     ${v.estimated_revenue:,.0f}" if v.estimated_revenue else "Revenue:     N/A",
        ))

        loc = f"{c.hq_city or '?'}, {c.hq_state or '?'}, {c.hq_country}"
        if v.estimated_ebitda and v.estimated_revenue and v.estimated_revenue > 0:
            margin = v.estimated_ebitda / v.estimated_revenue
            ebitda_str = f"EBITDA:      ${v.estimated_ebitda:,.0f} ({margin:.0%} margin)"
        elif v.estimated_ebitda:
            ebitda_str = f"EBITDA:      ${v.estimated_ebitda:,.0f}"
        else:
            ebitda_str = "EBITDA:      N/A"
        lines.append(("kv", f"Location:    {loc}", ebitda_str))
        lines.append(("kv", f"Founded:     {c.founded_year or 'N/A'}", ""))
        lines.append(("kv", f"Employees:   {c.employee_count or 'N/A'}", ""))
        lines.append(("kv", f"Ownership:   {c.ownership_type.value}", ""))

        lines.append(("sep",))

        # Shadow Valuation + Underwriting
        lines.append(("hdr", "SHADOW VALUATION", "UNDERWRITING"))
        lines.append((
            "kv",
            f"EV:          ${v.ev_point_estimate:,.0f}",
            "IRR Distribution:",
        ))
        lines.append((
            "kv",
            f"Range:       ${v.ev_range_80ci[0]:,.0f} \u2014 ${v.ev_range_80ci[1]:,.0f}",
            f"  P10: {u.irr_distribution.p10:>7.1%}   P25: {u.irr_distribution.p25:>7.1%}",
        ))
        mult_str = (
            f"{v.implied_ev_ebitda_multiple:.1f}x EBITDA"
            if v.implied_ev_ebitda_multiple
            else "N/A"
        )
        lines.append((
            "kv",
            f"Multiple:    {mult_str}",
            f"  P50: {u.irr_distribution.p50:>7.1%}   P75: {u.irr_distribution.p75:>7.1%}",
        ))
        lines.append((
            "kv",
            f"Discount:    {v.illiquidity_discount_applied:.0%} illiquidity",
            f"  P90: {u.irr_distribution.p90:>7.1%}   Mean: {u.irr_distribution.mean:>7.1%}",
        ))
        lines.append(("kv", f"Grade:       {v.confidence_grade.value}", ""))
        lines.append(("kv", "", f"MOIC P50:    {u.moic_distribution.p50:.2f}x"))
        lines.append(("kv", "", f"P(IRR>20%):  {u.p_irr_gt_20:.0%}"))
        lines.append(("kv", "", f"P(IRR>25%):  {u.p_irr_gt_25:.0%}"))
        lines.append(("kv", "", f"Downside:    {u.downside_irr:.1%} IRR"))

        lines.append(("sep",))

        # IRR Range Visual + Bid Analysis
        lines.append(("hdr", "IRR RANGE", "BID ANALYSIS"))

        irr_vals = [
            u.irr_distribution.p10, u.irr_distribution.p25,
            u.irr_distribution.p50, u.irr_distribution.p75,
            u.irr_distribution.p90,
        ]
        bar_w = max(min(mid - 6, 40), 12)
        lo, hi = irr_vals[0], irr_vals[4]
        span = hi - lo if hi > lo else 0.01
        positions = [int((iv - lo) / span * (bar_w - 1)) for iv in irr_vals]

        chars = list("\u2591" * bar_w)
        for j in range(max(0, positions[0]), min(bar_w, positions[4] + 1)):
            chars[j] = "\u2592"
        for j in range(max(0, positions[1]), min(bar_w, positions[3] + 1)):
            chars[j] = "\u2593"
        if 0 <= positions[2] < bar_w:
            chars[positions[2]] = "\u2588"

        lines.append(("kv", " " + "".join(chars), "Bid Range:"))
        lines.append((
            "kv",
            f" P10:{irr_vals[0]:.0%}{'':>{max(1, positions[2] - 8)}}P50:{irr_vals[2]:.0%}{'':>{max(1, bar_w - positions[2] - 9)}}P90:{irr_vals[4]:.0%}",
            f"  ${u.recommended_bid_range[0]:,.0f} \u2014 ${u.recommended_bid_range[1]:,.0f}",
        ))
        lines.append((
            "kv", "",
            f"Break-even:  {u.break_even_multiple:.1f}x exit multiple",
        ))
        if u.walkaway_price:
            lines.append(("kv", "", f"Walk-away:   ${u.walkaway_price:,.0f}"))

        lines.append(("sep",))

        # Sensitivity Analysis
        if u.key_sensitivities:
            lines.append(("hdr", "SENSITIVITY ANALYSIS", ""))
            for s in u.key_sensitivities:
                bar_len = min(int(s.impact * 200), 30)
                sens_bar = "\u2588" * bar_len + "\u2591" * (30 - bar_len)
                lines.append((
                    "kv",
                    f" {s.parameter:<18} {sens_bar}  \u00B1{s.impact:.1%} IRR",
                    "",
                ))

        # Render with scroll offset
        for idx, ln in enumerate(lines):
            if idx < self.detail_scroll:
                continue
            if y >= h - 2:
                break

            if ln[0] == "hdr":
                self._w(y, 1, f" {ln[1]}", ga)
                if len(ln) > 2 and ln[2]:
                    self._w(y, mid, f" {ln[2]}", ga)
            elif ln[0] == "kv":
                self._w(y, 1, f" {ln[1]}", gd)
                if len(ln) > 2 and ln[2]:
                    self._w(y, mid, f" {ln[2]}", gd)
            elif ln[0] == "sep":
                self._hl(y, "\u2500", gd)
            y += 1

        # Position indicator
        nav = f" [{self.sel + 1}/{len(self.results)}]"
        self._w(h - 3, max(0, w - len(nav) - 1), nav, gy)

    # ── Comps View ───────────────────────────────────────────────────────

    def _draw_comps(self) -> None:
        h, w = self.scr.getmaxyx()
        y = 2
        ga = curses.color_pair(C_AMBER) | curses.A_BOLD
        gc = curses.color_pair(C_GREEN)
        gd = curses.color_pair(C_DIM)

        self._w(y, 1, " COMPARABLE TRANSACTIONS", gc | curses.A_BOLD)
        y += 1
        self._hl(y, "\u2500", gd)
        y += 1

        if not self.transactions:
            self._w(y, 2, "No transaction data available.", gd)
            return

        # Table header
        hdr = (
            f" {'TARGET':<26} {'BUYER':<20}"
            f" {'EV':>14} {'MULT':>7} {'DATE':>12} {'TYPE':>8}"
        )
        self._wl(y, hdr, ga)
        y += 1
        self._hl(y, "\u2500", gd)
        y += 1

        for tx in self.transactions:
            if y >= h - 4:
                break
            ev_s = f"${tx.enterprise_value:,.0f}" if tx.enterprise_value else "undisclosed"
            ml_s = f"{tx.ev_ebitda_multiple:.1f}x" if tx.ev_ebitda_multiple else "\u2014"
            row = (
                f" {tx.target_name[:26]:<26} {(tx.buyer_name or '\u2014')[:20]:<20}"
                f" {ev_s:>14} {ml_s:>7} {tx.deal_date:>12}"
                f" {(tx.deal_type or '\u2014')[:8]:>8}"
            )
            self._wl(y, row, gd)
            y += 1

        # Summary stats
        y += 1
        multiples = [t.ev_ebitda_multiple for t in self.transactions if t.ev_ebitda_multiple]
        if multiples:
            avg_m = float(np.mean(multiples))
            med_m = float(np.median(multiples))
            self._w(
                y, 1,
                f" AVG: {avg_m:.1f}x  MEDIAN: {med_m:.1f}x"
                f"  RANGE: {min(multiples):.1f}x \u2014 {max(multiples):.1f}x"
                f"  N={len(multiples)}",
                gc | curses.A_BOLD,
            )

    # ── Help View ────────────────────────────────────────────────────────

    def _draw_help(self) -> None:
        h, _ = self.scr.getmaxyx()
        y = 2
        ga = curses.color_pair(C_AMBER) | curses.A_BOLD
        gc = curses.color_pair(C_CYAN) | curses.A_BOLD
        gd = curses.color_pair(C_DIM)

        self._w(y, 1, " KEYBOARD REFERENCE", curses.color_pair(C_GREEN) | curses.A_BOLD)
        y += 1
        self._hl(y, "\u2500", gd)
        y += 2

        sections = [
            ("GLOBAL", [("Q", "Quit terminal")]),
            ("DASHBOARD", [
                ("\u2191 / \u2193", "Navigate deals"),
                ("ENTER", "Open deal detail"),
                ("S", "Cycle sort column"),
                ("D", "Toggle sort direction"),
                ("T", "Transaction comps"),
                ("R", "Re-run pipeline"),
                ("H", "This help screen"),
                ("HOME/END", "Jump to first/last"),
            ]),
            ("DETAIL", [
                ("ESC", "Back to dashboard"),
                ("\u2190 / \u2192", "Previous / next deal"),
                ("\u2191 / \u2193", "Scroll content"),
                ("T", "Transaction comps"),
            ]),
            ("COMPS", [("ESC / T", "Back to dashboard")]),
        ]

        for title, keys in sections:
            if y >= h - 2:
                break
            self._w(y, 2, title, ga)
            y += 1
            for key, desc in keys:
                if y >= h - 2:
                    break
                self._w(y, 4, f"{key:>10}", gc)
                self._w(y, 16, desc, gd)
                y += 1
            y += 1
