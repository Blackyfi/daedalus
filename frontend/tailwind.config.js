/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    screens: {
      xs: "480px",
      sm: "640px",
      md: "768px",
      lg: "1024px",
      xl: "1280px",
      "2xl": "1536px",
    },
    extend: {
      colors: {
        bg: "#0a0e14",
        panel: "#10151c",
        panel2: "#161c25",
        border: "#1f2733",
        accent: "#7ee787",
        warning: "#f0883e",
        danger: "#f85149",
        muted: "#8b949e",
        text: "#e6edf3",
      },
      fontFamily: {
        mono: ['"JetBrains Mono"', '"SF Mono"', "Menlo", "monospace"],
      },
      maxWidth: {
        shell: "1600px",
      },
    },
  },
  plugins: [],
};
