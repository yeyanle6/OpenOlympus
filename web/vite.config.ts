import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    proxy: {
      "/health": { target: "http://localhost:8000" },
      "/agents": { target: "http://localhost:8000" },
      "/rooms": { target: "http://localhost:8000" },
      "/director": { target: "http://localhost:8000" },
      "/loop": { target: "http://localhost:8000" },
      "/consensus": { target: "http://localhost:8000" },
      "/decisions": { target: "http://localhost:8000" },
      "/speaker": { target: "http://localhost:8000" },
      "/ws": { target: "ws://localhost:8000", ws: true },
    },
  },
});
