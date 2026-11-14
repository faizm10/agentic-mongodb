import type { NextConfig } from "next";

// The browser only ever talks to Next.js; /api/* is proxied server-side to the uptime API,
// so no backend URL, MongoDB credential or admin token is shipped to the client.
const apiUrl = process.env.OPSRELAY_UPTIME_API_URL ?? "http://127.0.0.1:8300";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${apiUrl}/api/:path*` }];
  },
};

export default nextConfig;
