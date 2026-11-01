"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import PageHeaderShell from "@/components/PageHeaderShell";
import {
  deleteAssignmentRule,
  distributeUnassignedLeads,
  generateAssignmentQueue,
  getAssignmentAttendants,
  getAssignmentQueue,
  getAssignmentRules,
  getAssignmentSettings,
  getMetaAdsMetrics,
  getNextAssignmentAttendant,
  getUnassignedLeadCount,
  saveAssignmentRule,
  startAssignmentDistribution,
  stopAssignmentDistribution,
  shuffleAssignmentQueue,
  updateAssignmentAttendant,
  updateAssignmentSettings,
} from "@/lib/api";
import type { AssignmentAttendant, AssignmentRule, AssignmentSettings } from "@/lib/api";

const FUNCAO_LABEL: Record<string, string> = {
  superadmin: "Superadmin",
  admin: "Admin",
  gerente: "Gerente",
  sdr: "SDR",
  instancia: "Instancia",
};

const STATUS_LABEL: Record<string, string> = {
  available: "Disponivel",
  paused: "Pausado",
  absent: "Ausente",
};

function initials(name: string, email: string) {
  const base = name || email;
  return base
    .split(" ")
    .filter(Boolean)
    .map(part => part[0])
    .slice(0, 2)
    .join("")
    .toUpperCase();
}

export default function AssignmentsPage() {
  const router = useRouter();
  const [settings, setSettings] = useState<AssignmentSettings | null>(null);
  const [attendants, setAttendants] = useState<AssignmentAttendant[]>([]);
  const [rules, setRules] = useState<AssignmentRule[]>([]);
  const [newCampanha, setNewCampanha]               = useState("");
  const [availableCampaigns, setAvailableCampaigns] = useState<string[]>([]);
  const [loadingCampaigns, setLoadingCampaigns]     = useState(false);
  const [togglingCampanha, setTogglingCampanha]     = useState("");
  const [queue, setQueue] = useState<AssignmentAttendant[]>([]);
  const [next, setNext] = useState<AssignmentAttendant | null>(null);
  const [unassignedCount, setUnassignedCount] = useState(0);
  const [loading, setLoading] = useState(true);
  const [actionLoading, setActionLoading] = useState("");
  const [savingEmail, setSavingEmail] = useState("");
  const [error, setError] = useState("");

  const showNoCampaignWarning = useMemo(
    () => !loading && !!settings?.enabled && settings?.mode === "automatic" && !rules.some(r => r.enabled),
    [loading, settings, rules]
  );

  const load = useCallback(async () => {
    setError("");
    try {
      const [settingsData, attendantsData, nextData, queueData, unassignedData] = await Promise.all([
        getAssignmentSettings(),
        getAssignmentAttendants(),
        getNextAssignmentAttendant(),
        getAssignmentQueue(),
        getUnassignedLeadCount(),
      ]);
      const rulesData = await getAssignmentRules();

      if ((settingsData as AssignmentSettings & { detail?: unknown }).detail) {
        router.push("/");
        return;
      }

      setSettings(settingsData);
      setAttendants(attendantsData);
      setRules(rulesData.rules ?? []);
      setNext(nextData.next);
      setQueue(queueData.queue);
      setUnassignedCount(unassignedData.count);

      // Busca campanhas Meta de todas as instâncias
      const ids = (attendantsData as AssignmentAttendant[])
        .map(a => a.instance_id).filter(Boolean) as string[];
      if (ids.length > 0) {
        setLoadingCampaigns(true);
        Promise.allSettled(ids.map(id => getMetaAdsMetrics(id))).then(results => {
          const all = new Set<string>();
          results.forEach(r => {
            if (r.status === "fulfilled") {
              (r.value.campaigns ?? []).forEach((c: { campaign: string }) => {
                if (c.campaign) all.add(c.campaign);
              });
            }
          });
          setAvailableCampaigns([...all].sort());
          setLoadingCampaigns(false);
        });
      }
    } catch {
      setError("Nao foi possivel carregar a roleta.");
    } finally {
      setLoading(false);
    }
  }, [router]);

  useEffect(() => {
    load();
  }, [load]);

  const activeCount = useMemo(
    () => attendants.filter(a => a.active && a.status === "available").length,
    [attendants]
  );

  async function updateMode(mode: AssignmentSettings["mode"]) {
    if (!settings) return;
    const previous = settings;
    setSettings({ ...settings, mode });
    try {
      const updated = await updateAssignmentSettings({ mode });
      setSettings(updated);
    } catch {
      setSettings(previous);
      setError("Nao foi possivel atualizar o modo da roleta.");
    }
  }

  async function generateQueue() {
    setActionLoading("queue");
    setError("");
    try {
      const result = await generateAssignmentQueue();
      setSettings(result.settings);
      setQueue(result.queue);
      await load();
    } catch {
      setError("Nao foi possivel gerar a fila.");
    } finally {
      setActionLoading("");
    }
  }

  async function shuffleQueue() {
    setActionLoading("shuffle");
    setError("");
    try {
      const result = await shuffleAssignmentQueue();
      setSettings(result.settings);
      setQueue(result.queue);
      await load();
    } catch {
      setError("Nao foi possivel sortear a fila.");
    } finally {
      setActionLoading("");
    }
  }

  async function startDistribution() {
    setActionLoading("start");
    setError("");
    try {
      const result = await startAssignmentDistribution();
      setSettings(result.settings);
      if (!result.ok) {
        setError("Gere a fila antes de iniciar a distribuicao.");
      }
      await load();
    } catch {
      setError("Nao foi possivel iniciar a distribuicao.");
    } finally {
      setActionLoading("");
    }
  }

  async function stopDistribution() {
    setActionLoading("stop");
    setError("");
    try {
      const result = await stopAssignmentDistribution();
      setSettings(result.settings);
      await load();
    } catch {
      setError("Nao foi possivel pausar a distribuicao.");
    } finally {
      setActionLoading("");
    }
  }

  async function distributeUnassigned() {
    setActionLoading("distribute");
    setError("");
    try {
      const result = await distributeUnassignedLeads();
      setUnassignedCount(result.remaining);
      setNext(result.next);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Nao foi possivel distribuir os leads.");
    } finally {
      setActionLoading("");
    }
  }

  async function toggleCampanha(campanha: string) {
    const existing = rules.find(r => (r.campanha || r.name) === campanha);
    setTogglingCampanha(campanha);
    try {
      if (existing) {
        await deleteAssignmentRule(existing.id);
      } else {
        await saveAssignmentRule({ name: campanha, enabled: true, campanha, origem: "", instancia_id: "" });
      }
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Erro ao atualizar campanha.");
    } finally {
      setTogglingCampanha("");
    }
  }

  async function addCampanha() {
    const campanha = newCampanha.trim();
    if (!campanha) return;
    setActionLoading("rule");
    setError("");
    try {
      await saveAssignmentRule({ name: campanha, enabled: true, campanha, origem: "", instancia_id: "" });
      setNewCampanha("");
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Nao foi possivel adicionar a campanha.");
    } finally {
      setActionLoading("");
    }
  }

  async function removeRule(ruleId: string) {
    setActionLoading(ruleId);
    setError("");
    try {
      await deleteAssignmentRule(ruleId);
      await load();
    } catch {
      setError("Nao foi possivel remover a regra.");
    } finally {
      setActionLoading("");
    }
  }

  async function patchAttendant(email: string, data: Partial<AssignmentAttendant>) {
    setSavingEmail(email);
    setError("");
    try {
      await updateAssignmentAttendant(email, data);
      await load();
    } catch {
      setError("Nao foi possivel atualizar esta instancia.");
    } finally {
      setSavingEmail("");
    }
  }

  return (
    <div className="min-h-screen bg-[#F0EDE8]">
      <PageHeaderShell maxWidthClassName="max-w-5xl">
        <div className="text-center">
          <h1 className="text-[#1A1410] text-lg font-bold leading-tight">Roleta de leads</h1>
          <p className="text-[#6B5E55] text-[12px] mt-1">
            Escolha as instancias participantes, gere a fila do dia e inicie a distribuicao.
          </p>
        </div>
      </PageHeaderShell>

      <main className="max-w-5xl mx-auto px-6 py-6">
        {error && (
          <div className="mb-4 rounded-xl border border-red-500/30 bg-red-500/10 px-4 py-3 text-sm text-red-700">
            {error}
          </div>
        )}

        {showNoCampaignWarning && (
          <div className="mb-4 rounded-xl border border-[#F0B429]/30 bg-[#2D2000] px-4 py-3 text-sm text-[#F0B429]">
            Modo automático ativo, mas nenhuma campanha está habilitada na roleta. Nenhum lead novo será distribuído automaticamente até que uma campanha seja ativada abaixo.
          </div>
        )}

        <div className="flex flex-wrap gap-2 mb-4">
          <div className="bg-[#E8E4DF] border border-[#D4CFC9] rounded-lg px-3 py-2 flex items-center gap-2">
            <span className="text-[#6B5E55] text-[10px] font-semibold uppercase tracking-wide">Distribuição</span>
            <span className={`text-sm font-bold ${settings?.enabled ? "text-[#CC3300]" : "text-[#F0B429]"}`}>
              {settings?.enabled ? "Iniciada" : "Pausada"}
            </span>
          </div>
          <div className="bg-[#E8E4DF] border border-[#D4CFC9] rounded-lg px-3 py-2 flex items-center gap-2">
            <span className="text-[#6B5E55] text-[10px] font-semibold uppercase tracking-wide">Modo</span>
            <span className="text-sm font-bold text-[#60A5FA]">
              {settings?.mode === "automatic" ? "Automático" : "Manual"}
            </span>
          </div>
          <div className="bg-[#E8E4DF] border border-[#D4CFC9] rounded-lg px-3 py-2 flex items-center gap-2">
            <span className="text-[#6B5E55] text-[10px] font-semibold uppercase tracking-wide">Sem responsável</span>
            <span className="text-sm font-bold text-[#1A1410]">{unassignedCount}</span>
          </div>
        </div>

        <div className="bg-[#E8E4DF] border border-[#D4CFC9] rounded-xl p-4 mb-6">
          <div className="flex flex-col lg:flex-row lg:items-center justify-between gap-4">
            <div>
              <p className="text-[#1A1410] text-sm font-semibold">Modo de distribuicao</p>
              <p className="text-[#6B5E55] text-xs mt-1">
                Automatico atribui leads novos ao iniciar. Manual deixa a fila pronta para operacao assistida.
              </p>
            </div>
            <div className="flex flex-wrap gap-2">
              <button
                onClick={() => updateMode("automatic")}
                disabled={!settings || loading}
                className={`px-4 py-2 rounded-lg text-xs font-semibold border transition-colors disabled:opacity-50 ${
                  settings?.mode === "automatic"
                    ? "bg-[#3D0A00] border-[#CC3300]/40 text-[#CC3300]"
                    : "bg-[#F0EDE8] border-[#D4CFC9] text-[#1A1410] hover:border-[#CC3300] hover:text-[#CC3300]"
                }`}
              >
                Automatica
              </button>
              <button
                onClick={() => updateMode("manual")}
                disabled={!settings || loading}
                className={`px-4 py-2 rounded-lg text-xs font-semibold border transition-colors disabled:opacity-50 ${
                  settings?.mode === "manual"
                    ? "bg-[#3D0A00] border-[#CC3300]/40 text-[#CC3300]"
                    : "bg-[#F0EDE8] border-[#D4CFC9] text-[#1A1410] hover:border-[#CC3300] hover:text-[#CC3300]"
                }`}
              >
                Manual
              </button>
            </div>
          </div>
        </div>

        <div className="bg-[#E8E4DF] border border-[#D4CFC9] rounded-xl p-4 mb-6">
          <div className="flex flex-col lg:flex-row lg:items-center justify-between gap-4">
            <div>
              <p className="text-[#1A1410] text-sm font-semibold">Fila de distribuicao</p>
              <p className="text-[#6B5E55] text-xs mt-1">
                {queue.length > 0
                  ? `${queue.length} posicoes na fila. ${activeCount} instancias participantes disponiveis.`
                  : `${activeCount} instancias participantes disponiveis para gerar a fila.`}
              </p>
            </div>
            <div className="flex flex-wrap gap-2">
              <button
                onClick={generateQueue}
                disabled={loading || actionLoading !== "" || activeCount === 0}
                className="px-4 py-2 rounded-lg bg-[#3D0A00] text-[#CC3300] hover:bg-[#123829] text-xs font-semibold transition-colors disabled:opacity-50"
              >
                {actionLoading === "queue" ? "Gerando..." : "Gerar fila"}
              </button>
              <button
                onClick={shuffleQueue}
                disabled={loading || actionLoading !== "" || activeCount < 2}
                className="px-4 py-2 rounded-lg bg-[#0E0E18] text-[#CC3300] hover:bg-[#252535] text-xs font-semibold transition-colors disabled:opacity-50"
              >
                {actionLoading === "shuffle" ? "Sorteando..." : "Sortear ordem"}
              </button>
              {settings?.enabled ? (
                <button
                  onClick={stopDistribution}
                  disabled={loading || actionLoading !== ""}
                  className="px-4 py-2 rounded-lg bg-[#2D2000] text-[#F0B429] hover:bg-[#3A2A00] text-xs font-semibold transition-colors disabled:opacity-50"
                >
                  {actionLoading === "stop" ? "Pausando..." : "Pausar distribuicao"}
                </button>
              ) : (
                <button
                  onClick={startDistribution}
                  disabled={loading || actionLoading !== "" || queue.length === 0}
                  className="px-4 py-2 rounded-lg bg-[#CC3300] text-[#F0EDE8] hover:bg-[#991F00] text-xs font-semibold transition-colors disabled:opacity-50"
                >
                  {actionLoading === "start" ? "Iniciando..." : "Iniciar distribuicao"}
                </button>
              )}
              <button
                onClick={distributeUnassigned}
                disabled={loading || actionLoading !== "" || queue.length === 0 || unassignedCount === 0}
                className="px-4 py-2 rounded-lg bg-[#102338] border border-[#60A5FA]/60 text-[#60A5FA] hover:bg-[#1a3a5c] text-xs font-semibold transition-colors disabled:opacity-50"
              >
                {actionLoading === "distribute" ? "Distribuindo..." : "Distribuir sem responsavel"}
              </button>
            </div>
          </div>

          {queue.length > 0 && (
            <>
              <div className="mt-4 flex items-center gap-2 text-xs text-[#6B5E55]">
                <span>Proxima instancia:</span>
                <span className="font-semibold text-[#1A1410]">{next ? next.nome : "Sem instancia disponivel"}</span>
              </div>
              {activeCount > 1 && (
                <p className="mt-2 text-xs text-[#6B5E55]">
                  Use <span className="text-[#CC3300] font-semibold">Sortear ordem</span> para embaralhar os participantes antes de iniciar a distribuicao.
                </p>
              )}
              <div className="mt-3 flex flex-wrap gap-2">
                {queue.map((attendant, index) => (
                  <span
                    key={`${attendant.instance_id || attendant.email}-${index}`}
                    className="rounded-full bg-[#F0EDE8] border border-[#D4CFC9] px-3 py-1 text-xs text-[#1A1410]"
                  >
                    {index + 1}. {attendant.nome}
                  </span>
                ))}
              </div>
            </>
          )}
        </div>

        <div className="bg-[#E8E4DF] border border-[#D4CFC9] rounded-xl p-4 mb-6">
          <p className="text-[#1A1410] text-sm font-semibold mb-1">Campanhas na roleta</p>
          <p className="text-[#6B5E55] text-xs mb-4">
            Selecione quais campanhas terão leads distribuídos automaticamente.
          </p>

          {loadingCampaigns ? (
            <p className="text-[#6B5E55] text-xs mb-3">Buscando campanhas ativas...</p>
          ) : availableCampaigns.length > 0 ? (
            <div className="space-y-2 mb-4">
              {availableCampaigns.map(campanha => {
                const isSelected = rules.some(r => (r.campanha || r.name) === campanha);
                const isToggling = togglingCampanha === campanha;
                return (
                  <label
                    key={campanha}
                    className={`flex items-center gap-3 px-3 py-2.5 rounded-lg border cursor-pointer transition-colors ${
                      isSelected
                        ? "bg-[#3D0A00] border-[#CC3300]/40"
                        : "bg-[#F0EDE8] border-[#D4CFC9] hover:border-[#CC3300]/40"
                    } ${isToggling ? "opacity-60 pointer-events-none" : ""}`}
                  >
                    <input
                      type="checkbox"
                      checked={isSelected}
                      onChange={() => toggleCampanha(campanha)}
                      className="accent-[#CC3300] w-4 h-4 shrink-0"
                    />
                    <span className={`text-sm font-medium truncate ${isSelected ? "text-[#CC3300]" : "text-[#1A1410]"}`}>
                      {campanha}
                    </span>
                    {isToggling && (
                      <svg className="w-3.5 h-3.5 animate-spin text-[#CC3300] ml-auto shrink-0" viewBox="0 0 24 24" fill="none">
                        <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"/>
                        <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z"/>
                      </svg>
                    )}
                  </label>
                );
              })}
            </div>
          ) : (
            <p className="text-[#6B5E55] text-xs mb-3">Nenhuma campanha Meta encontrada nas instâncias.</p>
          )}

          {/* Campanhas manuais (não vêm do Meta) */}
          {rules.filter(r => !availableCampaigns.includes(r.campanha || r.name)).length > 0 && (
            <div className="flex flex-wrap gap-2 mb-4">
              {rules
                .filter(r => !availableCampaigns.includes(r.campanha || r.name))
                .map(rule => (
                  <div key={rule.id} className="flex items-center gap-2 rounded-full px-3 py-1.5 border bg-[#3D0A00] border-[#CC3300]/40 text-[#CC3300] text-xs font-semibold">
                    <span>{rule.campanha || rule.name}</span>
                    <button onClick={() => removeRule(rule.id)} disabled={actionLoading !== ""} className="opacity-60 hover:opacity-100 disabled:opacity-30">×</button>
                  </div>
                ))}
            </div>
          )}

          <div className="flex gap-2 pt-2 border-t border-[#D4CFC9]">
            <input
              value={newCampanha}
              onChange={(e) => setNewCampanha(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && addCampanha()}
              placeholder="Adicionar campanha manualmente..."
              className="flex-1 bg-[#F0EDE8] border border-[#D4CFC9] text-[#1A1410] placeholder-[#6B5E55] rounded-lg px-3 py-2 text-sm outline-none focus:border-[#CC3300]"
            />
            <button
              onClick={addCampanha}
              disabled={loading || actionLoading !== "" || !newCampanha.trim()}
              className="px-4 py-2 rounded-lg bg-[#F0EDE8] border border-[#D4CFC9] text-[#1A1410] hover:border-[#CC3300] hover:text-[#CC3300] text-xs font-semibold transition-colors disabled:opacity-50 shrink-0"
            >
              {actionLoading === "rule" ? "..." : "+ Adicionar"}
            </button>
          </div>
        </div>

        <div className="mb-3">
          <p className="text-[#1A1410] text-sm font-semibold">Instâncias participantes</p>
          <p className="text-[#6B5E55] text-xs mt-0.5">
            Selecione quais instâncias entram no sorteio. Apenas as marcadas como <span className="font-semibold text-[#CC3300]">Disponível</span> participam da fila.
          </p>
        </div>

        {loading ? (
          <div className="text-center py-24 text-[#6B5E55] text-sm">Carregando...</div>
        ) : (
          <div className="space-y-3">
            {attendants.length === 0 ? (
              <div className="bg-[#E8E4DF] border border-[#D4CFC9] rounded-xl p-6 text-center">
                <p className="text-[#1A1410] font-semibold">Nenhuma instancia encontrada</p>
                <p className="text-[#6B5E55] text-sm mt-1">Cadastre instancias WhatsApp para montar a fila.</p>
              </div>
            ) : (
              attendants.map(attendant => {
                const available = attendant.active && attendant.status === "available";
                const participantId = attendant.instance_id || attendant.email;
                const saving = savingEmail === participantId;

                return (
                  <div
                    key={participantId}
                    className="bg-[#E8E4DF] border border-[#D4CFC9] rounded-xl px-5 py-4 flex flex-col md:flex-row md:items-center gap-4"
                  >
                    <div className="flex items-center gap-4 flex-1 min-w-0">
                      <div className={`w-10 h-10 rounded-full flex items-center justify-center text-sm font-bold shrink-0 ${
                        available ? "bg-[#CC3300] text-[#F0EDE8]" : "bg-[#D4CFC9] text-[#6B5E55]"
                      }`}>
                        {initials(attendant.nome, participantId)}
                      </div>
                      <div className="min-w-0">
                        <p className="text-[#1A1410] font-semibold text-sm truncate">{attendant.nome}</p>
                        <p className="text-[#6B5E55] text-xs truncate">{participantId}</p>
                        {attendant.evo_instance_name && (
                          <p className="text-[#3D4F5A] text-[10px] truncate">{attendant.evo_instance_name}</p>
                        )}
                      </div>
                    </div>

                    <div className="grid grid-cols-3 gap-2 md:w-[330px]">
                      <div className="bg-[#F0EDE8] rounded-lg px-3 py-2">
                        <p className="text-[#6B5E55] text-[11px]">Tipo</p>
                        <p className="text-[#1A1410] text-xs font-semibold truncate">
                          {FUNCAO_LABEL[attendant.funcao] || attendant.funcao}
                        </p>
                      </div>
                      <div className="bg-[#F0EDE8] rounded-lg px-3 py-2">
                        <p className="text-[#6B5E55] text-[11px]">Status</p>
                        <p className={`text-xs font-semibold truncate ${available ? "text-[#CC3300]" : "text-[#F0B429]"}`}>
                          {STATUS_LABEL[attendant.status] || attendant.status}
                        </p>
                      </div>
                      <div className="bg-[#F0EDE8] rounded-lg px-3 py-2">
                        <p className="text-[#6B5E55] text-[11px]">Leads</p>
                        <p className="text-[#60A5FA] text-xs font-semibold">{attendant.total_assigned}</p>
                      </div>
                    </div>

                    <div className="flex gap-2 shrink-0">
                      <button
                        onClick={() => patchAttendant(participantId, { active: !attendant.active })}
                        disabled={saving}
                        className={`px-3 py-2 rounded-lg border text-xs font-semibold transition-colors disabled:opacity-50 ${
                          attendant.active
                            ? "border-[#D4CFC9] text-[#F0B429] hover:bg-[#2D2000]"
                            : "border-[#CC3300]/40 text-[#CC3300] hover:bg-[#3D0A00]"
                        }`}
                      >
                        {attendant.active ? "Remover" : "Incluir"}
                      </button>
                      <button
                        onClick={() => patchAttendant(participantId, {
                          status: attendant.status === "paused" ? "available" : "paused",
                          active: true,
                        })}
                        disabled={saving}
                        className="px-3 py-2 rounded-lg border border-[#D4CFC9] text-[#6B5E55] hover:text-[#1A1410] hover:bg-[#D4CFC9] text-xs font-semibold transition-colors disabled:opacity-50"
                      >
                        {attendant.status === "paused" ? "Retomar" : "Pausar"}
                      </button>
                    </div>
                  </div>
                );
              })
            )}
          </div>
        )}
      </main>
    </div>
  );
}
