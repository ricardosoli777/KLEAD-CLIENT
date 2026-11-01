"use client";

import { useParams, useRouter } from "next/navigation";
import { useEffect } from "react";

export default function InstanceCrmPage() {
  const params = useParams();
  const router = useRouter();
  const id     = params.id as string;

  useEffect(() => {
    if (id) router.replace(`/crm?instancia_id=${encodeURIComponent(id)}`);
  }, [id, router]);

  return (
    <div className="flex items-center justify-center min-h-screen bg-[#F0EDE8]">
      <p className="text-[#6B5E55] text-sm">Carregando CRM...</p>
    </div>
  );
}
