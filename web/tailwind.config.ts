import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}", "./lib/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: { DEFAULT: "#1c1a19", soft: "#2a2725", mute: "#6b6560" },
        paper: { DEFAULT: "#f7f3ec", soft: "#efe8dd", edge: "#e0d6c6" },
        sakura: { DEFAULT: "#e8a0aa", deep: "#d4707f", pale: "#f6dfe2" },
        dusk: { DEFAULT: "#2f3b52", soft: "#3d4b66", deep: "#1d2534" },
        moss: "#7d9471",
        amber: "#d8a24a",
      },
      fontFamily: {
        serif: ['"Noto Serif SC"', '"Songti SC"', 'Georgia', 'serif'],
        sans: ['"Noto Sans SC"', '-apple-system', '"PingFang SC"', 'system-ui', 'sans-serif'],
      },
      keyframes: {
        "fade-up": { "0%": { opacity: "0", transform: "translateY(8px)" }, "100%": { opacity: "1", transform: "none" } },
        "pulse-soft": { "0%,100%": { opacity: "0.55" }, "50%": { opacity: "1" } },
      },
      animation: {
        "fade-up": "fade-up 0.35s ease-out",
        "pulse-soft": "pulse-soft 1.6s ease-in-out infinite",
      },
    },
  },
  plugins: [],
};

export default config;
