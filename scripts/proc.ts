import { spawn, type ChildProcess } from "node:child_process";

export function startSandbox(stdio: "inherit" | "ignore" = "inherit"): ChildProcess {
  return spawn(process.execPath, ["--import", "tsx", "sandbox/server.ts"], { env: process.env, stdio });
}

export async function waitFor(url: string, timeoutMs = 20_000) {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    try {
      if ((await fetch(url)).ok) return;
    } catch {}
    await new Promise((r) => setTimeout(r, 250));
  }
  throw new Error(`timed out waiting for ${url}`);
}
