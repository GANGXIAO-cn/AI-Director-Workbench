import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

import { buildInfo } from "./buildInfo.mjs";

const info = buildInfo();
export default defineConfig({
  define: { __ADW_BUILD__: JSON.stringify(info) },
  plugins: [react(), {
    name:"workbench-build-info",
    generateBundle() {
      this.emitFile({type:"asset",fileName:"build-info.json",source:JSON.stringify(info)});
    }
  }],
  server: { host: "127.0.0.1", port: 5173, proxy: { "/api": "http://127.0.0.1:8765" } },
});
