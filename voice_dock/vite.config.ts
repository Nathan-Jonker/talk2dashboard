import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL(".", import.meta.url));

export default defineConfig({
  plugins: [react()],
  define: {
    "process.env.NODE_ENV": JSON.stringify("production")
  },
  build: {
    outDir: resolve(root, "../src/talk2dashboard/renderer/assets"),
    emptyOutDir: false,
    lib: {
      entry: resolve(root, "src/main.tsx"),
      name: "Talk2DashboardVoiceDock",
      formats: ["iife"],
      fileName: () => "voice-dock.js"
    },
    cssCodeSplit: false,
    rollupOptions: {
      output: {
        assetFileNames: (assetInfo) => assetInfo.name?.endsWith(".css") ? "voice-dock.css" : "voice-dock-[name][extname]"
      }
    }
  }
});
