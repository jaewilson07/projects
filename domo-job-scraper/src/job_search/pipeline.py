"""JobSearchPipeline — composition root for the job search pipeline.

Owns the three components (scraper, manifest, poster) and contains the
full orchestration logic. The entry point run() in runner.py delegates
here via JobSearchPipeline.from_config().
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date

from .interfaces import JobManifest, JobPoster, JobScraper
from .scraper import dedup_posts

log = logging.getLogger(__name__)


@dataclass
class JobSearchPipeline:
    """Holds the three pipeline components and runs the job search cycle.

    Construct directly for testing (inject fakes alongside explicit params).
    Use from_config() for production (builds adapters from JobSearchConfig).

    search_term, site_names, results_per_board drive the scraper call.
    slack_channel_id is used in log output only (poster owns the channel).
    """

    scraper: JobScraper
    manifest: JobManifest
    poster: JobPoster
    search_term: str = "domo"
    site_names: list[str] = field(
        default_factory=lambda: ["linkedin", "indeed", "glassdoor", "zip_recruiter", "google"]
    )
    results_per_board: int = 200
    slack_channel_id: str = ""

    @classmethod
    def from_config(cls, cfg: object = None) -> JobSearchPipeline:
        """Build a fully-wired pipeline from config.

        Imports are deferred so tests that import pipeline.py without
        Slack/jobspy installed don't fail at import time.
        """
        from .config import JobSearchConfig  # noqa: PLC0415

        if cfg is None:
            cfg = JobSearchConfig()

        if not isinstance(cfg, JobSearchConfig):
            raise TypeError(f"Expected JobSearchConfig, got {type(cfg).__name__}")

        from slack_sdk import WebClient  # noqa: PLC0415

        from .adapters import JobspyScraper, SlackJobPoster, SQLiteJobManifest  # noqa: PLC0415

        return cls(
            scraper=JobspyScraper(),
            manifest=SQLiteJobManifest(db_path=cfg.db_path),
            poster=SlackJobPoster(
                client=WebClient(token=cfg.slack_bot_token),
                channel_id=cfg.slack_channel_id,
            ),
            search_term=cfg.search_term,
            site_names=cfg.site_names_list,
            results_per_board=cfg.results_per_board,
            slack_channel_id=cfg.slack_channel_id,
        )

    def run(self) -> None:
        """Execute one full job search cycle.

        Pipeline stages:
          1. scraper.scrape()             — fetch from boards
          2. dedup_posts()                — cross-source dedup (free function)
          3. manifest split               — new vs already-seen
          4. poster.update_job()          — refresh existing listings in-place
          5. poster.post_session_header() — daily run banner
          6. poster.post_job()            — publish new listings
          7. manifest.mark_seen()         — record successes only
        """
        with self.manifest:
            self._run()

    def _run(self) -> None:
        """Inner run body — manifest context is already entered."""
        # Stage 1: fetch
        raw_posts = self.scraper.scrape(
            search_term=self.search_term,
            site_names=self.site_names,
            results_per_board=self.results_per_board,
        )

        # Stage 2: dedup (free function — not a scraper concern)
        all_posts = dedup_posts(raw_posts)
        log.info(
            "After cross-source dedup: %d → %d",
            len(raw_posts),
            len(all_posts),
        )

        # Stage 3: manifest split
        new_posts = [p for p in all_posts if p.url and not self.manifest.is_seen(p.url)]
        seen_posts = [p for p in all_posts if p.url and self.manifest.is_seen(p.url)]
        log.info(
            "Total: %d | New: %d | Updating existing: %d",
            len(all_posts),
            len(new_posts),
            len(seen_posts),
        )

        # Stage 4: update existing postings in-place
        updated = 0
        for post in seen_posts:
            existing_ts = self.manifest.get_slack_ts(post.url)
            if existing_ts and self.poster.update_job(existing_ts, post):
                updated += 1

        if not new_posts:
            log.info("No new listings — updated %d existing", updated)
            return

        # Stage 5: daily header banner
        today = date.today().strftime("%B %d, %Y")
        self.poster.post_session_header(len(new_posts), today)

        # Stages 6 + 7: post new listings, record only confirmed successes
        posted = 0
        for post in new_posts:
            result = self.poster.post_job(post)
            if result.ok:
                self.manifest.mark_seen(
                    url=post.url,
                    title=post.title,
                    company=post.company,
                    slack_permalink=result.permalink,
                    slack_ts=result.ts,
                )
                posted += 1
            else:
                log.warning("Skipping manifest entry for '%s' — post failed", post.title)

        log.info(
            "Posted %d new | Updated %d existing | channel %s",
            posted,
            updated,
            self.slack_channel_id,
        )
