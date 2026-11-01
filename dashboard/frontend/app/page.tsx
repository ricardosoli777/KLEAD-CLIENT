"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { getInstances, getWaStates, getMetaHealth, getAlerts, clearAlerts, type MetaHealthStatus, type Alert } from "@/lib/api";
import { getAuthFuncao } from "@/lib/auth";

type InstanceSummary = {
  status: string;
  envios_hoje: number;
  total_contacts: number;
  logs_count: number;
  falhos_count: number;
  daily_limit: number;
  provider?: string;
  team?: boolean;
  owner?: string | null;
};

const STATUS_COLOR: Record<string, string> = {
  idle:      "bg-[#D4CFC9] text-[#6B5E55]",
  running:   "bg-[#FDE8E4] text-[#CC3300]",
  stopping:  "bg-[#FEF3C7] text-[#B45309]",
  stopped:   "bg-[#FEE2E2] text-[#DC2626]",
  completed: "bg-[#EFF6FF] text-[#2563EB]",
};

const STATUS_LABEL: Record<string, string> = {
  idle:      "Aguardando",
  running:   "Rodando",
  stopping:  "Parando",
  stopped:   "Parada",
  completed: "Concluída",
};

function ProgressBar({ value, status }: { value: number; status: string }) {
  const color =
    status === "running"   ? "bg-[#CC3300]" :
    status === "completed" ? "bg-[#60A5FA]" :
    status === "stopped"   ? "bg-[#F87171]" : "bg-[#D4CFC9]";

  return (
    <div className="w-full bg-[#F0EDE8] rounded-full h-1.5 overflow-hidden">
      <div
        className={`h-1.5 rounded-full transition-all duration-500 ${color}`}
        style={{ width: `${Math.min(value, 100)}%` }}
      />
    </div>
  );
}

export default function Home() {
  const [instances, setInstances] = useState<Record<string, InstanceSummary>>({});
  const [waStates, setWaStates]   = useState<Record<string, string>>({});
  const [metaHealth, setMetaHealth] = useState<Record<string, MetaHealthStatus>>({});
  const [alerts, setAlerts]       = useState<Alert[]>([]);
  const [showAlerts, setShowAlerts] = useState(false);
  const [clearingAlerts, setClearingAlerts] = useState(false);
  const [loading, setLoading]     = useState(true);
  const [isSuperadmin] = useState(() => getAuthFuncao() === "superadmin");

  const loadMetaHealth = useCallback(async (ids: string[]) => {
    const results = await Promise.allSettled(ids.map(id => getMetaHealth(id)));
    setMetaHealth(prev => {
      const next = { ...prev };
      ids.forEach((id, i) => {
        const r = results[i];
        if (r.status === "fulfilled" && r.value) next[id] = r.value;
      });
      return next;
    });
  }, []);

  const loadAlerts = useCallback(async () => {
    const data = await getAlerts("", 50);
    setAlerts(data);
  }, []);

  const load = useCallback(async () => {
    const data = await getInstances();
    setInstances(data);
    setLoading(false);
    const metaIds = (Object.entries(data) as [string, InstanceSummary][])
      .filter(([, inst]) => inst.provider === "meta")
      .map(([id]) => id);
    if (metaIds.length > 0) loadMetaHealth(metaIds);
    loadAlerts();
  }, [loadAlerts, loadMetaHealth]);

  // Instâncias da equipe (SDR/gerente) não aparecem na grade do dashboard —
  // elas ficam na aba Usuários, junto do dono. Superadmin continua vendo tudo.
  const visibleEntries = (Object.entries(instances) as [string, InstanceSummary][])
    .filter(([, inst]) => isSuperadmin || !inst.team);

  const instanceIds = Object.keys(instances).join(",");

  const loadWaStates = useCallback(async (ids: string[]) => {
    // Uma chamada batch em vez de N chamadas por instância.
    try {
      const states = await getWaStates();
      const next: Record<string, string> = {};
      ids.forEach(id => {
        next[id] = states[id] ?? "unknown";
      });
      setWaStates(next);
    } catch {
      // Mantém os estados anteriores em falha transitória.
    }
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    load();
    const fast = setInterval(load, 10000);
    return () => clearInterval(fast);
  }, [load]);

  useEffect(() => {
    const ids = instanceIds.split(",").filter(Boolean);
    if (ids.length === 0) return;
    // eslint-disable-next-line react-hooks/set-state-in-effect
    loadWaStates(ids);
    const slow = setInterval(() => loadWaStates(ids), 15000);
    return () => clearInterval(slow);
  }, [instanceIds, loadWaStates]);

  return (
    <div className="min-h-screen bg-[#F0EDE8]">
      <main className="max-w-7xl mx-auto px-4 sm:px-6 py-6">

        {/* Painel de Alertas */}
        {alerts.length > 0 && (
          <div className="mb-5 rounded-2xl border border-[#F0B429]/40 bg-[#FFFBEB] overflow-hidden">
            <button
              onClick={() => setShowAlerts(v => !v)}
              className="w-full flex items-center justify-between px-4 py-3 text-sm font-semibold text-[#B45309] hover:bg-[#FEF3C7] transition-colors"
            >
              <span className="flex items-center gap-2">
                <span>⚠</span>
                <span>Alertas ({alerts.length})</span>
              </span>
              <div className="flex items-center gap-3">
                {isSuperadmin && (
                  <span
                    onClick={async e => {
                      e.stopPropagation();
                      setClearingAlerts(true);
                      await clearAlerts();
                      setAlerts([]);
                      setClearingAlerts(false);
                    }}
                    className="text-[10px] text-[#6B5E55] hover:text-[#F87171] cursor-pointer px-2 py-0.5 rounded border border-[#D4CFC9] hover:border-[#F87171]/40 transition-colors"
                  >
                    {clearingAlerts ? "..." : "Limpar"}
                  </span>
                )}
                <span className="text-[#6B5E55]">{showAlerts ? "▲" : "▼"}</span>
              </div>
            </button>
            {showAlerts && (
              <div className="px-4 pb-4 space-y-2 max-h-72 overflow-y-auto">
                {alerts.map(a => {
                  const LABEL: Record<string, string> = {
                    lead_sem_telefone: "Sem telefone",
                    lead_sem_campos:   "Sem campos",
                    capi_falha:        "CAPI falhou",
                  };
                  const timeAgo = (() => {
                    const diff = Math.floor((Date.now() - new Date(a.at).getTime()) / 60000);
                    if (diff < 60) return `${diff}min`;
                    if (diff < 1440) return `${Math.floor(diff / 60)}h`;
                    return `${Math.floor(diff / 1440)}d`;
                  })();
                  return (
                    <div key={a.id} className="rounded-xl border border-[#F0B429]/10 bg-[#F0EDE8] px-3 py-2 text-xs">
                      <div className="flex items-center justify-between gap-2 mb-0.5">
                        <span className="font-semibold text-[#F0B429]">{LABEL[a.type] ?? a.type}</span>
                        <div className="flex items-center gap-2 text-[#6B5E55]">
                          {a.instance_id && <span className="text-[#60A5FA]">{a.instance_id}</span>}
                          <span>{timeAgo}</span>
                        </div>
                      </div>
                      <div className="text-[#1A1410]">{a.message}</div>
                      {a.detail && <div className="text-[#6B5E55] mt-0.5">{a.detail}</div>}
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        )}

        {/* Cards de instâncias */}
        {loading ? (
          <div className="text-center py-24 text-[#6B5E55] text-sm">Carregando...</div>
        ) : visibleEntries.length === 0 ? (
          <div className="text-center py-32">
            <div className="w-16 h-16 rounded-full bg-[#E8E4DF] flex items-center justify-center mx-auto mb-4">
              <svg viewBox="0 0 24 24" className="w-8 h-8 fill-[#6B5E55]">
                <path d="M17.472 14.382c-.297-.149-1.758-.867-2.03-.967-.273-.099-.471-.148-.67.15-.197.297-.767.966-.94 1.164-.173.199-.347.223-.644.075-.297-.15-1.255-.463-2.39-1.475-.883-.788-1.48-1.761-1.653-2.059-.173-.297-.018-.458.13-.606.134-.133.298-.347.446-.52.149-.174.198-.298.298-.497.099-.198.05-.371-.025-.52-.075-.149-.669-1.612-.916-2.207-.242-.579-.487-.5-.669-.51-.173-.008-.371-.01-.57-.01-.198 0-.52.074-.792.372-.272.297-1.04 1.016-1.04 2.479 0 1.462 1.065 2.875 1.213 3.074.149.198 2.096 3.2 5.077 4.487.709.306 1.262.489 1.694.625.712.227 1.36.195 1.871.118.571-.085 1.758-.719 2.006-1.413.248-.694.248-1.289.173-1.413-.074-.124-.272-.198-.57-.347m-5.421 7.403h-.004a9.87 9.87 0 01-5.031-1.378l-.361-.214-3.741.982.998-3.648-.235-.374a9.86 9.86 0 01-1.51-5.26c.001-5.45 4.436-9.884 9.888-9.884 2.64 0 5.122 1.03 6.988 2.898a9.825 9.825 0 012.893 6.994c-.003 5.45-4.437 9.884-9.885 9.884m8.413-18.297A11.815 11.815 0 0012.05 0C5.495 0 .16 5.335.157 11.892c0 2.096.547 4.142 1.588 5.945L.057 24l6.305-1.654a11.882 11.882 0 005.683 1.448h.005c6.554 0 11.89-5.335 11.893-11.893a11.821 11.821 0 00-3.48-8.413z"/>
              </svg>
            </div>
            <p className="text-[#1A1410] text-lg font-semibold mb-2">Nenhuma instância criada</p>
            <p className="text-[#6B5E55] text-sm mb-6">Crie uma instância para começar a disparar mensagens</p>
            <Link
              href="/instances/new"
              className="inline-block bg-[#CC3300] hover:bg-[#991F00] text-[#F0EDE8] font-bold px-6 py-3 rounded-xl transition-colors text-sm"
            >
              Criar primeira instância
            </Link>
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4 lg:gap-5">
            {visibleEntries.map(([id, inst]) => {
              const progressMax = inst.daily_limit > 0 ? inst.daily_limit : inst.total_contacts;
              const progress    = progressMax > 0 ? Math.round(inst.envios_hoje / progressMax * 100) : 0;

              return (
                <div key={id} className="bg-white rounded-2xl border border-[#D4CFC9] p-5 shadow-sm hover:shadow-md hover:border-[#CC3300]/30 transition-all">
                  <div className="flex items-start justify-between mb-4">
                    <div className="flex items-center gap-3">
                      <div className="w-10 h-10 rounded-full bg-[#FDE8E4] border border-[#CC3300]/20 flex items-center justify-center text-[#CC3300] font-bold text-base">
                        {id.charAt(0).toUpperCase()}
                      </div>
                      <div>
                        <h2 className="font-bold text-[#1A1410] text-base leading-tight">{id}</h2>
                        <div className="flex items-center gap-1.5 mt-1 flex-wrap">
                          {(() => {
                            const wa = waStates[id] ?? "unknown";
                            return (
                              <span className={`flex items-center gap-1 text-xs font-semibold px-2 py-0.5 rounded-full ${
                                wa === "open"       ? "bg-[#FDE8E4] text-[#CC3300]" :
                                wa === "connecting" ? "bg-[#FEF3C7] text-[#B45309]" :
                                                     "bg-[#FEE2E2] text-[#DC2626]"
                              }`}>
                                <span className={`w-1.5 h-1.5 rounded-full ${
                                  wa === "open"       ? "bg-[#CC3300] animate-pulse" :
                                  wa === "connecting" ? "bg-[#F0B429] animate-pulse" :
                                                       "bg-[#F87171]"
                                }`} />
                                {wa === "open" ? "WA Conectado" : wa === "unknown" ? "WA ?" : "WA Desconectado"}
                              </span>
                            );
                          })()}
                          <span className={`text-xs font-semibold px-2 py-0.5 rounded-full ${STATUS_COLOR[inst.status] ?? "bg-[#D4CFC9] text-[#6B5E55]"}`}>
                            {STATUS_LABEL[inst.status] ?? inst.status}
                          </span>
                        </div>
                      </div>
                    </div>
                  </div>

                  {inst.provider === "meta" && (() => {
                    const h = metaHealth[id];
                    if (!h) return (
                      <div className="flex items-center gap-1.5 mb-4 text-[11px] text-[#6B5E55]">
                        <span className="w-1.5 h-1.5 rounded-full bg-[#6B5E55] animate-pulse" />
                        Verificando saúde Meta...
                      </div>
                    );
                    const lastCapiLabel = h.last_capi_at
                      ? (() => {
                          const diff = Math.floor((Date.now() - new Date(h.last_capi_at).getTime()) / 60000);
                          return diff < 60 ? `${diff}min` : diff < 1440 ? `${Math.floor(diff / 60)}h` : `${Math.floor(diff / 1440)}d`;
                        })()
                      : null;
                    return (
                      <div className="flex flex-wrap items-center gap-1.5 mb-4">
                        <span className={`flex items-center gap-1 text-[11px] font-semibold px-2 py-0.5 rounded-full ${h.token_valid ? "bg-[#FDE8E4] text-[#CC3300]" : "bg-[#FEE2E2] text-[#DC2626]"}`}>
                          {h.token_valid ? "✓" : "✗"} Token
                        </span>
                        <span className={`flex items-center gap-1 text-[11px] font-semibold px-2 py-0.5 rounded-full ${h.page_subscribed ? "bg-[#FDE8E4] text-[#CC3300]" : "bg-[#FEE2E2] text-[#DC2626]"}`}>
                          {h.page_subscribed ? "✓" : "✗"} Página
                        </span>
                        {h.capi_enabled && (
                          <span className={`flex items-center gap-1 text-[11px] font-semibold px-2 py-0.5 rounded-full ${h.last_capi_ok === false ? "bg-[#FEE2E2] text-[#DC2626]" : "bg-[#FDE8E4] text-[#CC3300]"}`}
                            title={h.last_capi_at ? `Último CAPI: ${h.last_capi_event} às ${h.last_capi_at.slice(0, 16).replace("T", " ")}` : "CAPI ativo — aguardando primeiro evento"}
                          >
                            {lastCapiLabel ? `CAPI ${lastCapiLabel}` : "✓ CAPI"}
                          </span>
                        )}
                      </div>
                    );
                  })()}

                  {(inst.status === "running" || inst.status === "completed" || inst.status === "stopped") && (
                    <div className="mb-4">
                      <div className="flex justify-between text-[11px] text-[#6B5E55] mb-1.5">
                        <span>Progresso</span>
                        <span className="font-semibold">{progress}%</span>
                      </div>
                      <ProgressBar value={progress} status={inst.status} />
                    </div>
                  )}


                  <div className="flex flex-col gap-2">
                    <Link
                      href={`/instances/${id}`}
                      className="text-center bg-[#CC3300] hover:bg-[#991F00] text-[#F0EDE8] font-bold py-3 rounded-xl transition-colors text-sm shadow-sm"
                    >
                      Abrir disparador
                    </Link>
                    <div className="grid grid-cols-2 gap-2">
                      <Link
                        href={`/instances/${id}/inbox`}
                        className="text-center bg-[#F0EDE8] hover:bg-[#D4CFC9] border border-[#D4CFC9]/80 text-[#1A1410] font-bold py-2.5 rounded-xl transition-colors text-sm"
                      >
                        Inbox SDR
                      </Link>
                      <Link
                        href={`/instances/${id}/crm`}
                        className="text-center bg-[#F0EDE8] hover:bg-[#D4CFC9] border border-[#D4CFC9]/80 text-[#60A5FA] font-bold py-2.5 rounded-xl transition-colors text-sm"
                      >
                        Ver CRM
                      </Link>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </main>

      {/* FAB — Nova Instância (mobile, só superadmin, só quando já há instâncias) */}
      {isSuperadmin && !loading && Object.keys(instances).length > 0 && (
        <Link
          href="/instances/new"
          className="fixed bottom-6 right-5 z-50 flex items-center gap-2 rounded-full bg-[#CC3300] px-5 py-3 text-sm font-bold text-white shadow-lg transition-colors hover:bg-[#991F00] lg:hidden"
        >
          + Nova Instância
        </Link>
      )}
    </div>
  );
}
