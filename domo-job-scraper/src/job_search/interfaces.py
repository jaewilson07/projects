"""Abstract interfaces for the job search pipeline.

Three ABCs define the contracts:
  - JobScraper   — fetches and normalises job listings from an external source
  - JobManifest  — persists which URLs have been posted (dedup store)
  - JobPoster    — publishes listings to a notification channel

PostResult is the value type returned by JobPoster.post_job().

Concrete adapters live in job_search/adapters/.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from .scraper import JobPost


class JobScraper(ABC):
    """Fetch and normalise job listings from one or more external boards.

    The adapter is responsible for:
    - Calling its upstream library / API
    - Normalising rows into JobPost objects
    - Gracefully swallowing per-board failures (log + continue)

    The adapter is NOT responsible for:
    - Cross-source deduplication (caller's job via dedup_posts())
    - Filtering against the manifest (runner's job)
    """

    @abstractmethod
    def scrape(
        self,
        search_term: str,
        site_names: list[str],
        results_per_board: int,
    ) -> list[JobPost]:
        """Return all listings fetched from this source.

        Must not raise. Returns [] on total failure.
        """
        ...


class JobManifest(ABC):
    """Persistent store tracking which job URLs have been posted.

    Implementations must be safe for single-threaded use within one run.
    close() releases any held resources (connections, file handles).
    """

    @abstractmethod
    def is_seen(self, url: str) -> bool:
        """Return True if *url* has been posted before."""
        ...

    @abstractmethod
    def get_slack_ts(self, url: str) -> str | None:
        """Return the Slack message timestamp for *url*, or None if not seen."""
        ...

    @abstractmethod
    def mark_seen(
        self,
        *,
        url: str,
        title: str = "",
        company: str = "",
        slack_permalink: str = "",
        slack_ts: str = "",
    ) -> None:
        """Record *url* as seen with its Slack posting metadata."""
        ...

    @abstractmethod
    def close(self) -> None:
        """Release any held resources."""
        ...

    def __enter__(self) -> JobManifest:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


@dataclass(frozen=True)
class PostResult:
    """Outcome of posting a single job listing.

    ``ts`` and ``permalink`` are empty strings on failure.
    Use ``.ok`` as the canonical success check rather than ``if ts:``.
    """

    ts: str
    permalink: str

    @property
    def ok(self) -> bool:
        return bool(self.ts)

    @classmethod
    def failure(cls) -> PostResult:
        return cls(ts="", permalink="")


class JobPoster(ABC):
    """Publish job listings to a single notification channel.

    post_session_header is intentionally non-abstract: channels that have
    no concept of a run header (email digests, webhooks) use the no-op
    default and don't need to override it.
    """

    def post_session_header(self, count: int, date_str: str) -> None:
        """Post the 'N new listings found today' banner. No-op by default."""

    @abstractmethod
    def post_job(self, post: JobPost) -> PostResult:
        """Publish a new listing. Must not raise — returns PostResult.failure() on error."""
        ...

    @abstractmethod
    def update_job(self, ts: str, post: JobPost) -> bool:
        """Update an already-posted listing in-place. Must not raise."""
        ...
