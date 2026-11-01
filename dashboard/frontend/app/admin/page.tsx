"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import PageHeaderShell from "@/components/PageHeaderShell";
import { getAdminOverview, getWhatsappStatus } from "@/lib/api";
import type { AdminOverview, AdminOverviewInstance } from "@/lib/api";

function timeAgo(iso: string) {
  if (!iso) return "sem atividade";
  const diff = Date.now() - new Date(iso).getTime();
  const minutes = Math.floor(diff / 60000);
  if (minutes < 1) return "agora";
  if (minutes < 60) return `${minutes}min atrás`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h atrás`;
  return `${Math.floor(hours / 24)}d atrás`;
}

type ConnStatus = "idle" | "checking" | "connected" | "disconnected" | "error";

// Links de divulgação: cada um é uma página individual do plano com checkout
// embutido e preview próprio ao colar no WhatsApp/Instagram (/planos/[slug]).
// Os links antigos de query (/plans?plano=...) continuam funcionando.
const LINKS_DIVULGACAO: { nome: string; path: string }[] = [
  { nome: "Lite Mensal — R$ 29,88/mês", path: "/planos/lite-mensal" },
  { nome: "Lite Avulso — R$ 39,88 (30 dias)", path: "/planos/lite-avulso" },
  { nome: "Lite Semestral — R$ 149,88", path: "/planos/lite-semestral" },
  { nome: "Lite Anual — R$ 249,88", path: "/planos/lite-anual" },
  { nome: "User — R$ 69,88/mês", path: "/planos/user" },
  { nome: "Pro — R$ 199,88/mês", path: "/planos/pro" },
];

export default function AdminPage() {
  const [data, setData] = useState<AdminOverview | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [connStatus, setConnStatus] = useState<Record<string, ConnStatus>>({});
  const [copiedLink, setCopiedLink] = useState("");

  const load = async () => {
    try {
      const overview = await getAdminOverview();
      setData(overview);
      setError("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Erro ao carregar painel.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
    const t = setInterval(load, 30000);
    return () => clearInterval(t);
  }, []);

  const checkConnection = async (instanceId: string) => {
    setConnStatus(prev => ({ ...prev, [instanceId]: "checking" }));
    try {
      const status = await getWhatsappStatus(instanceId);
      setConnStatus(prev => ({ ...prev, [instanceId]: status.connected ? "connected" : "disconnected" }));
    } catch {
      setConnStatus(prev => ({ ...prev, [instanceId]: "error" }));
    }
  };

  const rows: AdminOverviewInstance[] = data?.instances ?? [];

  return (
    <div className="min-h-screen bg-[#F0EDE8]">
      <PageHeaderShell>
        <div className="text-center">
          <h1 className="text-[#1A1410] text-lg font-bold leading-tight">Admin — Visão geral dos clientes</h1>
          <p className="text-[#6B5E55] text-[12px]">Uso e atividade por instância</p>
        </div>
      </PageHeaderShell>

      <main className="max-w-7xl mx-auto px-4 sm:px-6 py-6">
        {error && (
          <div className="mb-4 rounded-xl border border-red-500/30 bg-red-500/10 px-4 py-3 text-sm text-red-700">
            {error}
          </div>
        )}

        {data && (
          <div className="flex flex-wrap items-center gap-2 mb-4">
            {[
              ["Instâncias", data.totals.instance_count, "text-[#1A1410]"],
              ["Leads (total)", data.totals.lead_count, "text-[#1A1410]"],
              ["Mensagens (7 dias)", data.totals.messages_7d, "text-[#4267B2]"],
            ].map(([label, value, color]) => (
              <div key={label as string} className="bg-[#E8E4DF] border border-[#D4CFC9] rounded-lg px-3 py-2 flex items-center gap-2">
                <span className="text-[#6B5E55] text-[10px] font-semibold uppercase tracking-wide">{label}</span>
                <span className={`text-sm font-bold tabular-nums ${color}`}>{value}</span>
              </div>
            ))}
            <Link
              href="/admin/auditoria"
              className="ml-auto border border-[#CC3300] text-[#CC3300] hover:bg-[#CC3300] hover:text-white font-semibold px-4 py-2 rounded-lg text-sm transition-colors"
            >
              Auditoria →
            </Link>
          </div>
        )}

        {/* Links de divulgação */}
        <div className="mb-6 rounded-2xl border border-[#D4CFC9] bg-[#E8E4DF] p-4">
          <p className="text-[#1A1410] font-bold text-sm mb-1">Links de divulgação</p>
          <p className="text-[#6B5E55] text-xs mb-3">
            Cada link abre a página de planos com o checkout daquele plano já aberto — use em anúncios, bio e stories.
          </p>
          <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
            {LINKS_DIVULGACAO.map(l => (
              <button
                key={l.path}
                onClick={() => {
                  navigator.clipboard.writeText(`${window.location.origin}${l.path}`);
                  setCopiedLink(l.path);
                  setTimeout(() => setCopiedLink(""), 2000);
                }}
                className="flex items-center justify-between gap-2 border border-[#D4CFC9] bg-[#F0EDE8] hover:border-[#CC3300] rounded-lg px-3 py-2 text-left transition-colors"
              >
                <span className="text-[#1A1410] text-xs font-semibold truncate">{l.nome}</span>
                <span className={`text-[10px] font-bold shrink-0 ${copiedLink === l.path ? "text-green-700" : "text-[#CC3300]"}`}>
                  {copiedLink === l.path ? "Copiado!" : "Copiar link"}
                </span>
              </button>
            ))}
          </div>
        </div>

        {loading ? (
          <p className="text-[#6B5E55] text-sm">Carregando...</p>
        ) : rows.length === 0 ? (
          <p className="text-[#6B5E55] text-sm">Nenhuma instância cadastrada.</p>
        ) : (
          <div className="overflow-x-auto rounded-2xl border border-[#D4CFC9] bg-[#E8E4DF]">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-[#6B5E55] text-[11px] uppercase tracking-wide border-b border-[#D4CFC9]">
                  <th className="px-4 py-3">Instância</th>
                  <th className="px-4 py-3">Dono</th>
                  <th className="px-4 py-3">Provedor</th>
                  <th className="px-4 py-3">IA</th>
                  <th className="px-4 py-3">Leads</th>
                  <th className="px-4 py-3">Conversas</th>
                  <th className="px-4 py-3">Msgs (7d)</th>
                  <th className="px-4 py-3">Última atividade</th>
                  <th className="px-4 py-3">Conexão</th>
                  <th className="px-4 py-3"></th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => {
                  const status = connStatus[row.instance_id] ?? "idle";
                  return (
                    <tr key={row.instance_id} className="border-b border-[#D4CFC9]/60 last:border-0">
                      <td className="px-4 py-3 font-semibold text-[#1A1410]">{row.instance_id}</td>
                      <td className="px-4 py-3 text-[#6B5E55]">{row.owner || "—"}</td>
                      <td className="px-4 py-3 text-[#6B5E55]">{row.provider}</td>
                      <td className="px-4 py-3">
                        <span className={row.inbound_ai_enabled ? "text-[#25D366]" : "text-[#6B5E55]"}>
                          {row.inbound_ai_enabled ? "Ligada" : "Desligada"}
                        </span>
                      </td>
                      <td className="px-4 py-3 tabular-nums">{row.lead_count}</td>
                      <td className="px-4 py-3 tabular-nums">{row.conversation_count}</td>
                      <td className="px-4 py-3 tabular-nums">{row.messages_7d}</td>
                      <td className="px-4 py-3 text-[#6B5E55]">{timeAgo(row.last_activity)}</td>
                      <td className="px-4 py-3">
                        {status === "idle" && <span className="text-[#6B5E55] text-xs">—</span>}
                        {status === "checking" && <span className="text-[#F0B429] text-xs">verificando...</span>}
                        {status === "connected" && <span className="text-[#25D366] text-xs font-semibold">conectado</span>}
                        {status === "disconnected" && <span className="text-[#F87171] text-xs font-semibold">desconectado</span>}
                        {status === "error" && <span className="text-[#F87171] text-xs">erro</span>}
                        {status === "idle" && (
                          <button
                            onClick={() => checkConnection(row.instance_id)}
                            className="ml-2 text-[#4267B2] text-xs underline"
                          >
                            verificar
                          </button>
                        )}
                      </td>
                      <td className="px-4 py-3">
                        <Link
                          href={`/instances/${row.instance_id}/inbox`}
                          className="text-[#CC3300] text-xs font-semibold underline"
                        >
                          Abrir →
                        </Link>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </main>
    </div>
  );
}
