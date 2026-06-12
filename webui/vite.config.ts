import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    // ローカル開発時(npm run dev)は localhost:8000 のAPIへプロキシする
    proxy: {
      "/api": "http://localhost:8000",
    },
  },
});
