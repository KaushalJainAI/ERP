import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";
import path from "path";

/**
 * VITE_API_URL is baked into the bundle at build time, so a bad value breaks every API call
 * in production with no server-side trace. Accept only a same-origin path ("/api") or an
 * absolute http(s) URL. Git Bash on Windows rewrites `--build-arg VITE_API_URL=/api` into
 * "C:/Program Files/Git/api" unless MSYS_NO_PATHCONV=1 is set; that build shipped once
 * (2026-11-11) and broke sign-in, so refuse it here.
 */
function assertApiUrl(value: string | undefined) {
  if (value === undefined || value === "") return; // client.ts falls back to "/api"
  const sameOriginPath = value.startsWith("/") && !value.startsWith("//");
  const absoluteHttp = /^https?:\/\/[^/\s]+/i.test(value);
  if (!sameOriginPath && !absoluteHttp) {
    throw new Error(
      `VITE_API_URL must be a path like "/api" or an http(s) URL, got "${value}". ` +
        `If you built from Git Bash, set MSYS_NO_PATHCONV=1 so "/api" is not turned into a Windows path.`
    );
  }
}

// https://vitejs.dev/config/
export default defineConfig(({ command, mode }) => {
  if (command === "build") {
    const env = loadEnv(mode, process.cwd(), "VITE_");
    assertApiUrl(process.env.VITE_API_URL ?? env.VITE_API_URL);
  }
  return {
    plugins: [react()],
    resolve: {
      alias: {
        "@": path.resolve(__dirname, "./src"),
      },
    },
    server: {
      port: 5173,
      host: true,
      proxy: {
        "/api": {
          target: "http://127.0.0.1:8000",
          changeOrigin: true,
        },
      },
    },
  };
});
