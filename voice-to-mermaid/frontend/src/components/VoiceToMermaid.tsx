"use client";

/**
 * VoiceToMermaid — voice-to-diagram component.
 *
 * Left panel: editable textarea (source of truth for diagram content).
 *   - Voice segments append to the bottom, like typing.
 *   - User can freely edit, fix typos, or paste text directly.
 *   - Content persists in localStorage across page refreshes.
 *
 * Right panel: live Mermaid diagram, regenerated 1.5s after any content change.
 *
 * WS protocol:
 *   Client → server: binary audio chunks, {type:"config"}, {type:"generate", text, instructions}, {type:"clear"}
 *   Server → client: {type:"config"} handshake, {type:"transcript"} committed segment,
 *                    {type:"buffer"} in-progress text, {type:"diagram"}, {type:"processing"}, {type:"error"}
 */

import { useCallback, useEffect, useRef, useState } from "react";

type WsStatus    = "offline" | "connecting" | "ready";
type LlmMode     = "ollama" | "openai";
type OutputType  = "mermaid" | "marp";

// ── Detect output type ────────────────────────────────────────────────────────
function detectOutputType(code: string): OutputType {
  const t = code.trim();
  // Marp: YAML frontmatter OR slide separator  OR markdown headings without Mermaid keywords
  if (/^---[\r\n]/.test(t)) return "marp";
  if (/\n---\n/.test(t))    return "marp";
  // Starts with a markdown heading and has no Mermaid diagram keyword on the first line
  if (/^#/.test(t) && !/^(flowchart|graph|sequenceDiagram|classDiagram|stateDiagram|erDiagram|gantt|pie|mindmap|architecture)/.test(t)) return "marp";
  return "mermaid";
}

// ── Mermaid via CDN (avoids SSR issues) ───────────────────────────────────────
declare global {
  interface Window {
    mermaid?: {
      initialize: (cfg: object) => void;
      render: (id: string, code: string) => Promise<{ svg: string }>;
    };
  }
}

function useMermaid(theme: string) {
  const [ready, setReady] = useState(false);
  useEffect(() => {
    if (window.mermaid) { setReady(true); return; }
    const s = document.createElement("script");
    s.src = "https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js";
    s.onload = () => { window.mermaid!.initialize({ startOnLoad: false, theme }); setReady(true); };
    document.head.appendChild(s);
  }, [theme]);
  return ready;
}

// ── Marp via dynamic import (client-side only) ────────────────────────────────
type MarpInstance = { render: (md: string) => { html: string; css: string } };

function useMarp(): MarpInstance | null {
  const [marp, setMarp] = useState<MarpInstance | null>(null);
  useEffect(() => {
    import("@marp-team/marp-core").then(({ Marp }) => {
      setMarp(new Marp({ script: false }) as unknown as MarpInstance);
    }).catch(() => {});
  }, []);
  return marp;
}

// ── Types ─────────────────────────────────────────────────────────────────────

interface OllamaModel { id: string; label: string; vram_gb: number; notes: string; }
interface ServerConfig { ollama_url: string; ollama_model: string; ollama_models: OllamaModel[]; }

interface Props {
  wsUrl?:     string;
  configUrl?: string;
  theme?:     "dark" | "light";
}

const LS_EDITOR_KEY = "vtm-editor-text";

// ── Component ─────────────────────────────────────────────────────────────────

export default function VoiceToMermaid({
  wsUrl     = "ws://localhost:7625/ws/mermaid",
  configUrl = "http://localhost:7625/v1/config",
  theme     = "dark",
}: Props) {
  const mermaidReady = useMermaid(theme);
  const marp         = useMarp();

  // ── Core state ─────────────────────────────────────────────────────────────
  const [recording,   setRecording]   = useState(false);
  const [status,      setStatus]      = useState("Idle");
  const [loading,     setLoading]     = useState(false);
  const [loadingMsg,  setLoadingMsg]  = useState("Loading model…");
  const [wsStatus,    setWsStatus]    = useState<WsStatus>("offline");

  // Editor: single source of truth — voice appends here, user can freely edit
  const [editorText,  setEditorText]  = useState("");
  const [liveBuffer,  setLiveBuffer]  = useState(""); // in-progress voice, streamed into textarea

  // Diagram
  const [diagram,      setDiagram]     = useState("");
  const [diagramSvg,   setDiagramSvg]  = useState("");
  const lastGenIdRef   = useRef<string | null>(null);
  const [zoom,         setZoom]        = useState(1.0);
  const [pan,          setPan]         = useState({ x: 0, y: 0 });
  const diagramPanelRef = useRef<HTMLDivElement>(null);
  const isDragging      = useRef(false);
  const dragStart       = useRef({ x: 0, y: 0 });
  const panAtDragStart  = useRef({ x: 0, y: 0 });

  // Thinking stream — LLM reasoning shown in collapsible box while generating
  const [thinking,        setThinking]        = useState("");
  const [thinkingOpen,    setThinkingOpen]     = useState(false);
  const parseRetryRef     = useRef(0);  // cap auto-retries at 1

  // LLM settings — instructions sent with every generate request (not in WS config)
  const [llmMode,       setLlmMode]       = useState<LlmMode>("ollama");
  const [ollamaUrl,     setOllamaUrl]     = useState("http://localhost:11434");
  const [ollamaModel,   setOllamaModel]   = useState("qwen3:8b");
  const [ollamaModels,  setOllamaModels]  = useState<OllamaModel[]>([]);
  const [openaiKey,     setOpenaiKey]     = useState("");
  const [instructions,  setInstructions]  = useState("Generate a Mermaid flowchart diagram.");

  const wsRef          = useRef<WebSocket | null>(null);
  const recorderRef    = useRef<MediaRecorder | null>(null);
  const textareaRef    = useRef<HTMLTextAreaElement | null>(null);
  const editorTextRef  = useRef(editorText);
  useEffect(() => { editorTextRef.current = editorText; }, [editorText]);

  // ── localStorage persistence ───────────────────────────────────────────────
  useEffect(() => {
    const saved = localStorage.getItem(LS_EDITOR_KEY);
    if (saved) setEditorText(saved);
  }, []);

  useEffect(() => {
    localStorage.setItem(LS_EDITOR_KEY, editorText);
  }, [editorText]);

  // ── Load server config (retries every 5s until models are received) ──────────
  useEffect(() => {
    let cancelled = false;
    const tryFetch = () => {
      fetch(configUrl)
        .then(r => r.json())
        .then((cfg: ServerConfig) => {
          if (cancelled) return;
          setOllamaUrl(cfg.ollama_url ?? "http://localhost:11434");
          setOllamaModel(cfg.ollama_model ?? "qwen2.5:14b");
          setOllamaModels(cfg.ollama_models ?? []);
        })
        .catch(() => {
          if (!cancelled) setTimeout(tryFetch, 5000);
        });
    };
    tryFetch();
    return () => { cancelled = true; };
  }, [configUrl]);

  // ── Render diagram (Mermaid or Marp) ─────────────────────────────────────────
  useEffect(() => {
    if (!diagram) return;
    const type = detectOutputType(diagram);

    const sendRenderResult = (success: boolean, error: string | null) => {
      const ws = wsRef.current;
      const genId = lastGenIdRef.current;
      if (genId && ws?.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({
          type: "render_result", gen_id: genId,
          success, error, detected_type: type,
        }));
      }
    };

    if (type === "marp") {
      if (!marp) return;  // wait for Marp to load
      try {
        const { html, css } = marp.render(diagram);
        setDiagramSvg(
          `<style>${css}
           .marp-slides section { margin: 0 auto 1.5rem; box-shadow: 0 4px 24px rgba(0,0,0,.4); border-radius: 4px; overflow: hidden; display: block; }
           </style><div class="marp-slides">${html}</div>`
        );
        parseRetryRef.current = 0;
        sendRenderResult(true, null);
      } catch (err) {
        const errMsg = err instanceof Error ? err.message : String(err);
        setDiagramSvg(`<pre style="color:#e05454;white-space:pre-wrap;font-size:11px">Marp error:\n${errMsg}\n\n${diagram}</pre>`);
        sendRenderResult(false, errMsg);
      }
      return;
    }

    // Mermaid path
    if (!mermaidReady) return;
    window.mermaid!.render("vtm-svg-" + Date.now(), diagram)
      .then(({ svg }) => {
        setDiagramSvg(svg);
        parseRetryRef.current = 0;
        sendRenderResult(true, null);
      })
      .catch((err: unknown) => {
        const errMsg = err instanceof Error ? err.message : String(err);
        setDiagramSvg(`<pre style="color:#e05454;white-space:pre-wrap;font-size:11px">Parse error:\n${errMsg}\n\n${diagram}</pre>`);
        sendRenderResult(false, errMsg);
        if (parseRetryRef.current < 1 && wsRef.current?.readyState === WebSocket.OPEN) {
          parseRetryRef.current += 1;
          setStatus("Parse error — asking LLM to fix…");
          wsRef.current.send(JSON.stringify({
            type:            "generate",
            text:            editorTextRef.current,
            instructions:    instructionsRef.current +
              `\n\nThe previous diagram failed to parse with this error:\n${errMsg}\n\n` +
              `Common causes: parentheses () inside node labels, special characters in labels, ` +
              `or incorrect arrow syntax. Strip all () [] {} | > < from label text and retry. ` +
              `Return valid Mermaid code only — no fences, no explanation.`,
            current_diagram: diagram,
          }));
        }
      });
  }, [diagram, mermaidReady, marp]);

  // ── WS config — only LLM backend settings, NOT instructions ───────────────
  // Keeping instructions out of this chain prevents connectWs from being
  // recreated on every keystroke in the instructions field.
  const buildConfigMsg = useCallback(() => ({
    type:         "config",
    mode:         llmMode,
    ollama_url:   llmMode === "ollama" ? ollamaUrl   : null,
    ollama_model: llmMode === "ollama" ? ollamaModel : null,
    openai_key:   llmMode === "openai" ? openaiKey   : null,
  }), [llmMode, ollamaUrl, ollamaModel, openaiKey]);

  const sendConfig = useCallback((ws: WebSocket) => {
    if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(buildConfigMsg()));
  }, [buildConfigMsg]);

  useEffect(() => {
    if (wsRef.current) sendConfig(wsRef.current);
  }, [sendConfig]);

  // ── WebSocket connection ───────────────────────────────────────────────────
  const connectWs = useCallback((): Promise<WebSocket> => {
    setWsStatus("connecting");
    return new Promise((resolve, reject) => {
      const ws = new WebSocket(wsUrl);
      wsRef.current = ws;

      ws.onopen  = () => { sendConfig(ws); };
      ws.onclose = () => { setWsStatus("offline"); setRecording(false); setLoading(false); };
      ws.onerror = () => { setWsStatus("offline"); reject(new Error("WebSocket error")); };

      ws.onmessage = (ev) => {
        let msg: Record<string, unknown>;
        try { msg = JSON.parse(ev.data); } catch { return; }

        switch (msg.type) {
          case "config":
            setWsStatus("ready");
            setLoading(false);
            resolve(ws);
            break;

          case "processing":
            if (typeof msg.message === "string" &&
                (msg.message.startsWith("Loading") || msg.message.startsWith("Warming"))) {
              setLoading(true);
              setLoadingMsg(msg.message);
            } else {
              setLoading(false);
              setStatus((msg.message as string) ?? "");
            }
            break;

          case "buffer":
            setLiveBuffer((msg.text as string) ?? "");
            break;

          case "transcript": {
            // Voice segment committed — append to editor as a new line
            const text = (msg.text as string ?? "").trim();
            if (text) {
              setLiveBuffer("");
              setEditorText(prev => {
                if (prev === text || prev.endsWith("\n" + text)) return prev; // dedup
                return prev ? prev + "\n" + text : text;
              });
              requestAnimationFrame(() => {
                if (textareaRef.current) {
                  textareaRef.current.scrollTop = textareaRef.current.scrollHeight;
                }
              });
            }
            break;
          }

          case "thinking":
            setThinking((msg.text as string) ?? "");
            setThinkingOpen(true);
            break;

          case "diagram":
            setLoading(false);
            lastGenIdRef.current = (msg.gen_id as string) ?? null;
            setDiagram(msg.code as string);
            setZoom(1);             // reset zoom + pan for each new diagram
            setPan({ x: 0, y: 0 });
            setThinkingOpen(false); // auto-collapse when diagram arrives
            setStatus(`Updated · ${new Date().toLocaleTimeString()}`);
            break;

          case "error":
            setLoading(false);
            setStatus(`Error: ${msg.message}`);
            break;
        }
      };

      // Fallback resolve if server doesn't send config handshake
      ws.addEventListener("open", () => {
        setTimeout(() => {
          if (ws.readyState === WebSocket.OPEN) { setWsStatus("ready"); resolve(ws); }
        }, 3000);
      });
    });
  }, [wsUrl, sendConfig]);

  // ── Auto-connect / auto-reconnect ─────────────────────────────────────────
  const connectWsRef = useRef(connectWs);
  useEffect(() => { connectWsRef.current = connectWs; }, [connectWs]);

  useEffect(() => { connectWsRef.current().catch(() => {}); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (wsStatus !== "offline" || recording) return;
    const id = setTimeout(() => { connectWsRef.current().catch(() => {}); }, 3000);
    return () => clearTimeout(id);
  }, [wsStatus, recording]);

  // ── Diagram generation ─────────────────────────────────────────────────────
  // Instructions are sent with the generate request, not via the config message.
  // This keeps the dep chain clean and means instructions always reflect the
  // latest value at the moment of generation.
  const instructionsRef    = useRef(instructions);
  const diagramRef         = useRef(diagram);
  const lastGeneratedAtRef = useRef<number>(0);
  useEffect(() => { instructionsRef.current = instructions; }, [instructions]);
  useEffect(() => { diagramRef.current = diagram; }, [diagram]);

  const generateNow = useCallback(() => {
    const text = editorText.trim();
    if (!text) return;
    const ws = wsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      setWsStatus("offline");  // force reconnect cycle
      setStatus("Not connected — reconnecting…");
      return;
    }
    lastGeneratedAtRef.current = Date.now();
    setThinking("");
    ws.send(JSON.stringify({
      type:            "generate",
      text,
      instructions:    instructionsRef.current.trim(),
      current_diagram: diagramRef.current.trim() || null,
    }));
    setStatus("Generating diagram…");
  }, [editorText]);

  // Keep a ref so the debounce effect doesn't go stale
  const generateNowRef = useRef(generateNow);
  useEffect(() => { generateNowRef.current = generateNow; }, [generateNow]);

  // Auto-generate:
  //   • 1.5s after the user stops typing (idle debounce), OR
  //   • 300ms after a keystroke once 10s of combined activity has elapsed
  //     since the last generation — so actively typing content gets periodic updates.
  useEffect(() => {
    if (!editorText.trim()) return;
    const elapsed = Date.now() - lastGeneratedAtRef.current;
    const delay   = elapsed >= 10_000 ? 300 : 1500;
    const id      = setTimeout(() => { generateNowRef.current(); }, delay);
    return () => clearTimeout(id);
  }, [editorText]);

  // ── Recording controls ─────────────────────────────────────────────────────
  const startRecording = useCallback(async () => {
    // Always open a fresh WS for recording — FFmpeg inside WLK dies after idle
    // time, so reusing an open-but-stale connection produces silent failures.
    if (wsRef.current) {
      wsRef.current.onclose = null;  // suppress auto-reconnect on this close
      wsRef.current.close();
    }
    let ws: WebSocket;
    try {
      ws = await connectWs();
    } catch {
      setStatus("Cannot connect to server — is it running on port 7625?");
      return;
    }

    const stream = await navigator.mediaDevices.getUserMedia({ audio: true }).catch(() => {
      setStatus("Microphone access denied");
      return null;
    });
    if (!stream) return;
    const mime   = MediaRecorder.isTypeSupported("audio/webm;codecs=opus")
      ? "audio/webm;codecs=opus"
      : "audio/ogg;codecs=opus";

    const rec = new MediaRecorder(stream, { mimeType: mime, audioBitsPerSecond: 64000 });
    recorderRef.current = rec;

    rec.ondataavailable = (e) => {
      if (e.data.size > 0 && ws.readyState === WebSocket.OPEN) ws.send(e.data);
    };

    rec.start(250);
    setRecording(true);
    setStatus("Recording…");
  }, [connectWs]);

  const stopRecording = useCallback(() => {
    recorderRef.current?.stop();
    recorderRef.current?.stream.getTracks().forEach(t => t.stop());
    setRecording(false);
    // Commit any in-progress buffer text — WLK may not fire a silence boundary
    // before the stream ends, leaving transcribed text stranded in liveBuffer.
    // Delay flush so any in-flight transcript events arrive first and clear the buffer.
    // If transcript landed already, liveBuffer will be "" and nothing is committed.
    setTimeout(() => {
      setLiveBuffer(prev => {
        const text = prev.trim();
        if (text) {
          setEditorText(e => {
            if (e === text || e.endsWith("\n" + text)) return e; // dedup
            return e ? e + "\n" + text : text;
          });
          requestAnimationFrame(() => {
            if (textareaRef.current) textareaRef.current.scrollTop = textareaRef.current.scrollHeight;
          });
        }
        return "";
      });
    }, 1500);
    setStatus("Idle");
  }, []);

  // ── Other controls ─────────────────────────────────────────────────────────
  const clear = useCallback(() => {
    if (!window.confirm("Clear all transcript content and the current diagram?")) return;
    setEditorText("");
    setLiveBuffer("");
    setDiagram("");
    setDiagramSvg("");
    setZoom(1);
    setPan({ x: 0, y: 0 });
    setThinking("");
    setStatus("Idle");
    localStorage.removeItem(LS_EDITOR_KEY);
    wsRef.current?.send(JSON.stringify({ type: "clear" }));
  }, []);

  const copyDiagram = useCallback(() => {
    if (!diagram) return;
    navigator.clipboard.writeText("```mermaid\n" + diagram + "\n```");
    setStatus("Copied!");
  }, [diagram]);

  const selectedModel  = ollamaModels.find(m => m.id === ollamaModel);
  const displayStatus  = !recording && wsStatus !== "ready"
    ? ({ offline: "Server offline — retrying…", connecting: "Connecting…" } as const)[wsStatus]
    : status;

  // ── Styles ─────────────────────────────────────────────────────────────────
  const isDark   = theme === "dark";
  const bg       = isDark ? "bg-[#0f0f11]" : "bg-white";
  const border   = isDark ? "border-[#2a2a2e]" : "border-gray-200";
  const text     = isDark ? "text-[#e2e2e5]" : "text-gray-900";
  const muted    = isDark ? "text-[#666]" : "text-gray-500";
  const inputCls = isDark
    ? "bg-[#1a1a1e] border-[#333] text-[#e2e2e5] focus:border-indigo-500 focus:outline-none"
    : "bg-white border-gray-300 text-gray-900 focus:border-indigo-500 focus:outline-none";

  // ── Render ─────────────────────────────────────────────────────────────────
  return (
    <div className={`flex flex-col h-full ${bg} ${text} font-sans`}>

      {/* Loading overlay */}
      {loading && (
        <div className="fixed inset-0 z-50 flex flex-col items-center justify-center gap-5 bg-black/80 backdrop-blur-sm">
          <div className="w-12 h-12 rounded-full border-[3px] border-[#2a2a2e] border-t-indigo-500 animate-spin" />
          <p className="text-sm font-medium text-white">{loadingMsg}</p>
          <p className={`text-xs ${muted}`}>This only happens once per session</p>
        </div>
      )}

      {/* LLM mode bar */}
      <div className={`flex items-center gap-4 px-4 py-2 border-b ${border} text-xs ${muted} flex-wrap`}>
        <span className="uppercase tracking-widest">LLM</span>
        <div className={`flex rounded border ${border} overflow-hidden`}>
          {(["ollama", "openai"] as LlmMode[]).map(m => (
            <button key={m} onClick={() => setLlmMode(m)}
              className={`px-3 py-1 capitalize transition-colors ${
                llmMode === m
                  ? isDark ? "bg-[#2a2a2e] text-white" : "bg-gray-100 text-gray-900"
                  : "text-gray-500 hover:text-gray-300"
              }`}>
              {m}
            </button>
          ))}
        </div>

        {llmMode === "ollama" && (<>
          <input className={`px-2 py-1 rounded border text-xs ${inputCls} w-52`}
            value={ollamaUrl} onChange={e => setOllamaUrl(e.target.value)}
            placeholder="http://localhost:11434" />
          <select
            className={`px-2 py-1 rounded border text-xs ${inputCls} ${ollamaModels.length === 0 ? "opacity-50" : ""}`}
            value={ollamaModel}
            onChange={e => setOllamaModel(e.target.value)}
            disabled={ollamaModels.length === 0}
          >
            {ollamaModels.length === 0
              ? <option value="">Server unavailable…</option>
              : ollamaModels.map(m => (
                  <option key={m.id} value={m.id}>{m.label} — {m.notes}</option>
                ))
            }
          </select>
          {selectedModel && (
            <span className="px-2 py-0.5 rounded text-xs bg-emerald-950 text-emerald-400">
              ~{selectedModel.vram_gb} GB VRAM
            </span>
          )}
        </>)}

        {llmMode === "openai" && (
          <input type="password" className={`px-2 py-1 rounded border text-xs ${inputCls} w-64`}
            value={openaiKey} onChange={e => setOpenaiKey(e.target.value)}
            placeholder="sk-… OpenAI API key" />
        )}
      </div>

      {/* Main panels */}
      <div className="flex-1 grid grid-cols-2 overflow-hidden min-h-0">

        {/* Left: Editor */}
        <div className={`flex flex-col border-r ${border} overflow-hidden`}>
          <div className={`px-4 py-2 text-xs uppercase tracking-widest ${muted} border-b ${border} shrink-0 flex items-center justify-between`}>
            <span>Transcript</span>
            {recording && (
              <span className="flex items-center gap-1.5 text-red-400">
                <span className="w-1.5 h-1.5 rounded-full bg-red-400 animate-pulse" />
                Recording
              </span>
            )}
          </div>

          {/* Editable textarea — committed text + live buffer stream inline */}
          <textarea
            ref={textareaRef}
            className={`flex-1 resize-none p-4 text-sm leading-relaxed font-mono focus:outline-none ${
              isDark ? "bg-[#0f0f11] text-[#e2e2e5] placeholder-[#444]" : "bg-white text-gray-900 placeholder-gray-400"
            }`}
            placeholder={"Start dictating, or type / paste text here…\n\nVoice segments will appear as you speak."}
            value={editorText + (liveBuffer ? (editorText ? "\n" : "") + liveBuffer : "")}
            onChange={e => {
              // Strip live buffer suffix so user edits only affect committed text
              const full = e.target.value;
              const suffix = liveBuffer ? (editorText ? "\n" : "") + liveBuffer : "";
              if (suffix && full.endsWith(suffix)) {
                setEditorText(full.slice(0, full.length - suffix.length));
              } else {
                setEditorText(full);
              }
            }}
            spellCheck={false}
          />

          {/* Mermaid code editor — editable, auto-renders on change, sent as context */}
          <div className={`flex flex-col border-t ${border} shrink-0`} style={{ height: "200px" }}>
            <div className={`px-4 py-1.5 text-xs uppercase tracking-widest ${muted} flex items-center justify-between shrink-0`}>
              <span>Mermaid Code</span>
              {diagram && <span className={`text-xs normal-case ${muted}`}>edit to re-render instantly</span>}
            </div>
            <textarea
              className={`flex-1 resize-none px-4 py-2 text-xs font-mono focus:outline-none ${
                isDark ? "bg-[#0d0d10] text-[#c8c8d0] placeholder-[#444]" : "bg-gray-50 text-gray-700 placeholder-gray-400"
              }`}
              placeholder={"Mermaid code will appear here after generation…\nYou can also type or paste code directly."}
              value={diagram}
              onChange={e => setDiagram(e.target.value)}
              spellCheck={false}
            />
          </div>
        </div>

        {/* Right: Diagram */}
        <div className="flex flex-col overflow-hidden">
          <div className={`px-4 py-2 text-xs uppercase tracking-widest ${muted} border-b ${border} shrink-0 flex items-center justify-between`}>
            <span>Mermaid Diagram</span>
            {diagramSvg && (
              <div className="flex items-center gap-1">
                <button
                  onClick={() => setZoom(z => Math.max(0.25, +(z - 0.25).toFixed(2)))}
                  className={`w-6 h-6 flex items-center justify-center rounded text-sm ${muted} hover:text-current transition-colors`}
                  title="Zoom out"
                >−</button>
                <button
                  onClick={() => { setZoom(1); setPan({ x: 0, y: 0 }); }}
                  className={`px-2 py-0.5 rounded text-xs ${muted} hover:text-current transition-colors tabular-nums`}
                  title="Reset zoom and pan"
                >{Math.round(zoom * 100)}%</button>
                <button
                  onClick={() => setZoom(z => Math.min(4, +(z + 0.25).toFixed(2)))}
                  className={`w-6 h-6 flex items-center justify-center rounded text-sm ${muted} hover:text-current transition-colors`}
                  title="Zoom in"
                >+</button>
              </div>
            )}
          </div>
          <div
            ref={diagramPanelRef}
            className="flex-1 overflow-hidden relative"
            style={{ cursor: isDragging.current ? "grabbing" : "grab" }}
            onWheel={e => {
              e.preventDefault();
              const delta = e.deltaY * -0.001;
              setZoom(z => Math.min(4, Math.max(0.25, +(z + delta).toFixed(3))));
            }}
            onMouseDown={e => {
              if (e.button !== 0) return;
              isDragging.current = true;
              dragStart.current = { x: e.clientX, y: e.clientY };
              panAtDragStart.current = pan;
              // Force cursor update
              if (diagramPanelRef.current) diagramPanelRef.current.style.cursor = "grabbing";
            }}
            onMouseMove={e => {
              if (!isDragging.current) return;
              setPan({
                x: panAtDragStart.current.x + (e.clientX - dragStart.current.x),
                y: panAtDragStart.current.y + (e.clientY - dragStart.current.y),
              });
            }}
            onMouseUp={() => {
              isDragging.current = false;
              if (diagramPanelRef.current) diagramPanelRef.current.style.cursor = "grab";
            }}
            onMouseLeave={() => {
              isDragging.current = false;
              if (diagramPanelRef.current) diagramPanelRef.current.style.cursor = "grab";
            }}
          >
            {diagramSvg ? (
              <div
                style={{
                  position: "absolute",
                  top: "50%",
                  left: "50%",
                  transform: `translate(calc(-50% + ${pan.x}px), calc(-50% + ${pan.y}px)) scale(${zoom})`,
                  transformOrigin: "center center",
                  userSelect: "none",
                }}
                dangerouslySetInnerHTML={{ __html: diagramSvg }}
              />
            ) : (
              <p className={`text-sm ${muted} absolute inset-0 flex items-center justify-center`}
                style={{ cursor: "default" }}>
                Diagram will appear here as you speak or type…
              </p>
            )}
          </div>
        </div>
      </div>

      {/* Thinking box — collapsible, auto-opens while LLM is reasoning, auto-closes on diagram */}
      {thinking && (
        <div className={`border-t ${border} shrink-0`}>
          <button
            onClick={() => setThinkingOpen(o => !o)}
            className={`w-full flex items-center gap-2 px-4 py-1.5 text-xs ${muted} hover:text-current transition-colors text-left`}
          >
            <span className={`transition-transform ${thinkingOpen ? "rotate-90" : ""}`}>▶</span>
            <span className="uppercase tracking-widest">Thinking</span>
            {!thinkingOpen && <span className="italic truncate flex-1">{thinking.slice(0, 80)}…</span>}
          </button>
          {thinkingOpen && (
            <div className={`px-4 pb-3 text-xs font-mono whitespace-pre-wrap ${muted} max-h-40 overflow-y-auto`}>
              {thinking}
            </div>
          )}
        </div>
      )}

      {/* Instructions row */}
      <div className={`flex items-center gap-3 px-4 py-2 border-t ${border} shrink-0`}>
        <span className={`text-xs uppercase tracking-widest shrink-0 ${muted}`}>Instructions</span>
        <input
          className={`flex-1 px-3 py-1.5 rounded-md border text-sm focus:border-violet-500 ${inputCls}`}
          placeholder="e.g. 'show as a sequence diagram', 'focus on the auth flow'…"
          value={instructions}
          onChange={e => setInstructions(e.target.value)}
        />
      </div>

      {/* Controls */}
      <div className={`flex items-center gap-3 px-4 py-3 border-t ${border} shrink-0`}>
        <button
          onClick={recording ? stopRecording : startRecording}
          className={`px-4 py-2 rounded-md text-sm font-medium transition-colors ${
            recording
              ? "bg-red-500 hover:bg-red-600 text-white animate-pulse"
              : "bg-indigo-600 hover:bg-indigo-700 text-white"
          }`}
        >
          {recording ? "Stop dictating" : "Start dictating"}
        </button>

        <button
          onClick={generateNow}
          disabled={!editorText.trim()}
          className={`px-4 py-2 rounded-md text-sm font-medium transition-all active:scale-95 disabled:opacity-40 cursor-pointer disabled:cursor-default ${
            isDark ? "bg-violet-700 hover:bg-violet-500 text-white" : "bg-violet-600 hover:bg-violet-500 text-white"
          }`}
        >
          Generate
        </button>

        <button
          onClick={clear}
          className={`px-4 py-2 rounded-md text-sm transition-all active:scale-95 cursor-pointer ${
            isDark ? "bg-[#2a2a2e] hover:bg-[#3a3a3e] text-gray-300" : "bg-gray-100 hover:bg-gray-200 text-gray-600"
          }`}
        >
          Clear
        </button>

        <button
          onClick={copyDiagram}
          disabled={!diagram}
          className={`px-4 py-2 rounded-md text-sm transition-all active:scale-95 disabled:opacity-40 cursor-pointer disabled:cursor-default ${
            isDark ? "bg-[#2a2a2e] hover:bg-[#3a3a3e] text-gray-300" : "bg-gray-100 hover:bg-gray-200 text-gray-600"
          }`}
        >
          Copy
        </button>

        <span className="ml-auto flex items-center gap-2">
          <span className={`inline-block w-2 h-2 rounded-full ${
            wsStatus === "ready"      ? "bg-emerald-500" :
            wsStatus === "connecting" ? "bg-yellow-400 animate-pulse" :
                                        "bg-red-500"
          }`} title={{
            offline:    "Server offline — retrying…",
            connecting: "Connecting to server…",
            ready:      "Server connected",
          }[wsStatus]} />
          <span className={`text-xs ${muted}`}>{displayStatus}</span>
        </span>
      </div>
    </div>
  );
}
