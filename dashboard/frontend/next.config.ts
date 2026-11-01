import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactCompiler: true,
  generateBuildId: () => Date.now().toString(),
  experimental: {
    serverActions: {
      bodySizeLimit: "50mb",
    },
    // Middleware (proxy.ts) intercepta toda requisição, inclusive uploads —
    // sem isso, o limite padrão de 10MB derrubava vídeos maiores no meio do
    // caminho antes mesmo de chegar na rota de upload.
    proxyClientMaxBodySize: "50mb",
  },
};

export default nextConfig;
