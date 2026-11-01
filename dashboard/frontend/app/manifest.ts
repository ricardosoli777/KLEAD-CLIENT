import type { MetadataRoute } from "next";

export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "KLEAD-CLIENT",
    short_name: "KLEAD-CLIENT",
    description: "Plataforma de qualificação automática de leads via WhatsApp",
    start_url: "/",
    display: "standalone",
    background_color: "#F0EDE8",
    theme_color: "#0D1117",
    icons: [
      { src: "/icon-192.png", sizes: "192x192", type: "image/png" },
      { src: "/icon-512.png", sizes: "512x512", type: "image/png" },
      {
        src: "/icon-512-maskable.png",
        sizes: "512x512",
        type: "image/png",
        purpose: "maskable",
      },
    ],
  };
}
