import type { NextConfig } from "next";

const backendUrl = process.env.BACKEND_URL ?? "http://127.0.0.1:8200";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  // REST calls go through Next.js (/api/* -> FastAPI). WebSockets connect to the backend directly.
  async rewrites() {
    return [
      { source: "/api/:path*", destination: `${backendUrl}/api/:path*` },
      { source: "/health", destination: `${backendUrl}/health` },
    ];
  },
};

export default nextConfig;
