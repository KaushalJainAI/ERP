import React, { useEffect, useRef } from "react";

const BARS = 15;
/** Smallest bar height as a fraction of the row, so silence still shows a dot-row. */
const MIN_SCALE = 0.12;
/** Speech RMS is usually 0.02 to 0.3; this stretches it to the full row. */
const GAIN = 4;
/** Per-frame decay when the level falls (attack is instant). */
const RELEASE = 0.9;
/** Heights for the static fallback when no analyser is available. */
const STATIC_SCALE = Array.from({ length: BARS }, (_, i) => 0.3 + 0.4 * Math.abs(Math.sin(i * 1.3)));

interface VoiceWaveformProps {
  /** Mic analyser from useVoiceInput. Null means no Web Audio: show static bars. */
  analyser: AnalyserNode | null;
  label: string;
}

/**
 * Bars that pulse with the microphone level. The loop writes transforms straight to
 * the DOM, so the panel does not re-render per frame. It stops when the analyser
 * changes or the component unmounts; the audio context itself is owned by the hook.
 */
export const VoiceWaveform: React.FC<VoiceWaveformProps> = ({ analyser, label }) => {
  const barRefs = useRef<(HTMLSpanElement | null)[]>([]);

  useEffect(() => {
    if (!analyser) return;
    const bars = barRefs.current;
    const samples = new Uint8Array(analyser.fftSize);
    let level = 0;
    let frame = 0;

    const draw = () => {
      analyser.getByteTimeDomainData(samples);
      let sum = 0;
      for (let i = 0; i < samples.length; i++) {
        const v = (samples[i] - 128) / 128;
        sum += v * v;
      }
      const target = Math.min(1, Math.sqrt(sum / samples.length) * GAIN);
      level = target > level ? target : level * RELEASE;

      const now = performance.now() / 1000;
      for (let i = 0; i < bars.length; i++) {
        const bar = bars[i];
        if (!bar) continue;
        // A per-bar phase offset makes the row ripple instead of moving as one block.
        const wobble = 0.5 + 0.5 * Math.sin(now * 6 + i * 0.9);
        const scale = MIN_SCALE + (1 - MIN_SCALE) * Math.min(1, level * (0.4 + 0.6 * wobble));
        bar.style.transform = `scaleY(${scale.toFixed(3)})`;
      }
      frame = requestAnimationFrame(draw);
    };

    frame = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(frame);
  }, [analyser]);

  return (
    <div role="img" aria-label={label} className="flex h-8 items-center justify-center gap-1 px-2">
      {STATIC_SCALE.map((scale, i) => (
        <span
          key={i}
          ref={(el) => {
            barRefs.current[i] = el;
          }}
          className="block h-full w-1 origin-center rounded-full bg-rose-400 will-change-transform"
          style={analyser ? undefined : { transform: `scaleY(${scale.toFixed(3)})` }}
        />
      ))}
    </div>
  );
};
