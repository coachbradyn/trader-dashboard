import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./src/**/*.{js,ts,jsx,tsx,mdx}"],
  theme: {
    extend: {
      colors: {
        profit: "#22c55e",
        loss: "#ef4444",
        accent: "#3b82f6",
        surface: "#111827",
        "surface-light": "#1f2937",
        border: "#374151",
        // AI design tokens
        "ai-blue": "#6366f1",
        "ai-purple": "#8b5cf6",
        "ai-glow": "rgba(99, 102, 241, 0.15)",
        terminal: "#0a0a0f",
      },
      animation: {
        "gradient-rotate": "gradient-rotate 4s linear infinite",
        "fade-in": "fade-in 0.4s ease-out forwards",
        "pulse-slow": "pulse 3s cubic-bezier(0.4, 0, 0.6, 1) infinite",
        "blink": "blink 1s step-end infinite",
        "gauge-fill": "gauge-fill 0.6s ease-out forwards",
        "stagger-in": "fade-in 0.3s ease-out forwards",
      },
      keyframes: {
        "gradient-rotate": {
          "0%": { "--gradient-angle": "0deg" },
          "100%": { "--gradient-angle": "360deg" },
        },
        "fade-in": {
          from: { opacity: "0", transform: "translateY(8px)" },
          to: { opacity: "1", transform: "translateY(0)" },
        },
        "blink": {
          "0%, 100%": { opacity: "1" },
          "50%": { opacity: "0" },
        },
        "gauge-fill": {
          from: { width: "0%" },
          to: { width: "var(--gauge-width)" },
        },
      },
    },
  },
  plugins: [],
};
export default config;
