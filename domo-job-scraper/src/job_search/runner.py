"""Orchestrates the full job search pipeline: scrape → dedup → post → record.

This module is the backward-compatible entry point. All logic lives in
JobSearchPipeline (pipeline.py). Inject fakes there for testing.
"""

from __future__ import annotations

from .pipeline import JobSearchPipeline


def run(cfg: object = None) -> None:
    """Execute one full job search run.

    Scrapes all configured boards, filters against the local manifest,
    posts new listings to Slack, and records them so they won't be
    re-posted tomorrow.

    Args:
        cfg: Optional JobSearchConfig. Reads from environment variables
             if omitted.
    """
    JobSearchPipeline.from_config(cfg).run()
