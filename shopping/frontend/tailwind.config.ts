import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: {
          DEFAULT: "#14131a",
          50: "#f7f6f3",
          100: "#efede7",
          200: "#e2dfd6",
          300: "#c9c4b7",
          400: "#9a9488",
          500: "#6f6a60",
          600: "#4f4b44",
          700: "#35322e",
          800: "#221f1d",
          900: "#14131a",
        },
        nova: {
          50: "#f3f1ff",
          100: "#e9e5ff",
          200: "#d4ccff",
          300: "#b3a4ff",
          400: "#8f74ff",
          500: "#6f47ff",
          600: "#5d2cf0",
          700: "#4d20cc",
          800: "#3f1ca5",
          900: "#341b82",
        },
        ember: { 400: "#ff9b6a", 500: "#ff7a45" },
      },
      fontFamily: {
        sans: ["var(--font-sans)", "ui-sans-serif", "system-ui", "sans-serif"],
        display: ["var(--font-display)", "ui-serif", "Georgia", "serif"],
      },
      boxShadow: {
        soft: "0 1px 2px rgba(20,19,26,0.04), 0 8px 24px -8px rgba(20,19,26,0.10)",
        lift: "0 2px 4px rgba(20,19,26,0.04), 0 24px 48px -16px rgba(20,19,26,0.22)",
      },
      keyframes: {
        "fade-up": { "0%": { opacity: "0", transform: "translateY(12px)" }, "100%": { opacity: "1", transform: "none" } },
        "slide-in": { "0%": { transform: "translateX(100%)" }, "100%": { transform: "translateX(0)" } },
        "pop": { "0%": { transform: "scale(0.6)" }, "60%": { transform: "scale(1.15)" }, "100%": { transform: "scale(1)" } },
      },
      animation: {
        "fade-up": "fade-up 0.6s cubic-bezier(0.22,1,0.36,1) both",
        "slide-in": "slide-in 0.35s cubic-bezier(0.22,1,0.36,1)",
        pop: "pop 0.35s ease-out",
      },
    },
  },
  plugins: [],
};

export default config;
