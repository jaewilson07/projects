---
name: start-voice-to-mermaid
description: >
  Start the local voice-to-mermaid backend with WhisperLiveKit enabled so the
  live datacrew.space/projects/mermaid site has voice input via Twingate.
  Use for: "start mermaid backend", "enable voice on mermaid", "whisper not working".
metadata:
  version: 1.0.0
  created: 2026-04-18
---

# start-voice-to-mermaid

Starts `libraries/projects/voice-to-mermaid/backend/` with `WHISPER_ENABLED=1`.
The VPS gateway proxies `/ws/mermaid` and `/v1/config` through Twingate to this process.

## Prerequisites

- Ollama running and bound to `0.0.0.0` (see `troubleshoot-ollama`)
- Twingate connector running (`troubleshoot-twingate-connector`)
- `mermaid` Twingate resource provisioned:
  ```bash
  python infrastructure/vps/setup/twingate/provision_mermaid.py --dry-run
  # Then without --dry-run to apply
  ```

## Start

```bash
cd libraries/projects/voice-to-mermaid/backend

# First run only — recreate .venv if shebangs point to old path
python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt

# Start with voice enabled
WHISPER_ENABLED=1 .venv/bin/uvicorn main:app --host 0.0.0.0 --port 7625 \
  --log-level info > /tmp/vtm-backend.log 2>&1 &

sleep 3 && curl http://localhost:7625/health
```

## Verify end-to-end via Twingate

```bash
# From VPS (or any Twingate-connected machine):
curl http://mermaid.jaewilson07.twingate.com:7625/health
curl http://mermaid.jaewilson07.twingate.com:7625/v1/config | python3 -m json.tool
# stt_enabled should be true
```

## Verify gateway routes it correctly

```bash
# From VPS (internal):
curl http://gateway:7630/v1/config | python3 -m json.tool
# stt_enabled should be true
```

## Stop

```bash
fuser -k 7625/tcp
```

## Related Skills

- `troubleshoot-twingate-connector` — fix Twingate connector issues
- `troubleshoot-ollama` — fix Ollama bind address
- `start-all-services` — start full local Docker stack
