"use client";

import clsx from "clsx";
import { Mic, Square, Volume2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";

/* eslint-disable @typescript-eslint/no-explicit-any */
type Recognition = any;

function getRecognition(): (new () => Recognition) | null {
  if (typeof window === "undefined") return null;
  const w = window as any;
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null;
}

let serverConfig: Promise<{ server_transcription: boolean }> | null = null;
function voiceConfig() {
  serverConfig ??= api<{ server_transcription: boolean }>("/api/voice/config").catch(() => ({ server_transcription: false }));
  return serverConfig;
}

/**
 * Dictation button. Uses the browser's speech recognition when available (in Chrome this sends audio to
 * the browser vendor's service), otherwise records audio and sends it to Trio's server transcription if
 * the server has a voice provider configured.
 */
export function VoiceInput({ onText, className }: { onText: (text: string) => void; className?: string }) {
  const [mode, setMode] = useState<"browser" | "server" | "none" | null>(null);
  const [listening, setListening] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const recRef = useRef<Recognition | null>(null);
  const mediaRef = useRef<MediaRecorder | null>(null);

  useEffect(() => {
    let live = true;
    if (getRecognition()) {
      Promise.resolve().then(() => live && setMode("browser"));
    } else {
      voiceConfig().then((c) => live && setMode(c.server_transcription && typeof MediaRecorder !== "undefined" ? "server" : "none"));
    }
    return () => {
      live = false;
    };
  }, []);

  async function start() {
    setError(null);
    if (mode === "browser") {
      const R = getRecognition()!;
      const rec = new R();
      rec.lang = navigator.language || "en-US";
      rec.interimResults = false;
      rec.continuous = false;
      rec.onresult = (e: any) => onText(Array.from(e.results as ArrayLike<any>).map((r: any) => r[0].transcript).join(" "));
      rec.onerror = (e: any) => setError(e.error === "not-allowed" ? "Microphone permission was denied." : "Dictation failed.");
      rec.onend = () => setListening(false);
      recRef.current = rec;
      rec.start();
      setListening(true);
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const rec = new MediaRecorder(stream);
      const chunks: Blob[] = [];
      rec.ondataavailable = (e) => chunks.push(e.data);
      rec.onstop = async () => {
        stream.getTracks().forEach((t) => t.stop());
        setListening(false);
        const blob = new Blob(chunks, { type: rec.mimeType.split(";")[0] || "audio/webm" });
        const form = new FormData();
        form.append("file", blob, "speech.webm");
        const res = await fetch("/api/voice/transcribe", { method: "POST", body: form, credentials: "same-origin" });
        if (res.ok) onText((await res.json()).text);
        else setError("Transcription failed.");
      };
      mediaRef.current = rec;
      rec.start();
      setListening(true);
    } catch {
      setError("Microphone permission was denied.");
    }
  }

  function stop() {
    recRef.current?.stop();
    mediaRef.current?.stop();
  }

  if (mode === null || mode === "none") return null;
  return (
    <>
      <button
        type="button"
        onClick={() => (listening ? stop() : void start())}
        aria-label={listening ? "Stop dictation" : "Dictate"}
        aria-pressed={listening}
        title={mode === "browser" ? "Dictate (uses your browser's speech recognition)" : "Dictate (transcribed by the server)"}
        className={clsx(className, listening && "text-danger")}
        data-testid="voice-input"
      >
        {listening ? <Square size={16} /> : <Mic size={16} />}
      </button>
      {error && (
        <span role="alert" className="text-xs text-danger">
          {error}
        </span>
      )}
    </>
  );
}

export function speakableText(markdown: string) {
  return markdown
    .replace(/```[\s\S]*?```/g, " (code omitted) ")
    .replace(/`([^`]+)`/g, "$1")
    .replace(/!\[[^\]]*\]\([^)]*\)/g, "")
    .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1")
    .replace(/[#*_>|~-]{1,}/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

/** Read a reply aloud with the browser's speech synthesis (no server call). */
export function ReadAloud({ markdown, className }: { markdown: string; className?: string }) {
  const [speaking, setSpeaking] = useState(false);
  const [supported] = useState(() => typeof window !== "undefined" && "speechSynthesis" in window);
  useEffect(() => () => {
    if (speaking) window.speechSynthesis.cancel();
  }, [speaking]);
  if (!supported) return null;
  return (
    <button
      className={clsx(className, speaking && "text-accent")}
      aria-label={speaking ? "Stop reading aloud" : "Read aloud"}
      aria-pressed={speaking}
      onClick={() => {
        const synth = window.speechSynthesis;
        if (speaking) {
          synth.cancel();
          setSpeaking(false);
          return;
        }
        synth.cancel();
        const u = new SpeechSynthesisUtterance(speakableText(markdown));
        u.onend = () => setSpeaking(false);
        u.onerror = () => setSpeaking(false);
        synth.speak(u);
        setSpeaking(true);
      }}
    >
      <Volume2 size={14} />
    </button>
  );
}
