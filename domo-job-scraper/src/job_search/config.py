"""Configuration for the Domo job search module.

Reads from environment variables with the ``JOB_SEARCH_`` prefix.
Falls back to .env file (same directory as the process working directory).
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class JobSearchConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="JOB_SEARCH_",
        env_file=".env",
        case_sensitive=False,
        extra="ignore",
    )

    # Slack
    slack_bot_token: str
    slack_channel_id: str = "C0AT0GWBBPT"

    # Scraper
    search_term: str = "domo"
    results_per_board: int = 200
    # Comma-separated list of boards; default all five
    site_names: str = "linkedin,indeed,glassdoor,zip_recruiter,google"

    # Persistence
    db_path: str = "data/EXPORTS/job_search.db"

    @property
    def site_names_list(self) -> list[str]:
        return [s.strip() for s in self.site_names.split(",") if s.strip()]
