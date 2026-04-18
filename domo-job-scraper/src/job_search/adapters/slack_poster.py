"""JobPoster adapter backed by slack_sdk WebClient."""

from __future__ import annotations

import logging

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from ..interfaces import JobPoster, PostResult
from ..poster import _format_job
from ..scraper import JobPost

log = logging.getLogger(__name__)


class SlackJobPoster(JobPoster):
    """Posts job listings to a Slack channel.

    Hides:
    - SlackApiError handling at every call site (caught, logged, returns failure)
    - The two-call pattern for new posts: chat_postMessage → chat_getPermalink
    - Slack mrkdwn formatting (delegated to _format_job from poster.py)
    - channel_id — callers never see it

    Args:
        client:     An authenticated slack_sdk.WebClient.
        channel_id: The Slack channel ID to post into.
    """

    def __init__(self, client: WebClient, channel_id: str) -> None:
        self._client = client
        self._channel_id = channel_id

    def post_session_header(self, count: int, date_str: str) -> None:
        """Post the daily run header banner."""
        text = f":briefcase: *Domo Job Search — {date_str}* | " f"{count} new listing{'s' if count != 1 else ''} found"
        try:
            self._client.chat_postMessage(channel=self._channel_id, text=text)
        except SlackApiError as exc:
            log.error("Failed to post session header: %s", exc)

    def post_job(self, post: JobPost) -> PostResult:
        """Post a new job listing. Returns PostResult.failure() on error."""
        text = _format_job(post)
        try:
            resp = self._client.chat_postMessage(channel=self._channel_id, text=text)
            ts = resp["ts"]
            permalink_resp = self._client.chat_getPermalink(channel=self._channel_id, message_ts=ts)
            return PostResult(ts=ts, permalink=permalink_resp.get("permalink", ""))
        except SlackApiError as exc:
            log.error("Failed to post job '%s': %s", post.title, exc)
            return PostResult.failure()

    def update_job(self, ts: str, post: JobPost) -> bool:
        """Update an existing Slack message in-place. Returns True on success."""
        try:
            self._client.chat_update(
                channel=self._channel_id,
                ts=ts,
                text=_format_job(post),
            )
            return True
        except SlackApiError as exc:
            log.error("Failed to update job '%s' (ts=%s): %s", post.title, ts, exc)
            return False
