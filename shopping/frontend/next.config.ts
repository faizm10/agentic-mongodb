import type { NextConfig } from "next";

const backendUrl = process.env.BACKEND_URL ?? "http://127.0.0.1:8100";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  // The browser calls /api/* on the Next.js origin; Next proxies to the FastAPI backend.
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${backendUrl}/api/:path*` }];
  },
};

export default nextConfig;
