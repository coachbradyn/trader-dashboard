import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./src/**/*.{js,ts,jsx,tsx,mdx}"],
  theme: {
    extend: {
      colors: {
        // shadcn CSS variable system (dark-only values in globals.css :root)
        border: "hsl(var(--border))",
        input: "hsl(var(--input))",
        ring: "hsl(var(--ring))",
        background: "hsl(var(--background))",
        foreground: "hsl(var(--foreground))",
        primary: {
          DEFAULT: "hsl(var(--primary))",
          foreground: "hsl(var(--primary-foreground))",
        },
        secondary: {
          DEFAULT: "hsl(var(--secondary))",
          foreground: "hsl(var(--secondary-foreground))",
        },
        destructive: {
          DEFAULT: "hsl(var(--destructive))",
          foreground: "hsl(var(--destructive-foreground))",
        },
        muted: {
          DEFAULT: "hsl(var(--muted))",
          foreground: "hsl(var(--muted-foreground))",
        },
        accent: {
          DEFAULT: "hsl(var(--accent))",
          foreground: "hsl(var(--accent-foreground))",
        },
        popover: {
          DEFAULT: "hsl(var(--popover))",
          foreground: "hsl(var(--popover-foreground))",
        },
        card: {
          DEFAULT: "hsl(var(--card))",
          foreground: "hsl(var(--card-foreground))",
        },
        // Domain-specific trading colors (direct hex — not theme-togglable)
        profit: "#22c55e",
        loss: "#ef4444",
        surface: "#111827",
        "surface-light": "#1f2937",
        // AI design tokens
        "ai-blue": "#6366f1",
        "ai-purple": "#8b5cf6",
        "ai-glow": "rgba(99, 102, 241, 0.15)",
        terminal: "#0a0a0f",
        "screener-amber": "#fbbf24",
        "screener-glow": "rgba(251, 191, 36, 0.15)",
      },
      borderRadius: {
        lg: "var(--radius)",
        md: "calc(var(--radius) - 2px)",
        sm: "calc(var(--radius) - 4px)",
      },
      animation: {
        "gradient-rotate": "gradient-rotate 4s linear infinite",
        "fade-in": "fade-in 0.4s ease-out forwards",
        "pulse-slow": "pulse 3s cubic-bezier(0.4, 0, 0.6, 1) infinite",
        "blink": "blink 1s step-end infinite",
        "gauge-fill": "gauge-fill 0.6s ease-out forwards",
        "stagger-in": "fade-in 0.3s ease-out forwards",
        "scale-in": "scale-in 0.3s cubic-bezier(0.34, 1.56, 0.64, 1) forwards",
        "heat-glow-warm": "heat-glow-warm 4s ease-in-out infinite",
        "heat-glow-hot": "heat-glow-hot 3s ease-in-out infinite",
        "slide-up-panel": "slide-up-panel 0.4s cubic-bezier(0.16, 1, 0.3, 1) forwards",
        "shimmer-load": "shimmer-load 2s ease-in-out infinite",
        "breathe": "breathe 4s ease-in-out infinite",
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
        "scale-in": {
          from: { transform: "scale(0.9)", opacity: "0" },
          to: { transform: "scale(1)", opacity: "1" },
        },
        "heat-glow-warm": {
          "0%, 100%": { borderColor: "rgba(251, 191, 36, 0.2)" },
          "50%": { borderColor: "rgba(251, 191, 36, 0.4)" },
        },
        "heat-glow-hot": {
          "0%, 100%": {
            borderColor: "rgba(251, 191, 36, 0.5)",
            boxShadow: "0 0 40px rgba(251, 191, 36, 0.15), inset 0 0 20px rgba(251, 191, 36, 0.03)",
          },
          "50%": {
            borderColor: "rgba(251, 191, 36, 0.8)",
            boxShadow: "0 0 70px rgba(251, 191, 36, 0.25), inset 0 0 30px rgba(251, 191, 36, 0.05)",
          },
        },
        "slide-up-panel": {
          from: { opacity: "0", transform: "translateY(24px)" },
          to: { opacity: "1", transform: "translateY(0)" },
        },
        "shimmer-load": {
          "0%": { backgroundPosition: "200% 0" },
          "100%": { backgroundPosition: "-200% 0" },
        },
        "breathe": {
          "0%, 100%": { transform: "scale(1)" },
          "50%": { transform: "scale(1.01)" },
        },
      },
    },
  },
  plugins: [],
};
export default config;
