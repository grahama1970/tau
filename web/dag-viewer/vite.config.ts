import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  base: "/",
  build: {
    outDir: "../../src/tau_coding/dag_viewer/static",
    emptyOutDir: true,
    sourcemap: false,
  },
});
