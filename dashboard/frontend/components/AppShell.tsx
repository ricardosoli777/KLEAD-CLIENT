"use client";

import type { ReactNode } from "react";
import { usePathname } from "next/navigation";
import AppHeader from "@/components/AppHeader";

const HIDDEN_HEADER_PATHS = ["/login", "/change-password", "/plans", "/planos", "/sobre", "/privacidade", "/termos", "/excluirdados"];

export default function AppShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const hideHeader = HIDDEN_HEADER_PATHS.some(path => pathname.startsWith(path));

  return (
    <>
      {!hideHeader && (
        <div className="z-[100]">
          <AppHeader />
        </div>
      )}
      <div className={hideHeader ? "" : "pt-16"}>
        {children}
      </div>
    </>
  );
}
