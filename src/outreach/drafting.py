"""LLM-powered outreach draft generation."""

import structlog

from src.common.schemas.outreach import OutreachChannel, OutreachDraft, ToneRecommendation

logger = structlog.get_logger("outreach")

OUTREACH_SYSTEM_PROMPT = """\
You are a senior private equity professional drafting a personalized outreach email \
to a company founder/owner. Your tone should be professional, respectful, and genuine. \
Never mention specific valuation numbers. Focus on strategic value and partnership.

Guidelines:
- Reference a specific, recent signal about the company (growth, expansion, etc.)
- Articulate the thesis-specific value proposition
- Keep the email concise (150-250 words)
- Frame the conversation as exploratory, not transactional
- Maintain warmth without being presumptuous
"""


def build_outreach_prompt(
    company_name: str,
    thesis_rationale: str,
    company_signals: list[str],
    founder_name: str | None = None,
    warm_path: list[str] | None = None,
    tone: ToneRecommendation = ToneRecommendation.PARTNERSHIP,
) -> str:
    prompt_parts = [
        f"Draft a personalized outreach email to {founder_name or 'the founder/owner'} "
        f"of {company_name}.\n",
        f"**Investment thesis fit**: {thesis_rationale}\n",
        f"**Recent company signals**: {'; '.join(company_signals)}\n",
        f"**Desired tone**: {tone.value}\n",
    ]

    if warm_path:
        prompt_parts.append(
            f"**Warm introduction path**: {' → '.join(warm_path)}\n"
            "Reference this connection naturally in the opening.\n"
        )

    if tone == ToneRecommendation.PREMIUM_BUYER:
        prompt_parts.append(
            "Emphasize our track record and the premium we place on quality businesses.\n"
        )
    elif tone == ToneRecommendation.GROWTH_ACCELERATION:
        prompt_parts.append(
            "Focus on how our platform and resources can accelerate their growth trajectory.\n"
        )
    elif tone == ToneRecommendation.DISCIPLINED_VALUE:
        prompt_parts.append(
            "Be direct and professional. Emphasize operational partnership and long-term value.\n"
        )

    return "\n".join(prompt_parts)


class OutreachDrafter:
    """Generates personalized outreach drafts using an LLM."""

    def __init__(self, llm_client=None) -> None:
        self._llm = llm_client

    async def generate_draft(
        self,
        entity_id: str,
        company_name: str,
        thesis_id: str,
        thesis_rationale: str,
        company_signals: list[str],
        founder_name: str | None = None,
        warm_path: list[str] | None = None,
        tone: ToneRecommendation = ToneRecommendation.PARTNERSHIP,
        channel: OutreachChannel = OutreachChannel.EMAIL,
    ) -> OutreachDraft:
        """Generate a single outreach draft for human review."""
        prompt = build_outreach_prompt(
            company_name=company_name,
            thesis_rationale=thesis_rationale,
            company_signals=company_signals,
            founder_name=founder_name,
            warm_path=warm_path,
            tone=tone,
        )

        if self._llm is not None:
            response = await self._llm.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=1024,
                system=OUTREACH_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}],
            )
            body = response.content[0].text
        else:
            body = f"[DRAFT PLACEHOLDER — LLM not configured]\n\n{prompt}"

        draft = OutreachDraft(
            entity_id=entity_id,
            company_name=company_name,
            thesis_id=thesis_id,
            channel=channel,
            subject=f"Exploring a partnership with {company_name}",
            body=body,
            warm_path=warm_path or [],
            tone=tone,
            approved=False,
        )

        logger.info("outreach.draft_generated", entity_id=entity_id, channel=channel)
        return draft
