import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Em desenvolvimento o próprio Vite faz o papel do nginx de produção (TIE-27):
// /api/* vai para a API com a X-API-Key posta aqui, no Node. `API_KEY` não tem
// o prefixo VITE_, então nunca é inlinada no bundle.
const apiKey = process.env.API_KEY;

export default defineConfig({
  plugins: [react()],
  server: {
    strictPort: true,
    proxy: {
      "/api": {
        target: process.env.API_PROXY_TARGET || "http://localhost:8000",
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ""),
        headers: apiKey ? { "X-API-Key": apiKey } : {},
      },
    },
  },
});
