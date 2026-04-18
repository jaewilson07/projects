"""Format and post job listings to Slack."""

from __future__ import annotations

import logging

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from .scraper import JobPost

log = logging.getLogger(__name__)


def _format_job(post: JobPost) -> str:
    """Build the Slack message text for a single job posting."""
    parts: list[str] = []

    # Title + company
    parts.append(f"*{post.title}* — {post.company}")

    # Meta line
    meta: list[str] = []
    if post.location:
        meta.append(post.location)
    if post.is_remote:
        meta.append("Remote")
    if post.job_type:
        meta.append(post.job_type.replace("_", " ").title())
    if post.salary:
        meta.append(post.salary)
    if meta:
        parts.append(" | ".join(meta))

    # Description preview
    if post.description:
        parts.append(f"_{post.description}_")

    # Source board + URL
    parts.append(f"<{post.url}|View posting> _(via {post.site})_")

    return "\n".join(parts)


def post_header(client: WebClient, channel_id: str, count: int, date_str: str) -> str:
    """Post the daily run header. Returns the Slack message timestamp."""
    text = f":briefcase: *Domo Job Search — {date_str}* | {count} new listing{'s' if count != 1 else ''} found"
    try:
        resp = client.chat_postMessage(channel=channel_id, text=text)
        return resp["ts"]
    except SlackApiError as exc:
        log.error("Failed to post header: %s", exc)
        return ""


def post_job(client: WebClient, channel_id: str, post: JobPost) -> tuple[str, str]:
    """Post a new job listing. Returns (ts, permalink) or ("", "") on failure."""
    text = _format_job(post)
    try:
        resp = client.chat_postMessage(channel=channel_id, text=text)
        ts = resp["ts"]
        permalink_resp = client.chat_getPermalink(channel=channel_id, message_ts=ts)
        return ts, permalink_resp.get("permalink", "")
    except SlackApiError as exc:
        log.error("Failed to post job '%s': %s", post.title, exc)
        return "", ""


def update_job(client: WebClient, channel_id: str, ts: str, post: JobPost) -> bool:
    """Update an existing Slack message in-place. Returns True on success."""
    text = _format_job(post)
    try:
        client.chat_update(channel=channel_id, ts=ts, text=text)
        return True
    except SlackApiError as exc:
        log.error("Failed to update job '%s' (ts=%s): %s", post.title, ts, exc)
        return False
