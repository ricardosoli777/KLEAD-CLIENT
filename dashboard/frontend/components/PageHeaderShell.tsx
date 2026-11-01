"use client";

import type { ReactNode } from "react";

type PageHeaderShellProps = {
  children: ReactNode;
  className?: string;
  maxWidthClassName?: string;
};

export default function PageHeaderShell({
  children,
  className = "",
  maxWidthClassName = "max-w-7xl",
}: PageHeaderShellProps) {
  return (
    <header className={`px-6 pt-6 ${className}`}>
      <div
        className={`mx-auto ${maxWidthClassName} rounded-2xl border border-[#D4CFC9] bg-[#E8E4DF]/85 px-5 py-4 shadow-[0_12px_30px_rgba(26,20,16,0.10)]`}
      >
        {children}
      </div>
    </header>
  );
}
