import type { Config } from "tailwindcss";

const token = (name: string) => `rgb(var(--${name}) / <alpha-value>)`;

const config: Config = {
  darkMode: "class",
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        canvas: token("canvas"),
        surface: token("surface"),
        raised: token("raised"),
        sunken: token("sunken"),
        line: token("line"),
        ink: token("ink"),
        muted: token("muted"),
        faint: token("faint"),
        accent: token("accent"),
        "accent-soft": token("accent-soft"),
        "accent-ink": token("accent-ink"),
        bubble: token("bubble"),
        "bubble-ink": token("bubble-ink"),
        success: token("success"),
        danger: token("danger"),
        warning: token("warning"),
      },
      fontFamily: {
        sans: ["var(--font-sans)", "ui-sans-serif", "system-ui", "sans-serif"],
        display: ["var(--font-display)", "var(--font-sans)", "ui-sans-serif", "sans-serif"],
      },
      boxShadow: {
        float: "0 1px 1px rgb(0 0 0 / 0.04), 0 12px 32px -12px rgb(0 0 0 / 0.25)",
        pop: "0 0 0 1px rgb(var(--line) / 1), 0 16px 48px -12px rgb(0 0 0 / 0.35)",
      },
      keyframes: {
        blink: { "0%, 80%, 100%": { opacity: "0.25", transform: "translateY(0)" }, "40%": { opacity: "1", transform: "translateY(-2px)" } },
      },
      animation: { blink: "blink 1.2s infinite ease-in-out" },
    },
  },
  plugins: [],
};

export default config;
