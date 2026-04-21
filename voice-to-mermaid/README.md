# voice-to-mermaid

Describe a process out loud → get a live Mermaid diagram.

**Stack:** FastAPI + faster-whisper (backend) · Next.js 15 + Tailwind (frontend)
**Audio:** Browser MediaRecorder → WebSocket → FFmpeg → Whisper
**LLM:** Ollama (local, default) or any OpenAI-compatible API

---

## Prerequisites

- Docker + Docker Compose (for the backend)
- Node.js 18+ (for the frontend)
- FFmpeg — installed automatically inside Docker; for local dev: `apt install ffmpeg`
- An Ollama model: `ollama pull qwen2.5:14b` (or any model in the picker)

---

## Quick Start

### 1. Backend

```bash
cd backend
cp .env.example .env
# Edit .env — set OLLAMA_MODEL to a model you have pulled
docker-compose up
```

The API starts at **http://localhost:7625**.
Health check: `curl http://localhost:7625/health`

> **No GPU?** Remove the `deploy.resources` block from `docker-compose.yml`.
> Whisper will run on CPU (slower but works).

### 2. Frontend

```bash
cd frontend
cp .env.example .env.local
# NEXT_PUBLIC_API_URL defaults to http://localhost:7625 — no changes needed for dev
npm install
npm run dev
```

Open **http://localhost:3000/mermaid**.

---

## How It Works

```
Browser (MediaRecorder)
  │  3-second WebM audio chunks
  ▼
WebSocket /ws/mermaid
  │
  ├─ FFmpeg → 16kHz mono WAV
  ├─ faster-whisper → transcript text
  └─ Ollama / OpenAI → Mermaid code
        │
        ▼
  Browser renders diagram with mermaid.js
```

---

## Configuration

### `backend/config.yaml` — non-secret settings

Edit this file to change model names, Whisper settings, prompt path, and model filter.
It is volume-mounted in Docker, so changes take effect on restart without rebuilding.

| Key | Default | Description |
|---|---|---|
| `ollama.default_model` | `qwen3:8b` | Ollama model for diagram generation |
| `ollama.model_filter` | _(list)_ | Model name prefixes shown in the UI picker |
| `openai.model` | `gpt-4o-mini` | OpenAI-compatible model name |
| `whisper.enabled` | `false` | Set `true` after installing `requirements.stt.txt`; graceful no-op if import fails |
| `whisper.model` | `medium.en` | Whisper model size |
| `whisper.device` | `auto` | `auto` / `cpu` / `cuda` |
| `paths.prompt` | `prompts/mermaid.txt` | Path to LLM prompt template |
| `paths.log_dir` | `data/logs` | Directory for generation logs |

### Backend (`backend/.env`) — secrets only

| Variable | Default | Description |
|---|---|---|
| `OLLAMA_URL` | `http://ollama:11434` (Docker Compose) | Ollama server URL; use `http://host.docker.internal:11434` for a host-resident Ollama |
| `OPENAI_BASE_URL` | _(empty)_ | OpenAI-compatible API base (optional) |
| `OPENAI_API_KEY` | _(empty)_ | API key for OpenAI-compatible API |
| `API_KEY` | _(empty)_ | If set, require `X-Api-Key` header on all requests |
| `PORT` | `7625` | Server port |

### Frontend (`frontend/.env.local`)

| Variable | Default | Description |
|---|---|---|
| `NEXT_PUBLIC_API_URL` | `http://localhost:7625` | Backend URL (baked into browser bundle) |
| `API_KEY` | _(empty)_ | Server-side proxy secret (never exposed to browser) |

---

## Customising the Prompt

Edit `backend/prompts/mermaid.txt`. The `{transcript}` placeholder is replaced
with the accumulated spoken text. Changes take effect immediately — no restart
needed (Docker mounts the file as a volume).

Example customisation for software architecture diagrams:

```
Convert the following spoken description into a Mermaid C4 or sequenceDiagram.
Prefer sequenceDiagram for service interactions.
Return ONLY raw Mermaid code — no fences, no explanation.

Description:
{transcript}
```

---

## Production Deployment

For a public deployment with a private backend:

1. Set `API_KEY` to a strong random secret in both `backend/.env` and `frontend/.env.local`
2. The Next.js proxy (`/api/mermaid/*`) adds `X-Api-Key` server-side — the key never reaches the browser
3. Put the backend behind a VPN or firewall (e.g. Twingate) for an extra network layer
4. Set `NEXT_PUBLIC_API_URL` to your backend's internal URL via Cloudflare Pages / Vercel env vars

---

## Architecture

```
voice-to-mermaid/                    (jaewilson07/projects repo)
├── README.md
├── backend/
│   ├── main.py              FastAPI app — zero external deps
│   ├── prompts/mermaid.txt  editable LLM prompt
│   ├── .env.example
│   ├── requirements.txt
│   ├── Dockerfile
│   └── docker-compose.yml
└── frontend/
    └── src/
        ├── components/
        │   └── VoiceToMermaid.tsx   generic React component
        └── app/mermaid/
            └── page.tsx             standalone Next.js page
```
