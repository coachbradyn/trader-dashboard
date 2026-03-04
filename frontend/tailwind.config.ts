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
      },
    },
  },
  plugins: [],
};
export default config;
