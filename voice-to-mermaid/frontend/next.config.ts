import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Proxy /api/mermaid/* to the backend so the browser never needs
  // to know the backend URL directly.
  async rewrites() {
    return [
      {
        source: "/api/mermaid/:path*",
        destination: `${process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:7625"}/:path*`,
      },
    ];
  },
};

export default nextConfig;
