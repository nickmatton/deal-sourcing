"""Data connector that uses Claude Code CLI to research and generate structured company data.

Uses your existing Claude subscription via `claude -p` instead of paid API keys.
"""

import json
import subprocess
from datetime import datetime, timezone

import structlog

from src.common.schemas.ingestion import CompanyRaw, OwnershipType, TransactionRecord
from src.ingestion.connectors.base import BaseConnector

logger = structlog.get_logger("ingestion.claude_research")

def _extract_json_object(text: str) -> dict | None:
    """Extract the first valid top-level JSON object from text that may contain prose."""
    # Find each '{' and try to parse from there
    start = 0
    while True:
        idx = text.find("{", start)
        if idx == -1:
            return None
        # Try progressively larger slices from this { to find a valid JSON object
        depth = 0
        in_string = False
        escape = False
        for i in range(idx, len(text)):
            c = text[i]
            if escape:
                escape = False
                continue
            if c == "\\":
                escape = True
                continue
            if c == '"' and not escape:
                in_string = not in_string
                continue
            if in_string:
                continue
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    candidate = text[idx : i + 1]
                    try:
                        obj = json.loads(candidate)
                        if isinstance(obj, dict):
                            logger.debug("extracted_json", start=idx, end=i)
                            return obj
                    except json.JSONDecodeError:
                        break  # This opening { didn't work, try next one
        start = idx + 1


COMPANY_SCHEMA = {
    "type": "object",
    "properties": {
        "companies": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "domain": {"type": "string"},
                    "description": {"type": "string"},
                    "industry": {"type": "string"},
                    "naics_code": {"type": "string"},
                    "hq_city": {"type": "string"},
                    "hq_state": {"type": "string"},
                    "hq_country": {"type": "string"},
                    "founded_year": {"type": "integer"},
                    "employee_count": {"type": "integer"},
                    "estimated_revenue_usd": {"type": "number"},
                    "estimated_ebitda_usd": {"type": "number"},
                    "ownership_type": {
                        "type": "string",
                        "enum": ["founder", "family", "pe_backed", "public", "vc_backed", "unknown"],
                    },
                    "funding_total_usd": {"type": "number"},
                    "last_funding_date": {"type": "string"},
                    "last_funding_round": {"type": "string"},
                    "executives": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "title": {"type": "string"},
                            },
                            "required": ["name", "title"],
                        },
                    },
                },
                "required": ["name", "description", "industry", "hq_country"],
            },
        }
    },
    "required": ["companies"],
}

TRANSACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "transactions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "target_name": {"type": "string"},
                    "buyer_name": {"type": "string"},
                    "buyer_type": {"type": "string", "enum": ["pe", "strategic", "vc", "family_office"]},
                    "deal_type": {"type": "string", "enum": ["lbo", "growth", "add-on", "merger", "recapitalization"]},
                    "sector": {"type": "string"},
                    "enterprise_value_usd": {"type": "number"},
                    "ev_ebitda_multiple": {"type": "number"},
                    "ev_revenue_multiple": {"type": "number"},
                    "target_revenue_usd": {"type": "number"},
                    "target_ebitda_usd": {"type": "number"},
                    "target_ebitda_margin": {"type": "number"},
                    "target_revenue_growth": {"type": "number"},
                    "deal_date": {"type": "string"},
                    "geography": {"type": "string"},
                },
                "required": ["target_name", "sector", "deal_date"],
            },
        }
    },
    "required": ["transactions"],
}

OWNERSHIP_MAP = {
    "founder": OwnershipType.FOUNDER,
    "family": OwnershipType.FAMILY,
    "pe_backed": OwnershipType.PE_BACKED,
    "public": OwnershipType.PUBLIC,
    "vc_backed": OwnershipType.VC_BACKED,
    "unknown": OwnershipType.UNKNOWN,
}


def _call_claude(prompt: str, schema: dict, model: str = "sonnet", timeout: int = 600) -> dict:
    """Call Claude Code CLI with streaming output so we can log progress.

    Uses --output-format stream-json to get real-time events (tool calls,
    reasoning, partial responses) and logs them as they arrive.
    """
    full_prompt = (
        f"You MUST respond with ONLY a valid JSON object, no other text. "
        f"The JSON must match this schema:\n{json.dumps(schema, indent=2)}\n\n{prompt}"
    )
    cmd = [
        "claude",
        "-p",
        "--verbose",
        "--output-format", "stream-json",
        "--model", model,
        full_prompt,
    ]

    logger.info("claude_cli.calling", prompt_len=len(full_prompt), model=model, timeout=timeout)

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    result_data: dict | None = None
    try:
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue

            etype = event.get("type", "")
            esubtype = event.get("subtype", "")

            if etype == "system" and esubtype == "init":
                logger.info("claude_cli.session_started", session=event.get("session_id", "")[:8])

            elif etype == "assistant":
                msg = event.get("message", {})
                for block in msg.get("content", []):
                    if block.get("type") == "tool_use":
                        tool = block.get("name", "unknown")
                        tool_input = block.get("input", {})
                        # Log the tool being used — shows web searches, file reads, etc.
                        if tool in ("WebSearch", "web_search"):
                            query = tool_input.get("query", "")
                            logger.info("claude_cli.web_search", query=query[:100])
                        elif tool in ("WebFetch", "web_fetch"):
                            url = tool_input.get("url", "")
                            logger.info("claude_cli.web_fetch", url=url[:100])
                        else:
                            logger.info("claude_cli.tool_use", tool=tool)
                    elif block.get("type") == "text":
                        text_preview = block.get("text", "")[:120]
                        if text_preview:
                            logger.debug("claude_cli.text", preview=text_preview)
                    elif block.get("type") == "thinking":
                        thinking = block.get("thinking", "")[:150]
                        if thinking:
                            logger.info("claude_cli.reasoning", thought=thinking)

            elif etype == "tool_result":
                # Tool finished — log it
                tool_name = event.get("tool_name", "")
                if tool_name:
                    logger.debug("claude_cli.tool_done", tool=tool_name)

            elif etype == "result":
                result_data = event
                cost = event.get("total_cost_usd", 0)
                duration = event.get("duration_ms", 0)
                turns = event.get("num_turns", 0)
                logger.info(
                    "claude_cli.done",
                    duration_s=round(duration / 1000, 1),
                    turns=turns,
                    cost_usd=round(cost, 4),
                    is_error=event.get("is_error", False),
                )

        proc.wait(timeout=timeout)

    except subprocess.TimeoutExpired:
        proc.kill()
        raise

    if result_data is None:
        raise RuntimeError("Claude CLI produced no result event")

    if result_data.get("is_error"):
        error_msg = result_data.get("result", "Unknown error")
        logger.error("claude_cli.error_response", error=error_msg[:300])
        raise RuntimeError(f"Claude CLI error: {error_msg[:300]}")

    text = result_data.get("result", "")
    text = text.strip()
    # Strip markdown code fences if present
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text
        text = text.rsplit("```", 1)[0] if "```" in text else text
        text = text.strip()
    # Try direct parse, then extract from prose
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        parsed = _extract_json_object(text)
        if parsed is not None:
            return parsed
        logger.error("claude_cli.json_parse_failed", text_preview=text[:300])
        raise


class ClaudeResearchConnector(BaseConnector):
    """Uses Claude Code CLI to research companies and transactions.

    This connector leverages your Claude subscription to generate
    structured deal sourcing data without needing PitchBook/Crunchbase API keys.
    """

    source_name = "claude_research"

    def __init__(self, model: str = "sonnet") -> None:
        self._model = model

    async def fetch_companies(
        self,
        since: datetime | None = None,
        sector: str | None = None,
        geography: str = "US",
        count: int = 10,
        revenue_range: tuple[float, float] | None = None,
    ) -> list[CompanyRaw]:
        sector_clause = f"in the {sector} sector" if sector else "across various sectors"
        revenue_clause = ""
        if revenue_range:
            low_m = revenue_range[0] / 1_000_000
            high_m = revenue_range[1] / 1_000_000
            revenue_clause = f" with estimated revenue between ${low_m:.0f}M and ${high_m:.0f}M"

        prompt = (
            f"Research and identify {count} real private companies {sector_clause} "
            f"in {geography}{revenue_clause} that could be potential acquisition targets "
            f"for a private equity firm. Use web search to find real companies.\n\n"
            f"For each company provide: website domain, industry, headquarters, founding year, "
            f"employee count, estimated revenue and EBITDA (in USD), ownership type "
            f"(founder/family/pe_backed/vc_backed/public/unknown), funding history, "
            f"and 2-3 key executives with titles.\n\n"
            f"Focus on companies showing transaction signals: founder approaching retirement, "
            f"PE-backed past typical hold period, strong growth, etc.\n\n"
            f"Return your final answer as ONLY a JSON object matching the provided schema."
        )

        logger.info("fetch_companies", sector=sector, geography=geography, count=count)
        data = _call_claude(prompt, COMPANY_SCHEMA, self._model)

        results = []
        now = self._now_iso()
        for i, item in enumerate(data.get("companies", [])):
            raw = CompanyRaw(
                source=self.source_name,
                source_id=f"claude-{i}-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}",
                name=item["name"],
                domain=item.get("domain"),
                description=item.get("description"),
                industry=item.get("industry"),
                naics_code=item.get("naics_code"),
                hq_city=item.get("hq_city"),
                hq_state=item.get("hq_state"),
                hq_country=item.get("hq_country", geography),
                founded_year=item.get("founded_year"),
                employee_count=item.get("employee_count"),
                estimated_revenue=item.get("estimated_revenue_usd"),
                estimated_ebitda=item.get("estimated_ebitda_usd"),
                ownership_type=OWNERSHIP_MAP.get(
                    item.get("ownership_type", "unknown"), OwnershipType.UNKNOWN
                ),
                funding_total=item.get("funding_total_usd"),
                last_funding_date=item.get("last_funding_date"),
                last_funding_round=item.get("last_funding_round"),
                executives=item.get("executives", []),
                ingested_at=now,
            )
            results.append(raw)

        logger.info("fetch_companies.done", count=len(results))
        return results

    async def fetch_transactions(
        self,
        since: datetime | None = None,
        sector: str | None = None,
        geography: str = "US",
        count: int = 10,
    ) -> list[TransactionRecord]:
        sector_clause = f"in the {sector} sector" if sector else "across various sectors"
        time_clause = ""
        if since:
            time_clause = f" that occurred after {since.strftime('%Y-%m-%d')}"

        prompt = (
            f"Research and identify {count} real private equity and M&A transactions "
            f"{sector_clause} in {geography}{time_clause}. Use web search to find actual deals.\n\n"
            f"For each deal provide: target company name, buyer name, buyer type "
            f"(pe/strategic/vc/family_office), deal type (lbo/growth/add-on/merger/recapitalization), "
            f"sector, enterprise value (USD), EV/EBITDA and EV/Revenue multiples, "
            f"target financials, deal date, and geography.\n\n"
            f"Focus on lower-middle-market to middle-market deals ($10M-$500M EV).\n\n"
            f"Return your final answer as ONLY a JSON object matching the provided schema."
        )

        logger.info("fetch_transactions", sector=sector, geography=geography, count=count)
        data = _call_claude(prompt, TRANSACTION_SCHEMA, self._model)

        results = []
        now_str = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        for i, item in enumerate(data.get("transactions", [])):
            record = TransactionRecord(
                transaction_id=f"claude-tx-{i}-{now_str}",
                target_name=item["target_name"],
                buyer_name=item.get("buyer_name"),
                buyer_type=item.get("buyer_type"),
                deal_type=item.get("deal_type"),
                sector=item.get("sector"),
                enterprise_value=item.get("enterprise_value_usd"),
                ev_ebitda_multiple=item.get("ev_ebitda_multiple"),
                ev_revenue_multiple=item.get("ev_revenue_multiple"),
                target_revenue=item.get("target_revenue_usd"),
                target_ebitda=item.get("target_ebitda_usd"),
                target_ebitda_margin=item.get("target_ebitda_margin"),
                target_revenue_growth=item.get("target_revenue_growth"),
                deal_date=item.get("deal_date", "2024-01-01"),
                geography=item.get("geography"),
                source=self.source_name,
            )
            results.append(record)

        logger.info("fetch_transactions.done", count=len(results))
        return results
