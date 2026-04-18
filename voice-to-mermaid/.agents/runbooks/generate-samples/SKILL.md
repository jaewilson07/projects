---
name: generate-samples
description: >
  Generate sample Mermaid diagrams and Marp slide decks from context files
  (transcripts, resumes, meeting notes) to validate and tune prompts before
  wiring into the live streaming app. Use for: "test the mermaid prompt",
  "generate a marp sizzle reel", "prove out diagram generation".
metadata:
  version: 1.0.0
  created: 2026-04-14
---

# generate-samples

Validate and tune diagram/slide prompts against real context files.
The live app references these same prompts — edit here, test here, ship there.

## Directory layout

```
generate-samples/
├── data/
│   └── resume.md          ← sample context (Jae's resume)
├── prompts/
│   ├── mermaid.txt        ← Mermaid generation prompt  ← EDIT THIS
│   └── marp.txt           ← Marp generation prompt     ← EDIT THIS
└── scripts/
    ├── generate_mermaid.py
    └── generate_marp.py
```

The live backend at `backend/main.py` hot-reloads from `backend/prompts/`.
When these sample prompts are working well, copy them there.

---

## Generate a Mermaid diagram

```bash
# Career map from resume
python scripts/generate_mermaid.py \
  --input data/resume.md \
  --instructions "Map my career progression as a flowchart"

# Simulate a live meeting transcript
python scripts/generate_mermaid.py \
  --transcript "OAuth 2.0: user hits login, client redirects to auth server, user consents, auth server returns code, client exchanges for token, calls API" \
  --instructions "Generate a sequence diagram"

# Save to file
python scripts/generate_mermaid.py \
  --input data/resume.md \
  --instructions "career flowchart" \
  --output /tmp/career.mmd

# Use Anthropic instead of Ollama
python scripts/generate_mermaid.py \
  --input data/resume.md \
  --backend anthropic
```

---

## Generate a Marp slide deck

```bash
# 5-slide sizzle reel from resume
python scripts/generate_marp.py \
  --input data/resume.md \
  --instructions "5-slide sizzle reel: title, career snapshot, superpower, 3 impact wins, call to action"

# From a meeting transcript
python scripts/generate_marp.py \
  --transcript "Today we decided to rebuild the data pipeline using dbt and Snowflake..." \
  --instructions "Capture key decisions as a 3-slide summary deck"

# Save + export to PDF (requires marp-cli)
python scripts/generate_marp.py \
  --input data/resume.md \
  --instructions "5-slide sizzle reel" \
  --output data/EXPORTS/sizzle_reel.md \
  --pdf
```

---

## The intended live UX

User is in a Zoom meeting. They tell Letta:

> "Listen to me talk and generate a Mermaid diagram to complement the process I'm describing."

What happens:
1. WhisperLiveKit streams the meeting transcription in 250ms chunks
2. Each committed segment appends to an accumulating transcript
3. On each update, the app calls the LLM with:
   - `{instructions}` — what the user asked Letta for
   - `{transcript}` — the growing transcript
   - The prompt from `prompts/mermaid.txt` or `prompts/marp.txt` (loaded from disk)
4. The LLM returns updated Mermaid/Marp — rendered live in the browser

The `--watch` flag on `generate_mermaid.py` simulates this: it re-runs every 10s
so you can see how output evolves as you append text to the input file.

---

## Tuning workflow

1. Run a script against `data/resume.md`
2. Inspect the output — does it use the right diagram type? Are labels good?
3. Edit `prompts/mermaid.txt` or `prompts/marp.txt`
4. Re-run immediately (prompts are hot-reloaded from disk)
5. When happy, copy the prompt to `backend/prompts/` to update the live app

---

## Auth

- **Ollama** (default) — no API key needed, runs locally
- **Anthropic** — set `ANTHROPIC_API_KEY` env var

---

## Reference

| Resource | URL |
|---|---|
| Mermaid flowchart | https://mermaid.js.org/syntax/flowchart.html |
| Mermaid sequence | https://mermaid.js.org/syntax/sequenceDiagram.html |
| Mermaid architecture | https://mermaid.js.org/syntax/architecture.html |
| Marp main site | https://marp.app/ |
| Marpit directives | https://marpit.marp.app/ |
| Marp CLI | https://github.com/marp-team/marp-cli |
| Live Mermaid editor | https://mermaid.live |

---

## Related

- `backend/prompts/mermaid.txt` — prompt used by the live server (copy from here when ready)
- `backend/main.py` — standalone FastAPI backend (WhisperLiveKit + LLM)
- `alix/voice_adapter/meeting/server.py` — alix meeting server (port 7625)
- `alix/voice_adapter/meeting/tests/test_diagram_generation.py` — e2e OAuth test
