"""voice-to-mermaid — standalone FastAPI backend.

Zero dependencies on any external project. Fully self-contained.

Endpoints:
  GET  /health
  GET  /v1/config       — returns model list and current settings
  WS   /ws/mermaid      — streaming pipeline: audio chunks → transcript → diagram

WS protocol (client → server):
  Binary blob                         Raw audio from MediaRecorder (WebM/Opus, 250ms chunks)
  {"type": "config", ...}             LLM settings: mode, ollama_url, ollama_model, openai_key
  {"type": "generate", text, ...}     Client-driven diagram generation (full editor content)
  {"type": "render_result", ...}      Client reports render success/failure for logging
  {"type": "clear"}                   Reset transcript and diagram

WS protocol (server → client):
  {"type": "config", "useAudioWorklet": false}  WLK handshake
  {"type": "processing", "message": "..."}      Status updates
  {"type": "buffer",     "text": "..."}         Live in-progress transcription
  {"type": "transcript", "text": "..."}         Committed segment
  {"type": "thinking",   "text": "..."}         LLM reasoning tokens (streaming)
  {"type": "diagram",    "code": "...", "gen_id": "..."}  Updated diagram
  {"type": "error",      "message": "..."}      Errors
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
import uuid
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# ── Config (config.yaml for settings, .env for secrets) ──────────────────────

# config.yaml lives next to this file in source; /app/config.yaml in Docker
_CONFIG_PATH = Path(__file__).parent / "config.yaml"


def _load_config() -> dict:
    try:
        import yaml  # type: ignore[import-untyped]
        raw = yaml.safe_load(_CONFIG_PATH.read_text())
        if not isinstance(raw, dict):
            log.warning("config.yaml is not a mapping — using defaults")
            return {}
        return raw
    except FileNotFoundError:
        log.warning("config.yaml not found — using defaults")
        return {}
    except Exception as exc:
        log.warning("Failed to load config.yaml: %s", exc)
        return {}


_cfg = _load_config()

# Secrets — env only
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434").rstrip("/")
OPENAI_URL = os.environ.get("OPENAI_BASE_URL", "").rstrip("/")
OPENAI_KEY = os.environ.get("OPENAI_API_KEY", "")

# Settings — config.yaml only; edit config.yaml to change these
OLLAMA_MODEL = _cfg.get("ollama", {}).get("default_model", "qwen3:8b")
OPENAI_MODEL = _cfg.get("openai", {}).get("model", "gpt-4o-mini")
WHISPER_ENABLED = _cfg.get("whisper", {}).get("enabled", False)
WHISPER_MODEL = _cfg.get("whisper", {}).get("model", "medium.en")
WHISPER_LANG = _cfg.get("whisper", {}).get("language", "en")
WHISPER_DEVICE = _cfg.get("whisper", {}).get("device", "auto")
PROMPT_PATH = Path(_cfg.get("paths", {}).get("prompt", "prompts/mermaid.txt"))
LOG_DIR = Path(_cfg.get("paths", {}).get("log_dir", "data/logs"))
_MODEL_FILTER: list[str] = [str(p) for p in (_cfg.get("ollama", {}).get("model_filter") or [])]

_ollama_models_cache: list[dict] | None = None


async def _fetch_ollama_models() -> list[dict]:
    """Fetch installed models from Ollama /api/tags, optionally filtered by prefix."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{OLLAMA_URL}/api/tags")
            resp.raise_for_status()
            data = resp.json()
            models = data.get("models", [])
            if _MODEL_FILTER:
                models = [m for m in models if any(m["name"].startswith(p) for p in _MODEL_FILTER)]
            return [{"id": m["name"], "label": m["name"]} for m in models]
    except Exception:
        return []

# ── Generation logger — JSONL ─────────────────────────────────────────────────

_GEN_LOG: Path | None = None
_pending_entries: dict[str, dict] = {}


def _init_log_file() -> None:
    global _GEN_LOG
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    _GEN_LOG = LOG_DIR / "diagram_generations.jsonl"


def _log_start(gen_id: str, *, instructions: str, transcript: str, model: str, current_diagram: str) -> None:
    _pending_entries[gen_id] = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "gen_id": gen_id,
        "instructions": instructions,
        "transcript_preview": transcript[:300],
        "transcript_chars": len(transcript),
        "current_diagram_chars": len(current_diagram),
        "model": model,
        "llm_ms": None,
        "raw_output": None,
        "output_chars": None,
        "render_result": None,
        "render_error": None,
        "detected_type": None,
    }


def _log_output(gen_id: str, *, raw_output: str, llm_ms: int) -> None:
    entry = _pending_entries.get(gen_id)
    if entry:
        entry["raw_output"] = raw_output
        entry["output_chars"] = len(raw_output)
        entry["llm_ms"] = llm_ms


def _log_render(gen_id: str, *, success: bool, error: str | None, detected_type: str | None) -> None:
    entry = _pending_entries.pop(gen_id, None)
    if entry is None or _GEN_LOG is None:
        return
    entry["render_result"] = "success" if success else "error"
    entry["render_error"] = error
    entry["detected_type"] = detected_type
    with _GEN_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


# ── LLM helpers ───────────────────────────────────────────────────────────────


def _load_prompt(transcript: str, instructions: str = "", current_diagram: str = "") -> str:
    """Load prompt from PROMPT_PATH; use .replace() to avoid KeyError on {} in content."""
    try:
        template = PROMPT_PATH.read_text()
    except FileNotFoundError:
        template = (
            "## Task\n{instructions}\n\n"
            "Return ONLY the raw Mermaid code — no markdown fences, no explanation.\n\n"
            "## Current output (refine if exists, otherwise generate fresh)\n{current_diagram}\n\n"
            "## Description / transcript\n{transcript}"
        )
    return (
        template.replace("{instructions}", instructions or "Generate a Mermaid flowchart diagram.")
        .replace("{current_diagram}", current_diagram or "None — generate fresh.")
        .replace("{transcript}", transcript)
    )


def _clean_output(text: str) -> str:
    """Strip <think> blocks and outer markdown fences. Preserve Marp/markdown content."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    # Detect Marp / plain markdown — leave as-is (fences inside slides are content)
    if re.match(r"^---[\r\n]", text) or "\n---\n" in text or re.match(r"^#", text):
        return text
    text = re.sub(r"^```(?:mermaid)?\s*\n?", "", text)
    text = re.sub(r"\n?```\s*$", "", text)
    return text.strip()


async def _to_mermaid(
    transcript: str,
    mode: str = "ollama",
    ollama_url: str | None = None,
    ollama_model: str | None = None,
    openai_key: str | None = None,
    instructions: str = "",
    current_diagram: str = "",
    on_thinking: "Callable[[str], Awaitable[None]] | None" = None,
) -> str:
    prompt = _load_prompt(transcript, instructions=instructions, current_diagram=current_diagram)

    if mode == "openai":
        key = openai_key or OPENAI_KEY
        base_url = (OPENAI_URL or "https://api.openai.com/v1").rstrip("/")
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{base_url}/chat/completions",
                headers={"Authorization": f"Bearer {key}"},
                json={"model": OPENAI_MODEL, "messages": [{"role": "user", "content": prompt}], "temperature": 0.3},
            )
            resp.raise_for_status()
            return _clean_output(resp.json()["choices"][0]["message"]["content"])

    # Ollama — streaming with optional thinking support
    base = (ollama_url or OLLAMA_URL).rstrip("/").removesuffix("/v1")
    model = ollama_model or OLLAMA_MODEL
    endpoint = f"{base}/api/generate"
    # Try with think=True; fall back without it for models that don't support thinking (400)
    for use_think in (True, False):
        payload: dict = {"model": model, "prompt": prompt, "stream": True}
        if use_think:
            payload["think"] = True
        async with httpx.AsyncClient(timeout=300.0) as client:
            async with client.stream("POST", endpoint, json=payload) as resp:
                if resp.status_code == 400 and use_think:
                    continue  # retry without think
                resp.raise_for_status()
                full = ""
                think_buf = ""
                think_sent = 0
                async for line in resp.aiter_lines():
                    if not line:
                        continue
                    try:
                        chunk = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    full += chunk.get("response", "")
                    think_buf += chunk.get("thinking", "")
                    if on_thinking and len(think_buf) > think_sent + 80:
                        think_sent = len(think_buf)
                        await on_thinking(think_buf)
                    if chunk.get("done"):
                        if on_thinking and think_buf and len(think_buf) > think_sent:
                            await on_thinking(think_buf)
                        break
                return _clean_output(full)
    return ""  # unreachable — raise_for_status would have thrown


# ── App + lifespan ────────────────────────────────────────────────────────────

_transcription_engine = None


@asynccontextmanager
async def _lifespan(app: FastAPI):
    global _transcription_engine
    _init_log_file()
    if WHISPER_ENABLED:
        try:
            from whisperlivekit import TranscriptionEngine
            from whisperlivekit.config import WhisperLiveKitConfig
        except ImportError:
            log.warning("whisper.enabled=true but whisperlivekit not installed — STT disabled. Install requirements.stt.txt to enable.")
            yield
            return

        log.info("Loading WhisperLiveKit  model=%s  lang=%s  device=%s", WHISPER_MODEL, WHISPER_LANG, WHISPER_DEVICE)
        _transcription_engine = TranscriptionEngine(
            config=WhisperLiveKitConfig(
                model_size=WHISPER_MODEL,
                lan=WHISPER_LANG,
                backend=WHISPER_DEVICE,
                backend_policy="simulstreaming",
                vad=True,
                vac=False,
                confidence_validation=True,
                min_chunk_size=0.5,
                init_prompt="Transcribe the following speech accurately.",
                pcm_input=False,
            )
        )
        log.info("WhisperLiveKit ready")
    else:
        log.info("STT disabled (whisper.enabled=false) — audio transcription unavailable")
    yield
    log.info("Server shutting down")


app = FastAPI(title="voice-to-mermaid", version="3.0.0", lifespan=_lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3750",
        "http://127.0.0.1:3750",  # standalone dev
        "http://localhost:3701",
        "http://127.0.0.1:3701",  # datacrew website dev
        "https://datacrew.space",  # production
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── REST endpoints ────────────────────────────────────────────────────────────


@app.get("/health")
async def health():
    return {"ok": True}


@app.get("/v1/config")
async def config():
    global _ollama_models_cache
    if _ollama_models_cache is None:
        _ollama_models_cache = await _fetch_ollama_models()
    return {
        "ollama_url": OLLAMA_URL,
        "ollama_model": OLLAMA_MODEL,
        "ollama_models": _ollama_models_cache,
        "openai_model": OPENAI_MODEL,
        "stt_enabled": WHISPER_ENABLED,
        "whisper_model": WHISPER_MODEL if WHISPER_ENABLED else None,
    }


@app.post("/v1/models/refresh")
async def refresh_models():
    global _ollama_models_cache
    _ollama_models_cache = await _fetch_ollama_models()
    return {"ollama_models": _ollama_models_cache}


# ── WebSocket — streaming pipeline ───────────────────────────────────────────


@app.websocket("/ws/mermaid")
async def ws_mermaid(websocket: WebSocket):
    if WHISPER_ENABLED:
        from whisperlivekit import AudioProcessor

    await websocket.accept()

    llm_mode: str = "ollama"
    llm_ollama_url: str | None = None
    llm_ollama_model: str | None = None
    llm_openai_key: str | None = None
    llm_instructions: str = ""

    _diagram_generating: bool = False
    _diagram_queued: bool = False

    async def _bg_diagram(transcript: str, m: str, ou, om, ok, instr: str = "", current_diagram: str = "") -> None:
        nonlocal _diagram_generating, _diagram_queued
        _diagram_generating = True
        gen_id = str(uuid.uuid4())
        model = om or OLLAMA_MODEL
        _log_start(gen_id, instructions=instr, transcript=transcript, model=model, current_diagram=current_diagram)
        try:
            await websocket.send_json({"type": "processing", "message": "Generating diagram…"})

            async def _on_thinking(text: str) -> None:
                await websocket.send_json({"type": "thinking", "text": text})

            t0 = time.perf_counter()
            try:
                diagram_code = await _to_mermaid(
                    transcript,
                    mode=m,
                    ollama_url=ou,
                    ollama_model=om,
                    openai_key=ok,
                    instructions=instr,
                    current_diagram=current_diagram,
                    on_thinking=_on_thinking,
                )
            except Exception as exc:
                log.error("LLM error (mode=%s): %s", m, exc)
                await websocket.send_json({"type": "error", "message": f"LLM error: {exc}"})
                _log_render(gen_id, success=False, error=str(exc), detected_type=None)
                return
            llm_ms = int((time.perf_counter() - t0) * 1000)
            _log_output(gen_id, raw_output=diagram_code, llm_ms=llm_ms)
            if diagram_code:
                log.info("diagram  took=%dms  chars=%d", llm_ms, len(diagram_code))
                await websocket.send_json({"type": "diagram", "code": diagram_code, "gen_id": gen_id})
            else:
                _log_render(gen_id, success=False, error="empty output", detected_type=None)
        finally:
            _diagram_generating = False
            if _diagram_queued:
                _diagram_queued = False
                asyncio.create_task(_bg_diagram(" ".join(transcript_lines), m, ou, om, ok, instr))

    async def _consume_results(results_generator, line_count_ref: list[int]) -> None:
        _last_emitted = ""
        try:
            async for front_data in results_generator:
                lines = front_data.lines or []
                n = len(lines)

                if n > 0 and not lines[-1].is_silence():
                    committed_n = n - 1
                    in_progress = (lines[-1].text or "").strip()
                else:
                    committed_n = n
                    in_progress = ""

                buf = in_progress or (front_data.buffer_transcription or "")
                if buf:
                    await websocket.send_json({"type": "buffer", "text": buf})

                for seg in lines[line_count_ref[0] : committed_n]:
                    text = (seg.text or "").strip()
                    if not text or seg.is_silence():
                        continue
                    if text == _last_emitted:
                        continue
                    _last_emitted = text
                    transcript_lines.append(text)
                    await websocket.send_json({"type": "transcript", "text": text})

                line_count_ref[0] = max(line_count_ref[0], committed_n)
        except Exception as exc:
            log.error("Results consumer error: %s", exc, exc_info=True)

    # Handshake
    await websocket.send_json({"type": "config", "useAudioWorklet": False})
    ready_msg = "Ready — speak now…" if WHISPER_ENABLED else "Ready — type or paste text to generate a diagram"
    await websocket.send_json({"type": "processing", "message": ready_msg})

    transcript_lines: list[str] = []
    audio_processor = None
    results_task = None
    line_count_ref = [0]

    if WHISPER_ENABLED:
        audio_processor = AudioProcessor(transcription_engine=_transcription_engine)
        results_generator = await audio_processor.create_tasks()
        results_task = asyncio.create_task(_consume_results(results_generator, line_count_ref))

    try:
        while True:
            msg = await websocket.receive()

            if msg.get("type") == "websocket.disconnect":
                break

            if "text" in msg:
                try:
                    data = json.loads(msg["text"])
                    t = data.get("type")

                    if t == "clear":
                        transcript_lines.clear()
                        line_count_ref[0] = 0

                    elif t == "config":
                        llm_mode = data.get("mode", "ollama")
                        llm_ollama_url = data.get("ollama_url") or None
                        llm_ollama_model = data.get("ollama_model") or None
                        llm_openai_key = data.get("openai_key") or None
                        if "instructions" in data:
                            llm_instructions = data.get("instructions") or ""

                    elif t == "generate":
                        gen_text = (data.get("text") or "").strip()
                        gen_instr = (data.get("instructions") or llm_instructions or "").strip()
                        gen_current = (data.get("current_diagram") or "").strip()
                        if gen_text:
                            asyncio.create_task(
                                _bg_diagram(
                                    gen_text,
                                    llm_mode,
                                    llm_ollama_url,
                                    llm_ollama_model,
                                    llm_openai_key,
                                    gen_instr,
                                    current_diagram=gen_current,
                                )
                            )

                    elif t == "render_result":
                        rid = data.get("gen_id") or ""
                        ok = bool(data.get("success"))
                        err = data.get("error") or None
                        dtype = data.get("detected_type") or None
                        if rid:
                            _log_render(rid, success=ok, error=err, detected_type=dtype)

                    elif t == "text":
                        typed = (data.get("text") or "").strip()
                        if typed:
                            transcript_lines.append(typed)

                except Exception:
                    pass
                continue

            if WHISPER_ENABLED and audio_processor:
                audio_bytes = msg.get("bytes")
                if audio_bytes:
                    await audio_processor.process_audio(audio_bytes)

    except WebSocketDisconnect:
        log.info("Client disconnected")
    finally:
        if WHISPER_ENABLED and audio_processor:
            await audio_processor.process_audio(b"")
            if results_task and not results_task.done():
                results_task.cancel()
            if results_task:
                try:
                    await results_task
                except asyncio.CancelledError:
                    pass
            await audio_processor.cleanup()
