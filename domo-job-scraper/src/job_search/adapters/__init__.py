"""Concrete adapters implementing the job_search ABCs.

JobspyScraper      — JobScraper backed by python-jobspy
SQLiteJobManifest  — JobManifest backed by SQLite
SlackJobPoster     — JobPoster backed by slack_sdk WebClient
"""

from .jobspy_scraper import JobspyScraper
from .slack_poster import SlackJobPoster
from .sqlite_manifest import SQLiteJobManifest

__all__ = ["JobspyScraper", "SQLiteJobManifest", "SlackJobPoster"]
