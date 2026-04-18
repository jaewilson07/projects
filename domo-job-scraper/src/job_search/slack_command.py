"""Slack slash command: /check-job-postings

Registers on the Lettie Bolt app so users can trigger a job search run
on demand from any Slack channel.

Wire up via::

    from job_search.slack_command import register_job_search_command
    register_job_search_command(bolt_app, job_search_cfg)
"""

from __future__ import annotations

import asyncio
import logging

from slack_bolt.async_app import AsyncApp

from .config import JobSearchConfig
from .runner import run

log = logging.getLogger(__name__)


def register_job_search_command(app: AsyncApp, cfg: JobSearchConfig) -> None:
    """Register /check-job-postings on *app*."""

    @app.command("/check-job-postings")
    async def handle_check_job_postings(ack, respond) -> None:  # type: ignore[no-untyped-def]
        # Must ack within 3 seconds or Slack shows an error
        await ack()
        await respond(f":mag: Running Domo job search… results will appear in <#{cfg.slack_channel_id}>.")

        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(None, run, cfg)
        except Exception as exc:
            log.error("Job search command failed: %s", exc)
            await respond(f":x: Job search failed: {exc}")
