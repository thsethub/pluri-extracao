import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  async rewrites() {
    // Em produção (Docker) a API está em http://api:8000 (serviço Docker Compose).
    // Em dev local, Next.js seta NODE_ENV=development automaticamente → localhost:8000.
    // API_URL sobrescreve qualquer dos dois se necessário.
    const apiUrl =
      process.env.API_URL ||
      (process.env.NODE_ENV === "production"
        ? "http://api:8000"
        : "http://localhost:8000");
    return [
      {
        source: "/api-proxy/:path*",
        destination: `${apiUrl}/:path*`,
      },
    ];
  },
};

export default nextConfig;
