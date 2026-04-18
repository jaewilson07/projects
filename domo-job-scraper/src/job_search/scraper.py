"""Scrape job listings from multiple boards via python-jobspy."""

from __future__ import annotations

import logging
from dataclasses import dataclass

log = logging.getLogger(__name__)


@dataclass
class JobPost:
    """Normalised job listing."""

    url: str
    title: str
    company: str
    location: str
    is_remote: bool
    job_type: str  # e.g. "fulltime", "contract", ""
    salary: str  # formatted string, e.g. "$80–$120/hr" or ""
    site: str  # source board
    description: str = ""  # first 200 words of the raw description


def _format_salary(row: object) -> str:
    """Build a human-readable salary string from a jobspy DataFrame row."""
    try:
        min_amt = getattr(row, "min_amount", None)
        max_amt = getattr(row, "max_amount", None)
        interval = getattr(row, "interval", None) or ""
        currency = getattr(row, "currency", None) or "$"

        import math

        def _valid(v: object) -> bool:
            try:
                return v is not None and not math.isnan(float(v))  # type: ignore[arg-type]
            except (TypeError, ValueError):
                return False

        if not _valid(min_amt) and not _valid(max_amt):
            return ""

        sym = currency if currency != "USD" else "$"
        interval_label = f"/{interval}" if interval else ""

        if _valid(min_amt) and _valid(max_amt):
            return f"{sym}{int(min_amt):,}–{sym}{int(max_amt):,}{interval_label}"
        if _valid(min_amt):
            return f"{sym}{int(min_amt):,}+{interval_label}"
        return f"up to {sym}{int(max_amt):,}{interval_label}"
    except Exception:
        return ""


def _normalize(text: str) -> str:
    """Lowercase + collapse whitespace for fuzzy matching."""
    import re

    return re.sub(r"\s+", " ", text.lower().strip())


def dedup_posts(posts: list[JobPost]) -> list[JobPost]:
    """Remove cross-board duplicates by (title, company) key.

    When the same job appears on multiple boards, keep the copy with the
    longest description (Indeed > LinkedIn since LinkedIn truncates).
    """
    best: dict[tuple[str, str], JobPost] = {}
    for post in posts:
        key = (_normalize(post.title), _normalize(post.company))
        existing = best.get(key)
        if existing is None or len(post.description) > len(existing.description):
            best[key] = post
    deduped = list(best.values())
    return deduped


def _extract_domo_context(description: str, keyword: str) -> str:
    """Return the sentence containing *keyword* plus its neighbours.

    Splits on '. ' boundaries, finds the first sentence containing the
    keyword (case-insensitive), then returns [prev, match, next] joined
    together.  Falls back to the first two sentences if the keyword
    doesn't appear in the description at all.
    """
    import re

    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", description) if s.strip()]
    if not sentences:
        return ""

    kw = keyword.lower()
    _MAX_CHARS = 500

    for i, sentence in enumerate(sentences):
        if kw in sentence.lower():
            window = sentences[max(0, i - 1) : i + 2]
            snippet = " ".join(window)
            return snippet[:_MAX_CHARS] + ("…" if len(snippet) > _MAX_CHARS else "")

    # keyword only in title — fall back to first two sentences
    snippet = " ".join(sentences[:2])
    return snippet[:_MAX_CHARS] + ("…" if len(snippet) > _MAX_CHARS else "")


def scrape(
    search_term: str,
    site_names: list[str],
    results_per_board: int = 50,
) -> list[JobPost]:
    """Return a flat list of JobPost objects from all requested boards.

    Boards that fail are logged and skipped — the run continues with
    whatever boards succeeded.
    """
    try:
        from jobspy import scrape_jobs
    except ImportError as exc:
        raise RuntimeError("python-jobspy is not installed. " "Run: pip install python-jobspy") from exc

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

        # Filter out noise: require the keyword in title OR description.
        # If the description is very short (<50 chars) the board truncated it —
        # trust the board's own search relevance and keep the result.
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

    log.info("Scraped %d total listings", len(posts))
    return posts
