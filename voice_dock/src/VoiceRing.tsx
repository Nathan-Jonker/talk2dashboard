import { useEffect, useRef } from "react";

type Props = {
  inputLevel: () => number;
  outputLevel: () => number;
  state: "offline" | "connecting" | "listening" | "thinking" | "tool" | "speaking" | "error";
};

function boundedLevel(value: number): number {
  return Math.max(0, Math.min(1, Number.isFinite(value) ? value : 0));
}

export function VoiceRing({ inputLevel, outputLevel, state }: Props) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const context = canvas.getContext("2d");
    if (!context) return;
    const tokens = getComputedStyle(document.documentElement);
    const color = (name: string, fallback: string) => tokens.getPropertyValue(name).trim() || fallback;
    const accent = color("--color-accent", "#0071bc");
    const live = color("--color-live", "#00a6a6");
    const yellow = color("--color-yellow", "#f7d417");
    const coral = color("--color-coral", "#e75b43");
    const error = color("--color-error", "#cc2f36");
    const rule = color("--color-rule", "#aab6bf");
    const paper = color("--color-paper", "#ffffff");
    const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    let frame = 0;
    let smoothed = 0;
    let raf = 0;

    const strokeCircle = (center: number, radius: number, stroke: string, width: number, alpha = 1) => {
      context.save();
      context.globalAlpha = alpha;
      context.strokeStyle = stroke;
      context.lineWidth = width;
      context.beginPath();
      context.arc(center, center, Math.max(1, radius), 0, Math.PI * 2);
      context.stroke();
      context.restore();
    };

    const draw = () => {
      const ratio = Math.min(window.devicePixelRatio || 1, 2);
      const size = canvas.clientWidth;
      if (!Number.isFinite(size) || size <= 0) {
        raf = window.requestAnimationFrame(draw);
        return;
      }
      const pixelSize = Math.round(size * ratio);
      if (canvas.width !== pixelSize || canvas.height !== pixelSize) {
        canvas.width = pixelSize;
        canvas.height = pixelSize;
      }
      context.setTransform(ratio, 0, 0, ratio, 0, 0);
      context.clearRect(0, 0, size, size);

      const target = state === "speaking"
        ? boundedLevel(outputLevel())
        : state === "listening" ? boundedLevel(inputLevel()) : 0;
      smoothed += (target - smoothed) * 0.2;
      const center = size / 2;
      const base = size * 0.22;
      const motion = reduceMotion ? 0 : frame;

      if (state === "speaking") {
        const orbRadius = base + smoothed * size * 0.075;
        context.save();
        context.shadowColor = accent;
        context.shadowBlur = 10 + smoothed * 16;
        const gradient = context.createRadialGradient(
          center - orbRadius * 0.25,
          center - orbRadius * 0.3,
          orbRadius * 0.12,
          center,
          center,
          orbRadius
        );
        gradient.addColorStop(0, paper);
        gradient.addColorStop(0.25, live);
        gradient.addColorStop(1, accent);
        context.fillStyle = gradient;
        context.beginPath();
        context.arc(center, center, orbRadius, 0, Math.PI * 2);
        context.fill();
        context.restore();
        strokeCircle(center, orbRadius + 6 + smoothed * 5, live, 2, 0.75);
        strokeCircle(center, orbRadius + 11 + smoothed * 8, accent, 1, 0.35);
      } else if (state === "listening") {
        strokeCircle(center, base, rule, 1.5, 0.7);
        if (smoothed > 0.025) {
          const colors = [live, yellow, coral];
          colors.forEach((stroke, index) => {
            const radius = base + 4 + index * 3 + smoothed * size * (0.07 + index * 0.018);
            const start = motion * 0.035 + index * 2.05;
            const sweep = 0.65 + smoothed * 1.5;
            context.save();
            context.strokeStyle = stroke;
            context.lineWidth = 2 + smoothed * 2;
            context.lineCap = "round";
            context.globalAlpha = 0.62 + smoothed * 0.3;
            context.beginPath();
            context.arc(center, center, radius, start, start + sweep);
            context.stroke();
            context.restore();
          });
        }
      } else if (state === "thinking" || state === "connecting" || state === "tool") {
        context.save();
        context.translate(center, center);
        context.rotate(motion * 0.025);
        context.setLineDash([4, 5]);
        context.lineWidth = state === "tool" ? 3 : 2;
        context.strokeStyle = state === "tool" ? coral : accent;
        context.beginPath();
        context.arc(0, 0, base + 6, 0, Math.PI * 2);
        context.stroke();
        context.restore();
        strokeCircle(center, base, rule, 1, 0.65);
      } else if (state === "error") {
        strokeCircle(center, base + 3, error, 3, 1);
        strokeCircle(center, base + 9, error, 1, 0.35);
      } else {
        strokeCircle(center, base, rule, 1.5, 0.8);
      }

      const animated = !reduceMotion && !["offline", "error"].includes(state);
      if (animated) {
        frame += 1;
        raf = window.requestAnimationFrame(draw);
      }
    };

    draw();
    return () => window.cancelAnimationFrame(raf);
  }, [inputLevel, outputLevel, state]);

  return <canvas ref={canvasRef} className="voice-ring" width="128" height="128" aria-hidden="true" />;
}
