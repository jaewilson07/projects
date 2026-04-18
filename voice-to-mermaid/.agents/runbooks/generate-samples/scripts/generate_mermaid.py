#!/usr/bin/env python3
"""Generate a Mermaid diagram from a context file (transcript, resume, notes, etc.).

This script proves out the voice-to-mermaid prompt pipeline. Run it to validate
and tune the prompt before wiring it into the live streaming app.

The prompt lives at:
  .agents/runbooks/generate-samples/prompts/mermaid.txt

Edit that file, re-run this script, repeat. The prompt is hot-reloaded on every
call — no restart needed.

Usage:
    # Quick test with sample resume
    python scripts/generate_mermaid.py --input data/resume.md --instructions "Map my career as a flowchart"

    # Simulate a live transcript
    python scripts/generate_mermaid.py --transcript "OAuth 2.0 flow: user logs in, client gets auth code, exchanges for token, calls API"

    # Save output to file
    python scripts/generate_mermaid.py --input data/resume.md --output /tmp/career.mmd

    # Use Anthropic instead of Ollama
    python scripts/generate_mermaid.py --input data/resume.md --backend anthropic

    # Streaming simulation: re-run every N seconds as transcript grows
    python scripts/generate_mermaid.py --input data/resume.md --watch

Backends:
    ollama    — http://localhost:11434  (default, no API key needed)
    anthropic — claude-sonnet-4-6       (needs ANTHROPIC_API_KEY env var)
    server    — http://localhost:7625   (calls running voice-to-mermaid server)
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

PROMPTS_DIR = Path(__file__).parent.parent / "prompts"
DEFAULT_INPUT = Path(__file__).parent.parent / "data" / "resume.md"


# ── Prompt loading ────────────────────────────────────────────────────────────


def load_prompt(transcript: str, instructions: str) -> str:
    """Load mermaid.txt and substitute placeholders. Hot-reloads from disk."""
    template = (PROMPTS_DIR / "mermaid.txt").read_text()
    return template.format(transcript=transcript, instructions=instructions or "(none)")


# ── Backends ──────────────────────────────────────────────────────────────────


def call_ollama(prompt: str, model: str, url: str) -> str:
    import httpx

    base = url.rstrip("/").removesuffix("/v1")
    resp = httpx.post(
        f"{base}/api/generate",
        json={"model": model, "prompt": prompt, "stream": False},
        timeout=300.0,
    )
    resp.raise_for_status()
    return resp.json()["response"].strip()


def call_anthropic(prompt: str, model: str) -> str:
    import httpx

    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        sys.exit("ANTHROPIC_API_KEY not set")
    resp = httpx.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": model,
            "max_tokens": 2048,
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=120.0,
    )
    resp.raise_for_status()
    return resp.json()["content"][0]["text"].strip()


def call_server(transcript: str, instructions: str, url: str, mode: str = "ollama") -> str:
    """Call the running voice-to-mermaid backend via REST /diagram endpoint."""
    import httpx

    full_transcript = f"{instructions}\n\n{transcript}".strip() if instructions else transcript
    resp = httpx.post(
        f"{url.rstrip('/')}/diagram",
        json={"transcript": full_transcript, "mode": mode},
        timeout=300.0,
    )
    resp.raise_for_status()
    return resp.json()["diagram"]


# ── Fence stripping ───────────────────────────────────────────────────────────


def strip_fences(text: str) -> str:
    import re

    text = re.sub(r"^```(?:mermaid)?\s*\n?", "", text, flags=re.MULTILINE)
    text = re.sub(r"\n?```\s*$", "", text, flags=re.MULTILINE)
    return text.strip()


# ── Main ──────────────────────────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate Mermaid from context")
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--input", "-i", type=Path, default=DEFAULT_INPUT, help="Path to context file (default: data/resume.md)"
    )
    group.add_argument("--transcript", "-t", type=str, help="Inline transcript string (skips --input)")
    parser.add_argument(
        "--instructions", default="", help="User instructions to the model (e.g. 'Map my career as a flowchart')"
    )
    parser.add_argument("--output", "-o", type=Path, help="Save output to file (default: print to stdout)")
    parser.add_argument("--backend", choices=["ollama", "anthropic", "server"], default="ollama")
    parser.add_argument("--ollama-url", default=os.environ.get("OLLAMA_URL", "http://localhost:11434"))
    parser.add_argument("--ollama-model", default=os.environ.get("OLLAMA_MODEL", "gemma3:12b"))
    parser.add_argument("--anthropic-model", default="claude-sonnet-4-6")
    parser.add_argument("--server-url", default=os.environ.get("SERVER_URL", "http://localhost:7625"))
    parser.add_argument("--watch", action="store_true", help="Re-run every 10s (simulates live transcript updates)")
    args = parser.parse_args()

    def run_once() -> str:
        transcript = args.transcript if args.transcript else args.input.read_text()
        t0 = time.perf_counter()

        if args.backend == "ollama":
            prompt = load_prompt(transcript, args.instructions)
            print(f"[ollama] model={args.ollama_model}  prompt_chars={len(prompt)}", file=sys.stderr)
            result = call_ollama(prompt, args.ollama_model, args.ollama_url)
        elif args.backend == "anthropic":
            prompt = load_prompt(transcript, args.instructions)
            print(f"[anthropic] model={args.anthropic_model}  prompt_chars={len(prompt)}", file=sys.stderr)
            result = call_anthropic(prompt, args.anthropic_model)
        else:  # server
            print(f"[server] url={args.server_url}", file=sys.stderr)
            result = call_server(transcript, args.instructions, args.server_url)

        result = strip_fences(result)
        elapsed = time.perf_counter() - t0
        print(f"[done] {len(result)} chars  {elapsed:.1f}s", file=sys.stderr)
        return result

    if args.watch:
        print("[watch] Re-running every 10s. Edit the input file or prompt to see changes.", file=sys.stderr)
        while True:
            result = run_once()
            print("\n" + "─" * 60)
            print(result)
            print("─" * 60 + "\n")
            time.sleep(10)
    else:
        result = run_once()
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(result)
            print(f"Saved to {args.output}", file=sys.stderr)
        else:
            print(result)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
