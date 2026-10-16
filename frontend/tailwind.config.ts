import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        brand: {
          50: "#f0f6fc",
          100: "#e8f1fb",
          200: "#c9dbf0",
          500: "#1a5fb4",
          600: "#144f96",
          700: "#0d3d7a",
          800: "#0b3263",
          900: "#0a274d",
        },
        ink: "#0f172a",
        canvas: "#f4f6f9",
      },
      fontFamily: {
        sans: ["var(--font-jakarta)", "Segoe UI", "sans-serif"],
      },
      boxShadow: {
        soft: "0 10px 40px -18px rgba(15, 23, 42, 0.22)",
        composer: "0 8px 28px -10px rgba(15, 23, 42, 0.16)",
      },
      maxWidth: {
        chat: "48rem",
      },
    },
  },
  plugins: [],
};

export default config;
