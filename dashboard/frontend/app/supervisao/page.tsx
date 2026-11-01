"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import type { KeyboardEvent } from "react";
import Link from "next/link";
import PageHeaderShell from "@/components/PageHeaderShell";
import {
  assignConversation,
  crmGetLeads,
  getAssignmentAttendants,
  getConversation,
  getInstances,
  humanSendMessage,
  listConversations,
  releaseConversation,
  retroactiveAssign,
  takeoverConversation,
} from "@/lib/api";
import type { AssignmentAttendant, ConversationDetail, ConversationSummary, CrmLead } from "@/lib/api";

type InstanceSummary = {
  status: string;
  envios_hoje: number;
  total_contacts: number;
  logs_count: number;
  falhos_count: number;
  daily_limit: number;
};

type SupervisionConversation = ConversationSummary & {
  instance_id: string;
  lead?: CrmLead;
  assigned_to: string;
  assigned_to_name: string;
  origem: string;
  campanha: string;
  followup_date: string;
};

const STAGE_LABEL: Record<string, string> = {
  qualifying: "Qualificando",
  initiated: "Iniciou conversa",
  handoff: "Handoff",
  disqualified: "Desqualificado",
};

function timeAgo(iso: string) {
  if (!iso) return "";
  const diff = Date.now() - new Date(iso).getTime();
  const minutes = Math.floor(diff / 60000);
  if (minutes < 1) return "agora";
  if (minutes < 60) return `${minutes}min`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h`;
  return `${Math.floor(hours / 24)}d`;
}

function isFollowupLate(date: string) {
  if (!date) return false;
  return new Date(date).toISOString().slice(0, 10) < new Date().toISOString().slice(0, 10);
}

function isStale(conversation: SupervisionConversation) {
  if (!conversation.last_activity) return false;
  return Date.now() - new Date(conversation.last_activity).getTime() > 24 * 60 * 60 * 1000;
}

function stageClass(stage: string) {
  if (stage === "initiated" || stage === "handoff") return "bg-[#3D0A00] text-[#CC3300]";
  if (stage === "disqualified") return "bg-[#2D0A0A] text-[#F87171]";
  return "bg-[#2D2000] text-[#F0B429]";
}

function scoreClass(score: number | null) {
  if (score === null || score === undefined) return "text-[#6B5E55]";
  if (score >= 70) return "text-[#CC3300]";
  if (score >= 40) return "text-[#F0B429]";
  return "text-[#F87171]";
}

export default function SupervisaoPage() {
  const [instances, setInstances] = useState<Record<string, InstanceSummary>>({});
  const [attendants, setAttendants] = useState<AssignmentAttendant[]>([]);
  const [items, setItems] = useState<SupervisionConversation[]>([]);
  const [selected, setSelected] = useState<{ instance_id: string; phone: string } | null>(null);
  const [detail, setDetail] = useState<ConversationDetail | null>(null);
  const [message, setMessage] = useState("");
  const [sending, setSending] = useState(false);
  const [takingOver, setTakingOver] = useState(false);
  const [releasing, setReleasing] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [retroLoading, setRetroLoading] = useState(false);
  const [retroResult, setRetroResult] = useState<{ assigned: number; skipped: number } | null>(null);
  const [assignTo, setAssignTo] = useState("");
  const [assigning, setAssigning] = useState(false);
  const [filters, setFilters] = useState({
    instance_id: "all",
    attendant: "all",
    flag: "all",
    search: "",
  });

  const load = useCallback(async () => {
    setError("");
    try {
      const [instancesData, leadsData, attendantsData] = await Promise.all([
        getInstances(),
        crmGetLeads(),
        getAssignmentAttendants().catch(() => [] as AssignmentAttendant[]),
      ]);
      const ids = Object.keys(instancesData || {});
      const lists = await Promise.all(
        ids.map(async (instance_id) => {
          const data = await listConversations(instance_id).catch(() => ({ conversations: [] as ConversationSummary[] }));
          return (data.conversations ?? []).map((conversation) => ({ ...conversation, instance_id }));
        })
      );
      const leadsByKey = new Map<string, CrmLead>();
      for (const lead of leadsData ?? []) {
        leadsByKey.set(`${lead.instancia_id}:${lead.telefone}`, lead);
      }
      const merged = lists.flat().map((conversation) => {
        const lead = leadsByKey.get(`${conversation.instance_id}:${conversation.phone}`);
        return {
          ...conversation,
          lead,
          assigned_to: lead?.assigned_to ?? "",
          assigned_to_name: lead?.assigned_to_name ?? "",
          origem: lead?.origem ?? "inbox",
          campanha: lead?.campanha ?? "",
          followup_date: lead?.followup_date ?? "",
        };
      });
      setInstances(instancesData ?? {});
      setAttendants(attendantsData);
      setItems(merged.sort((a, b) => new Date(b.last_activity || 0).getTime() - new Date(a.last_activity || 0).getTime()));
      setLoading(false);
    } catch {
      setError("Nao foi possivel carregar a supervisao.");
      setLoading(false);
    }
  }, []);

  const loadDetail = useCallback(async () => {
    if (!selected) return;
    const data = await getConversation(selected.instance_id, selected.phone).catch(() => null);
    if (data) setDetail(data);
  }, [selected]);

  useEffect(() => {
    load();
    const interval = setInterval(load, 15000);
    return () => clearInterval(interval);
  }, [load]);

  useEffect(() => {
    setDetail(null);
    setAssignTo("");
    loadDetail();
    const interval = setInterval(loadDetail, 10000);
    return () => clearInterval(interval);
  }, [loadDetail]);

  const filtered = useMemo(() => {
    const q = filters.search.trim().toLowerCase();
    return items.filter((item) => {
      if (filters.instance_id !== "all" && item.instance_id !== filters.instance_id) return false;
      if (filters.attendant !== "all" && item.assigned_to !== filters.attendant) return false;
      if (filters.flag === "unassigned" && item.assigned_to) return false;
      if (filters.flag === "handoff" && !item.needs_human) return false;
      if (filters.flag === "late" && !isFollowupLate(item.followup_date)) return false;
      if (filters.flag === "stale" && !isStale(item)) return false;
      if (q && !`${item.name} ${item.phone} ${item.campanha} ${item.assigned_to_name}`.toLowerCase().includes(q)) return false;
      return true;
    });
  }, [filters, items]);

  const stats = useMemo(() => ({
    total: items.length,
    unread: items.filter((item) => item.unread).length,
    handoff: items.filter((item) => item.needs_human).length,
    unassigned: items.filter((item) => !item.assigned_to).length,
    late: items.filter((item) => isFollowupLate(item.followup_date)).length,
  }), [items]);

  const selectedItem = selected
    ? items.find((item) => item.instance_id === selected.instance_id && item.phone === selected.phone)
    : null;

  const refreshSelected = async () => {
    if (!selected) return;
    const data = await getConversation(selected.instance_id, selected.phone);
    setDetail(data);
  };

  const handleTakeover = async () => {
    if (!selected || takingOver) return;
    setTakingOver(true);
    try {
      await takeoverConversation(selected.instance_id, selected.phone);
      await refreshSelected();
      await load();
    } finally {
      setTakingOver(false);
    }
  };

  const handleRelease = async () => {
    if (!selected || releasing) return;
    setReleasing(true);
    try {
      await releaseConversation(selected.instance_id, selected.phone);
      await refreshSelected();
      await load();
    } finally {
      setReleasing(false);
    }
  };

  const handleSend = async () => {
    if (!selected || !message.trim() || sending) return;
    setSending(true);
    setError("");
    try {
      await humanSendMessage(selected.instance_id, selected.phone, message.trim());
      setMessage("");
      await refreshSelected();
      await load();
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Falha ao enviar a mensagem.";
      setError(msg);
    } finally {
      setSending(false);
    }
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      handleSend();
    }
  };

  const handleAssign = async () => {
    if (!selected || !assignTo || assigning) return;
    setAssigning(true);
    try {
      const attendant = attendants.find(a => a.email === assignTo);
      await assignConversation(
        selected.instance_id,
        selected.phone,
        selectedItem?.name || selected.phone,
        assignTo,
        attendant?.nome || assignTo,
      );
      setAssignTo("");
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Erro ao atribuir.");
    } finally {
      setAssigning(false);
    }
  };

  const handleRetroAssign = async () => {
    if (retroLoading) return;
    setRetroLoading(true);
    setRetroResult(null);
    try {
      const result = await retroactiveAssign();
      setRetroResult({ assigned: result.assigned, skipped: result.skipped });
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Erro ao atribuir retroativamente.");
    } finally {
      setRetroLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-[#F0EDE8]">
      <PageHeaderShell>
        <div className="text-center">
          <h1 className="text-[#1A1410] text-lg font-bold leading-tight">Supervisao</h1>
          <p className="text-[#6B5E55] text-[12px]">Conversas das instancias em tempo quase real</p>
        </div>
      </PageHeaderShell>

      <main className="max-w-7xl mx-auto px-4 sm:px-6 py-6">
        {error && (
          <div className="mb-4 rounded-xl border border-red-500/30 bg-red-500/10 px-4 py-3 text-sm text-red-700">
            {error}
          </div>
        )}

        <div className="flex flex-wrap items-center gap-2 mb-4">
          {[
            ["Conversas", stats.total, "text-[#1A1410]"],
            ["Não lidas", stats.unread, "text-[#CC3300]"],
            ["Sem responsável", stats.unassigned, "text-[#F0B429]"],
            ["Follow-up atrasado", stats.late, "text-[#F87171]"],
          ].map(([label, value, color]) => (
            <div key={label} className="bg-[#E8E4DF] border border-[#D4CFC9] rounded-lg px-3 py-2 flex items-center gap-2">
              <span className="text-[#6B5E55] text-[10px] font-semibold uppercase tracking-wide">{label}</span>
              <span className={`text-sm font-bold tabular-nums ${color}`}>{value}</span>
            </div>
          ))}
          <div className="ml-auto flex items-center gap-2">
            {retroResult && (
              <span className="text-xs text-[#6B5E55]">
                {retroResult.assigned} atribuídos · {retroResult.skipped} ignorados
              </span>
            )}
            <button
              onClick={handleRetroAssign}
              disabled={retroLoading}
              title="Atribuir todos os leads sem responsável ao atendente da instância correspondente"
              className="px-3 py-2 rounded-lg border border-[#D4CFC9] bg-[#E8E4DF] text-[#1A1410] hover:border-[#CC3300] hover:text-[#CC3300] text-xs font-semibold transition-colors disabled:opacity-50"
            >
              {retroLoading ? "Atribuindo..." : "Atribuir histórico"}
            </button>
          </div>
        </div>

        <section className="bg-[#E8E4DF] border border-[#D4CFC9] rounded-xl p-3 mb-5">
          <div className="grid grid-cols-1 md:grid-cols-4 gap-2">
            <input
              value={filters.search}
              onChange={(event) => setFilters({ ...filters, search: event.target.value })}
              placeholder="Buscar lead, telefone, campanha..."
              className="md:col-span-2 bg-[#F0EDE8] border border-[#D4CFC9] text-[#1A1410] placeholder-[#3D4F5A] rounded-lg px-3 py-2 text-sm outline-none focus:border-[#CC3300]"
            />
            <select value={filters.instance_id} onChange={(event) => setFilters({ ...filters, instance_id: event.target.value })} className="bg-[#F0EDE8] border border-[#D4CFC9] text-[#1A1410] rounded-lg px-3 py-2 text-sm outline-none focus:border-[#CC3300]">
              <option value="all">Todas instancias</option>
              {Object.keys(instances).map((id) => <option key={id} value={id}>{id}</option>)}
            </select>
            <select value={filters.attendant} onChange={(event) => setFilters({ ...filters, attendant: event.target.value })} className="bg-[#F0EDE8] border border-[#D4CFC9] text-[#1A1410] rounded-lg px-3 py-2 text-sm outline-none focus:border-[#CC3300]">
              <option value="all">Todos atendentes</option>
              {attendants.map((attendant) => <option key={attendant.email} value={attendant.email}>{attendant.nome}</option>)}
            </select>
          </div>
          <div className="flex flex-wrap gap-2 mt-3">
            {[
              ["all", "Todos"],
              ["handoff", `Precisa humano (${stats.handoff})`],
              ["unassigned", "Sem responsavel"],
              ["late", "Follow-up atrasado"],
              ["stale", "Parados 24h"],
            ].map(([key, label]) => (
              <button
                key={key}
                onClick={() => setFilters({ ...filters, flag: key })}
                className={`px-3 py-1.5 rounded-lg text-xs font-semibold border transition-colors ${
                  filters.flag === key
                    ? "bg-[#CC3300] border-[#CC3300] text-[#F0EDE8]"
                    : "border-[#D4CFC9] text-[#6B5E55] hover:text-[#1A1410] hover:bg-[#D4CFC9]"
                }`}
              >
                {label}
              </button>
            ))}
          </div>
        </section>

        <div className="grid grid-cols-1 xl:grid-cols-[1fr_460px] gap-5 min-h-[640px]">
          <section className="bg-[#E8E4DF] border border-[#D4CFC9] rounded-xl overflow-hidden flex flex-col">
            <div className="px-4 py-3 border-b border-[#D4CFC9] flex items-center justify-between">
              <p className="text-[#1A1410] text-sm font-bold">{filtered.length} conversas</p>
              <span className="text-[#6B5E55] text-xs">Cards do CRM</span>
            </div>
            <div className="overflow-y-auto max-h-[760px] p-3">
              {loading ? (
                <div className="py-20 text-center text-[#6B5E55] text-sm">Carregando...</div>
              ) : filtered.length === 0 ? (
                <div className="py-20 text-center text-[#6B5E55] text-sm">Nenhuma conversa encontrada</div>
              ) : (
                <div className="grid grid-cols-1 md:grid-cols-2 2xl:grid-cols-3 gap-3">
                  {filtered.map((item) => {
                    const active = selected?.instance_id === item.instance_id && selected?.phone === item.phone;
                    const needsHuman = !!item.needs_human;
                    return (
                      <button
                        key={`${item.instance_id}-${item.phone}`}
                        onClick={() => setSelected({ instance_id: item.instance_id, phone: item.phone })}
                        className={`text-left rounded-xl border p-4 transition-colors min-h-[230px] flex flex-col ${
                          active
                            ? "bg-[#F5D5C8] border-[#CC3300]/60 shadow-[0_0_0_1px_rgba(204,51,0,0.25)]"
                            : needsHuman
                              ? "bg-[#FBF1DC] border-[#F0B429]/60 hover:bg-[#F7E8C4]"
                            : "bg-[#F0EDE8] border-[#D4CFC9] hover:border-[#3D4F5A] hover:bg-[#E8E4DF]"
                        }`}
                      >
                        <div className="flex items-start justify-between gap-3">
                          <div className="flex items-center gap-3 min-w-0">
                            <div className={`relative w-11 h-11 rounded-full flex items-center justify-center text-sm font-bold shrink-0 ${
                              needsHuman || item.ai_paused ? "bg-[#2D1F00] text-[#F0B429]" : "bg-[#0E0E18] text-[#CC3300]"
                            }`}>
                              {(item.name || item.phone).charAt(0).toUpperCase()}
                              {needsHuman ? (
                                <span className="absolute -right-0.5 -top-0.5 w-3 h-3 rounded-full bg-[#F0B429] animate-ping" />
                              ) : item.unread && <span className="absolute -right-0.5 -top-0.5 w-3 h-3 rounded-full bg-[#CC3300] animate-pulse" />}
                            </div>
                            <div className="min-w-0">
                              <p className="text-[#1A1410] text-sm font-bold truncate">{item.name || item.phone}</p>
                              <p className="text-[#6B5E55] text-xs truncate">{item.phone}</p>
                              <p className="text-[#3D4F5A] text-[10px] truncate">{item.instance_id}</p>
                            </div>
                          </div>
                          <div className="text-right shrink-0">
                            <p className={`text-xl font-bold tabular-nums ${scoreClass(item.score)}`}>{item.score ?? "-"}</p>
                            <p className="text-[#6B5E55] text-[10px]">score</p>
                          </div>
                        </div>
                        <div className="mt-4 flex items-center justify-between gap-2">
                          <span className={`text-[10px] font-semibold px-2 py-0.5 rounded-full ${stageClass(item.stage)}`}>
                            {STAGE_LABEL[item.stage] ?? item.stage}
                          </span>
                          <span className="text-[#6B5E55] text-[10px]">{timeAgo(item.last_activity)}</span>
                        </div>
                        <p className="text-[#B0BEC5] text-xs leading-relaxed line-clamp-2 mt-3 min-h-[36px]">{item.last_message || "Sem mensagem recente"}</p>
                        <div className="mt-auto pt-4 space-y-2">
                          <div className="bg-[#E8E4DF] rounded-lg px-3 py-2 min-w-0">
                            <p className="text-[#3D4F5A] text-[10px] uppercase">Atendente</p>
                            <p className={`text-xs font-semibold truncate ${item.assigned_to ? "text-[#CC3300]" : "text-[#F0B429]"}`}>
                              {item.assigned_to_name || "Sem responsavel"}
                            </p>
                          </div>
                          <div className="flex flex-wrap gap-1.5">
                            {item.campanha && <span className="text-[10px] bg-[#0D1A3A] text-[#60A5FA] px-2 py-0.5 rounded-full truncate max-w-full">{item.campanha}</span>}
                            {needsHuman && <span className="text-[10px] bg-[#2D1F00] text-[#F0B429] px-2 py-0.5 rounded-full animate-pulse">Precisa humano</span>}
                            {item.ai_paused && <span className="text-[10px] bg-[#2D1F00] text-[#F0B429] px-2 py-0.5 rounded-full">Humano</span>}
                            {isFollowupLate(item.followup_date) && <span className="text-[10px] bg-[#2D0A0A] text-[#F87171] px-2 py-0.5 rounded-full">Follow-up atrasado</span>}
                            {isStale(item) && <span className="text-[10px] bg-[#2D0A0A] text-[#F87171] px-2 py-0.5 rounded-full">Parado 24h</span>}
                          </div>
                        </div>
                      </button>
                    );
                  })}
                </div>
              )}
            </div>
          </section>

          <section className="bg-[#E8E4DF] border border-[#D4CFC9] rounded-xl overflow-hidden flex flex-col min-h-[640px] xl:sticky xl:top-6 xl:max-h-[calc(100vh-48px)]">
            {!selected ? (
              <div className="flex-1 flex items-center justify-center text-center px-8">
                <div>
                  <p className="text-[#1A1410] text-base font-semibold">Selecione uma conversa</p>
                  <p className="text-[#6B5E55] text-sm mt-1">Acompanhe mensagens, status e responsavel em uma unica tela.</p>
                </div>
              </div>
            ) : (
              <>
                <div className="px-5 py-4 border-b border-[#D4CFC9] flex flex-col gap-3">
                  <div className="min-w-0">
                    <p className="text-[#1A1410] text-base font-bold truncate">{detail?.name || selectedItem?.name || selected.phone}</p>
                    <p className="text-[#6B5E55] text-xs truncate">
                      {selected.phone} - {selected.instance_id} - {selectedItem?.assigned_to_name || "Sem responsavel"}
                    </p>
                  </div>
                  <div className="flex flex-wrap gap-2">
                    {detail?.ai_paused ? (
                      <button
                        onClick={handleRelease}
                        disabled={releasing}
                        className="px-3 py-2 rounded-lg border border-[#CC3300]/30 bg-[#3D0A00] text-[#CC3300] text-xs font-bold hover:bg-[#0F3824] disabled:opacity-50 transition-colors"
                      >
                        {releasing ? "Devolvendo..." : "Devolver IA"}
                      </button>
                    ) : (
                      <button
                        onClick={handleTakeover}
                        disabled={takingOver}
                        className="px-3 py-2 rounded-lg border border-[#F0B429]/30 bg-[#2D1F00] text-[#F0B429] text-xs font-bold hover:bg-[#3D2900] disabled:opacity-50 transition-colors"
                      >
                        {takingOver ? "Assumindo..." : "Assumir conversa"}
                      </button>
                    )}
                    <Link href={`/instances/${selected.instance_id}/inbox`} className="px-3 py-2 rounded-lg bg-[#CC3300] text-[#F0EDE8] text-xs font-bold hover:bg-[#991F00] transition-colors">
                      Abrir inbox
                    </Link>
                    <Link href="/crm" className="px-3 py-2 rounded-lg border border-[#CC3300]/30 text-[#CC3300] bg-[#0E0E18] text-xs font-bold hover:bg-[#252535] transition-colors">
                      CRM
                    </Link>
                  </div>
                  <div className="flex gap-2">
                    <select
                      value={assignTo}
                      onChange={(e) => setAssignTo(e.target.value)}
                      className="flex-1 bg-[#F0EDE8] border border-[#D4CFC9] text-[#1A1410] rounded-lg px-3 py-2 text-xs outline-none focus:border-[#CC3300]"
                    >
                      <option value="">
                        {selectedItem?.assigned_to_name
                          ? `Atendente: ${selectedItem.assigned_to_name}`
                          : "Atribuir atendente..."}
                      </option>
                      {attendants.map(a => (
                        <option key={a.email} value={a.email}>{a.nome}</option>
                      ))}
                    </select>
                    <button
                      onClick={handleAssign}
                      disabled={!assignTo || assigning}
                      className="px-4 py-2 rounded-lg bg-[#CC3300] text-[#F0EDE8] text-xs font-bold hover:bg-[#991F00] disabled:opacity-40 transition-colors shrink-0"
                    >
                      {assigning ? "..." : "Atribuir"}
                    </button>
                  </div>
                </div>

                {selectedItem && (
                  <div className="px-5 py-3 border-b border-[#D4CFC9] flex flex-col gap-2">
                    {selectedItem.needs_human && (
                      <div className="rounded-lg border border-[#F0B429]/40 bg-[#2D1F00] px-3 py-2 flex items-center gap-3">
                        <span className="w-3 h-3 rounded-full bg-[#F0B429] animate-ping flex-shrink-0" />
                        <div>
                          <p className="text-[#F0B429] text-xs font-bold">Atendimento humano solicitado</p>
                          <p className="text-[#1A1410] text-[11px] mt-0.5">{selectedItem.handoff_reason || "A IA sinalizou que um humano deve assumir esta conversa."}</p>
                        </div>
                      </div>
                    )}
                    <div className="grid grid-cols-2 gap-2">
                      <div className="bg-[#F0EDE8] rounded-lg px-3 py-2">
                        <p className="text-[#6B5E55] text-[10px] uppercase">Score</p>
                        <p className={`text-xs font-bold ${scoreClass(selectedItem.score)}`}>{selectedItem.score ?? "-"}</p>
                      </div>
                      <div className="bg-[#F0EDE8] rounded-lg px-3 py-2">
                        <p className="text-[#6B5E55] text-[10px] uppercase">Campanha</p>
                        <p className="text-[#1A1410] text-xs font-semibold truncate">{selectedItem.campanha || "-"}</p>
                      </div>
                    </div>
                  </div>
                )}

                <div className="flex-1 overflow-y-auto px-5 py-4 space-y-2 bg-[#F0EDE8]">
                  {!detail ? (
                    <div className="py-20 text-center text-[#6B5E55] text-sm">Carregando conversa...</div>
                  ) : detail.messages.length === 0 ? (
                    <div className="py-20 text-center text-[#6B5E55] text-sm">Nenhuma mensagem</div>
                  ) : detail.messages.map((chatMessage, index) => {
                    const isLead = chatMessage.role === "user";
                    const isHuman = chatMessage.role === "human";
                    return (
                      <div key={`${chatMessage.timestamp}-${index}`} className={`flex ${isLead ? "justify-start" : "justify-end"}`}>
                        <div className={`max-w-[82%] rounded-2xl px-4 py-2.5 ${
                          isLead ? "bg-[#E8E4DF] rounded-tl-sm" : isHuman ? "bg-[#CC3300] rounded-tr-sm" : "bg-[#991F00] rounded-tr-sm"
                        }`}>
                          {!isLead && (
                            <p className="text-[10px] font-bold mb-1 uppercase text-white/70">
                              {isHuman ? "Humano" : "IA"}
                            </p>
                          )}
                          <p className={`text-sm whitespace-pre-wrap break-words leading-relaxed ${isLead ? "text-[#1A1410]" : "text-white"}`}>{chatMessage.content}</p>
                          <p className={`text-[10px] mt-1 text-right ${isLead ? "text-[#6B5E55]" : "text-white/70"}`}>
                            {chatMessage.timestamp ? new Date(chatMessage.timestamp).toLocaleString("pt-BR", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }) : ""}
                          </p>
                        </div>
                      </div>
                    );
                  })}
                </div>

                {detail && (
                  <div className="border-t border-[#D4CFC9] bg-[#E8E4DF] px-4 py-3">
                    {!detail.ai_paused && (
                      <div className="mb-2 flex items-center gap-2 text-[10px] text-[#6B5E55]">
                        <span className="w-1.5 h-1.5 rounded-full bg-[#CC3300] animate-pulse" />
                        Ao enviar, a conversa sera assumida no modo humano.
                      </div>
                    )}
                    <div className="flex items-end gap-2">
                      <textarea
                        value={message}
                        onChange={(event) => setMessage(event.target.value)}
                        onKeyDown={handleKeyDown}
                        placeholder="Digite uma mensagem..."
                        rows={2}
                        className="flex-1 resize-none rounded-xl border border-[#D4CFC9] bg-[#F0EDE8] px-3 py-2 text-sm text-[#1A1410] placeholder-[#3D4F5A] outline-none focus:border-[#CC3300]"
                      />
                      <button
                        onClick={handleSend}
                        disabled={!message.trim() || sending}
                        className="h-10 w-10 rounded-full bg-[#CC3300] text-[#F0EDE8] hover:bg-[#991F00] disabled:bg-[#D4CFC9] disabled:text-[#6B5E55] flex items-center justify-center transition-colors"
                        title="Enviar mensagem"
                      >
                        {sending ? (
                          <svg className="w-4 h-4 animate-spin" viewBox="0 0 24 24" fill="none">
                            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" />
                          </svg>
                        ) : (
                          <svg viewBox="0 0 24 24" className="w-4 h-4 fill-current">
                            <path d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z" />
                          </svg>
                        )}
                      </button>
                    </div>
                  </div>
                )}
              </>
            )}
          </section>
        </div>
      </main>
    </div>
  );
}
