import { useEffect, useRef } from "react";

type Props = {
  inputLevel: () => number;
  outputLevel: () => number;
  state: "idle" | "listening" | "thinking" | "tool" | "speaking" | "error";
};

export function VoiceRing({ inputLevel, outputLevel, state }: Props) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const context = canvas.getContext("2d");
    if (!context) return;
    const tokens = getComputedStyle(document.documentElement);
    const accent = tokens.getPropertyValue("--color-accent").trim();
    const tool = tokens.getPropertyValue("--color-coral").trim();
    const error = tokens.getPropertyValue("--color-error").trim();
    const secondary = tokens.getPropertyValue("--color-rule").trim();
    const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    let frame = 0;
    let raf = 0;
    const draw = () => {
      const ratio = Math.min(window.devicePixelRatio || 1, 2);
      const size = canvas.clientWidth;
      if (canvas.width !== size * ratio) {
        canvas.width = size * ratio;
        canvas.height = size * ratio;
      }
      context.setTransform(ratio, 0, 0, ratio, 0, 0);
      context.clearRect(0, 0, size, size);
      const input = Math.max(0, Math.min(1, inputLevel() || 0));
      const output = Math.max(0, Math.min(1, outputLevel() || 0));
      const level = state === "speaking" ? output : input;
      const center = size / 2;
      const base = size * 0.24;
      const pulse = state === "idle" || reduceMotion ? 0 : Math.sin(frame / 18) * 2;
      const radius = base + level * size * 0.11 + pulse;
      context.lineWidth = state === "error" ? 3 : 2;
      context.strokeStyle = state === "error" ? error : state === "tool" ? tool : accent;
      context.beginPath();
      context.arc(center, center, radius, 0, Math.PI * 2);
      context.stroke();
      context.lineWidth = 1;
      context.strokeStyle = secondary;
      context.beginPath();
      context.arc(center, center, radius + 8 + level * 8, 0, Math.PI * 2);
      context.stroke();
      if (!reduceMotion) {
        frame += 1;
        raf = window.requestAnimationFrame(draw);
      }
    };
    draw();
    return () => window.cancelAnimationFrame(raf);
  }, [inputLevel, outputLevel, state]);

  return <canvas ref={canvasRef} className="voice-ring" width="128" height="128" aria-hidden="true" />;
}
