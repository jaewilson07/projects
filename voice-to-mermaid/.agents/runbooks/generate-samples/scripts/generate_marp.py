#!/usr/bin/env python3
"""Generate a Marp slide deck from a context file (transcript, resume, notes, etc.).

This script proves out the voice-to-marp prompt pipeline. Run it to validate
and tune the prompt before wiring it into the live streaming app.

The prompt lives at:
  .agents/runbooks/generate-samples/prompts/marp.txt

Edit that file, re-run this script, repeat.

Usage:
    # 5-slide sizzle reel from resume
    python scripts/generate_marp.py --input data/resume.md \
        --instructions "Create a 5-slide sizzle reel. Title, career snapshot, superpower, 3 wins, CTA."

    # From a live meeting transcript
    python scripts/generate_marp.py --transcript "We need to redesign the data pipeline..." \
        --instructions "Capture the key decisions as a 3-slide summary"

    # Save to file (open in VS Code with Marp extension, or marp-cli to export PDF)
    python scripts/generate_marp.py --input data/resume.md \
        --instructions "5-slide sizzle reel" \
        --output /tmp/sizzle_reel.md

    # Use Anthropic
    python scripts/generate_marp.py --input data/resume.md \
        --instructions "5 slides, punchy" \
        --backend anthropic

    # Export to PDF (requires marp-cli: npm install -g @marp-team/marp-cli)
    python scripts/generate_marp.py --input data/resume.md \
        --instructions "5-slide sizzle reel" \
        --output /tmp/sizzle.md --pdf

Backends:
    ollama    — http://localhost:11434  (default)
    anthropic — claude-sonnet-4-6       (needs ANTHROPIC_API_KEY)

Reference:
    Marp main site:    https://marp.app/
    Marpit directives: https://marpit.marp.app/
    Marp CLI:          https://github.com/marp-team/marp-cli
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

PROMPTS_DIR = Path(__file__).parent.parent / "prompts"
DEFAULT_INPUT = Path(__file__).parent.parent / "data" / "resume.md"


# ── Prompt loading ────────────────────────────────────────────────────────────


def load_prompt(transcript: str, instructions: str) -> str:
    """Load marp.txt and substitute placeholders. Hot-reloads from disk."""
    template = (PROMPTS_DIR / "marp.txt").read_text()
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
            "max_tokens": 4096,
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=120.0,
    )
    resp.raise_for_status()
    return resp.json()["content"][0]["text"].strip()


# ── Fence stripping ───────────────────────────────────────────────────────────


def strip_fences(text: str) -> str:
    """Remove accidental markdown code fences around the Marp output."""
    import re

    text = re.sub(r"^```(?:markdown|marp)?\s*\n?", "", text, flags=re.MULTILINE)
    text = re.sub(r"\n?```\s*$", "", text, flags=re.MULTILINE)
    return text.strip()


def ensure_frontmatter(text: str) -> str:
    """Guarantee the output starts with valid Marp frontmatter."""
    if text.startswith("---") and "marp: true" in text[:200]:
        return text
    frontmatter = "---\nmarp: true\ntheme: default\npaginate: true\n---\n\n"
    return frontmatter + text


# ── PDF export ────────────────────────────────────────────────────────────────


def export_pdf(md_path: Path) -> Path:
    pdf_path = md_path.with_suffix(".pdf")
    try:
        subprocess.run(
            ["marp", str(md_path), "--pdf", "--output", str(pdf_path)],
            check=True,
            capture_output=True,
        )
        print(f"PDF saved to {pdf_path}", file=sys.stderr)
        return pdf_path
    except FileNotFoundError:
        print("marp-cli not found. Install with: npm install -g @marp-team/marp-cli", file=sys.stderr)
        return md_path
    except subprocess.CalledProcessError as e:
        print(f"marp export failed: {e.stderr.decode()}", file=sys.stderr)
        return md_path


# ── Main ──────────────────────────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate Marp slide deck from context")
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--input", "-i", type=Path, default=DEFAULT_INPUT, help="Path to context file (default: data/resume.md)"
    )
    group.add_argument("--transcript", "-t", type=str, help="Inline transcript string")
    parser.add_argument(
        "--instructions", required=True, help="What to create (e.g. '5-slide sizzle reel, title + 3 content + CTA')"
    )
    parser.add_argument("--output", "-o", type=Path, help="Save Marp markdown to file (default: print to stdout)")
    parser.add_argument("--pdf", action="store_true", help="Also export to PDF via marp-cli (requires --output)")
    parser.add_argument("--backend", choices=["ollama", "anthropic"], default="ollama")
    parser.add_argument("--ollama-url", default=os.environ.get("OLLAMA_URL", "http://localhost:11434"))
    parser.add_argument("--ollama-model", default=os.environ.get("OLLAMA_MODEL", "gemma3:12b"))
    parser.add_argument("--anthropic-model", default="claude-sonnet-4-6")
    args = parser.parse_args()

    transcript = args.transcript if args.transcript else args.input.read_text()
    t0 = time.perf_counter()

    if args.backend == "ollama":
        prompt = load_prompt(transcript, args.instructions)
        print(f"[ollama] model={args.ollama_model}  prompt_chars={len(prompt)}", file=sys.stderr)
        result = call_ollama(prompt, args.ollama_model, args.ollama_url)
    else:
        prompt = load_prompt(transcript, args.instructions)
        print(f"[anthropic] model={args.anthropic_model}  prompt_chars={len(prompt)}", file=sys.stderr)
        result = call_anthropic(prompt, args.anthropic_model)

    result = ensure_frontmatter(strip_fences(result))
    elapsed = time.perf_counter() - t0
    slide_count = result.count("\n---\n")
    print(f"[done] {len(result)} chars  {slide_count} slide separators  {elapsed:.1f}s", file=sys.stderr)

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(result)
        print(f"Saved to {args.output}", file=sys.stderr)
        if args.pdf:
            export_pdf(args.output)
    else:
        print(result)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
