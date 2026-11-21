import path from "path";
import { fileURLToPath } from "url";
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";
import { viteSingleFile } from "vite-plugin-singlefile";

const here = path.dirname(fileURLToPath(import.meta.url));

// The chat panel builds to one self-contained file that chat/server.py serves.
export default defineConfig({
  root: here,
  base: "./",
  plugins: [react(), tailwindcss(), viteSingleFile()],
  build: { outDir: path.resolve(here, "../dist"), emptyOutDir: true },
  server: { port: 5199 },
});
