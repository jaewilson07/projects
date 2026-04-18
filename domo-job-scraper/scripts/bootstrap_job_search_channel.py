"""One-time setup: register the #domo-jobs channel in the lettie registry.

Run once from the slack-overlord directory:

    python scripts/bootstrap_job_search_channel.py

This inserts (or updates) the row that lets lettie's MessageResponder
route user messages in C0AT0GWBBPT to the job-search Letta agent.
"""

import sys
from pathlib import Path

# Allow running from the slack-overlord root without installing the package
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import os

from lettie.registry import OverlordRegistry

AGENT_NAME = "job-search"
AGENT_ID = "agent-55e609e7-2a76-4400-a510-fa8b96c47aa3"
CONVERSATION_ID = "conv-678bc812-75fe-402d-a498-b25e60a43ae0"
SLACK_CHANNEL_ID = "C0AT0GWBBPT"
SLACK_CHANNEL_NAME = "domo-jobs"


def main() -> None:
    # Read db_path from the same env var OverlordConfig uses at runtime so the
    # bootstrap script and the bot always reference the same database file.
    db_path = os.environ.get("LETTIE_DB_PATH", "lettie_overlord.db")
    print(f"Registering job-search channel in lettie registry at: {db_path}")

    registry = OverlordRegistry(db_path=db_path)
    registry.upsert_mapping(
        agent_name=AGENT_NAME,
        agent_id=AGENT_ID,
        conversation_id=CONVERSATION_ID,
        conversation_name="Domo Job Search",
        slack_channel_id=SLACK_CHANNEL_ID,
        slack_channel_name=SLACK_CHANNEL_NAME,
    )
    registry.close()

    print(f"Done. Channel {SLACK_CHANNEL_ID} ({SLACK_CHANNEL_NAME}) mapped to agent {AGENT_ID[:12]}…")
    print("Lettie will now route user messages in #domo-jobs to the job-search agent.")


if __name__ == "__main__":
    main()
