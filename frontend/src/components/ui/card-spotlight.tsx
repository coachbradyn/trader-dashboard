"use client";
import { useRef, useState, useCallback, CSSProperties } from "react";

/**
 * CardSpotlight
 * A card wrapper that tracks the cursor and renders a radial spotlight
 * gradient under it. Wraps children in a rounded panel matching the
 * design system (surface + subtle border, ai-blue accent on spotlight).
 *
 * Usage:
 *   <CardSpotlight className="col-span-2">...</CardSpotlight>
 */
export default function CardSpotlight({
  children,
  className = "",
  glow = "rgba(99, 102, 241, 0.12)",
  radius = 300,
  as: Tag = "div",
  ...rest
}: {
  children: React.ReactNode;
  className?: string;
  glow?: string;
  radius?: number;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  as?: any;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  [key: string]: any;
}) {
  const ref = useRef<HTMLDivElement | null>(null);
  const [pos, setPos] = useState<{ x: number; y: number } | null>(null);
  const [hovering, setHovering] = useState(false);

  const onMove = useCallback((e: React.MouseEvent) => {
    const el = ref.current;
    if (!el) return;
    const r = el.getBoundingClientRect();
    setPos({ x: e.clientX - r.left, y: e.clientY - r.top });
  }, []);

  const style: CSSProperties = pos
    ? {
        backgroundImage: `radial-gradient(${radius}px circle at ${pos.x}px ${pos.y}px, ${glow}, transparent 65%)`,
      }
    : {};

  return (
    <Tag
      ref={ref}
      onMouseMove={onMove}
      onMouseEnter={() => setHovering(true)}
      onMouseLeave={() => setHovering(false)}
      className={`relative overflow-hidden rounded-2xl border border-[#1f2937] bg-[#111827]/70 backdrop-blur-sm transition-colors duration-200 ${
        hovering ? "border-[#374151]" : ""
      } ${className}`}
      {...rest}
    >
      {/* Spotlight layer */}
      <div
        className="pointer-events-none absolute inset-0 opacity-100 transition-opacity duration-200"
        style={{
          ...style,
          opacity: hovering ? 1 : 0,
        }}
      />
      {/* Content */}
      <div className="relative">{children}</div>
    </Tag>
  );
}
