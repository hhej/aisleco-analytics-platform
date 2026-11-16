/** Design tokens (Spec 05 §4): calm cream surfaces, one wine accent for data, red/amber reserved for status. */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      fontFamily: { sans: ["Inter", "Noto Sans Thai", "system-ui", "sans-serif"] },
      colors: {
        page: "#FAF7F2",
        surface: "#FFFFFF",
        line: "#ECE6DD",
        hair: "#F3EFE9",
        ink: { DEFAULT: "#1F1B18", 2: "#5A534C", 3: "#857D74" },
        wine: { DEFAULT: "#7A2E3B", 50: "#F8EFF1", 100: "#F0DDE1", 200: "#E2BEC4", 300: "#C98893", 400: "#A5525F", 600: "#62232F", 700: "#4A1A23" },
        stone: { data: "#ADA59B", soft: "#EFEAE3", cell: "#F6F3EE" },
        over: { DEFAULT: "#A3221A", bg: "#F9DAD6", soft: "#FCEDEB" },
        tight: { DEFAULT: "#8F4A00", bg: "#FBE7C6", soft: "#FDF4E5" },
      },
      boxShadow: { card: "0 1px 2px rgba(31,27,24,.04), 0 1px 1px rgba(31,27,24,.03)", pop: "0 8px 30px rgba(31,27,24,.14)" },
    },
  },
};
