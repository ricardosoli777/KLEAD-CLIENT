import type { Metadata, Viewport } from "next";
import AppShell from "@/components/AppShell";
import ErrorBoundary from "@/components/ErrorBoundary";
import "./globals.css";

export const metadata: Metadata = {
  title: "KLEAD-CLIENT",
  description: "Plataforma de qualificação automática de leads via WhatsApp",
  appleWebApp: {
    capable: true,
    statusBarStyle: "black-translucent",
    title: "KLEAD-CLIENT",
  },
  icons: {
    icon: "/icon.svg",
    apple: "/apple-icon.png",
  },
};

export const viewport: Viewport = {
  themeColor: "#0D1117",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="pt-BR"
      suppressHydrationWarning
      className="h-full antialiased"
    >
      <body className="min-h-full flex flex-col bg-[#F0EDE8] text-[#1A1410]">
        <ErrorBoundary><AppShell>{children}</AppShell></ErrorBoundary>
      </body>
    </html>
  );
}
