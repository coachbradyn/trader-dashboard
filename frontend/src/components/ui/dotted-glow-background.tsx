"use client";
import { useEffect, useRef } from "react";

/**
 * DottedGlowBackground
 * Animated dot grid with a subtle ai-blue glow that follows a slow drift.
 * Purely decorative — pointer-events-none, sits behind all content.
 *
 * Uses a <canvas> for performance. The grid density scales with viewport.
 */
export default function DottedGlowBackground({
  className = "",
  dotColor = "rgba(99, 102, 241, 0.35)", // ai-blue
  glowColor = "rgba(99, 102, 241, 0.20)",
  spacing = 28,
  dotRadius = 1,
}: {
  className?: string;
  dotColor?: string;
  glowColor?: string;
  spacing?: number;
  dotRadius?: number;
}) {
  const ref = useRef<HTMLCanvasElement | null>(null);

  useEffect(() => {
    const canvas = ref.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    let raf = 0;
    let t = 0;

    const resize = () => {
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      const rect = canvas.getBoundingClientRect();
      canvas.width = Math.max(1, Math.floor(rect.width * dpr));
      canvas.height = Math.max(1, Math.floor(rect.height * dpr));
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    };
    resize();
    const onResize = () => resize();
    window.addEventListener("resize", onResize);

    const draw = () => {
      const w = canvas.clientWidth;
      const h = canvas.clientHeight;
      ctx.clearRect(0, 0, w, h);

      // Drifting glow — soft radial gradient that moves on a slow sinusoid.
      const gx = w * (0.5 + 0.35 * Math.cos(t * 0.00025));
      const gy = h * (0.45 + 0.30 * Math.sin(t * 0.00031));
      const radius = Math.max(w, h) * 0.55;
      const grad = ctx.createRadialGradient(gx, gy, 0, gx, gy, radius);
      grad.addColorStop(0, glowColor);
      grad.addColorStop(1, "rgba(0,0,0,0)");
      ctx.fillStyle = grad;
      ctx.fillRect(0, 0, w, h);

      // Dot grid. Slight breathing on opacity.
      const breathe = 0.75 + 0.25 * (0.5 + 0.5 * Math.sin(t * 0.001));
      ctx.fillStyle = dotColor.replace(
        /rgba\(([^,]+),([^,]+),([^,]+),([^)]+)\)/,
        (_m, r, g, b, a) => `rgba(${r},${g},${b},${parseFloat(a) * breathe})`
      );
      for (let y = spacing / 2; y < h; y += spacing) {
        for (let x = spacing / 2; x < w; x += spacing) {
          ctx.beginPath();
          ctx.arc(x, y, dotRadius, 0, Math.PI * 2);
          ctx.fill();
        }
      }

      t += 16;
      raf = requestAnimationFrame(draw);
    };
    raf = requestAnimationFrame(draw);

    return () => {
      cancelAnimationFrame(raf);
      window.removeEventListener("resize", onResize);
    };
  }, [dotColor, glowColor, spacing, dotRadius]);

  return (
    <div
      aria-hidden
      className={`pointer-events-none fixed inset-0 -z-10 ${className}`}
    >
      <canvas ref={ref} className="w-full h-full" />
      {/* Gentle top/bottom fade so the grid blends into the page edges */}
      <div className="absolute inset-0 bg-gradient-to-b from-transparent via-transparent to-[#0a0a0f]/60" />
    </div>
  );
}
