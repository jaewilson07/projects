"""Tests for the job search pipeline using injected fakes.

No Slack SDK, no SQLite, no jobspy required — all I/O is replaced by
in-memory fakes that implement the ABCs.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from job_search.interfaces import JobManifest, JobPoster, JobScraper, PostResult
from job_search.pipeline import JobSearchPipeline
from job_search.scraper import JobPost


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _job(url: str = "http://example.com/job/1", title: str = "Domo Dev") -> JobPost:
    return JobPost(
        url=url,
        title=title,
        company="Acme Corp",
        location="Denver, CO",
        is_remote=True,
        job_type="fulltime",
        salary="$120k",
        site="indeed",
        description="Experience with Domo required.",
    )


# ---------------------------------------------------------------------------
# Fake implementations
# ---------------------------------------------------------------------------


class FakeScraper(JobScraper):
    def __init__(self, posts: list[JobPost] | None = None) -> None:
        self.posts = posts or []
        self.calls: list[dict] = []

    def scrape(self, search_term: str, site_names: list[str], results_per_board: int) -> list[JobPost]:
        self.calls.append({"search_term": search_term, "site_names": site_names})
        return self.posts


class FakeManifest(JobManifest):
    def __init__(self, seen: dict[str, str] | None = None) -> None:
        # maps url → slack_ts
        self._seen: dict[str, str] = seen or {}
        self.marked: list[dict] = []
        self.closed = False

    def is_seen(self, url: str) -> bool:
        return url in self._seen

    def get_slack_ts(self, url: str) -> str | None:
        return self._seen.get(url)

    def mark_seen(
        self, *, url: str, title: str = "", company: str = "", slack_permalink: str = "", slack_ts: str = ""
    ) -> None:
        self._seen[url] = slack_ts
        self.marked.append({"url": url, "slack_ts": slack_ts})

    def close(self) -> None:
        self.closed = True


class FakePoster(JobPoster):
    def __init__(self) -> None:
        self.posted: list[JobPost] = []
        self.updated: list[tuple[str, JobPost]] = []
        self.headers: list[tuple[int, str]] = []
        self._fail_urls: set[str] = set()

    def fail_on(self, url: str) -> None:
        """Configure this poster to fail when posting a specific URL."""
        self._fail_urls.add(url)

    def post_session_header(self, count: int, date_str: str) -> None:
        self.headers.append((count, date_str))

    def post_job(self, post: JobPost) -> PostResult:
        if post.url in self._fail_urls:
            return PostResult.failure()
        self.posted.append(post)
        return PostResult(ts=f"ts-{len(self.posted)}", permalink=f"https://slack.example/p{len(self.posted)}")

    def update_job(self, ts: str, post: JobPost) -> bool:
        self.updated.append((ts, post))
        return True


def _pipeline(
    posts: list[JobPost] | None = None,
    seen: dict[str, str] | None = None,
) -> tuple[JobSearchPipeline, FakeScraper, FakeManifest, FakePoster]:
    scraper = FakeScraper(posts)
    manifest = FakeManifest(seen)
    poster = FakePoster()
    pipeline = JobSearchPipeline(scraper=scraper, manifest=manifest, poster=poster)
    return pipeline, scraper, manifest, poster


# ---------------------------------------------------------------------------
# Tests: new listings
# ---------------------------------------------------------------------------


def test_new_job_is_posted_and_recorded():
    post = _job()
    pl, _, manifest, poster = _pipeline(posts=[post])

    pl.run()

    assert len(poster.posted) == 1
    assert poster.posted[0].url == post.url
    assert len(manifest.marked) == 1
    assert manifest.marked[0]["url"] == post.url


def test_session_header_posted_before_jobs():
    # Use distinct titles so dedup_posts doesn't collapse them.
    posts = [_job("http://a", title="Domo Analyst"), _job("http://b", title="Domo Engineer")]
    pl, _, _, poster = _pipeline(posts=posts)

    pl.run()

    assert len(poster.headers) == 1
    count, _ = poster.headers[0]
    assert count == 2


def test_no_header_when_no_new_posts():
    post = _job()
    # Mark the URL as seen with an existing ts
    pl, _, _, poster = _pipeline(posts=[post], seen={post.url: "ts-existing"})

    pl.run()

    assert poster.headers == []
    assert poster.posted == []


def test_empty_scrape_does_nothing():
    pl, _, manifest, poster = _pipeline(posts=[])

    pl.run()

    assert poster.posted == []
    assert poster.headers == []
    assert manifest.marked == []


# ---------------------------------------------------------------------------
# Tests: existing listings (update path)
# ---------------------------------------------------------------------------


def test_seen_job_is_updated_not_reposted():
    post = _job()
    pl, _, manifest, poster = _pipeline(posts=[post], seen={post.url: "ts-orig"})

    pl.run()

    assert poster.posted == []
    assert len(poster.updated) == 1
    ts, updated_post = poster.updated[0]
    assert ts == "ts-orig"
    assert updated_post.url == post.url
    # Manifest should NOT be re-recorded for updates
    assert manifest.marked == []


def test_seen_job_with_missing_ts_is_skipped():
    post = _job()
    # Seen but no ts stored (e.g. from a manual DB entry)
    pl, _, _, poster = _pipeline(posts=[post], seen={post.url: ""})

    pl.run()

    assert poster.updated == []
    assert poster.posted == []


# ---------------------------------------------------------------------------
# Tests: failure handling
# ---------------------------------------------------------------------------


def test_failed_post_not_recorded_in_manifest():
    post = _job()
    pl, _, manifest, poster = _pipeline(posts=[post])
    poster.fail_on(post.url)

    pl.run()

    assert len(poster.posted) == 0
    assert manifest.marked == []


def test_partial_failure_records_only_successes():
    ok = _job("http://ok")
    fail = _job("http://fail", title="Failing Job")
    pl, _, manifest, poster = _pipeline(posts=[ok, fail])
    poster.fail_on(fail.url)

    pl.run()

    assert len(poster.posted) == 1
    assert poster.posted[0].url == ok.url
    assert len(manifest.marked) == 1
    assert manifest.marked[0]["url"] == ok.url


# ---------------------------------------------------------------------------
# Tests: manifest lifecycle
# ---------------------------------------------------------------------------


def test_manifest_closed_after_run():
    pl, _, manifest, _ = _pipeline(posts=[])

    pl.run()

    assert manifest.closed is True


def test_manifest_closed_even_if_scraper_returns_empty():
    pl, _, manifest, _ = _pipeline(posts=[])
    pl.run()
    assert manifest.closed is True


# ---------------------------------------------------------------------------
# Tests: scraper is called with pipeline params
# ---------------------------------------------------------------------------


def test_scraper_receives_search_term():
    pl, scraper, _, _ = _pipeline(posts=[])
    pl.search_term = "domo"

    pl.run()

    assert scraper.calls[0]["search_term"] == "domo"


def test_dedup_removes_cross_source_duplicates():
    """Two identical (title, company) posts → only one reaches the poster."""
    a = _job("http://indeed/1")
    b = JobPost(
        url="http://linkedin/1",
        title=a.title,
        company=a.company,
        location=a.location,
        is_remote=a.is_remote,
        job_type=a.job_type,
        salary=a.salary,
        site="linkedin",
        description=a.description + " extra words to be the longer one",
    )
    pl, _, _, poster = _pipeline(posts=[a, b])

    pl.run()

    assert len(poster.posted) == 1
    # dedup_posts keeps the copy with the longest description
    assert poster.posted[0].url == b.url
