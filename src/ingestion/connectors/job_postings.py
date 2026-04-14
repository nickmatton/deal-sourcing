"""Job postings connector — headcount and hiring velocity signals.

Scrapes publicly available job listing counts from company career pages
and job board APIs to derive growth signals:
  - Current open positions (hiring velocity proxy)
  - Department-level breakdown (engineering, sales, ops)
  - Seniority distribution (executive searches signal transitions)

Sources (all free, no API key):
  - Google Jobs API (via SerpAPI pattern, or direct scrape)
  - Greenhouse public job boards (greenhouse.io)
  - Lever public job boards (lever.co)
  - Ashby public job boards (ashbyhq.com)

These are company-hosted public career pages — no scraping of
aggregator sites like Indeed/LinkedIn that prohibit it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import httpx
import structlog

logger = structlog.get_logger("ingestion.job_postings")

DEPARTMENT_KEYWORDS: dict[str, list[str]] = {
    "engineering": ["engineer", "developer", "software", "devops", "sre", "architect", "platform", "infrastructure", "data scientist", "machine learning"],
    "sales": ["sales", "account executive", "business development", "bdr", "sdr", "revenue"],
    "marketing": ["marketing", "growth", "brand", "content", "demand gen", "communications"],
    "operations": ["operations", "supply chain", "logistics", "procurement", "facilities"],
    "product": ["product manager", "product design", "ux", "ui", "user experience"],
    "finance": ["finance", "accounting", "controller", "fp&a", "treasury"],
    "hr": ["human resources", "people", "talent", "recruiting", "recruiter"],
    "executive": ["chief", "vp ", "vice president", "head of", "director", "c-suite", "cto", "cfo", "coo", "cmo"],
}


@dataclass
class JobPostingSummary:
    """Aggregated hiring signal for a company."""

    company_name: str
    source: str
    total_open_positions: int = 0
    department_breakdown: dict[str, int] = field(default_factory=dict)
    executive_searches: int = 0
    sample_titles: list[str] = field(default_factory=list)
    locations: list[str] = field(default_factory=list)
    board_url: str | None = None


def _classify_department(title: str) -> str:
    title_lower = title.lower()
    for dept, keywords in DEPARTMENT_KEYWORDS.items():
        for kw in keywords:
            if kw in title_lower:
                return dept
    return "other"


def _is_executive_search(title: str) -> bool:
    title_lower = title.lower()
    executive_kws = DEPARTMENT_KEYWORDS["executive"]
    return any(kw in title_lower for kw in executive_kws)


class JobPostingsConnector:
    """Scrapes public company career pages for hiring signals."""

    def __init__(self) -> None:
        self._client = httpx.AsyncClient(
            timeout=15.0,
            follow_redirects=True,
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; DealSourcingBot/1.0)",
                "Accept": "application/json",
            },
        )

    async def fetch_greenhouse(self, board_token: str) -> JobPostingSummary:
        """Fetch open positions from a Greenhouse public job board.

        Board token is the subdomain slug: e.g., "airbnb" for
        boards.greenhouse.io/airbnb
        """
        url = f"https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs"
        resp = await self._client.get(url)
        resp.raise_for_status()
        data = resp.json()
        jobs = data.get("jobs", [])

        summary = JobPostingSummary(
            company_name=board_token,
            source="greenhouse",
            total_open_positions=len(jobs),
            board_url=f"https://boards.greenhouse.io/{board_token}",
        )

        dept_counts: dict[str, int] = {}
        locations_set: set[str] = set()
        for job in jobs:
            title = job.get("title", "")
            dept = _classify_department(title)
            dept_counts[dept] = dept_counts.get(dept, 0) + 1
            if _is_executive_search(title):
                summary.executive_searches += 1
            if len(summary.sample_titles) < 10:
                summary.sample_titles.append(title)
            loc = job.get("location", {}).get("name", "")
            if loc:
                locations_set.add(loc)

        summary.department_breakdown = dept_counts
        summary.locations = sorted(locations_set)[:20]

        logger.info(
            "greenhouse_fetched",
            board=board_token,
            jobs=summary.total_open_positions,
        )
        return summary

    async def fetch_lever(self, company_slug: str) -> JobPostingSummary:
        """Fetch open positions from a Lever public job board.

        Company slug: e.g., "netflix" for jobs.lever.co/netflix
        """
        url = f"https://api.lever.co/v0/postings/{company_slug}"
        resp = await self._client.get(url, params={"mode": "json"})
        resp.raise_for_status()
        jobs = resp.json()

        if not isinstance(jobs, list):
            jobs = []

        summary = JobPostingSummary(
            company_name=company_slug,
            source="lever",
            total_open_positions=len(jobs),
            board_url=f"https://jobs.lever.co/{company_slug}",
        )

        dept_counts: dict[str, int] = {}
        locations_set: set[str] = set()
        for job in jobs:
            title = job.get("text", "")
            dept = _classify_department(title)
            dept_counts[dept] = dept_counts.get(dept, 0) + 1
            if _is_executive_search(title):
                summary.executive_searches += 1
            if len(summary.sample_titles) < 10:
                summary.sample_titles.append(title)
            loc = job.get("categories", {}).get("location", "")
            if loc:
                locations_set.add(loc)

        summary.department_breakdown = dept_counts
        summary.locations = sorted(locations_set)[:20]

        logger.info(
            "lever_fetched",
            company=company_slug,
            jobs=summary.total_open_positions,
        )
        return summary

    async def fetch_ashby(self, board_slug: str) -> JobPostingSummary:
        """Fetch open positions from an Ashby public job board."""
        url = f"https://api.ashbyhq.com/posting-api/job-board/{board_slug}"
        resp = await self._client.get(url)
        resp.raise_for_status()
        data = resp.json()
        jobs = data.get("jobs", [])

        summary = JobPostingSummary(
            company_name=board_slug,
            source="ashby",
            total_open_positions=len(jobs),
            board_url=f"https://jobs.ashbyhq.com/{board_slug}",
        )

        dept_counts: dict[str, int] = {}
        locations_set: set[str] = set()
        for job in jobs:
            title = job.get("title", "")
            dept = _classify_department(title)
            dept_counts[dept] = dept_counts.get(dept, 0) + 1
            if _is_executive_search(title):
                summary.executive_searches += 1
            if len(summary.sample_titles) < 10:
                summary.sample_titles.append(title)
            loc = job.get("location", "")
            if loc:
                locations_set.add(loc)

        summary.department_breakdown = dept_counts
        summary.locations = sorted(locations_set)[:20]

        logger.info(
            "ashby_fetched",
            board=board_slug,
            jobs=summary.total_open_positions,
        )
        return summary

    async def detect_board_type(self, domain: str) -> tuple[str | None, str | None]:
        """Try to detect which ATS a company uses from their domain.

        Returns (platform, slug) or (None, None) if not detectable.
        Checks common ATS redirect patterns from the company's careers page.
        """
        careers_paths = ["/careers", "/jobs", "/about/careers", "/company/careers"]

        for path in careers_paths:
            try:
                url = f"https://{domain}{path}"
                resp = await self._client.get(url)
                final_url = str(resp.url)
                body = resp.text[:5000].lower()

                # Check redirect URL
                if "greenhouse.io" in final_url:
                    slug = re.search(r"greenhouse\.io/(\w+)", final_url)
                    if slug:
                        return "greenhouse", slug.group(1)
                if "lever.co" in final_url:
                    slug = re.search(r"lever\.co/(\w+)", final_url)
                    if slug:
                        return "lever", slug.group(1)
                if "ashbyhq.com" in final_url:
                    slug = re.search(r"ashbyhq\.com/(\w+)", final_url)
                    if slug:
                        return "ashby", slug.group(1)

                # Check page content for embedded ATS
                if "boards.greenhouse.io" in body or "boards-api.greenhouse.io" in body:
                    slug = re.search(r"greenhouse\.io/(\w+)", body)
                    if slug:
                        return "greenhouse", slug.group(1)
                if "jobs.lever.co" in body or "api.lever.co" in body:
                    slug = re.search(r"lever\.co/(\w+)", body)
                    if slug:
                        return "lever", slug.group(1)
                if "ashbyhq.com" in body:
                    slug = re.search(r"ashbyhq\.com/(\w+)", body)
                    if slug:
                        return "ashby", slug.group(1)

            except (httpx.HTTPError, httpx.InvalidURL):
                continue

        return None, None

    async def fetch_for_company(
        self, company_name: str, domain: str | None = None
    ) -> JobPostingSummary | None:
        """Auto-detect ATS and fetch job postings for a company.

        Tries domain-based detection first, then falls back to slug guessing.
        """
        platform, slug = None, None

        if domain:
            platform, slug = await self.detect_board_type(domain)

        if not platform:
            # Guess common slug from company name
            name_slug = re.sub(r"[^a-z0-9]", "", company_name.lower())
            for p, fetcher in [
                ("greenhouse", self.fetch_greenhouse),
                ("lever", self.fetch_lever),
                ("ashby", self.fetch_ashby),
            ]:
                try:
                    result = await fetcher(name_slug)
                    if result.total_open_positions > 0:
                        result.company_name = company_name
                        return result
                except (httpx.HTTPStatusError, httpx.HTTPError):
                    continue
            return None

        try:
            if platform == "greenhouse":
                result = await self.fetch_greenhouse(slug)
            elif platform == "lever":
                result = await self.fetch_lever(slug)
            elif platform == "ashby":
                result = await self.fetch_ashby(slug)
            else:
                return None
            result.company_name = company_name
            return result
        except (httpx.HTTPStatusError, httpx.HTTPError) as e:
            logger.debug("job_fetch_failed", company=company_name, error=str(e))
            return None

    async def close(self) -> None:
        await self._client.aclose()
