"use client";

import { Fragment, useCallback, useEffect, useState } from "react";
import PageHeaderShell from "@/components/PageHeaderShell";
import { getAuditEvents } from "@/lib/api";
import type { AuditEvent } from "@/lib/api";

// Rótulo humano + cor por tipo de ação. Destrutivo em vermelho, segurança em
// amarelo, o resto neutro — pra varredura visual rápida da tabela.
const ACTIONS: Record<string, { label: string; tone: "danger" | "warn" | "neutral" }> = {
  lead_deleted: { label: "Lead excluído", tone: "danger" },
  user_deleted: { label: "Usuário excluído", tone: "danger" },
  user_revoked: { label: "Acesso revogado", tone: "danger" },
  login_failed: { label: "Login falhou", tone: "warn" },
  login_rate_limited: { label: "Login bloqueado", tone: "warn" },
  "2fa_disabled": { label: "2FA desativado", tone: "warn" },
  password_reset: { label: "Senha resetada", tone: "warn" },
  password_changed: { label: "Senha alterada", tone: "warn" },
  "2fa_enabled": { label: "2FA ativado", tone: "neutral" },
  login_ok: { label: "Login", tone: "neutral" },
  user_invited: { label: "Usuário convidado", tone: "neutral" },
  user_reactivated: { label: "Acesso reativado", tone: "neutral" },
  plan_changed: { label: "Plano alterado", tone: "neutral" },
};

const TONE_CLASS = {
  danger: "bg-red-500/10 text-red-700 border-red-500/30",
  warn: "bg-[#F0B429]/10 text-[#8A6100] border-[#F0B429]/40",
  neutral: "bg-[#D4CFC9]/40 text-[#6B5E55] border-[#D4CFC9]",
} as const;

function formatDate(iso: string) {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "medium" });
}

export default function AuditoriaPage() {
  const [entries, setEntries] = useState<AuditEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [filtro, setFiltro] = useState("");
  const [aberto, setAberto] = useState<number | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await getAuditEvents({ action: filtro, limit: 200 });
      setEntries(data.entries || []);
      setError("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Erro ao carregar a auditoria.");
    } finally {
      setLoading(false);
    }
  }, [filtro]);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <div className="min-h-screen bg-[#F0EDE8]">
      <PageHeaderShell>
        <div className="text-center">
          <h1 className="text-[#1A1410] text-lg font-bold leading-tight">Auditoria — Trilha de ações</h1>
          <p className="text-[#6B5E55] text-[12px]">Quem apagou, mudou ou criou o quê</p>
        </div>
      </PageHeaderShell>

      <main className="max-w-7xl mx-auto px-4 sm:px-6 py-6">
        {error && (
          <div className="mb-4 rounded-xl border border-red-500/30 bg-red-500/10 px-4 py-3 text-sm text-red-700">
            {error}
          </div>
        )}

        <div className="mb-4 flex flex-wrap items-center gap-2">
          <select
            value={filtro}
            onChange={e => setFiltro(e.target.value)}
            className="bg-[#E8E4DF] border border-[#D4CFC9] rounded-lg px-3 py-2 text-sm text-[#1A1410] focus:outline-none focus:border-[#CC3300]"
          >
            <option value="">Todas as ações</option>
            {Object.entries(ACTIONS).map(([key, { label }]) => (
              <option key={key} value={key}>{label}</option>
            ))}
          </select>
          <button
            onClick={load}
            className="border border-[#CC3300] text-[#CC3300] hover:bg-[#CC3300] hover:text-white font-semibold px-4 py-2 rounded-lg text-sm transition-colors"
          >
            Atualizar
          </button>
          <span className="text-[#6B5E55] text-xs ml-auto">
            Eventos de exclusão guardam o registro completo — clique na linha para ver e recuperar os dados.
          </span>
        </div>

        {loading ? (
          <p className="text-[#6B5E55] text-sm">Carregando...</p>
        ) : entries.length === 0 ? (
          <p className="text-[#6B5E55] text-sm">Nenhum evento registrado ainda.</p>
        ) : (
          <div className="overflow-x-auto rounded-2xl border border-[#D4CFC9] bg-[#E8E4DF]">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-[#6B5E55] text-[11px] uppercase tracking-wide border-b border-[#D4CFC9]">
                  <th className="px-4 py-3">Quando</th>
                  <th className="px-4 py-3">Ação</th>
                  <th className="px-4 py-3">Quem fez</th>
                  <th className="px-4 py-3">O que aconteceu</th>
                  <th className="px-4 py-3">IP</th>
                </tr>
              </thead>
              <tbody>
                {entries.map((e, i) => {
                  const info = ACTIONS[e.action] ?? { label: e.action, tone: "neutral" as const };
                  const temSnapshot = e.meta != null;
                  const expandido = aberto === i;
                  return (
                    <Fragment key={`${e.at}-${i}`}>
                      <tr
                        onClick={() => setAberto(expandido ? null : i)}
                        className={`border-b border-[#D4CFC9]/60 last:border-0 ${temSnapshot ? "cursor-pointer hover:bg-[#D4CFC9]/20" : ""}`}
                      >
                        <td className="px-4 py-3 text-[#6B5E55] whitespace-nowrap tabular-nums">{formatDate(e.at)}</td>
                        <td className="px-4 py-3">
                          <span className={`inline-block border rounded-full px-2 py-0.5 text-[11px] font-semibold whitespace-nowrap ${TONE_CLASS[info.tone]}`}>
                            {info.label}
                          </span>
                        </td>
                        <td className="px-4 py-3 text-[#1A1410]">
                          {e.actor_email || <span className="text-[#6B5E55] italic">anônimo</span>}
                          {e.actor_funcao && <span className="text-[#6B5E55] text-xs"> ({e.actor_funcao})</span>}
                        </td>
                        <td className="px-4 py-3 text-[#1A1410]">
                          {e.detail || "—"}
                          {temSnapshot && (
                            <span className="ml-2 text-[#4267B2] text-xs underline">
                              {expandido ? "ocultar dados" : "ver dados salvos"}
                            </span>
                          )}
                        </td>
                        <td className="px-4 py-3 text-[#6B5E55] text-xs tabular-nums">{e.ip || "—"}</td>
                      </tr>
                      {expandido && temSnapshot && (
                        <tr className="border-b border-[#D4CFC9]/60">
                          <td colSpan={5} className="px-4 py-3 bg-[#F0EDE8]">
                            <p className="text-[#6B5E55] text-[11px] uppercase tracking-wide font-semibold mb-2">
                              Registro salvo no momento da exclusão
                            </p>
                            <pre className="text-[#1A1410] text-xs bg-[#E8E4DF] border border-[#D4CFC9] rounded-lg p-3 overflow-x-auto whitespace-pre-wrap break-all">
                              {JSON.stringify(e.meta, null, 2)}
                            </pre>
                          </td>
                        </tr>
                      )}
                    </Fragment>
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
