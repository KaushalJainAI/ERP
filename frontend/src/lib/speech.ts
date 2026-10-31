// Spoken read-back of assistant summaries via the browser's speechSynthesis.
// No TTS provider is named in planning/ or assistant_plan.md, so the browser voice is used.

/** App language code to a BCP-47 tag for the speech voice. Unknown codes fall back to English. */
const SPEECH_LANG: Record<string, string> = {
  en: "en-IN",
  hi: "hi-IN",
  ta: "ta-IN",
  pa: "pa-IN",
  gu: "gu-IN",
  or: "or-IN",
  bn: "bn-IN",
  kn: "kn-IN",
  as: "as-IN",
  te: "te-IN",
  ml: "ml-IN",
  mr: "mr-IN",
  ur: "ur-IN",
};

export function canSpeak(): boolean {
  return (
    typeof window !== "undefined" &&
    "speechSynthesis" in window &&
    typeof window.SpeechSynthesisUtterance !== "undefined"
  );
}

export function speechLangTag(code: string): string {
  return SPEECH_LANG[code] ?? SPEECH_LANG.en;
}

// Held so the utterance (and its onend) is not garbage collected mid-speech, which
// some browsers do.
let current: SpeechSynthesisUtterance | null = null;

/**
 * Read `text` aloud in the language `code`. `onEnd` runs when speech finishes, fails
 * or is cancelled. Returns false when speech is unavailable, so the caller can stay idle.
 */
export function speak(text: string, code: string, onEnd: () => void): boolean {
  if (!canSpeak() || !text.trim()) return false;
  try {
    const synth = window.speechSynthesis;
    synth.cancel();
    const utterance = new SpeechSynthesisUtterance(text);
    utterance.lang = speechLangTag(code);
    const done = () => {
      if (current === utterance) current = null;
      onEnd();
    };
    utterance.onend = done;
    utterance.onerror = done;
    current = utterance;
    synth.speak(utterance);
    return true;
  } catch {
    current = null;
    return false;
  }
}

export function stopSpeaking(): void {
  if (!canSpeak()) return;
  current = null;
  try {
    window.speechSynthesis.cancel();
  } catch {
    /* nothing to stop */
  }
}
