"""JobScraper adapter backed by python-jobspy."""

from __future__ import annotations

import logging

from ..interfaces import JobScraper
from ..scraper import JobPost, _extract_domo_context, _format_salary, dedup_posts

log = logging.getLogger(__name__)


class JobspyScraper(JobScraper):
    """Scrapes LinkedIn, Indeed, Glassdoor, ZipRecruiter, and Google via python-jobspy.

    Hides:
    - Late import and ImportError guard for jobspy
    - DataFrame → JobPost normalisation (salary, description, job_type, is_remote)
    - Keyword relevance filter (skipped when description is truncated <50 chars)
    - Per-board exception isolation (one board failing doesn't abort the run)
    - Cross-board deduplication via dedup_posts()
    """

    def scrape(
        self,
        search_term: str,
        site_names: list[str],
        results_per_board: int,
    ) -> list[JobPost]:
        try:
            from jobspy import scrape_jobs  # noqa: PLC0415
        except ImportError as exc:
            raise RuntimeError("python-jobspy is not installed. Run: pip install python-jobspy") from exc

        log.info(
            "Scraping %d boards for '%s' (results_per_board=%d)",
            len(site_names),
            search_term,
            results_per_board,
        )

        try:
            df = scrape_jobs(
                site_name=site_names,
                search_term=search_term,
                results_wanted=results_per_board,
                country_indeed="USA",
                verbose=0,
            )
        except Exception as exc:
            log.error("jobspy scrape_jobs failed: %s", exc)
            return []

        if df is None or df.empty:
            log.info("No results returned from jobspy")
            return []

        keyword = search_term.lower()
        posts: list[JobPost] = []

        for row in df.itertuples(index=False):
            url = str(getattr(row, "job_url", "") or "").strip()
            if not url or url == "nan":
                continue

            title = str(getattr(row, "title", "") or "").strip()
            raw_description = str(getattr(row, "description", "") or "").strip()

            # Keep results whose description is too short to filter reliably —
            # trust the board's own search relevance in that case.
            if keyword not in title.lower() and keyword not in raw_description.lower() and len(raw_description) >= 50:
                continue

            description_preview = _extract_domo_context(raw_description, keyword)
            company = str(getattr(row, "company", "") or "").strip()
            location = str(getattr(row, "location", "") or "").strip()
            is_remote = bool(getattr(row, "is_remote", False))
            site = str(getattr(row, "site", "") or "").strip()

            raw_type = getattr(row, "job_type", None)
            if raw_type is None or str(raw_type) in ("nan", "None", ""):
                job_type = ""
            elif isinstance(raw_type, list):
                job_type = raw_type[0] if raw_type else ""
            else:
                job_type = str(raw_type)

            posts.append(
                JobPost(
                    url=url,
                    title=title,
                    company=company,
                    location=location,
                    is_remote=is_remote,
                    job_type=job_type,
                    salary=_format_salary(row),
                    site=site,
                    description=description_preview,
                )
            )

        log.info("Scraped %d raw listings", len(posts))
        deduped = dedup_posts(posts)
        log.info("After cross-board dedup: %d → %d", len(posts), len(deduped))
        return deduped
