import type { Metadata } from "next";
import Link from "next/link";
import VoiceToMermaid from "@/components/VoiceToMermaid";

export const metadata: Metadata = {
  title: "Voice to Mermaid",
  description: "Describe a process out loud and watch a Mermaid diagram generate in real time.",
};

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:7625";
const WS_URL  = API_URL.replace(/^http/, "ws") + "/ws/mermaid";

export default function MermaidPage() {
  return (
    <main className="h-screen flex flex-col">
      <header className="px-5 py-3 border-b border-[#2a2a2e] flex items-center gap-3 bg-[#0d0d0f]">
        <Link href="https://datacrew.space/projects" className="font-mono text-xs text-[#666] hover:text-[#e2e2e5] transition-colors">
          ← projects
        </Link>
        <h1 className="text-sm font-semibold tracking-tight">Voice → Mermaid</h1>
        <span className="text-xs px-2 py-0.5 rounded-full bg-[#2a2a2e] text-[#888]">
          voice-to-mermaid
        </span>
      </header>
      <div className="flex-1 overflow-hidden">
        <VoiceToMermaid
          wsUrl={WS_URL}
          configUrl={`${API_URL}/v1/config`}
          theme="dark"
        />
      </div>
    </main>
  );
}
