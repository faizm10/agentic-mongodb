import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        page: "#0a101b",
        surface: "#162334",
        raised: "#1c2b3d",
        hover: "#28394e",
        line: "#344458",
        "line-strong": "#52657d",
        ink: "#eef4fc",
        muted: "#adbbce",
        faint: "#8798ae",
        accent: "#3987e5",
        // Status palette (dataviz reference, validated on dark): always paired with an icon + label.
        ok: "#0ca30c",
        warn: "#fab219",
        crit: "#d03b3b",
        nodata: "#2b2f37",
      },
      fontFamily: {
        sans: ["var(--font-sans)", "system-ui", "-apple-system", "Segoe UI", "sans-serif"],
        mono: ["var(--font-mono)", "ui-monospace", "SFMono-Regular", "monospace"],
      },
      keyframes: {
        ping2: { "75%, 100%": { transform: "scale(2.2)", opacity: "0" } },
        rise: { "0%": { opacity: "0", transform: "translateY(6px)" }, "100%": { opacity: "1", transform: "none" } },
      },
      animation: {
        ping2: "ping2 1.8s cubic-bezier(0, 0, 0.2, 1) infinite",
        rise: "rise 0.45s cubic-bezier(0.22, 1, 0.36, 1) both",
      },
    },
  },
  plugins: [],
};

export default config;
