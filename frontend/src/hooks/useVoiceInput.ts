import { useCallback, useRef, useState } from "react";
import { apiClient } from "@/api/client";
import { toWav16kMono } from "@/lib/audio";

const isSupported = (): boolean =>
  typeof navigator !== "undefined" &&
  !!navigator.mediaDevices?.getUserMedia &&
  typeof MediaRecorder !== "undefined";

const SILENCE_MS = 1500;
const MAX_MS = 30_000;
const QUIET_RMS = 0.015;

export type VoiceError = "mic-denied" | "transcribe-failed" | "nothing-heard" | null;

interface UseVoiceInput {
  supported: boolean;
  recording: boolean;
  transcribing: boolean;
  error: VoiceError;
  clearError: () => void;
  start: () => Promise<void>;
  stop: () => void;
}

export function useVoiceInput(
  onTranscript: (text: string) => void,
  getLanguage: () => string
): UseVoiceInput {
  const [recording, setRecording] = useState(false);
  const [transcribing, setTranscribing] = useState(false);
  const [error, setError] = useState<VoiceError>(null);

  const onTranscriptRef = useRef(onTranscript);
  const getLanguageRef = useRef(getLanguage);
  onTranscriptRef.current = onTranscript;
  getLanguageRef.current = getLanguage;

  const recorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const streamRef = useRef<MediaStream | null>(null);
  const stoppedRef = useRef(false);
  const audioCtxRef = useRef<AudioContext | null>(null);
  const meterTimerRef = useRef<number | null>(null);

  const clearError = useCallback(() => setError(null), []);

  const releaseStream = useCallback(() => {
    if (meterTimerRef.current !== null) {
      window.clearInterval(meterTimerRef.current);
      meterTimerRef.current = null;
    }
    if (audioCtxRef.current) {
      audioCtxRef.current.close().catch(() => undefined);
      audioCtxRef.current = null;
    }
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;
    recorderRef.current = null;
    chunksRef.current = [];
  }, []);

  const transcribeNow = useCallback(async () => {
    const mime = recorderRef.current?.mimeType || "audio/webm";
    const blob = new Blob(chunksRef.current, { type: mime });
    releaseStream();
    setRecording(false);
    if (!blob.size) {
      setError("nothing-heard");
      return;
    }
    setTranscribing(true);
    try {
      const wav = await toWav16kMono(blob);
      const formData = new FormData();
      formData.append("file", wav, "audio.wav");
      formData.append("language", getLanguageRef.current());

      const res = await apiClient.post<{ text: string }>("/assistant/transcribe/", formData, {
        headers: { "Content-Type": "multipart/form-data" },
      });
      const transcript = res.data?.text?.trim();
      if (transcript) {
        onTranscriptRef.current(transcript);
      } else {
        setError("nothing-heard");
      }
    } catch {
      setError("transcribe-failed");
    } finally {
      setTranscribing(false);
    }
  }, [releaseStream]);

  const finish = useCallback(() => {
    if (stoppedRef.current) return;
    stoppedRef.current = true;
    const rec = recorderRef.current;
    if (rec && rec.state !== "inactive") rec.stop();
    else void transcribeNow();
  }, [transcribeNow]);

  const start = useCallback(async () => {
    if (recorderRef.current) return;
    setError(null);
    stoppedRef.current = false;
    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch {
      setRecording(false);
      setError("mic-denied");
      return;
    }
    streamRef.current = stream;
    const recorder = new MediaRecorder(stream);
    chunksRef.current = [];

    recorder.ondataavailable = (e) => {
      if (e.data.size) chunksRef.current.push(e.data);
    };
    recorder.onstop = () => {
      void transcribeNow();
    };

    recorder.start(250);
    recorderRef.current = recorder;
    setRecording(true);

    try {
      const Ctx =
        window.AudioContext ??
        (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
      if (Ctx) {
        const ctx = new Ctx();
        audioCtxRef.current = ctx;
        const src = ctx.createMediaStreamSource(stream);
        const analyser = ctx.createAnalyser();
        analyser.fftSize = 1024;
        src.connect(analyser);
        const buf = new Float32Array(analyser.fftSize);
        let quietSince: number | null = null;
        const startedAt = Date.now();
        meterTimerRef.current = window.setInterval(() => {
          analyser.getFloatTimeDomainData(buf);
          let sum = 0;
          for (let i = 0; i < buf.length; i++) sum += buf[i] * buf[i];
          const rms = Math.sqrt(sum / buf.length);
          const now = Date.now();
          if (rms < QUIET_RMS) {
            if (quietSince === null) quietSince = now;
            else if (now - quietSince >= SILENCE_MS) void finish();
          } else {
            quietSince = null;
          }
          if (now - startedAt >= MAX_MS) void finish();
        }, 200);
      }
    } catch {
      // AudioContext fallback
    }
  }, [finish, releaseStream]);

  const stop = useCallback(() => {
    void finish();
  }, [finish]);

  return { supported: isSupported(), recording, transcribing, error, clearError, start, stop };
}
