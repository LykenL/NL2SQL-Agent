import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Emits .next/standalone with a self-contained server.js and only the
  // node_modules it actually imports, which is what frontend/Dockerfile copies.
  output: "standalone",
};

export default nextConfig;
