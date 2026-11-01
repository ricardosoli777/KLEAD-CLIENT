"use client";

import React, { Suspense, useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import PageHeaderShell from "@/components/PageHeaderShell";
import {
  crmGetColumns, crmCreateColumn, crmUpdateColumn, crmDeleteColumn, crmBackfillLeadgen,
  crmGetLeads, crmCreateLead, crmMoveLead, crmDeleteLead,
  crmAddNote, crmSendMessage, crmSendMedia, crmGetStats, crmSetTags,
  crmUpdateFields, crmExportCsv, crmAddContactLog,
  crmGetListas, crmCreateLista, crmDeleteLista, crmAddLeadToLista, crmRemoveLeadFromLista, crmGetListaLeads, crmSetListaFollowup,
  CrmColumn, CrmLead, CrmStats, CrmLista,
  getConversation, humanSendMessage, takeoverConversation,
  crmSeedConversation,
  getInstancesCached,
  ConversationDetail,
  getLeadMetaCapiAudit, type MetaCapiAuditItem,
  crmGetConfirmationTemplates, crmUpdateConfirmationTemplate, crmSendConfirmation,
  type ConfirmationTemplate, type ConfirmationOption,
} from "@/lib/api";

// ── Helpers ───────────────────────────────────────────────────────────────────

const ORIGEM_STYLE: Record<string, { label: string; icon: string; bg: string; text: string }> = {
  sdr_inbox: { label: "SDR Inbox",  icon: "🤖", bg: "bg-[#3D0A00]",  text: "text-[#CC3300]"  },
  meta:      { label: "Meta Ads",   icon: "📣", bg: "bg-[#0E0E18]",  text: "text-[#CC3300]"  },
  meta_api:  { label: "Meta API",   icon: "⚡", bg: "bg-[#0D1A3A]",  text: "text-[#3B82F6]"  },
  whatsapp:  { label: "WhatsApp",   icon: "💬", bg: "bg-[#0D2533]",  text: "text-[#60A5FA]"  },
  manual:    { label: "Manual",     icon: "✏️", bg: "bg-[#D4CFC9]",  text: "text-[#6B5E55]"  },
};
ORIGEM_STYLE.meta_ads = { label: "Meta Ads", icon: "Ads", bg: "bg-[#0E0E18]", text: "text-[#CC3300]" };
ORIGEM_STYLE.meta_leads = { label: "Meta Leads", icon: "Lead", bg: "bg-[#12203C]", text: "text-[#93C5FD]" };
ORIGEM_STYLE.meta_forms = { label: "Meta Forms", icon: "Form", bg: "bg-[#102A43]", text: "text-[#60A5FA]" };
ORIGEM_STYLE.meta = { label: "Meta Inbox", icon: "Meta", bg: "bg-[#0E0E18]", text: "text-[#CC3300]" };
function origemKey(origem: string) {
  return (origem || "").trim().toLowerCase().replace(/-/g, "_");
}
function origemBadge(origem: string) {
  return ORIGEM_STYLE[origemKey(origem)] ?? {
    ...ORIGEM_STYLE.manual,
    label: origem || ORIGEM_STYLE.manual.label,
  };
}

function scoreColor(s: number | null) {
  if (s === null || s === undefined) return "text-[#6B5E55]";
  return s >= 70 ? "text-[#CC3300]" : s >= 40 ? "text-[#F0B429]" : "text-[#F87171]";
}

const SCORE_PRESETS = [
  { value: "20", label: "Frio", hint: "0-39" },
  { value: "55", label: "Morno", hint: "40-69" },
  { value: "85", label: "Quente", hint: "70-100" },
];

const LEAD_STATUS_OPTIONS = [
  { value: "", label: "Sem status" },
  { value: "em_contato", label: "Em contato" },
  { value: "qualificado", label: "Qualificado" },
  { value: "agendado", label: "Agendou" },
  { value: "visita_agendada", label: "Visita agendada" },
  { value: "vendido", label: "Virou venda" },
  { value: "desqualificado", label: "Desqualificado" },
];

function statusLabel(value: string) {
  return LEAD_STATUS_OPTIONS.find((s) => s.value === value)?.label ?? value;
}

function scorePresetFromValue(score: number | null | undefined) {
  if (score === null || score === undefined) return "";
  if (score >= 70) return "85";
  if (score >= 40) return "55";
  return "20";
}

function scoreBg(s: number | null) {
  if (s === null || s === undefined) return "#D4CFC9";
  return s >= 70 ? "#CC3300" : s >= 40 ? "#F0B429" : "#F87171";
}

function leadWhatsAppLid(lead: CrmLead) {
  const value = lead.source_meta?.evolution_identity?.lid_jid;
  return typeof value === "string" ? value.trim() : "";
}

function IdentityValue({ label, value, hint }: { label: string; value: string; hint?: string }) {
  if (!value) return null;
  return (
    <div>
      <div className="flex items-center justify-between gap-2 mb-0.5">
        <label className="text-[#6B5E55] text-[10px]">{label}</label>
        <button
          type="button"
          onClick={() => void navigator.clipboard.writeText(value)}
          className="text-[9px] font-semibold text-[#3B82F6] hover:text-[#1D4ED8]"
          title={`Copiar ${label}`}
        >
          Copiar
        </button>
      </div>
      <p className="text-[#1A1410] text-[11px] font-mono break-all select-all">{value}</p>
      {hint && <p className="text-[#8A817A] text-[9px] mt-0.5">{hint}</p>}
    </div>
  );
}

function timeAgo(iso: string) {
  if (!iso) return "";
  const diff = Date.now() - new Date(iso).getTime();
  const m = Math.floor(diff / 60000);
  if (m < 1)  return "agora";
  if (m < 60) return `${m}min`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h`;
  return `${Math.floor(h / 24)}d`;
}

function daysSince(iso: string) {
  if (!iso) return 0;
  return Math.floor((Date.now() - new Date(iso).getTime()) / 86400000);
}

function escapeConfirmationRegex(value: string) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function emphasizeConfirmationDetails(text: string, leadName: string) {
  const boldMatches = (value: string, pattern: RegExp) => value
    .split(/(\*[^*\n]+\*)/g)
    .map(part => part.startsWith("*") && part.endsWith("*")
      ? part
      : part.replace(pattern, match => `*${match}*`))
    .join("");
  let result = text;
  const name = leadName.trim();
  if (name) {
    result = boldMatches(result, new RegExp(escapeConfirmationRegex(name), "gi"));
    const firstName = name.split(/\s+/)[0];
    if (firstName) result = boldMatches(result, new RegExp(`\\b${escapeConfirmationRegex(firstName)}\\b`, "gi"));
  }
  const patterns = [
    /\b\d{1,2}\/\d{1,2}(?:\/\d{2,4})?\b/gi,
    /\b\d{4}-\d{2}-\d{2}\b/gi,
    /\b\d{1,2}\s+de\s+(?:janeiro|fevereiro|março|marco|abril|maio|junho|julho|agosto|setembro|outubro|novembro|dezembro)(?:\s+de\s+\d{4})?\b/gi,
    /\b(?:[01]?\d|2[0-3])(?::[0-5]\d|h(?:[0-5]\d)?)(?:\s*(?:h|horas?))?\b/gi,
  ];
  for (const pattern of patterns) result = boldMatches(result, pattern);
  return result;
}

function WhatsAppBoldPreview({ text }: { text: string }) {
  return text.split(/(\*[^*\n]+\*)/g).map((part, index) =>
    part.startsWith("*") && part.endsWith("*")
      ? <strong key={index}>{part.slice(1, -1)}</strong>
      : <React.Fragment key={index}>{part}</React.Fragment>
  );
}

function FollowupDot({ status, size = "sm" }: { status: "vencido" | "hoje" | "proximo"; size?: "sm" | "lg" }) {
  const color = status === "vencido" ? "#F87171" : status === "hoje" ? "#F0B429" : "#60A5FA";
  const dim = size === "lg" ? "w-3 h-3" : "w-1.5 h-1.5";
  return (
    <span className={`relative inline-flex ${dim} shrink-0`}>
      <span className={`animate-ping absolute inline-flex h-full w-full rounded-full opacity-75`} style={{ background: color }} />
      <span className={`relative inline-flex rounded-full ${dim}`} style={{ background: color, boxShadow: `0 0 6px ${color}` }} />
    </span>
  );
}

/** Selo grande fixado no canto do card — pensado para chamar atenção numa lista com muitos leads */
function FollowupBeacon({ date }: { date: string }) {
  const status = followupStatus(date);
  if (!status) return null;
  const label = status === "vencido" ? "atrasado" : status === "hoje" ? "hoje" : "próximo";
  const bg = status === "vencido" ? "#2D0A0A" : status === "hoje" ? "#2D2000" : "#0A1A2D";
  const text = status === "vencido" ? "#F87171" : status === "hoje" ? "#F0B429" : "#60A5FA";
  return (
    <span
      className="absolute -top-2 -right-2 z-10 flex items-center gap-1 rounded-full px-2 py-1 text-[9px] font-bold shadow-lg"
      style={{ background: bg, color: text, border: `1px solid ${text}` }}
      title={`Follow-up ${label}`}
    >
      <FollowupDot status={status} size="lg" />
      {label}
    </span>
  );
}

function followupStatus(date: string): "vencido" | "hoje" | "proximo" | null {
  if (!date) return null;
  const dateStr  = date.slice(0, 10);
  const todayStr = new Date().toISOString().slice(0, 10);
  if (dateStr < todayStr) return "vencido";
  if (dateStr === todayStr) return "hoje";
  const d     = new Date(dateStr + "T00:00:00Z");
  const today = new Date(todayStr + "T00:00:00Z");
  const diffDays = Math.round((d.getTime() - today.getTime()) / 86400000);
  if (diffDays <= 2) return "proximo"; // amanhã ou depois de amanhã
  return null;
}

// ── LeadCard ──────────────────────────────────────────────────────────────────

function LeadCard({
  lead,
  onDragStart,
  onClick,
  listas,
  onConfirmLista,
  onRefresh,
}: {
  lead: CrmLead;
  onDragStart: (e: React.DragEvent, id: string) => void;
  onClick: () => void;
  listas: CrmLista[];
  onConfirmLista: (listaId: string) => Promise<void>;
  onRefresh: () => void;
}) {
  const router = useRouter();
  const [seeding, setSeeding]               = useState(false);
  const [seedErr, setSeedErr]               = useState("");
  const [instanceList, setInstanceList]     = useState<{ id: string }[]>([]);
  const [instancia, setInstancia]           = useState(lead.instancia_id ?? "");
  const [savingInst, setSavingInst]         = useState(false);
  const o = origemBadge(lead.origem);
  const stale = daysSince(lead.updated_at) >= 7;
  const fuStatus = followupStatus(lead.followup_date);

  useEffect(() => {
    getInstancesCached().then((data) => setInstanceList(Object.keys(data).map((id) => ({ id })))).catch(() => {});
  }, []);

  const saveInstancia = async (e: React.MouseEvent) => {
    e.stopPropagation();
    if (!instancia || savingInst) return;
    setSavingInst(true);
    try {
      await crmUpdateFields(lead.id, { instancia_id: instancia });
      onRefresh();
    } finally {
      setSavingInst(false);
    }
  };

  const openInbox = async (e: React.MouseEvent) => {
    e.stopPropagation();
    setSeedErr("");
    setSeeding(true);
    try {
      const res = await crmSeedConversation(lead.id);
      router.push(`/instances/${res.instance_id}/inbox`);
    } catch (err: unknown) {
      setSeedErr(err instanceof Error ? err.message : "Erro ao abrir inbox.");
      setSeeding(false);
    }
  };

  return (
    <div
      draggable
      onDragStart={(e) => onDragStart(e, lead.id)}
      onClick={onClick}
      className={`relative bg-[#E8E4DF] border rounded-xl p-3 mb-2 cursor-grab active:cursor-grabbing hover:border-[#3D4F5A] transition-colors select-none ${
        fuStatus === "vencido" ? "border-[#F87171] followup-glow" :
        fuStatus === "hoje"    ? "border-[#F0B429] followup-glow" :
        fuStatus === "proximo" ? "border-[#60A5FA] followup-glow" :
        stale                  ? "border-[#3D3020]" :
                                 "border-[#D4CFC9]"
      }`}
      style={{
        ["--glow-color" as string]:
          fuStatus === "vencido" ? "#F87171" : fuStatus === "hoje" ? "#F0B429" : fuStatus === "proximo" ? "#60A5FA" : undefined,
      }}
    >
      <FollowupBeacon date={lead.followup_date} />
      <div className="flex items-start justify-between gap-2 mb-1.5">
        <p className="font-semibold text-[#1A1410] text-sm leading-tight truncate">{lead.nome}</p>
        {lead.score !== null && lead.score !== undefined && (
          <span className={`text-xs font-bold tabular-nums shrink-0 ${scoreColor(lead.score)}`}>
            {lead.score}
          </span>
        )}
      </div>

      {/* Score bar */}
      {lead.score !== null && lead.score !== undefined && (
        <div className="h-1 bg-[#D4CFC9] rounded-full mb-2 overflow-hidden">
          <div
            className="h-full rounded-full transition-all"
            style={{ width: `${lead.score}%`, backgroundColor: scoreBg(lead.score) }}
          />
        </div>
      )}

      <p className="text-[#6B5E55] text-xs truncate">{lead.empresa || lead.telefone}</p>
      {lead.campanha && (
        <p className="text-[#3B82F6] text-[10px] truncate mb-1">📢 {lead.campanha}</p>
      )}
      {lead.assigned_to_name && (
        <p className="text-[#CC3300] text-[10px] truncate mb-1">
          Responsavel: {lead.assigned_to_name}
        </p>
      )}
      {lead.lead_status && (
        <p className="text-[#CC3300] text-[10px] truncate mb-1">
          Status: {statusLabel(lead.lead_status)}
        </p>
      )}
      {(lead.segmento || lead.poder_aquisitivo) && (
        <div className="flex gap-1 flex-wrap mt-1">
          {lead.segmento === "alto_padrao"  && <span className="text-[9px] bg-[#1A2F1A] text-[#CC3300] px-1.5 py-0.5 rounded-full font-semibold">🏆 Alto Padrão</span>}
          {lead.segmento === "medio_padrao" && <span className="text-[9px] bg-[#0E0E18] text-[#60A5FA] px-1.5 py-0.5 rounded-full font-semibold">🏠 Médio Padrão</span>}
          {lead.segmento === "mcmv"         && <span className="text-[9px] bg-[#2A2010] text-[#F0B429] px-1.5 py-0.5 rounded-full font-semibold">🏗️ MCMV</span>}
          {lead.poder_aquisitivo && <span className="text-[9px] bg-[#D4CFC9] text-[#6B5E55] px-1.5 py-0.5 rounded-full">{lead.poder_aquisitivo}</span>}
        </div>
      )}

      {/* Última mensagem */}
      {lead.historico && lead.historico.length > 0 && (() => {
        const last = [...lead.historico].reverse().find(m => m.content);
        return last ? (
          <p className="text-[#6B5E55] text-[10px] truncate mt-1 border-t border-[#D4CFC9] pt-1">
            <span className={last.role === "user" ? "text-[#60A5FA]" : "text-[#6B5E55]"}>
              {last.role === "user" ? "Lead: " : "SDR: "}
            </span>
            {last.content}
          </p>
        ) : null;
      })()}

      {/* Follow-up agendado */}
      {lead.followup_date && !fuStatus && (
        <p className="text-[#6B5E55] text-[10px] mt-1">🔔 Follow-up: {lead.followup_date}</p>
      )}

      <div className="flex items-center justify-between mt-1.5">
        <span className={`text-[10px] font-semibold px-2 py-0.5 rounded-full flex items-center gap-1 ${o.bg} ${o.text}`}>
          <span>{o.icon}</span>{o.label}
        </span>
        <div className="flex items-center gap-1.5">
          {fuStatus === "vencido" && (
            <span className="flex items-center gap-1 text-[9px] font-bold text-[#F87171] bg-[#2D0A0A] px-1.5 py-0.5 rounded-full"><FollowupDot status="vencido" />atrasado</span>
          )}
          {fuStatus === "hoje" && (
            <span className="flex items-center gap-1 text-[9px] font-bold text-[#F0B429] bg-[#2D2000] px-1.5 py-0.5 rounded-full"><FollowupDot status="hoje" />hoje</span>
          )}
          {fuStatus === "proximo" && (
            <span className="flex items-center gap-1 text-[9px] font-bold text-[#60A5FA] bg-[#0A1A2D] px-1.5 py-0.5 rounded-full"><FollowupDot status="proximo" />próximo</span>
          )}
          {stale && !fuStatus && (
            <span className="text-[9px] text-[#6B5E55]">parado</span>
          )}
          <span className="text-[10px] text-[#6B5E55]">{timeAgo(lead.updated_at)}</span>
        </div>
      </div>

      {lead.tags && lead.tags.length > 0 && (
        <div className="flex flex-wrap gap-1 mt-2 max-h-[36px] overflow-hidden">
          {lead.tags.map((t) => (
            <span key={t} className="text-[9px] bg-[#D4CFC9] text-[#6B5E55] px-1.5 py-0.5 rounded-full">{t}</span>
          ))}
        </div>
      )}

      {listas.length > 0 && (
        <div className="mt-2" onClick={(e) => e.stopPropagation()}>
          <AddToListaPopover listas={listas} onConfirm={onConfirmLista} />
        </div>
      )}

      <div onClick={(e) => e.stopPropagation()} className="mt-2 space-y-1.5">
        {/* Seletor de instância */}
        <div className="flex gap-1">
          <select
            value={instancia}
            onChange={(e) => setInstancia(e.target.value)}
            className="flex-1 bg-[#F0EDE8] border border-[#D4CFC9] rounded-lg px-2 py-2 text-xs text-[#6B5E55] outline-none focus:border-[#CC3300] min-w-0"
          >
            <option value="">— Instância —</option>
            {instanceList.map((inst) => (
              <option key={inst.id} value={inst.id}>{inst.id}</option>
            ))}
          </select>
          <button
            onClick={saveInstancia}
            disabled={savingInst || !instancia || instancia === lead.instancia_id}
            className="text-xs font-semibold px-3 py-2 rounded-lg bg-[#D4CFC9] text-[#6B5E55] hover:text-[#1A1410] disabled:opacity-40 transition-colors shrink-0"
          >
            {savingInst ? "..." : "Salvar"}
          </button>
        </div>
        {/* Abrir Inbox */}
        <button
          onClick={openInbox}
          disabled={seeding || !lead.instancia_id}
          className="w-full text-xs font-semibold text-[#CC3300] hover:bg-[#3D0A00] disabled:opacity-40 py-2 rounded-lg transition-colors border border-[#CC3300]/20 hover:border-[#CC3300]/50"
        >
          {seeding ? "Abrindo..." : "📥 Abrir no Inbox"}
        </button>
        {seedErr && <p className="text-[9px] text-[#F87171] text-center">{seedErr}</p>}
      </div>
    </div>
  );
}

// ── LeadModal ─────────────────────────────────────────────────────────────────

type ModalTab = "info" | "chat" | "followup" | "history" | "notes" | "timeline";

function LeadModal({
  lead,
  columns,
  listas,
  onConfirmLista,
  onClose,
  onRefresh,
}: {
  lead: CrmLead;
  columns: CrmColumn[];
  listas: CrmLista[];
  onConfirmLista: (listaId: string) => Promise<void>;
  onClose: () => void;
  onRefresh: () => void;
}) {
  const [tab, setTab]           = useState<ModalTab>("info");
  const [note, setNote]         = useState("");
  const [msg, setMsg]           = useState("");
  const [sending, setSending]   = useState(false);
  const [savingNote, setSavingNote] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [moving, setMoving]     = useState(false);
  const [tagInput, setTagInput] = useState("");
  const [capiAudit, setCapiAudit] = useState<MetaCapiAuditItem[] | null>(null);
  const [capiLoading, setCapiLoading] = useState(true);
  const [showCapi, setShowCapi]   = useState(false);
  const [localTags, setLocalTags] = useState<string[]>(lead.tags ?? []);

  // Custom fields
  const [nomeEdit, setNomeEdit]                 = useState(lead.nome ?? "");
  const [telefoneEdit, setTelefoneEdit]         = useState(lead.telefone ?? "");
  const [email, setEmail]                       = useState(lead.email ?? "");
  const [empresa, setEmpresa]                   = useState(lead.empresa ?? "");
  const [cidade, setCidade]                     = useState(lead.cidade ?? "");
  const [estado, setEstado]                     = useState(lead.estado ?? "");
  const [cep, setCep]                           = useState(lead.cep ?? "");
  const [pais, setPais]                         = useState(lead.pais ?? "BR");
  const [valor, setValor]                       = useState(lead.valor_estimado ?? "");
  const [followup, setFollowup]                 = useState(lead.followup_date ?? "");
  const [segmento, setSegmento]                 = useState(lead.segmento ?? "");
  const [poderAquisitivo, setPoderAquisitivo]   = useState(lead.poder_aquisitivo ?? "");
  const [manualScore, setManualScore]           = useState<string>(scorePresetFromValue(lead.score));
  const [leadStatus, setLeadStatus]             = useState<string>(lead.lead_status ?? "");
  const [instanciaEdit, setInstanciaEdit]       = useState(lead.instancia_id ?? "");
  const [leadgenIdEdit, setLeadgenIdEdit]       = useState(lead.leadgen_id ?? "");
  const [instanceList, setInstanceList]         = useState<{ id: string }[]>([]);
  const [savingFields, setSavingFields]         = useState(false);
  const whatsappLid = leadWhatsAppLid(lead);

  useEffect(() => {
    getInstancesCached().then((data) => setInstanceList(Object.keys(data).map((id) => ({ id })))).catch(() => {});
  }, []);

  const router = useRouter();

  // Chat
  const [conv, setConv]         = useState<ConversationDetail | null>(null);
  const [convLoading, setConvLoading] = useState(false);
  const [chatMsg, setChatMsg]   = useState("");
  const [chatSending, setChatSending] = useState(false);
  const [chatFile, setChatFile] = useState<File | null>(null);
  const [chatSendingMedia, setChatSendingMedia] = useState(false);
  const [confirmationTemplates, setConfirmationTemplates] = useState<ConfirmationTemplate[]>([]);
  const [confirmationTemplateId, setConfirmationTemplateId] = useState("");
  const [confirmationText, setConfirmationText] = useState("");
  const [confirmationOptions, setConfirmationOptions] = useState<ConfirmationOption[]>([]);
  const [confirmationSending, setConfirmationSending] = useState(false);
  const [confirmationError, setConfirmationError] = useState("");
  const [confirmationDeliveryFormat, setConfirmationDeliveryFormat] = useState<"text" | "poll">("text");
  const [confirmationEditTemplate, setConfirmationEditTemplate] = useState(false);
  const [confirmationTemplateDirty, setConfirmationTemplateDirty] = useState(false);
  const [confirmationSaveStatus, setConfirmationSaveStatus] = useState<"idle" | "saving" | "saved" | "error">("idle");
  const [seeding, setSeeding]   = useState(false);
  const [seedError, setSeedError] = useState("");
  const chatEndRef              = useRef<HTMLDivElement>(null);
  const chatFileRef             = useRef<HTMLInputElement>(null);

  const [convArchived, setConvArchived] = useState(false);

  const fillConfirmationVariables = useCallback((text: string) => text
    .replaceAll("{nome}", lead.nome || "")
    .replaceAll("{data}", lead.followup_date || "")
    .replaceAll("{servico}", lead.segmento || "")
    .replaceAll("{atendente}", lead.assigned_to_name || "")
    .replaceAll("{hora}", ""), [lead.nome, lead.followup_date, lead.segmento, lead.assigned_to_name]);

  const loadConv = useCallback(async (silent = false) => {
    if (!lead.instancia_id || !lead.telefone) return;
    if (!silent) setConvLoading(true);
    // Histórico gravado no card quando o lead foi enviado ao CRM / a conversa
    // foi apagada do inbox — é o que garante que apagar do inbox não apaga daqui
    const archived = ((lead.historico ?? []) as { role: string; content: string; timestamp?: string }[])
      .map(m => ({ role: m.role, content: m.content, timestamp: m.timestamp ?? "" })) as ConversationDetail["messages"];
    try {
      const d = await getConversation(lead.instancia_id, lead.telefone);
      // 404 returns {detail: "..."} — conversa não existe mais no inbox
      if ((d as unknown as { detail?: string }).detail) {
        if (archived.length > 0) {
          // Exibe o histórico arquivado; enviar mensagem reabre a conversa
          setConvArchived(true);
          setConv({
            phone: lead.telefone, name: lead.nome, stage: "qualifying", ai_paused: true, unread: false,
            score: lead.score ?? null, score_reason: "", turn_count: 0, msg_count: archived.length,
            last_message: "", last_activity: "", created_at: "",
            messages: archived,
          });
          setTimeout(() => chatEndRef.current?.scrollIntoView({ behavior: "smooth" }), 50);
        } else if (!silent) {
          setConvArchived(false);
          setConv({ phone: lead.telefone, name: lead.nome, stage: "qualifying", ai_paused: false, unread: false, score: null, score_reason: "", turn_count: 0, msg_count: 0, last_message: "", last_activity: "", created_at: "", messages: [] });
        }
      } else {
        // Conversa viva: se o card tem histórico de um ciclo anterior (conversa
        // apagada e retomada), mescla o arquivo antes das mensagens novas para
        // a visão ficar contínua. Dedup por role+content — numa conversa nunca
        // apagada o snapshot é subconjunto das mensagens vivas e nada muda.
        if (archived.length > 0 && Array.isArray(d.messages)) {
          const liveSigs = new Set(d.messages.map(m => `${m.role}|${m.content}`));
          const missing = archived.filter(m => !liveSigs.has(`${m.role}|${m.content}`));
          if (missing.length > 0) d.messages = [...missing, ...d.messages];
        }
        setConvArchived(false);
        setConv(d);
        setTimeout(() => chatEndRef.current?.scrollIntoView({ behavior: "smooth" }), 50);
      }
    } catch { if (!silent) setConv(null); }
    finally { if (!silent) setConvLoading(false); }
  }, [lead.instancia_id, lead.nome, lead.telefone, lead.historico, lead.score]);

  // Carrega a conversa ao vivo assim que o card abre (silencioso, sem
  // spinner) — assim a aba Histórico já mostra dado atual mesmo que o
  // usuário nunca clique em "Carregar chat" na aba Chat. Sem isso, o card
  // exibia o snapshot congelado do último envio ao CRM em vez da conversa
  // corrente, e só atualizava com reenvio manual.
  useEffect(() => {
    loadConv(true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lead.id]);

  // Polling automático quando aba Chat está aberta
  useEffect(() => {
    if (tab !== "chat") return;
    const interval = setInterval(() => loadConv(true), 15000);
    return () => clearInterval(interval);
  }, [tab, loadConv]);

  // Carrega audit CAPI ao abrir modal (sempre — seção aparece em todos os leads)
  useEffect(() => {
    setCapiLoading(true);
    getLeadMetaCapiAudit(lead.id, 5)
      .then(items => setCapiAudit(items))
      .catch(() => setCapiAudit([]))
      .finally(() => setCapiLoading(false));
  }, [lead.id]);

  useEffect(() => {
    crmGetConfirmationTemplates().then((templates) => {
      setConfirmationTemplates(templates);
      const first = templates[0];
      if (first) {
        setConfirmationTemplateId(first.id);
        setConfirmationText(fillConfirmationVariables(first.message_text));
        setConfirmationOptions(first.options_json.map(option => ({ ...option, synonyms: [...(option.synonyms ?? [])] })));
      }
    }).catch(() => setConfirmationError("Não foi possível carregar os modelos."));
  }, [lead.id, fillConfirmationVariables]);

  const selectConfirmationTemplate = (templateId: string) => {
    setConfirmationTemplateId(templateId);
    const template = confirmationTemplates.find(item => item.id === templateId);
    if (!template) return;
    setConfirmationText(fillConfirmationVariables(template.message_text));
    setConfirmationOptions(template.options_json.map(option => ({ ...option, synonyms: [...(option.synonyms ?? [])] })));
    setConfirmationError("");
    setConfirmationEditTemplate(false);
    setConfirmationTemplateDirty(false);
    setConfirmationSaveStatus("idle");
  };

  useEffect(() => {
    if (!confirmationEditTemplate || !confirmationTemplateDirty || !confirmationTemplateId) return;
    const timer = setTimeout(async () => {
      setConfirmationSaveStatus("saving");
      try {
        const updated = await crmUpdateConfirmationTemplate(confirmationTemplateId, {
          message_text: confirmationText,
          options: confirmationOptions,
        });
        setConfirmationTemplates(items => items.map(item => item.id === updated.id ? updated : item));
        setConfirmationTemplateDirty(false);
        setConfirmationSaveStatus("saved");
      } catch {
        setConfirmationSaveStatus("error");
      }
    }, 900);
    return () => clearTimeout(timer);
  }, [confirmationEditTemplate, confirmationTemplateDirty, confirmationTemplateId, confirmationText, confirmationOptions]);

  const sendConfirmation = async () => {
    if (!confirmationText.trim() || confirmationOptions.some(option => !option.label.trim())) return;
    setConfirmationSending(true);
    setConfirmationError("");
    try {
      await crmSendConfirmation(lead.id, {
        template_id: confirmationTemplateId || undefined,
        message_text: confirmationText,
        options: confirmationOptions,
        include_public_link: false,
        delivery_format: confirmationDeliveryFormat,
      });
      await loadConv(true);
    } catch (error) {
      setConfirmationError(error instanceof Error ? error.message : "Erro ao enviar confirmação.");
    } finally {
      setConfirmationSending(false);
    }
  };

  const sendChatMsg = async () => {
    if (!chatMsg.trim() || !lead.instancia_id) return;
    setChatSending(true);
    try {
      await takeoverConversation(lead.instancia_id, lead.telefone);
      await humanSendMessage(lead.instancia_id, lead.telefone, chatMsg.trim());
      setChatMsg("");
      await loadConv();
    } finally { setChatSending(false); }
  };

  const sendChatMedia = async () => {
    if (!chatFile || !lead.id || chatSendingMedia) return;
    setChatSendingMedia(true);
    try {
      await crmSendMedia(lead.id, chatFile);
      setChatFile(null);
      await loadConv();
    } catch { alert("Erro ao enviar arquivo."); }
    finally { setChatSendingMedia(false); }
  };

  // Acompanhamento
  const [contactTipo, setContactTipo]       = useState("ligação");
  const [contactNota, setContactNota]       = useState("");
  const [contactProximo, setContactProximo] = useState("");
  const [savingContact, setSavingContact]   = useState(false);

  const o = origemBadge(lead.origem);
  const fuStatus = followupStatus(lead.followup_date);

  const saveFields = async () => {
    setSavingFields(true);
    try {
      const scoreValue = manualScore ? Number(manualScore) : undefined;
      await crmUpdateFields(lead.id, {
        nome:             nomeEdit || undefined,
        telefone:         telefoneEdit || undefined,
        email:            email,
        empresa:          empresa,
        cidade:           cidade,
        estado:           estado,
        cep:              cep,
        pais:             pais,
        valor_estimado:   valor,
        followup_date:    followup,
        segmento:         segmento,
        poder_aquisitivo: poderAquisitivo,
        score:            scoreValue,
        score_reason:     manualScore ? `Marcado manualmente como ${SCORE_PRESETS.find((s) => s.value === manualScore)?.label ?? "nota"}` : undefined,
        lead_status:      leadStatus || undefined,
        instancia_id:     instanciaEdit || undefined,
        leadgen_id:       leadgenIdEdit !== (lead.leadgen_id ?? "") ? leadgenIdEdit : undefined,
      });
      onRefresh();
    } finally {
      setSavingFields(false);
    }
  };

  const addTag = async () => {
    const tag = tagInput.trim();
    if (!tag || localTags.includes(tag)) { setTagInput(""); return; }
    const prev = localTags;
    const next = [...localTags, tag];
    setLocalTags(next);
    setTagInput("");
    try {
      await crmSetTags(lead.id, next);
      onRefresh();
    } catch {
      setLocalTags(prev);
      alert("Erro ao salvar tag. Tente novamente.");
    }
  };

  const removeTag = async (tag: string) => {
    const prev = localTags;
    const next = localTags.filter((t) => t !== tag);
    setLocalTags(next);
    try {
      await crmSetTags(lead.id, next);
      onRefresh();
    } catch {
      setLocalTags(prev);
      alert("Erro ao remover tag. Tente novamente.");
    }
  };

  const sendMsg = async () => {
    if (!msg.trim()) return;
    setSending(true);
    try {
      await crmSendMessage(lead.id, msg.trim());
      setMsg("");
      onRefresh();
    } finally {
      setSending(false);
    }
  };

  const saveNote = async () => {
    if (!note.trim()) return;
    setSavingNote(true);
    try {
      await crmAddNote(lead.id, note.trim());
      setNote("");
      onRefresh();
    } finally {
      setSavingNote(false);
    }
  };

  const deleteLead = async () => {
    if (!confirm(`Remover "${lead.nome}" do CRM?`)) return;
    setDeleting(true);
    try {
      await crmDeleteLead(lead.id);
      onClose();
      onRefresh();
    } catch {
      alert("Erro ao remover lead. Tente novamente.");
    } finally {
      setDeleting(false);
    }
  };

  const moveTo = async (colId: string) => {
    setMoving(true);
    try {
      await crmMoveLead(lead.id, colId);
      onRefresh();
    } catch {
      alert("Erro ao mover lead. Tente novamente.");
    } finally {
      setMoving(false);
    }
  };

  const saveContact = async () => {
    if (!contactNota.trim()) return;
    setSavingContact(true);
    try {
      await crmAddContactLog(lead.id, contactTipo, contactNota.trim(), contactProximo || undefined);
      setContactNota("");
      setContactProximo("");
      onRefresh();
    } finally {
      setSavingContact(false);
    }
  };

  const TABS: { id: ModalTab; label: string }[] = [
    { id: "info",      label: "Info" },
    { id: "chat",      label: "💬 Chat" },
    { id: "followup",  label: "Acompanhamento" },
    { id: "history",   label: "Histórico SDR" },
    { id: "notes",     label: "Notas" },
    { id: "timeline",  label: "Timeline" },
  ];

  return (
    <div
      className="fixed inset-0 bg-black/60 z-[200] flex items-center justify-center p-4"
      onClick={(e) => e.target === e.currentTarget && onClose()}
    >
      <div className="relative bg-[#E8E4DF] border border-[#D4CFC9] rounded-2xl w-full max-w-lg max-h-[90vh] overflow-y-auto">
        <FollowupBeacon date={lead.followup_date} />
        {/* Header */}
        <div className="flex items-start justify-between p-5 border-b border-[#D4CFC9]">
          <div className="flex-1 min-w-0">
            <h2 className="text-[#1A1410] font-bold text-lg leading-tight truncate">{lead.nome}</h2>
            <p className="text-[#6B5E55] text-sm">{lead.telefone}</p>
            <div className="flex items-center gap-2 mt-2 flex-wrap">
              <span className={`text-[10px] font-semibold px-2 py-0.5 rounded-full flex items-center gap-1 ${o.bg} ${o.text}`}><span>{o.icon}</span>{o.label}</span>
              {lead.instancia_id && (
                <span className="text-[10px] text-[#6B5E55] bg-[#F0EDE8] px-2 py-0.5 rounded-full">
                  {lead.instancia_id}
                </span>
              )}
              {lead.assigned_to_name && (
                <span className="text-[10px] text-[#CC3300] bg-[#3D0A00] px-2 py-0.5 rounded-full">
                  {lead.assigned_to_name}
                </span>
              )}
              {lead.score !== null && lead.score !== undefined && (
                <span className={`text-xs font-bold ${scoreColor(lead.score)}`}>Score {lead.score}</span>
              )}
              {lead.lead_status && (
                <span className="text-[10px] font-semibold px-2 py-0.5 rounded-full bg-[#0E0E18] text-[#CC3300]">
                  {statusLabel(lead.lead_status)}
                </span>
              )}
              {fuStatus === "vencido" && (
                <span className="flex items-center gap-1.5 text-[10px] font-bold text-[#F87171] bg-[#2D0A0A] px-2 py-0.5 rounded-full"><FollowupDot status="vencido" />Follow-up atrasado</span>
              )}
              {fuStatus === "hoje" && (
                <span className="flex items-center gap-1.5 text-[10px] font-bold text-[#F0B429] bg-[#2D2000] px-2 py-0.5 rounded-full"><FollowupDot status="hoje" />Follow-up hoje</span>
              )}
              {fuStatus === "proximo" && (
                <span className="flex items-center gap-1.5 text-[10px] font-bold text-[#60A5FA] bg-[#0A1A2D] px-2 py-0.5 rounded-full"><FollowupDot status="proximo" />Follow-up próximo</span>
              )}
            </div>
          </div>
          <button onClick={onClose} className="text-[#6B5E55] hover:text-[#1A1410] text-2xl leading-none ml-4 shrink-0">✕</button>
        </div>

        {/* Score bar */}
        {lead.score !== null && lead.score !== undefined && (
          <div className="px-5 pt-3">
            <div className="flex items-center gap-2 mb-1">
              <span className="text-[#6B5E55] text-xs">Score</span>
              <span className={`text-xs font-bold ${scoreColor(lead.score)}`}>{lead.score}/100</span>
            </div>
            <div className="h-2 bg-[#F0EDE8] rounded-full overflow-hidden">
              <div
                className="h-full rounded-full transition-all"
                style={{ width: `${lead.score}%`, backgroundColor: scoreBg(lead.score) }}
              />
            </div>
            {lead.score_reason && (
              <p className="text-[#6B5E55] text-xs mt-1 italic">{lead.score_reason}</p>
            )}
          </div>
        )}

        {/* Tab bar */}
        <div className="flex border-b border-[#D4CFC9] px-5 mt-3">
          {TABS.map((t) => (
            <button
              key={t.id}
              onClick={() => setTab(t.id)}
              className={`px-3 py-2 text-xs font-semibold border-b-2 transition-colors -mb-px ${
                tab === t.id
                  ? "border-[#CC3300] text-[#CC3300]"
                  : "border-transparent text-[#6B5E55] hover:text-[#1A1410]"
              }`}
            >
              {t.label}
            </button>
          ))}
        </div>

        <div className="p-5 space-y-5">

          {/* ── Tab: Info ── */}
          {tab === "info" && (
            <>
              {/* Mover para coluna */}
              <div>
                <p className="text-[#6B5E55] text-xs font-semibold uppercase tracking-wide mb-2">Mover para etapa</p>
                <div className="flex flex-wrap gap-2">
                  {columns.map((col) => (
                    <button
                      key={col.id}
                      disabled={col.id === lead.coluna_id || moving}
                      onClick={() => moveTo(col.id)}
                      className={`text-xs px-3 py-1.5 rounded-lg font-semibold transition-colors ${
                        col.id === lead.coluna_id
                          ? "bg-[#D4CFC9] text-[#1A1410] cursor-default"
                          : "bg-[#F0EDE8] text-[#6B5E55] hover:bg-[#D4CFC9] hover:text-[#1A1410]"
                      }`}
                    >
                      {col.id === lead.coluna_id ? "▶ " : ""}{col.name}
                    </button>
                  ))}
                </div>
              </div>

              {/* Qualificação manual */}
              <div>
                <p className="text-[#6B5E55] text-xs font-semibold uppercase tracking-wide mb-2">Qualificação manual</p>
                <div className="space-y-3">
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                    <div>
                      <label className="text-[#6B5E55] text-[10px] block mb-1">Nota / temperatura</label>
                      <select
                        value={manualScore}
                        onChange={(e) => setManualScore(e.target.value)}
                        className="w-full bg-[#F0EDE8] border border-[#D4CFC9] rounded-xl px-3 py-2 text-[#1A1410] text-xs outline-none focus:border-[#CC3300]"
                      >
                        <option value="">— Manter nota atual —</option>
                        {SCORE_PRESETS.map((preset) => (
                          <option key={preset.value} value={preset.value}>
                            {preset.label} ({preset.hint})
                          </option>
                        ))}
                      </select>
                    </div>
                    <div>
                      <label className="text-[#6B5E55] text-[10px] block mb-1">Status comercial</label>
                      <select
                        value={leadStatus}
                        onChange={(e) => setLeadStatus(e.target.value)}
                        className="w-full bg-[#F0EDE8] border border-[#D4CFC9] rounded-xl px-3 py-2 text-[#1A1410] text-xs outline-none focus:border-[#CC3300]"
                      >
                        {LEAD_STATUS_OPTIONS.map((status) => (
                          <option key={status.value} value={status.value}>
                            {status.label}
                          </option>
                        ))}
                      </select>
                    </div>
                  </div>
                  <p className="text-[#6B5E55] text-[11px] leading-relaxed">
                    A nota ajuda o time a priorizar o lead. O status comercial é o que dispara o evento Meta CAPI quando houver correspondência.
                  </p>
                </div>
              </div>

              {/* Campos customizados */}
              <div>
                <p className="text-[#6B5E55] text-xs font-semibold uppercase tracking-wide mb-2">Dados do lead</p>
                <div className="space-y-2">
                  <div className="grid grid-cols-2 gap-2">
                    <div>
                      <label className="text-[#6B5E55] text-[10px] block mb-1">Nome</label>
                      <input
                        value={nomeEdit}
                        onChange={(e) => setNomeEdit(e.target.value)}
                        placeholder="Nome do lead"
                        className="w-full bg-[#F0EDE8] border border-[#D4CFC9] rounded-xl px-3 py-2 text-[#1A1410] text-xs placeholder-[#6B5E55] outline-none focus:border-[#CC3300]"
                      />
                    </div>
                    <div>
                      <label className="text-[#6B5E55] text-[10px] block mb-1">Telefone</label>
                      <input
                        value={telefoneEdit}
                        onChange={(e) => setTelefoneEdit(e.target.value)}
                        placeholder="5511999999999"
                        className="w-full bg-[#F0EDE8] border border-[#D4CFC9] rounded-xl px-3 py-2 text-[#1A1410] text-xs placeholder-[#6B5E55] outline-none focus:border-[#CC3300]"
                      />
                    </div>
                  </div>
                  <div className="grid grid-cols-2 gap-2">
                    <div>
                      <label className="text-[#6B5E55] text-[10px] block mb-1">Email</label>
                      <input
                        value={email}
                        onChange={(e) => setEmail(e.target.value)}
                        placeholder="email@exemplo.com"
                        className="w-full bg-[#F0EDE8] border border-[#D4CFC9] rounded-xl px-3 py-2 text-[#1A1410] text-xs placeholder-[#6B5E55] outline-none focus:border-[#CC3300]"
                      />
                    </div>
                    <div>
                      <label className="text-[#6B5E55] text-[10px] block mb-1">Empresa</label>
                      <input
                        value={empresa}
                        onChange={(e) => setEmpresa(e.target.value)}
                        placeholder="Nome da empresa"
                        className="w-full bg-[#F0EDE8] border border-[#D4CFC9] rounded-xl px-3 py-2 text-[#1A1410] text-xs placeholder-[#6B5E55] outline-none focus:border-[#CC3300]"
                      />
                    </div>
                  </div>
                  <div className="mb-2">
                    <label className="text-[#6B5E55] text-[10px] block mb-1">Instância</label>
                    <select
                      value={instanciaEdit}
                      onChange={(e) => setInstanciaEdit(e.target.value)}
                      className="w-full bg-[#F0EDE8] border border-[#D4CFC9] rounded-xl px-3 py-2 text-[#1A1410] text-xs outline-none focus:border-[#CC3300]"
                    >
                      <option value="">— Nenhuma —</option>
                      {instanceList.map((inst) => (
                        <option key={inst.id} value={inst.id}>{inst.id}</option>
                      ))}
                    </select>
                  </div>
                  <div className="grid grid-cols-2 gap-2">
                    <div>
                      <label className="text-[#6B5E55] text-[10px] block mb-1">Cidade</label>
                      <input
                        value={cidade}
                        onChange={(e) => setCidade(e.target.value)}
                        placeholder="Ex: Sao Paulo"
                        className="w-full bg-[#F0EDE8] border border-[#D4CFC9] rounded-xl px-3 py-2 text-[#1A1410] text-xs placeholder-[#6B5E55] outline-none focus:border-[#CC3300]"
                      />
                    </div>
                    <div>
                      <label className="text-[#6B5E55] text-[10px] block mb-1">Estado</label>
                      <input
                        value={estado}
                        onChange={(e) => setEstado(e.target.value)}
                        placeholder="Ex: SP"
                        maxLength={32}
                        className="w-full bg-[#F0EDE8] border border-[#D4CFC9] rounded-xl px-3 py-2 text-[#1A1410] text-xs placeholder-[#6B5E55] outline-none focus:border-[#CC3300]"
                      />
                    </div>
                  </div>
                  <div className="grid grid-cols-2 gap-2">
                    <div>
                      <label className="text-[#6B5E55] text-[10px] block mb-1">CEP</label>
                      <input
                        value={cep}
                        onChange={(e) => setCep(e.target.value)}
                        placeholder="Ex: 01001000"
                        className="w-full bg-[#F0EDE8] border border-[#D4CFC9] rounded-xl px-3 py-2 text-[#1A1410] text-xs placeholder-[#6B5E55] outline-none focus:border-[#CC3300]"
                      />
                    </div>
                    <div>
                      <label className="text-[#6B5E55] text-[10px] block mb-1">Pais</label>
                      <input
                        value={pais}
                        onChange={(e) => setPais(e.target.value)}
                        placeholder="BR"
                        maxLength={32}
                        className="w-full bg-[#F0EDE8] border border-[#D4CFC9] rounded-xl px-3 py-2 text-[#1A1410] text-xs placeholder-[#6B5E55] outline-none focus:border-[#CC3300]"
                      />
                    </div>
                  </div>
                  <div className="rounded-xl bg-[#F0EDE8] border border-[#D4CFC9] p-3 space-y-3">
                    <div>
                      <p className="text-[#6B5E55] text-[10px] font-semibold uppercase tracking-wide">Identidade e rastreamento</p>
                      <p className="text-[#8A817A] text-[9px] mt-0.5">Identificadores técnicos vinculados ao mesmo contato.</p>
                    </div>
                    <div className="grid grid-cols-1 gap-2">
                      <IdentityValue
                        label="Telefone WhatsApp"
                        value={lead.telefone || ""}
                        hint="Número usado para contato e chave principal da conversa."
                      />
                      <IdentityValue
                        label="WhatsApp LID"
                        value={whatsappLid}
                        hint="Identificador interno do usuário no WhatsApp; não é um número de telefone."
                      />
                      <div>
                        <label className="text-[#6B5E55] text-[10px] block mb-1">Meta Lead ID (Lead Ads)</label>
                        <input
                          value={leadgenIdEdit}
                          onChange={(e) => setLeadgenIdEdit(e.target.value)}
                          placeholder="ID do cadastro gerado pelo formulário"
                          className="w-full bg-white border border-[#D4CFC9] rounded-xl px-3 py-2 text-[#1A1410] text-[11px] font-mono placeholder-[#9B9490] outline-none focus:border-[#CC3300]"
                        />
                        <p className="text-[#8A817A] text-[9px] mt-0.5">Identificador do cadastro na Meta; diferente do WhatsApp LID.</p>
                      </div>
                      <IdentityValue
                        label="CTWA Click ID"
                        value={lead.ctwa_clid || ""}
                        hint="Identificador do clique no anúncio que abriu o WhatsApp."
                      />
                      <IdentityValue label="Business Suite UID" value={lead.bsuid || ""} />
                    </div>
                  </div>
                  <div className="grid grid-cols-2 gap-2">
                    <div>
                      <label className="text-[#6B5E55] text-[10px] block mb-1">Valor estimado</label>
                      <input
                        value={valor}
                        onChange={(e) => setValor(e.target.value)}
                        placeholder="Ex: R$ 5.000"
                        className="w-full bg-[#F0EDE8] border border-[#D4CFC9] rounded-xl px-3 py-2 text-[#1A1410] text-xs placeholder-[#6B5E55] outline-none focus:border-[#CC3300]"
                      />
                    </div>
                    <div>
                      <label className="text-[#6B5E55] text-[10px] block mb-1">Follow-up</label>
                      <input
                        type="date"
                        value={followup}
                        onChange={(e) => setFollowup(e.target.value)}
                        className="w-full bg-[#F0EDE8] border border-[#D4CFC9] rounded-xl px-3 py-2 text-[#1A1410] text-xs outline-none focus:border-[#CC3300]"
                      />
                    </div>
                  </div>
                  <div className="grid grid-cols-2 gap-3">
                    <div>
                      <label className="text-[#6B5E55] text-[10px] block mb-1">Segmento</label>
                      <select
                        value={segmento}
                        onChange={(e) => setSegmento(e.target.value)}
                        className="w-full bg-[#F0EDE8] border border-[#D4CFC9] rounded-xl px-3 py-2 text-[#1A1410] text-xs outline-none focus:border-[#CC3300]"
                      >
                        <option value="">— Selecione —</option>
                        <option value="alto_padrao">🏆 Alto Padrão</option>
                        <option value="medio_padrao">🏠 Médio Padrão</option>
                        <option value="mcmv">🏗️ MCMV</option>
                      </select>
                    </div>
                    <div>
                      <label className="text-[#6B5E55] text-[10px] block mb-1">Poder Aquisitivo</label>
                      <select
                        value={poderAquisitivo}
                        onChange={(e) => setPoderAquisitivo(e.target.value)}
                        className="w-full bg-[#F0EDE8] border border-[#D4CFC9] rounded-xl px-3 py-2 text-[#1A1410] text-xs outline-none focus:border-[#CC3300]"
                      >
                        <option value="">— Selecione —</option>
                        <option value="A">A — acima de R$ 1M</option>
                        <option value="B">B — R$ 500k a 1M</option>
                        <option value="C">C — R$ 200k a 500k</option>
                        <option value="D">D — abaixo de R$ 200k</option>
                      </select>
                    </div>
                  </div>

                  <button
                    onClick={saveFields}
                    disabled={savingFields}
                    className="w-full bg-[#D4CFC9] hover:bg-[#3D4F5A] text-[#1A1410] text-xs py-2 rounded-xl font-semibold transition-colors disabled:opacity-40"
                  >
                    {savingFields ? "Salvando..." : "Salvar dados"}
                  </button>
                </div>
              </div>

              {/* Tags */}
              <div>
                <p className="text-[#6B5E55] text-xs font-semibold uppercase tracking-wide mb-2">Tags</p>
                <div className="flex flex-wrap gap-1.5 mb-2">
                  {localTags.map((tag) => (
                    <span key={tag} className="flex items-center gap-1 bg-[#D4CFC9] text-[#1A1410] text-xs px-2 py-0.5 rounded-full">
                      {tag}
                      <button onClick={() => removeTag(tag)} className="text-[#6B5E55] hover:text-[#F87171] leading-none ml-0.5">x</button>
                    </span>
                  ))}
                </div>
                <div className="flex gap-2">
                  <input
                    value={tagInput}
                    onChange={(e) => setTagInput(e.target.value)}
                    onKeyDown={(e) => e.key === "Enter" && addTag()}
                    placeholder="Nova tag..."
                    className="flex-1 bg-[#F0EDE8] border border-[#D4CFC9] rounded-xl px-3 py-2 text-[#1A1410] text-xs placeholder-[#6B5E55] outline-none focus:border-[#CC3300]"
                  />
                  <button
                    onClick={addTag}
                    disabled={!tagInput.trim()}
                    className="bg-[#D4CFC9] hover:bg-[#3D4F5A] text-[#1A1410] px-3 py-2 rounded-xl text-xs font-semibold transition-colors disabled:opacity-40"
                  >
                    Add
                  </button>
                </div>
              </div>

              {/* Confirmação manual */}
              {lead.instancia_id && (
                <div className="rounded-2xl border border-[#CC3300]/30 bg-[#FFF0EC] p-4 space-y-3">
                  <div className="flex items-center justify-between gap-3">
                    <div>
                      <p className="text-[#1A1410] text-xs font-bold uppercase tracking-wide">Enviar confirmação</p>
                      <p className="text-[#6B5E55] text-[10px]">Escolha texto comum ou enquete nativa.</p>
                    </div>
                  </div>
                  <div className="grid grid-cols-2 gap-2 rounded-xl bg-white border border-[#D4CFC9] p-1">
                    <button type="button" onClick={() => setConfirmationDeliveryFormat("text")} className={`rounded-lg px-3 py-2 text-xs font-semibold ${confirmationDeliveryFormat === "text" ? "bg-[#CC3300] text-white" : "text-[#6B5E55]"}`}>Texto</button>
                    <button type="button" onClick={() => setConfirmationDeliveryFormat("poll")} className={`rounded-lg px-3 py-2 text-xs font-semibold ${confirmationDeliveryFormat === "poll" ? "bg-[#CC3300] text-white" : "text-[#6B5E55]"}`}>Enquete</button>
                  </div>
                  <div className="flex gap-2">
                    <select value={confirmationTemplateId} onChange={(e) => selectConfirmationTemplate(e.target.value)} disabled={confirmationEditTemplate} className="min-w-0 flex-1 bg-white border border-[#D4CFC9] rounded-xl px-3 py-2 text-[#1A1410] text-xs outline-none focus:border-[#CC3300] disabled:opacity-60">
                      {confirmationTemplates.map(template => <option key={template.id} value={template.id}>{template.name}</option>)}
                    </select>
                    <button
                      type="button"
                      onClick={() => {
                        const nextEditMode = !confirmationEditTemplate;
                        const template = confirmationTemplates.find(item => item.id === confirmationTemplateId);
                        if (template) {
                          setConfirmationText(nextEditMode ? template.message_text : fillConfirmationVariables(template.message_text));
                        }
                        setConfirmationEditTemplate(nextEditMode);
                        setConfirmationTemplateDirty(false);
                        setConfirmationSaveStatus("idle");
                      }}
                      disabled={confirmationTemplateDirty || confirmationSaveStatus === "saving"}
                      aria-label={confirmationEditTemplate ? "Parar de editar modelo" : "Editar modelo"}
                      title={confirmationEditTemplate ? "Parar de editar modelo" : "Editar modelo"}
                      className={`h-9 w-10 rounded-xl border text-base transition disabled:opacity-50 ${confirmationEditTemplate ? "border-[#CC3300] bg-[#CC3300] text-white" : "border-[#D4CFC9] bg-white text-[#6B5E55] hover:border-[#CC3300] hover:text-[#CC3300]"}`}
                    >
                      ✎
                    </button>
                  </div>
                  {confirmationEditTemplate && (
                    <div className="flex items-center justify-between text-[10px]">
                      <span className="font-semibold text-[#CC3300]">Editando o modelo — salvamento automático</span>
                      <span className={confirmationSaveStatus === "error" ? "text-[#D9534F]" : "text-[#6B5E55]"}>
                        {confirmationSaveStatus === "saving" ? "Salvando..." : confirmationSaveStatus === "saved" ? "Salvo" : confirmationSaveStatus === "error" ? "Erro ao salvar" : confirmationTemplateDirty ? "Alterações não salvas" : "Sem alterações"}
                      </span>
                    </div>
                  )}
                  {!confirmationEditTemplate && (
                    <p className="text-[10px] text-[#6B5E55]">O nome e os dados do lead já foram aplicados. Ajuste este texto livremente antes do envio.</p>
                  )}
                  <textarea value={confirmationText} onChange={(e) => { setConfirmationText(e.target.value); if (confirmationEditTemplate) { setConfirmationTemplateDirty(true); setConfirmationSaveStatus("idle"); } }} rows={3} className="w-full resize-y bg-white border border-[#D4CFC9] rounded-xl px-3 py-2 text-[#1A1410] text-xs outline-none focus:border-[#CC3300]" />
                  <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
                    {confirmationOptions.map((option, index) => (
                      <label key={option.id} className="bg-white border border-[#D4CFC9] rounded-xl p-2 space-y-1">
                        <span className="text-[10px] text-[#6B5E55]">Opção {index + 1}</span>
                        <div className="flex gap-1 mt-1">
                          <input value={option.emoji} onChange={(e) => { setConfirmationOptions(items => items.map((item, i) => i === index ? { ...item, emoji: e.target.value } : item)); if (confirmationEditTemplate) { setConfirmationTemplateDirty(true); setConfirmationSaveStatus("idle"); } }} className="w-9 bg-[#F0EDE8] rounded px-1 text-center text-xs" />
                          <input value={option.label} onChange={(e) => { setConfirmationOptions(items => items.map((item, i) => i === index ? { ...item, label: e.target.value } : item)); if (confirmationEditTemplate) { setConfirmationTemplateDirty(true); setConfirmationSaveStatus("idle"); } }} className="min-w-0 flex-1 bg-[#F0EDE8] rounded px-2 py-1 text-xs font-semibold" />
                        </div>
                      </label>
                    ))}
                  </div>
                  <div className="rounded-xl bg-white border border-[#D4CFC9] p-3 text-xs whitespace-pre-wrap text-[#1A1410]">
                    <WhatsAppBoldPreview text={emphasizeConfirmationDetails(fillConfirmationVariables(confirmationText), lead.nome || "")} />
                    {confirmationDeliveryFormat === "text" ? "\n\nResponda com uma das opções:\n\n" : "\n\nEscolha somente uma opção:\n\n"}
                    {confirmationOptions.map(option => `${option.emoji} ${option.label}`.trim()).join("\n")}
                  </div>
                  {confirmationError && <p className="text-[#D9534F] text-xs">{confirmationError}</p>}
                  <button onClick={sendConfirmation} disabled={confirmationEditTemplate || confirmationSending || !confirmationText.trim() || confirmationOptions.length === 0} className="w-full bg-[#CC3300] hover:bg-[#991F00] text-white font-bold py-2 rounded-xl text-sm disabled:opacity-40">
                    {confirmationSending ? "Enviando..." : confirmationDeliveryFormat === "poll" ? "Enviar enquete" : "Enviar mensagem de texto"}
                  </button>
                </div>
              )}

              {/* Enviar mensagem WhatsApp */}
              {lead.instancia_id && (
                <div>
                  <p className="text-[#6B5E55] text-xs font-semibold uppercase tracking-wide mb-2">Enviar mensagem WhatsApp</p>
                  <div className="flex gap-2">
                    <input
                      value={msg}
                      onChange={(e) => setMsg(e.target.value)}
                      onKeyDown={(e) => e.key === "Enter" && !e.shiftKey && sendMsg()}
                      placeholder="Mensagem..."
                      className="flex-1 bg-[#F0EDE8] border border-[#D4CFC9] rounded-xl px-3 py-2 text-[#1A1410] text-sm placeholder-[#6B5E55] outline-none focus:border-[#CC3300]"
                    />
                    <button
                      onClick={sendMsg}
                      disabled={sending || !msg.trim()}
                      className="bg-[#CC3300] hover:bg-[#991F00] text-[#F0EDE8] font-bold px-4 py-2 rounded-xl text-sm transition-colors disabled:opacity-40"
                    >
                      {sending ? "..." : "Enviar"}
                    </button>
                  </div>
                </div>
              )}

              {/* Adicionar à lista de espera */}
              {listas.length > 0 && (
                <div>
                  <p className="text-[#6B5E55] text-xs font-semibold uppercase tracking-wide mb-2">Adicionar à lista de espera</p>
                  <AddToListaPopover listas={listas} onConfirm={async (listaId) => { await onConfirmLista(listaId); onRefresh(); }} />
                </div>
              )}

              {/* Cobertura Meta CAPI — sempre visível */}
              <div className="rounded-2xl border border-[#D4CFC9] bg-[#F0EDE8]">
                <button
                  onClick={() => setShowCapi(v => !v)}
                  className="w-full flex items-center justify-between px-4 py-3 text-xs font-semibold text-[#6B5E55] hover:text-[#1A1410] transition-colors"
                >
                  <span className="uppercase tracking-wide">
                    Meta CAPI — últimos eventos
                    {capiAudit && capiAudit.length > 0 && (
                      <span className={`ml-2 ${capiAudit[0].ok ? "text-[#CC3300]" : "text-[#F87171]"}`}>
                        ({capiAudit[0].ok ? "✓" : "✗"} {capiAudit[0].event_name})
                      </span>
                    )}
                  </span>
                  <span>{showCapi ? "▲" : "▼"}</span>
                </button>
                {showCapi && (
                  <div className="px-4 pb-4 space-y-3">
                    {capiLoading && (
                      <p className="text-[#6B5E55] text-xs">Carregando...</p>
                    )}
                    {!capiLoading && (!capiAudit || capiAudit.length === 0) && (
                      <p className="text-[#6B5E55] text-xs">Nenhum evento CAPI enviado para este lead.</p>
                    )}
                    {capiAudit && capiAudit.map((item, i) => {
                      const ud = (((item.payload as Record<string, unknown>)?.data as Record<string, unknown>[])?.[0]?.user_data) as Record<string, unknown> | undefined;
                      const fields = ud ? Object.keys(ud) : [];
                      const timeAgo = (() => {
                        const diff = Math.floor((Date.now() - new Date(item.timestamp).getTime()) / 60000);
                        if (diff < 60) return `${diff}min`;
                        if (diff < 1440) return `${Math.floor(diff / 60)}h`;
                        return `${Math.floor(diff / 1440)}d`;
                      })();
                      return (
                        <div key={i} className={`rounded-xl border px-3 py-2.5 text-xs ${item.ok ? "border-[#CC3300]/25 bg-[#FFF0EC]" : "border-[#F87171]/30 bg-[#FFF0F0]"}`}>
                          <div className="flex items-center justify-between gap-2 mb-1">
                            <span className={`font-bold ${item.ok ? "text-[#CC3300]" : "text-[#D9534F]"}`}>
                              {item.ok ? "✓" : "✗"} {item.event_name}
                            </span>
                            <span className="text-[#6B5E55]">{timeAgo} atrás</span>
                          </div>
                          {fields.length > 0 && (
                            <div className="flex flex-wrap gap-1 mb-1">
                              {fields.map(f => (
                                <span key={f} className="bg-[#E8E4DF] border border-[#D4CFC9] text-[#1A1410] px-1.5 py-0.5 rounded text-[10px] font-mono">{f}</span>
                              ))}
                            </div>
                          )}
                          {!item.ok && item.detail && (
                            <div className="text-[#F87171] mt-0.5 truncate" title={item.detail}>{item.detail}</div>
                          )}
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>

              {/* Deletar lead */}
              <button
                onClick={deleteLead}
                disabled={deleting}
                className="w-full text-[#F87171] text-sm py-2 hover:bg-[#2D0A0A] rounded-xl transition-colors"
              >
                {deleting ? "Removendo..." : "Remover lead do CRM"}
              </button>
            </>
          )}

          {/* ── Tab: Chat ── */}
          {tab === "chat" && (
            <div className="flex flex-col h-[500px]">
              {/* Abrir no Inbox */}
              {lead.instancia_id && lead.telefone && (
                <div className="flex items-center gap-2 pb-2 border-b border-[#D4CFC9] mb-2">
                  <button
                    onClick={async () => {
                      setSeeding(true);
                      setSeedError("");
                      try {
                        const res = await crmSeedConversation(lead.id);
                        router.push(`/instances/${res.instance_id}/inbox`);
                      } catch (e: unknown) {
                        setSeedError(e instanceof Error ? e.message : "Erro ao abrir inbox.");
                      } finally {
                        setSeeding(false);
                      }
                    }}
                    disabled={seeding}
                    className="bg-[#CC3300] hover:bg-[#1da851] disabled:opacity-50 text-[#F0EDE8] font-bold text-xs px-4 py-1.5 rounded-lg transition-colors"
                  >
                    {seeding ? "Abrindo..." : "Abrir no Inbox →"}
                  </button>
                  {seedError && <span className="text-[#F87171] text-xs">{seedError}</span>}
                </div>
              )}

              {/* Carregar conversa */}
              {!conv && !convLoading && (
                <div className="flex flex-col items-center justify-center flex-1 gap-3">
                  <p className="text-[#6B5E55] text-sm">Clique para carregar a conversa do WhatsApp</p>
                  <button
                    onClick={() => loadConv()}
                    className="bg-[#CC3300] hover:bg-[#1da851] text-[#F0EDE8] font-bold text-sm px-5 py-2 rounded-xl transition-colors"
                  >
                    Carregar chat
                  </button>
                </div>
              )}
              {convLoading && (
                <div className="flex items-center justify-center flex-1">
                  <p className="text-[#6B5E55] text-sm">Carregando...</p>
                </div>
              )}
              {conv && !convLoading && (
                <>
                  {/* Conversa apagada do inbox — histórico preservado no card */}
                  {convArchived && (
                    <div className="mx-1 mb-2 bg-amber-50 border border-amber-300 rounded-xl px-3 py-2">
                      <p className="text-amber-800 text-xs">
                        📁 Esta conversa foi removida do inbox — exibindo o histórico gravado no card.
                        Enviar uma mensagem reabre a conversa no WhatsApp.
                      </p>
                    </div>
                  )}
                  {/* Status da conversa */}
                  <div className="flex items-center justify-between px-1 pb-2 border-b border-[#D4CFC9]">
                    <div className="flex items-center gap-2">
                      <span className={`text-[10px] font-bold px-2 py-0.5 rounded-full ${
                        conv.stage === "handoff"     ? "bg-[#3D0A00] text-[#CC3300]" :
                        conv.stage === "qualifying"  ? "bg-[#0E0E18] text-[#60A5FA]" :
                        conv.stage === "disqualified"? "bg-[#2D0A0A] text-[#F87171]" :
                                                       "bg-[#D4CFC9] text-[#6B5E55]"
                      }`}>{conv.stage}</span>
                      {conv.ai_paused && <span className="text-[10px] text-[#F0B429]">👤 Humano no controle</span>}
                    </div>
                    <button onClick={() => loadConv()} className="text-[#6B5E55] hover:text-[#1A1410] text-xs">↺ Atualizar</button>
                  </div>

                  {/* Mensagens */}
                  <div className="flex-1 overflow-y-auto py-3 space-y-2 px-1">
                    {conv.messages && conv.messages.length > 0 ? conv.messages.map((m, i) => (
                      <div key={i} className={`flex ${m.role === "user" ? "justify-start" : "justify-end"}`}>
                        <div className={`max-w-[80%] rounded-2xl px-3 py-2 text-sm ${
                          m.role === "user"
                            ? "bg-[#E8E4DF] text-[#1A1410] rounded-tl-sm"
                            : m.role === "human"
                            ? "bg-[#CC3300] text-white rounded-tr-sm"
                            : "bg-[#991F00] text-white rounded-tr-sm"
                        }`}>
                          {m.role !== "user" && (
                            <p className="text-[9px] font-bold mb-0.5 opacity-70">
                              {m.role === "human" ? "Atendente" : "SDR"}
                            </p>
                          )}
                          <p className="leading-snug">{m.content}</p>
                          {m.timestamp && (
                            <p className="text-[9px] opacity-50 text-right mt-0.5">
                              {new Date(m.timestamp).toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" })}
                            </p>
                          )}
                        </div>
                      </div>
                    )) : (
                      <p className="text-[#6B5E55] text-sm text-center py-8">Nenhuma mensagem ainda.</p>
                    )}
                    <div ref={chatEndRef} />
                  </div>

                  {/* Input envio */}
                  <div className="border-t border-[#D4CFC9] pt-3 space-y-2">
                    {chatFile && (
                      <div className="flex items-center gap-2 bg-[#D4CFC9] rounded-xl px-3 py-2">
                        <span className="text-base">{chatFile.type.startsWith("image/") ? "🖼" : "📄"}</span>
                        <span className="text-[#1A1410] flex-1 truncate text-xs">{chatFile.name}</span>
                        <button onClick={sendChatMedia} disabled={chatSendingMedia} className="bg-[#CC3300] hover:bg-[#AA2200] disabled:opacity-50 text-white text-xs font-semibold px-3 py-1 rounded-lg transition-colors">
                          {chatSendingMedia ? "..." : "Enviar"}
                        </button>
                        <button onClick={() => setChatFile(null)} className="text-[#6B5E55] hover:text-[#CC3300]">
                          <svg viewBox="0 0 24 24" className="w-4 h-4 fill-current"><path d="M19 6.41L17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z"/></svg>
                        </button>
                      </div>
                    )}
                    <input
                      ref={chatFileRef}
                      type="file"
                      accept="image/jpeg,image/png,image/webp,image/gif,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                      className="hidden"
                      onChange={e => { const f = e.target.files?.[0]; if (f) setChatFile(f); e.target.value = ""; }}
                    />
                    <div className="flex gap-2">
                      <button
                        type="button"
                        onClick={() => chatFileRef.current?.click()}
                        title="Enviar imagem ou documento"
                        className="w-9 h-9 rounded-full flex items-center justify-center flex-shrink-0 bg-[#D4CFC9] hover:bg-[#CC3300]/20 text-[#6B5E55] hover:text-[#CC3300] transition-all"
                      >
                        <svg viewBox="0 0 24 24" className="w-4 h-4 fill-current">
                          <path d="M16.5 6v11.5c0 2.21-1.79 4-4 4s-4-1.79-4-4V5c0-1.38 1.12-2.5 2.5-2.5s2.5 1.12 2.5 2.5v10.5c0 .55-.45 1-1 1s-1-.45-1-1V6H10v9.5c0 1.38 1.12 2.5 2.5 2.5s2.5-1.12 2.5-2.5V5c0-2.21-1.79-4-4-4S7 2.79 7 5v12.5c0 3.04 2.46 5.5 5.5 5.5s5.5-2.46 5.5-5.5V6h-1.5z"/>
                        </svg>
                      </button>
                      <input
                        value={chatMsg}
                        onChange={(e) => setChatMsg(e.target.value)}
                        onKeyDown={(e) => e.key === "Enter" && !e.shiftKey && sendChatMsg()}
                        placeholder="Escreva uma mensagem..."
                        className="flex-1 bg-[#D4CFC9] text-[#1A1410] text-sm rounded-xl px-3 py-2 outline-none placeholder-[#6B5E55] border border-[#3D4F5A] focus:border-[#CC3300]"
                      />
                      <button
                        onClick={sendChatMsg}
                        disabled={chatSending || !chatMsg.trim()}
                        className="bg-[#CC3300] hover:bg-[#991F00] disabled:opacity-50 text-[#F0EDE8] font-bold text-sm px-4 rounded-xl transition-colors"
                      >
                        {chatSending ? "..." : "➤"}
                      </button>
                    </div>
                  </div>
                </>
              )}
            </div>
          )}

          {/* ── Tab: Acompanhamento ── */}
          {tab === "followup" && (
            <div className="space-y-4">
              {/* Formulário novo contato */}
              <div className="bg-[#F0EDE8] rounded-xl p-4 space-y-3">
                <p className="text-[#1A1410] text-xs font-semibold">Registrar contato</p>
                <select
                  value={contactTipo}
                  onChange={(e) => setContactTipo(e.target.value)}
                  className="w-full bg-[#D4CFC9] text-[#1A1410] text-sm rounded-lg px-3 py-2 border border-[#3D4F5A] focus:outline-none"
                >
                  <option value="ligação">📞 Ligação</option>
                  <option value="whatsapp">💬 WhatsApp</option>
                  <option value="visita">🏠 Visita</option>
                  <option value="email">✉️ E-mail</option>
                  <option value="proposta">📄 Proposta</option>
                  <option value="outro">📝 Outro</option>
                </select>
                <textarea
                  value={contactNota}
                  onChange={(e) => setContactNota(e.target.value)}
                  placeholder="O que aconteceu neste contato?"
                  rows={3}
                  className="w-full bg-[#D4CFC9] text-[#1A1410] text-sm rounded-lg px-3 py-2 border border-[#3D4F5A] focus:outline-none resize-none placeholder-[#6B5E55]"
                />
                <div className="flex gap-2 items-center">
                  <div className="flex-1">
                    <p className="text-[#6B5E55] text-[10px] mb-1">Próximo contato</p>
                    <input
                      type="date"
                      value={contactProximo}
                      onChange={(e) => setContactProximo(e.target.value)}
                      className="w-full bg-[#D4CFC9] text-[#1A1410] text-sm rounded-lg px-3 py-2 border border-[#3D4F5A] focus:outline-none"
                    />
                  </div>
                  <button
                    onClick={saveContact}
                    disabled={savingContact || !contactNota.trim()}
                    className="mt-4 bg-[#CC3300] hover:bg-[#1da851] disabled:opacity-50 text-[#F0EDE8] font-bold text-sm px-4 py-2 rounded-lg transition-colors"
                  >
                    {savingContact ? "..." : "Salvar"}
                  </button>
                </div>
              </div>

              {/* Histórico de contatos */}
              {lead.contatos && lead.contatos.length > 0 ? (
                <div className="space-y-2">
                  {[...lead.contatos].reverse().map((c, i) => (
                    <div key={i} className="bg-[#F0EDE8] rounded-xl p-3 border border-[#D4CFC9]">
                      <div className="flex items-center justify-between mb-1">
                        <span className="text-[#CC3300] text-xs font-semibold capitalize">{c.tipo}</span>
                        <span className="text-[#6B5E55] text-[10px]">{new Date(c.at).toLocaleDateString("pt-BR", { day:"2-digit", month:"short", year:"numeric", hour:"2-digit", minute:"2-digit" })}</span>
                      </div>
                      <p className="text-[#1A1410] text-sm">{c.nota}</p>
                    </div>
                  ))}
                </div>
              ) : (
                <p className="text-[#6B5E55] text-sm text-center py-4">Nenhum contato registrado ainda.</p>
              )}
            </div>
          )}

          {/* ── Tab: Histórico ── */}
          {tab === "history" && (() => {
            // Preferência: conversa ao vivo (já mesclada com o arquivo pelo
            // loadConv) > snapshot estático do lead — assim esta aba reflete
            // a conversa atual sem precisar reenviar manualmente pro CRM.
            const displayMessages = conv?.messages && conv.messages.length > 0
              ? conv.messages
              : lead.historico;
            return displayMessages && displayMessages.length > 0 ? (
              <div className="bg-[#F0EDE8] rounded-xl p-3 space-y-2 max-h-96 overflow-y-auto">
                {displayMessages.map((m: { role: string; content: string }, i: number) => (
                  <div key={i} className={`flex ${m.role === "user" ? "justify-end" : "justify-start"}`}>
                    <div className={`max-w-[80%] px-3 py-1.5 rounded-xl text-xs ${
                      m.role === "user"
                        ? "bg-[#CC3300] text-white"
                        : "bg-[#E8E4DF] text-[#1A1410]"
                    }`}>
                      {m.content}
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <p className="text-center text-[#6B5E55] text-sm py-8">Nenhum histórico de conversa.</p>
            );
          })()}

          {/* ── Tab: Notas ── */}
          {tab === "notes" && (
            <>
              {lead.notas && lead.notas.length > 0 && (
                <div className="space-y-1.5 mb-4">
                  {lead.notas.map((n: { text: string; at: string }, i: number) => (
                    <div key={i} className="bg-[#F0EDE8] rounded-lg px-3 py-2">
                      <p className="text-[#1A1410] text-xs">{n.text}</p>
                      <p className="text-[#6B5E55] text-[10px] mt-0.5">{timeAgo(n.at)}</p>
                    </div>
                  ))}
                </div>
              )}
              <div>
                <p className="text-[#6B5E55] text-xs font-semibold uppercase tracking-wide mb-2">Adicionar nota</p>
                <div className="flex gap-2">
                  <input
                    value={note}
                    onChange={(e) => setNote(e.target.value)}
                    onKeyDown={(e) => e.key === "Enter" && saveNote()}
                    placeholder="Escreva uma nota..."
                    className="flex-1 bg-[#F0EDE8] border border-[#D4CFC9] rounded-xl px-3 py-2 text-[#1A1410] text-sm placeholder-[#6B5E55] outline-none focus:border-[#CC3300]"
                  />
                  <button
                    onClick={saveNote}
                    disabled={savingNote || !note.trim()}
                    className="bg-[#D4CFC9] hover:bg-[#3D4F5A] text-[#1A1410] px-4 py-2 rounded-xl text-sm font-semibold transition-colors disabled:opacity-40"
                  >
                    Salvar
                  </button>
                </div>
              </div>
            </>
          )}

          {/* ── Tab: Timeline ── */}
          {tab === "timeline" && (
            <>
              {lead.timeline && lead.timeline.length > 0 ? (
                <div className="space-y-2">
                  {[...lead.timeline].reverse().map((ev, i) => (
                    <div key={i} className="flex gap-3 items-start">
                      <div className="w-2 h-2 rounded-full bg-[#CC3300] mt-1.5 shrink-0" />
                      <div className="flex-1">
                        <div className="flex items-baseline gap-2">
                          <span className="text-[#1A1410] text-xs font-semibold capitalize">{ev.action}</span>
                          <span className="text-[#6B5E55] text-[10px]">{timeAgo(ev.at)}</span>
                        </div>
                        {ev.detail && (
                          <p className="text-[#6B5E55] text-xs mt-0.5">{ev.detail}</p>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              ) : (
                <p className="text-center text-[#6B5E55] text-sm py-8">Nenhuma atividade registrada.</p>
              )}
            </>
          )}

        </div>
      </div>
    </div>
  );
}

// ── CAPI helpers ─────────────────────────────────────────────────────────────

function resolveColumnCapiEvent(col: CrmColumn): string | null {
  // 1. Configuração explícita (prioridade máxima)
  const explicit = (col.capi_event ?? "").trim();
  if (explicit === "none") return "none";
  if (["Lead", "Contact", "QualifiedLead", "SubmitApplication", "Schedule", "Purchase"].includes(explicit)) return explicit;

  // 2. Heurística por nome (espelha o backend)
  const nome = (col.name ?? "").toLowerCase();
  const id   = (col.id ?? "").toLowerCase();

  if (["desqual", "descart", "perd", "lost"].some(t => nome.includes(t))) return "none";

  const byId: Record<string, string> = { novo: "Lead", qualif: "Contact", quente: "SubmitApplication", reuniao: "Schedule", fechado: "Purchase" };
  if (byId[id]) return byId[id];

  if (["agend", "reuni", "call", "demo"].some(t => nome.includes(t))) return "Schedule";
  if (["fech", "compr", "vend", "cliente"].some(t => nome.includes(t))) return "Purchase";
  if (["quente", "qualif", "aquec", "reativ", "aguard", "lanca", "lanç"].some(t => nome.includes(t))) return "SubmitApplication";
  if (["contato", "follow", "nutri"].some(t => nome.includes(t))) return "Contact";
  if (["novo", "entrada", "inbound"].some(t => nome.includes(t))) return "Lead";

  return null; // sem cobertura
}

function ColumnCapiBadge({ col }: { col: CrmColumn }) {
  const event = resolveColumnCapiEvent(col);
  if (event === "none") return <span className="text-[9px] text-[#6B5E55] bg-[#E8E4DF] px-1.5 py-0.5 rounded-full">sem CAPI</span>;
  if (event) return <span className="text-[9px] text-[#4267B2] bg-[#F0EDE8] border border-[#4267B2]/30 px-1.5 py-0.5 rounded-full">{event}</span>;
  return <span className="text-[9px] text-[#92610A] bg-[#FEF3C7] border border-[#F0B429]/40 px-1.5 py-0.5 rounded-full" title="Nenhum evento CAPI será disparado ao mover leads para esta etapa. Edite a etapa e configure o Evento CAPI.">⚠ sem CAPI</span>;
}

// ── Modal de nova coluna ──────────────────────────────────────────────────────

const CAPI_EVENT_OPTIONS = [
  { value: "",         label: "Automático (heurística)" },
  { value: "Lead",     label: "Lead" },
  { value: "Contact",  label: "Contact" },
  { value: "QualifiedLead", label: "QualifiedLead (customizado)" },
  { value: "SubmitApplication", label: "SubmitApplication" },
  { value: "Schedule", label: "Schedule" },
  { value: "Purchase", label: "Purchase" },
  { value: "none",     label: "Não disparar CAPI" },
];

function ColumnModal({
  column,
  instanceId,
  onClose,
  onSaved,
}: {
  column?: CrmColumn;
  instanceId?: string;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [name, setName] = useState(column?.name ?? "");
  const [color, setColor] = useState(column?.color ?? "#CC3300");
  const [capiEvent, setCapiEvent] = useState(column?.capi_event ?? "");
  const [saving, setSaving] = useState(false);

  const PRESET_COLORS = ["#CC3300", "#F0B429", "#F97316", "#60A5FA", "#A78BFA", "#F87171", "#6B5E55"];
  const targetInstanceId = column?.instancia_id || instanceId || "";

  const save = async () => {
    if (!name.trim() || !targetInstanceId) return;
    setSaving(true);
    try {
      const capi = capiEvent || undefined;
      if (column) {
        await crmUpdateColumn(targetInstanceId, column.id, name.trim(), color, column.order, capi);
      } else {
        await crmCreateColumn(targetInstanceId, name.trim(), color, capi);
      }
      onSaved();
      onClose();
    } catch {
      alert("Erro ao salvar etapa. Tente novamente.");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div
      className="fixed inset-0 bg-black/60 z-[200] flex items-center justify-center p-4"
      onClick={(e) => e.target === e.currentTarget && onClose()}
    >
      <div className="bg-[#E8E4DF] border border-[#D4CFC9] rounded-2xl w-full max-w-sm p-6">
        <h2 className="text-[#1A1410] font-bold text-base mb-4">{column ? "Editar Etapa" : "Nova Etapa"}</h2>
        <input
          autoFocus
          value={name}
          onChange={(e) => setName(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && save()}
          placeholder="Nome da etapa..."
          className="w-full bg-[#F0EDE8] border border-[#D4CFC9] rounded-xl px-3 py-2.5 text-[#1A1410] text-sm placeholder-[#6B5E55] outline-none focus:border-[#CC3300] mb-4"
        />
        <p className="text-[#6B5E55] text-xs font-semibold uppercase tracking-wide mb-2">Cor</p>
        <div className="flex gap-2 mb-4">
          {PRESET_COLORS.map((c) => (
            <button
              key={c}
              onClick={() => setColor(c)}
              className={`w-7 h-7 rounded-full border-2 transition-all ${color === c ? "border-white scale-110" : "border-transparent"}`}
              style={{ backgroundColor: c }}
            />
          ))}
        </div>
        <p className="text-[#6B5E55] text-xs font-semibold uppercase tracking-wide mb-2">Evento CAPI Meta</p>
        <select
          value={capiEvent ?? ""}
          onChange={(e) => setCapiEvent(e.target.value)}
          className="w-full bg-[#F0EDE8] border border-[#D4CFC9] rounded-xl px-3 py-2.5 text-[#1A1410] text-sm outline-none focus:border-[#CC3300] mb-2"
        >
          {CAPI_EVENT_OPTIONS.map(opt => (
            <option key={opt.value} value={opt.value}>{opt.label}</option>
          ))}
        </select>
        {(() => {
          const preview = resolveColumnCapiEvent({ id: name.toLowerCase().replace(/\s+/g, "_"), name, color, order: 0, capi_event: capiEvent || undefined });
          if (preview === "none") return <p className="text-[#6B5E55] text-[11px] mb-4">Nenhum evento será disparado para esta etapa.</p>;
          if (preview) return <p className="text-[#4267B2] text-[11px] mb-4">Evento que será disparado: <strong>{preview}</strong></p>;
          return <p className="text-[#F0B429] text-[11px] mb-4">⚠ Nenhum evento CAPI reconhecido — configure um evento acima ou renomeie a etapa.</p>;
        })()}
        {!targetInstanceId && (
          <p className="text-[#F87171] text-[11px] mb-4">⚠ Selecione uma instância no filtro do CRM antes de criar ou editar etapas.</p>
        )}
        <div className="flex gap-2">
          <button onClick={onClose} className="flex-1 bg-[#D4CFC9] text-[#1A1410] py-2 rounded-xl text-sm font-semibold">
            Cancelar
          </button>
          <button
            onClick={save}
            disabled={saving || !name.trim() || !targetInstanceId}
            className="flex-1 bg-[#CC3300] hover:bg-[#991F00] text-[#F0EDE8] font-bold py-2 rounded-xl text-sm transition-colors disabled:opacity-40"
          >
            {saving ? "Salvando..." : column ? "Salvar" : "Criar"}
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Modal de novo lead ────────────────────────────────────────────────────────

function NewLeadModal({ columns, instanceId, onClose, onCreated }: { columns: CrmColumn[]; instanceId: string | null; onClose: () => void; onCreated: () => void }) {
  const [nome, setNome]         = useState("");
  const [telefone, setTelefone] = useState("");
  const [email, setEmail]       = useState("");
  const [cidade, setCidade]     = useState("");
  const [estado, setEstado]     = useState("");
  const [cep, setCep]           = useState("");
  const [pais, setPais]         = useState("BR");
  const [coluna, setColuna]     = useState(columns[0]?.id ?? "");
  const [instancia, setInstancia] = useState(instanceId ?? "");
  const [instanceList, setInstanceList] = useState<{ id: string }[]>([]);
  const [saving, setSaving]     = useState(false);

  useEffect(() => {
    getInstancesCached().then((data) => {
      const list = Object.keys(data).map((id) => ({ id }));
      setInstanceList(list);
      setInstancia(current => current || list[0]?.id || "");
    }).catch(() => {});
  }, []);

  const save = async () => {
    if (!nome.trim() || !telefone.trim()) return;
    setSaving(true);
    try {
      await crmCreateLead({
        nome: nome.trim(),
        telefone: telefone.trim(),
        origem: "manual",
        coluna_id: coluna,
        instancia_id: instancia || undefined,
        email: email.trim(),
        cidade: cidade.trim(),
        estado: estado.trim(),
        cep: cep.trim(),
        pais: pais.trim(),
      });
      onCreated();
      onClose();
    } catch {
      alert("Erro ao adicionar lead. Tente novamente.");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div
      className="fixed inset-0 bg-black/60 z-[200] flex items-center justify-center p-4"
      onClick={(e) => e.target === e.currentTarget && onClose()}
    >
      <div className="bg-[#E8E4DF] border border-[#D4CFC9] rounded-2xl w-full max-w-sm p-6">
        <h2 className="text-[#1A1410] font-bold text-base mb-4">Adicionar Lead</h2>
        <div className="space-y-3 mb-5">
          <input
            autoFocus
            value={nome}
            onChange={(e) => setNome(e.target.value)}
            placeholder="Nome do lead"
            className="w-full bg-[#F0EDE8] border border-[#D4CFC9] rounded-xl px-3 py-2.5 text-[#1A1410] text-sm placeholder-[#6B5E55] outline-none focus:border-[#CC3300]"
          />
          <input
            value={telefone}
            onChange={(e) => setTelefone(e.target.value)}
            placeholder="Telefone (ex: 5511999999999)"
            className="w-full bg-[#F0EDE8] border border-[#D4CFC9] rounded-xl px-3 py-2.5 text-[#1A1410] text-sm placeholder-[#6B5E55] outline-none focus:border-[#CC3300]"
          />
          <input
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="Email (opcional)"
            className="w-full bg-[#F0EDE8] border border-[#D4CFC9] rounded-xl px-3 py-2.5 text-[#1A1410] text-sm placeholder-[#6B5E55] outline-none focus:border-[#CC3300]"
          />
          <div className="grid grid-cols-2 gap-2">
            <input
              value={cidade}
              onChange={(e) => setCidade(e.target.value)}
              placeholder="Cidade"
              className="w-full bg-[#F0EDE8] border border-[#D4CFC9] rounded-xl px-3 py-2.5 text-[#1A1410] text-sm placeholder-[#6B5E55] outline-none focus:border-[#CC3300]"
            />
            <input
              value={estado}
              onChange={(e) => setEstado(e.target.value)}
              placeholder="Estado"
              maxLength={32}
              className="w-full bg-[#F0EDE8] border border-[#D4CFC9] rounded-xl px-3 py-2.5 text-[#1A1410] text-sm placeholder-[#6B5E55] outline-none focus:border-[#CC3300]"
            />
          </div>
          <div className="grid grid-cols-2 gap-2">
            <input
              value={cep}
              onChange={(e) => setCep(e.target.value)}
              placeholder="CEP"
              className="w-full bg-[#F0EDE8] border border-[#D4CFC9] rounded-xl px-3 py-2.5 text-[#1A1410] text-sm placeholder-[#6B5E55] outline-none focus:border-[#CC3300]"
            />
            <input
              value={pais}
              onChange={(e) => setPais(e.target.value)}
              placeholder="Pais"
              maxLength={32}
              className="w-full bg-[#F0EDE8] border border-[#D4CFC9] rounded-xl px-3 py-2.5 text-[#1A1410] text-sm placeholder-[#6B5E55] outline-none focus:border-[#CC3300]"
            />
          </div>
          <select
            value={instancia}
            onChange={(e) => setInstancia(e.target.value)}
            className="w-full bg-[#F0EDE8] border border-[#D4CFC9] rounded-xl px-3 py-2.5 text-[#1A1410] text-sm outline-none focus:border-[#CC3300]"
          >
            <option value="">— Instância —</option>
            {instanceList.map((inst) => (
              <option key={inst.id} value={inst.id}>{inst.id}</option>
            ))}
          </select>
          <select
            value={coluna}
            onChange={(e) => setColuna(e.target.value)}
            className="w-full bg-[#F0EDE8] border border-[#D4CFC9] rounded-xl px-3 py-2.5 text-[#1A1410] text-sm outline-none focus:border-[#CC3300]"
          >
            {columns.map((c) => (
              <option key={c.id} value={c.id}>{c.name}</option>
            ))}
          </select>
        </div>
        <div className="flex gap-2">
          <button onClick={onClose} className="flex-1 bg-[#D4CFC9] text-[#1A1410] py-2 rounded-xl text-sm font-semibold">
            Cancelar
          </button>
          <button
            onClick={save}
            disabled={saving || !nome.trim() || !telefone.trim()}
            className="flex-1 bg-[#CC3300] hover:bg-[#991F00] text-[#F0EDE8] font-bold py-2 rounded-xl text-sm transition-colors disabled:opacity-40"
          >
            {saving ? "Salvando..." : "Adicionar"}
          </button>
        </div>
      </div>
    </div>
  );
}

// ── AddToListaPopover ─────────────────────────────────────────────────────────

function AddToListaPopover({
  listas,
  onConfirm,
}: {
  listas: CrmLista[];
  onConfirm: (listaId: string) => Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  const [listaId, setListaId] = useState(listas[0]?.id ?? "");
  const [saving, setSaving] = useState(false);
  const [done, setDone] = useState(false);

  const confirm = async () => {
    if (!listaId) return;
    setSaving(true);
    try {
      await onConfirm(listaId);
      setDone(true);
      setOpen(false);
      setTimeout(() => setDone(false), 2000);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="relative">
      <button
        onClick={() => setOpen((v) => !v)}
        className={`text-[11px] font-semibold px-2.5 py-1 rounded-lg border transition-colors ${
          done
            ? "border-[#CC3300] text-[#CC3300] bg-[#3D0A00]"
            : "border-[#D4CFC9] text-[#3B82F6] hover:border-[#3B82F6] hover:bg-[#0D2040]"
        }`}
      >
        {done ? "✓ Salvo" : "+ Lista"}
      </button>

      {open && (
        <div className="absolute z-30 right-0 top-8 bg-[#E8E4DF] border border-[#D4CFC9] rounded-2xl shadow-2xl p-4 w-64">
          <p className="text-[#1A1410] text-xs font-bold mb-3">Enviar para lista de espera</p>

          <div className="mb-3">
            <label className="text-[#6B5E55] text-[10px] block mb-1">Qual lista?</label>
            <select
              value={listaId}
              onChange={(e) => setListaId(e.target.value)}
              className="w-full bg-[#F0EDE8] border border-[#D4CFC9] rounded-xl px-3 py-2 text-[#1A1410] text-xs outline-none focus:border-[#3B82F6]"
            >
              {listas.map((l) => (
                <option key={l.id} value={l.id}>{l.name}</option>
              ))}
            </select>
          </div>

          <p className="text-[#6B5E55] text-[10px] mb-4">O lead sairá das colunas do Kanban automaticamente.</p>

          <div className="flex gap-2">
            <button
              onClick={() => setOpen(false)}
              className="flex-1 text-[#6B5E55] text-xs py-1.5 rounded-xl bg-[#F0EDE8] border border-[#D4CFC9] hover:text-[#1A1410] transition-colors"
            >
              Cancelar
            </button>
            <button
              onClick={confirm}
              disabled={saving || !listaId}
              className="flex-1 bg-[#3B82F6] hover:bg-[#2563EB] disabled:opacity-40 text-white text-xs font-bold py-1.5 rounded-xl transition-colors"
            >
              {saving ? "..." : "Confirmar"}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

// ── Página principal CRM ──────────────────────────────────────────────────────

type ViewMode = "kanban" | "list" | "listas";

function CrmPageInner() {
  const searchParams  = useSearchParams();
  const instanceId    = searchParams.get("instancia_id") || "";

  const [columns, setColumns]   = useState<CrmColumn[]>([]);
  const [leads, setLeads]       = useState<CrmLead[]>([]);
  const [stats, setStats]       = useState<CrmStats | null>(null);
  const [view, setView]         = useState<ViewMode>("kanban");
  const [lastInbox, setLastInbox] = useState<string | null>(null);
  const [selectedLead, setSelectedLead] = useState<CrmLead | null>(null);
  const [showNewCol, setShowNewCol]     = useState(false);
  const [editingColumn, setEditingColumn] = useState<CrmColumn | null>(null);
  const [backfilling, setBackfilling]   = useState(false);
  const [backfillResult, setBackfillResult] = useState<string | null>(null);
  const [showNewLead, setShowNewLead]   = useState(false);
  // Filtros sem UI no momento — valores fixos em "" mantêm a lógica de
  // filtragem abaixo inerte até a UI de filtros voltar.
  const [search]       = useState("");
  const [filterOrigem] = useState("");
  const [filterTag]    = useState("");
  const [loading, setLoading]           = useState(true);
  const [loadError, setLoadError]       = useState("");
  const [exporting, setExporting]       = useState(false);
  const [showSaveLista, setShowSaveLista] = useState(false);
  const [savingLista, setSavingLista]     = useState(false);
  const [listaName, setListaName]         = useState("");
  const [listas, setListas]               = useState<CrmLista[]>([]);
  const [selectedListaId, setSelectedListaId] = useState("");
  const [listaLeads, setListaLeads]           = useState<CrmLead[]>([]);
  const [loadingListaLeads, setLoadingListaLeads] = useState(false);

  const dragLeadId = useRef<string | null>(null);

  const load = async () => {
    try {
      const [cols, ls, st, lsts] = await Promise.all([crmGetColumns(instanceId || undefined), crmGetLeads(undefined, undefined, undefined, instanceId || undefined), crmGetStats(instanceId || undefined), crmGetListas()]);
      setListas(lsts);
      setColumns(cols);
      setLeads(ls);
      setStats(st);
      setLoadError("");
    } catch (e) {
      // Sem catch, um 403 (usuário sem instância liberada) deixava a tela em
      // "Carregando..." pra sempre — mostrar o motivo em vez de mascarar.
      setLoadError(e instanceof Error && e.message.includes("403")
        ? "Você ainda não tem acesso a nenhuma instância. Peça ao administrador para liberar uma instância para o seu usuário."
        : "Não foi possível carregar o CRM. Tente novamente ou contate o administrador.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
    fetch("/api/proxy/auth/me")
      .then(r => r.ok ? r.json() : null)
      .then(data => { if (data?.last_instance_id) setLastInbox(data.last_instance_id); });
    const t = setInterval(load, 30000);
    return () => clearInterval(t);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [instanceId]);

  const loadListaLeads = async (listaId: string) => {
    if (!listaId) {
      setListaLeads([]);
      return;
    }
    setLoadingListaLeads(true);
    try {
      const res = await crmGetListaLeads(listaId);
      setListaLeads(Array.isArray(res?.leads) ? res.leads : []);
    } catch {
      setListaLeads([]);
    } finally {
      setLoadingListaLeads(false);
    }
  };

  useEffect(() => {
    if (!selectedListaId) return;
    loadListaLeads(selectedListaId);
  }, [selectedListaId]);

  const refresh = async () => {
    try {
      const [cols, ls, st, lsts] = await Promise.all([crmGetColumns(instanceId || undefined), crmGetLeads(undefined, undefined, undefined, instanceId || undefined), crmGetStats(instanceId || undefined), crmGetListas()]);
      setColumns(cols);
      setLeads(ls);
      setStats(st);
      setListas(lsts);
      if (selectedLead) {
        const updated = ls.find((l) => l.id === selectedLead.id);
        if (updated) setSelectedLead(updated);
      }
      if (selectedListaId) {
        await loadListaLeads(selectedListaId);
      }
    } catch {
      // refresh pós-ação: falha transitória não pode derrubar a tela já carregada
    }
  };

  const handleExport = async () => {
    setExporting(true);
    try {
      await crmExportCsv(
        undefined,
        filterOrigem || undefined,
        search || undefined,
      );
    } finally {
      setExporting(false);
    }
  };



  const handleSaveLista = async () => {
    if (!listaName.trim()) return;
    setSavingLista(true);
    try {
      const filtros: CrmLista["filtros"] = {};
      if (filterOrigem) filtros.origem = filterOrigem;
      const nova = await crmCreateLista(listaName.trim(), filtros);
      setListas((prev: CrmLista[]) => [...prev, nova]);
      setSelectedListaId(nova.id);
      setListaName("");
      setShowSaveLista(false);
    } finally {
      setSavingLista(false);
    }
  };

  const handleDeleteLista = async (lista: CrmLista) => {
    if (!confirm(`Remover a lista "${lista.name}"?`)) return;
    try {
      await crmDeleteLista(lista.id);
      setListas((prev) => prev.filter((item) => item.id !== lista.id));
      if (selectedListaId === lista.id) {
        setSelectedListaId("");
        setListaLeads([]);
      }
    } catch {
      alert("Erro ao remover lista. Tente novamente.");
    }
  };

  const handleSetListaFollowup = async (lista: CrmLista, date: string) => {
    try {
      const updated = await crmSetListaFollowup(lista.id, date);
      setListas((prev) => prev.map((item) => (item.id === lista.id ? updated : item)));
    } catch {
      alert("Erro ao definir follow-up da lista. Tente novamente.");
    }
  };

  const handleRemoveLeadFromLista = async (lista: CrmLista, lead: CrmLead) => {
    try {
      await crmRemoveLeadFromLista(lista.id, lead.id);
      setListas((prev) => prev.map((item) =>
        item.id === lista.id
          ? { ...item, lead_ids: (item.lead_ids || []).filter((id) => id !== lead.id) }
          : item
      ));
      await loadListaLeads(lista.id);
    } catch {
      alert("Erro ao remover lead da lista. Tente novamente.");
    }
  };

  const moveColumn = async (column: CrmColumn, direction: -1 | 1) => {
    const ordered = [...columns].sort((a, b) => a.order - b.order);
    const index = ordered.findIndex((c) => c.id === column.id);
    const targetIndex = index + direction;
    if (index < 0 || targetIndex < 0 || targetIndex >= ordered.length) return;

    const target = ordered[targetIndex];
    const prevColumns = columns;
    const next = [...ordered];
    next[index] = { ...target, order: column.order };
    next[targetIndex] = { ...column, order: target.order };
    setColumns([...next].sort((a, b) => a.order - b.order));

    try {
      await Promise.all([
        crmUpdateColumn(column.instancia_id || instanceId || lastInbox || "", column.id, column.name, column.color, target.order, column.capi_event ?? undefined),
        crmUpdateColumn(target.instancia_id || instanceId || lastInbox || "", target.id, target.name, target.color, column.order, target.capi_event ?? undefined),
      ]);
      await refresh();
    } catch {
      setColumns(prevColumns);
      alert("Erro ao reordenar etapa. Tente novamente.");
    }
  };

  const deleteColumn = async (column: CrmColumn) => {
    const colLeads = leads.filter((lead) => lead.coluna_id === column.id).length;
    const message = colLeads > 0
      ? `Remover a etapa "${column.name}"? Os ${colLeads} lead(s) serao movidos para a primeira etapa disponivel.`
      : `Remover a etapa "${column.name}"?`;
    if (!confirm(message)) return;
    try {
      await crmDeleteColumn(column.instancia_id || instanceId || lastInbox || "", column.id);
      await refresh();
    } catch {
      alert("Erro ao remover etapa. Tente novamente.");
    }
  };

  // ── Drag & drop handlers ──

  const onDragStart = (e: React.DragEvent, leadId: string) => {
    dragLeadId.current = leadId;
    e.dataTransfer.effectAllowed = "move";
  };

  const onDragOver = (e: React.DragEvent) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
  };

  const onDrop = async (e: React.DragEvent, colId: string) => {
    e.preventDefault();
    const id = dragLeadId.current;
    if (!id) return;
    dragLeadId.current = null;
    const lead = leads.find((l) => l.id === id);
    if (!lead || lead.coluna_id === colId) return;
    const prevColId = lead.coluna_id;
    setLeads((prev) => prev.map((l) => l.id === id ? { ...l, coluna_id: colId } : l));
    try {
      await crmMoveLead(id, colId);
    } catch {
      setLeads((prev) => prev.map((l) => l.id === id ? { ...l, coluna_id: prevColId } : l));
      alert("Erro ao mover lead. Tente novamente.");
    }
  };

  // ── Filtered leads ──

  const selectedLista = listas.find((lista) => lista.id === selectedListaId) ?? null;

  const filteredLeads = leads.filter((l) => {
    if (filterOrigem && l.origem !== filterOrigem) return false;
    if (filterTag && !(l.tags ?? []).includes(filterTag)) return false;
    if (search) {
      const q = search.toLowerCase();
      if (!l.nome.toLowerCase().includes(q) && !l.telefone.includes(q) && !(l.empresa ?? "").toLowerCase().includes(q)) return false;
    }
    return true;
  });

  const leadsFor = (colId: string) => filteredLeads.filter((l) => l.coluna_id === colId);

  return (
    <div className="min-h-screen bg-[#F0EDE8] flex flex-col">

      {/* Header */}
      <PageHeaderShell maxWidthClassName="max-w-full" className="pt-4 shrink-0">
        <div className="relative mx-auto flex min-h-[48px] max-w-full items-center justify-end gap-4">
          <div className="ml-auto flex items-center gap-2 flex-wrap justify-end w-full sm:w-auto">
            {instanceId && (
              <div className="flex items-center gap-2 mr-auto">
                <Link
                  href="/"
                  className="text-[#6B5E55] hover:text-[#1A1410] text-sm transition-colors"
                >
                  ← Início
                </Link>
                <span className="text-[#6B5E55]">/</span>
                <span className="text-[#1A1410] font-bold text-sm">{instanceId}</span>
                <span className="bg-[#0E0E18] text-[#60A5FA] text-xs font-semibold px-2 py-0.5 rounded-full">CRM</span>
              </div>
            )}
            {lastInbox && !instanceId && (
              <Link
                href={`/instances/${lastInbox}/inbox`}
                className="bg-[#CC3300] hover:bg-[#991F00] text-[#F0EDE8] font-bold px-4 py-1.5 rounded-xl text-sm transition-colors whitespace-nowrap"
              >
                Abrir Inbox SDR
              </Link>
            )}
            {instanceId && (
              <Link
                href={`/instances/${instanceId}/inbox`}
                className="bg-[#CC3300] hover:bg-[#991F00] text-[#F0EDE8] font-bold px-4 py-1.5 rounded-xl text-sm transition-colors whitespace-nowrap"
              >
                Inbox SDR
              </Link>
            )}
            <div className="flex bg-[#E8E4DF] border border-[#D4CFC9] rounded-xl overflow-hidden">
              <button
                onClick={() => setView("kanban")}
                className={`px-3 py-1.5 text-sm font-semibold transition-colors ${view === "kanban" ? "bg-[#D4CFC9] text-[#1A1410]" : "text-[#6B5E55] hover:text-[#1A1410]"}`}
              >
                Kanban
              </button>
              <button
                onClick={() => setView("list")}
                className={`px-3 py-1.5 text-sm font-semibold transition-colors ${view === "list" ? "bg-[#D4CFC9] text-[#1A1410]" : "text-[#6B5E55] hover:text-[#1A1410]"}`}
              >
                Lista
              </button>
              <button
                onClick={() => setView("listas")}
                className={`px-3 py-1.5 text-sm font-semibold transition-colors ${view === "listas" ? "bg-[#D4CFC9] text-[#1A1410]" : "text-[#6B5E55] hover:text-[#1A1410]"}`}
              >
                Listas
              </button>
            </div>
            <button
              onClick={handleExport}
              disabled={exporting}
              className="bg-[#E8E4DF] hover:bg-[#D4CFC9] border border-[#D4CFC9] text-[#6B5E55] hover:text-[#1A1410] font-semibold px-3 py-1.5 rounded-xl transition-colors text-sm disabled:opacity-40"
            >
              {exporting ? "..." : "CSV"}
            </button>
            <button
              onClick={() => setShowSaveLista(true)}
              className="bg-[#E8E4DF] hover:bg-[#D4CFC9] border border-[#D4CFC9] text-[#3B82F6] hover:text-[#60A5FA] font-semibold px-3 py-1.5 rounded-xl transition-colors text-sm"
              title="Salvar leads filtrados como lista para disparo"
            >
              + Lista
            </button>
            <button
              onClick={() => setShowNewLead(true)}
              className="bg-[#CC3300] hover:bg-[#991F00] text-[#F0EDE8] font-bold px-4 py-1.5 rounded-xl transition-colors text-sm"
            >
              + Lead
            </button>
            <button
              onClick={() => setShowNewCol(true)}
              className="bg-[#E8E4DF] hover:bg-[#D4CFC9] border border-[#D4CFC9] text-[#6B5E55] hover:text-[#1A1410] font-semibold px-3 py-1.5 rounded-xl transition-colors text-sm"
            >
              + Etapa
            </button>
            <button
              onClick={async () => {
                setBackfilling(true);
                setBackfillResult(null);
                try {
                  const r = await crmBackfillLeadgen();
                  setBackfillResult(`${r.updated} leads atualizados de ${r.total}`);
                } catch {
                  setBackfillResult("Erro ao executar backfill");
                } finally {
                  setBackfilling(false);
                }
              }}
              disabled={backfilling}
              title="Preencher leadgen_id em leads antigos (cruzamento por telefone)"
              className="bg-[#E8E4DF] hover:bg-[#D4CFC9] border border-[#D4CFC9] text-[#6B5E55] hover:text-[#60A5FA] font-semibold px-3 py-1.5 rounded-xl transition-colors text-sm disabled:opacity-40"
            >
              {backfilling ? "..." : "Backfill IDs"}
            </button>
            {backfillResult && (
              <span className="text-[11px] text-[#60A5FA]">{backfillResult}</span>
            )}
          </div>
        </div>
      </PageHeaderShell>

      {/* Stats bar */}
      {!loading && stats && stats.total > 0 && (
        <div className="bg-[#E8E4DF] border-b border-[#D4CFC9] px-6 py-3 shrink-0">
          <div className="flex items-center gap-6 flex-wrap">
            <div className="flex items-center gap-1.5">
              <span className="text-[#6B5E55] text-xs">Total</span>
              <span className="text-[#1A1410] font-bold text-sm tabular-nums">{stats.total}</span>
            </div>
            {stats.avg_score !== null && (
              <div className="flex items-center gap-1.5">
                <span className="text-[#6B5E55] text-xs">Score medio</span>
                <span className={`font-bold text-sm tabular-nums ${stats.avg_score >= 70 ? "text-[#CC3300]" : stats.avg_score >= 40 ? "text-[#F0B429]" : "text-[#F87171]"}`}>{stats.avg_score}</span>
              </div>
            )}
            <div className="flex items-center gap-1.5">
              <span className="text-[#6B5E55] text-xs">Conversao</span>
              <span className="text-[#60A5FA] font-bold text-sm tabular-nums">{stats.conversion_pct}%</span>
            </div>
            <div className="h-4 w-px bg-[#D4CFC9]" />
            {stats.by_col.map((c) => (
              <div key={c.name} className="flex items-center gap-1.5">
                <span className="w-2 h-2 rounded-full shrink-0" style={{ backgroundColor: c.color }} />
                <span className="text-[#6B5E55] text-xs">{c.name}</span>
                <span className="text-[#1A1410] font-semibold text-xs tabular-nums">{c.count}</span>
              </div>
            ))}
            <div className="h-4 w-px bg-[#D4CFC9]" />
            {Object.entries(stats.by_origem).map(([orig, count]) => {
              const o = ORIGEM_STYLE[orig] ?? ORIGEM_STYLE.manual;
              return (
                <span key={orig} className={`text-[10px] font-semibold px-2 py-0.5 rounded-full flex items-center gap-1 ${o.bg} ${o.text}`}>
                  <span>{o.icon}</span>{o.label} {count}
                </span>
              );
            })}
          </div>
        </div>
      )}

      {/* Body */}
      {loading ? (
        <div className="flex-1 flex items-center justify-center text-[#6B5E55] text-sm">Carregando...</div>
      ) : loadError ? (
        <div className="flex-1 flex items-center justify-center px-6">
          <div className="max-w-md text-center bg-[#E8E4DF] border border-[#D4CFC9] rounded-2xl p-6">
            <p className="text-[#1A1410] text-sm font-semibold mb-1">Sem acesso ao CRM</p>
            <p className="text-[#6B5E55] text-sm">{loadError}</p>
          </div>
        </div>
      ) : view === "kanban" ? (
        <div className="flex-1 overflow-x-auto px-4 py-5">
          <div className="flex items-start gap-4" style={{ minWidth: `${columns.length * 280 + 60}px` }}>
            {columns.map((col, colIndex) => {
              const colLeads = leadsFor(col.id);
              return (
                <div
                  key={col.id}
                  onDragOver={onDragOver}
                  onDrop={(e) => onDrop(e, col.id)}
                  className="flex w-64 shrink-0 flex-col self-start"
                >
                  <div className="flex items-center gap-2 mb-1 px-1">
                    <div className="w-2.5 h-2.5 rounded-full shrink-0" style={{ backgroundColor: col.color }} />
                    <span className="font-semibold text-[#1A1410] text-sm truncate flex-1">{col.name}</span>
                    <span className="text-[#6B5E55] text-xs bg-[#E8E4DF] px-2 py-0.5 rounded-full shrink-0">
                      {colLeads.length}
                    </span>
                    <div className="flex items-center gap-1 shrink-0">
                      <button
                        onClick={() => moveColumn(col, -1)}
                        disabled={colIndex === 0}
                        className="w-6 h-6 rounded-md bg-[#E8E4DF] text-[#6B5E55] hover:text-[#1A1410] disabled:opacity-30 transition-colors text-xs"
                        title="Mover etapa para a esquerda"
                      >
                        {"<"}
                      </button>
                      <button
                        onClick={() => moveColumn(col, 1)}
                        disabled={colIndex === columns.length - 1}
                        className="w-6 h-6 rounded-md bg-[#E8E4DF] text-[#6B5E55] hover:text-[#1A1410] disabled:opacity-30 transition-colors text-xs"
                        title="Mover etapa para a direita"
                      >
                        {">"}
                      </button>
                      <button
                        onClick={() => setEditingColumn(col)}
                        className="w-6 h-6 rounded-md bg-[#E8E4DF] text-[#60A5FA] hover:bg-[#D4CFC9] transition-colors text-xs"
                        title="Editar etapa"
                      >
                        E
                      </button>
                      <button
                        onClick={() => deleteColumn(col)}
                        disabled={columns.length <= 1}
                        className="w-6 h-6 rounded-md bg-[#E8E4DF] text-[#F87171] hover:bg-[#D4CFC9] disabled:opacity-30 transition-colors text-xs"
                        title="Remover etapa"
                      >
                        X
                      </button>
                    </div>
                  </div>
                  <div className="px-1 mb-2">
                    <ColumnCapiBadge col={col} />
                  </div>
                  <div
                    className="bg-[#1A2730] rounded-xl p-2 min-h-32 border-2 border-transparent transition-colors"
                    onDragOver={(e) => { e.preventDefault(); e.currentTarget.classList.add("border-[#D4CFC9]"); }}
                    onDragLeave={(e) => e.currentTarget.classList.remove("border-[#D4CFC9]")}
                    onDrop={(e) => { e.currentTarget.classList.remove("border-[#D4CFC9]"); onDrop(e, col.id); }}
                  >
                    {colLeads.length === 0 ? (
                      <div className="h-16 flex items-center justify-center">
                        <p className="text-[#D4CFC9] text-xs">Arraste leads aqui</p>
                      </div>
                    ) : (
                      colLeads.map((lead) => (
                        <LeadCard
                          key={lead.id}
                          lead={lead}
                          onDragStart={onDragStart}
                          onClick={() => setSelectedLead(lead)}
                          listas={listas}
                          onConfirmLista={async (listaId) => {
                            await crmAddLeadToLista(listaId, lead.id);
                            await crmMoveLead(lead.id, null);
                            await refresh();
                          }}
                          onRefresh={refresh}
                        />
                      ))
                    )}
                  </div>
                </div>
              );
            })}
            <div className="w-64 shrink-0">
              <button
                onClick={() => setShowNewCol(true)}
                className="w-full h-12 border-2 border-dashed border-[#D4CFC9] rounded-xl text-[#6B5E55] hover:border-[#3D4F5A] hover:text-[#1A1410] transition-colors text-sm font-semibold"
              >
                + Nova etapa
              </button>
            </div>
          </div>
        </div>
      ) : view === "list" ? (
        <div className="flex-1 overflow-y-auto px-6 py-5">
          <div className="max-w-4xl mx-auto">
            {filteredLeads.length === 0 ? (
              <div className="text-center py-24 text-[#6B5E55] text-sm">Nenhum lead encontrado.</div>
            ) : (
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-[#6B5E55] text-xs uppercase tracking-wide border-b border-[#D4CFC9]">
                    <th className="text-left py-3 px-2 font-semibold">Nome</th>
                    <th className="text-left py-3 px-2 font-semibold">Telefone</th>
                    <th className="text-left py-3 px-2 font-semibold">Empresa</th>
                    <th className="text-left py-3 px-2 font-semibold">Origem</th>
                    <th className="text-left py-3 px-2 font-semibold">Responsavel</th>
                    <th className="text-left py-3 px-2 font-semibold">Etapa</th>
                    <th className="text-left py-3 px-2 font-semibold">Score</th>
                    <th className="text-left py-3 px-2 font-semibold">Follow-up</th>
                    <th className="text-left py-3 px-2 font-semibold">Atualizado</th>
                    {listas.length > 0 && <th className="text-left py-3 px-2 font-semibold">Lista</th>}
                  </tr>
                </thead>
                <tbody>
                  {filteredLeads.map((lead) => {
                    const col = columns.find((c) => c.id === lead.coluna_id);
                    const o = origemBadge(lead.origem);
                    const fuStatus = followupStatus(lead.followup_date);
                    return (
                      <tr
                        key={lead.id}
                        onClick={() => setSelectedLead(lead)}
                        className="border-b border-[#1A2730] hover:bg-[#E8E4DF] cursor-pointer transition-colors"
                      >
                        <td className="py-3 px-2 text-[#1A1410] font-semibold">{lead.nome}</td>
                        <td className="py-3 px-2 text-[#6B5E55]">{lead.telefone}</td>
                        <td className="py-3 px-2 text-[#6B5E55]">{lead.empresa || "—"}</td>
                        <td className="py-3 px-2">
                          <span className={`text-[10px] font-semibold px-2 py-0.5 rounded-full flex items-center gap-1 ${o.bg} ${o.text}`}><span>{o.icon}</span>{o.label}</span>
                        </td>
                        <td className="py-3 px-2 text-[#6B5E55] text-xs">
                          {lead.assigned_to_name || "Sem responsavel"}
                        </td>
                        <td className="py-3 px-2">
                          {col && (
                            <span className="text-xs" style={{ color: col.color }}>{col.name}</span>
                          )}
                        </td>
                        <td className={`py-3 px-2 font-bold tabular-nums ${scoreColor(lead.score)}`}>
                          {lead.score ?? "—"}
                        </td>
                        <td className="py-3 px-2">
                          {fuStatus === "vencido" && (
                            <span className="flex items-center gap-1 text-[10px] font-bold text-[#F87171]"><FollowupDot status="vencido" />atrasado</span>
                          )}
                          {fuStatus === "hoje" && (
                            <span className="flex items-center gap-1 text-[10px] font-bold text-[#F0B429]"><FollowupDot status="hoje" />hoje</span>
                          )}
                          {fuStatus === "proximo" && (
                            <span className="flex items-center gap-1 text-[10px] font-bold text-[#60A5FA]"><FollowupDot status="proximo" />próximo</span>
                          )}
                          {!fuStatus && lead.followup_date && (
                            <span className="text-[10px] text-[#6B5E55]">{lead.followup_date}</span>
                          )}
                          {!lead.followup_date && <span className="text-[#D4CFC9]">—</span>}
                        </td>
                        <td className="py-3 px-2 text-[#6B5E55] text-xs">{timeAgo(lead.updated_at)}</td>
                        {listas.length > 0 && (
                          <td className="py-3 px-2" onClick={(e) => e.stopPropagation()}>
                            <AddToListaPopover
                              listas={listas}
                              onConfirm={async (listaId) => {
                                await crmAddLeadToLista(listaId, lead.id);
                                await crmMoveLead(lead.id, null);
                                await refresh();
                              }}
                            />
                          </td>
                        )}
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            )}
          </div>
        </div>
      ) : (
        <div className="flex-1 overflow-hidden px-6 py-5">
          <div className="grid h-full gap-5 lg:grid-cols-[320px_minmax(0,1fr)]">
            <div className="overflow-y-auto rounded-2xl border border-[#D4CFC9] bg-[#E8E4DF] p-4">
              <div className="mb-4 flex items-center justify-between gap-3">
                <div>
                  <h2 className="text-lg font-bold text-[#1A1410]">Listas de espera</h2>
                  <p className="text-xs text-[#6B5E55]">Agrupe leads sem esticar o funil principal.</p>
                </div>
                <span className="rounded-full bg-[#F0EDE8] px-2 py-1 text-xs font-semibold text-[#6B5E55]">
                  {listas.length} listas
                </span>
              </div>
              {listas.length === 0 ? (
                <div className="rounded-xl border border-dashed border-[#D4CFC9] px-4 py-10 text-center text-sm text-[#6B5E55]">
                  Nenhuma lista criada ainda. Use o botao <span className="font-semibold text-[#1A1410]">+ Lista</span> para salvar seus grupos de espera.
                </div>
              ) : (
                <div className="space-y-3">
                  {listas.map((lista) => {
                    const active = lista.id === selectedListaId;
                    const filtroOrigem = lista.filtros?.origem ? origemBadge(lista.filtros.origem).label : "Sem filtro";
                    const listaFu = followupStatus(lista.followup_date);
                    return (
                      <button
                        key={lista.id}
                        onClick={() => setSelectedListaId(lista.id)}
                        className={`relative w-full rounded-xl border p-3 text-left transition-colors ${
                          listaFu === "vencido" || listaFu === "hoje" || listaFu === "proximo" ? "followup-glow" : ""
                        } ${
                          active ? "border-[#3B82F6] bg-[#F0EDE8]" : "border-[#D4CFC9] bg-[#F0EDE8]/60 hover:border-[#3D4F5A]"
                        }`}
                        style={{
                          ["--glow-color" as string]:
                            listaFu === "vencido" ? "#F87171" : listaFu === "hoje" ? "#F0B429" : listaFu === "proximo" ? "#60A5FA" : undefined,
                        }}
                      >
                        <FollowupBeacon date={lista.followup_date} />
                        <div className="mb-2 flex items-start justify-between gap-3">
                          <div>
                            <p className="font-semibold text-[#1A1410]">{lista.name}</p>
                            <p className="text-[11px] text-[#6B5E55]">Criada em {lista.created_at?.slice(0, 10) ?? "—"}</p>
                          </div>
                          <span className="rounded-full bg-[#E8E4DF] px-2 py-1 text-[10px] font-semibold text-[#6B5E55]">
                            {(lista.lead_ids || []).length} manuais
                          </span>
                        </div>
                        <div className="flex flex-wrap gap-2 text-[10px]">
                          <span className="rounded-full bg-[#E8E4DF] px-2 py-1 text-[#CC3300]">Origem: {filtroOrigem}</span>
                          {lista.filtros?.coluna_id && <span className="rounded-full bg-[#E8E4DF] px-2 py-1 text-[#6B5E55]">Etapa filtrada</span>}
                          {lista.filtros?.tags?.length ? <span className="rounded-full bg-[#E8E4DF] px-2 py-1 text-[#6B5E55]">{lista.filtros.tags.length} tags</span> : null}
                          {listaFu === "vencido" && (
                            <span className="flex items-center gap-1 rounded-full bg-[#2D0A0A] px-2 py-1 font-bold text-[#F87171]"><FollowupDot status="vencido" />atrasado</span>
                          )}
                          {listaFu === "hoje" && (
                            <span className="flex items-center gap-1 rounded-full bg-[#2D2000] px-2 py-1 font-bold text-[#F0B429]"><FollowupDot status="hoje" />hoje</span>
                          )}
                          {listaFu === "proximo" && (
                            <span className="flex items-center gap-1 rounded-full bg-[#0A1A2D] px-2 py-1 font-bold text-[#60A5FA]"><FollowupDot status="proximo" />próximo</span>
                          )}
                        </div>
                      </button>
                    );
                  })}
                </div>
              )}
            </div>

            <div className="overflow-y-auto rounded-2xl border border-[#D4CFC9] bg-[#E8E4DF] p-4">
              {!selectedLista ? (
                <div className="flex h-full items-center justify-center text-sm text-[#6B5E55]">
                  Selecione uma lista para ver os cards completos dos leads.
                </div>
              ) : (
                <>
                  <div className="mb-4 flex items-start justify-between gap-4">
                    <div>
                      <h3 className="text-xl font-bold text-[#1A1410]">{selectedLista.name}</h3>
                      <p className="text-sm text-[#6B5E55]">
                        Cards completos da lista, prontos para triagem, acompanhamento e envio futuro.
                      </p>
                    </div>
                    <div className="flex items-center gap-2">
                      <span className="rounded-full bg-[#F0EDE8] px-3 py-1 text-xs font-semibold text-[#6B5E55]">
                        {loadingListaLeads ? "..." : `${listaLeads.length} leads`}
                      </span>
                      <button
                        onClick={() => handleDeleteLista(selectedLista)}
                        className="rounded-xl border border-[#3B1B1B] bg-[#201112] px-3 py-1.5 text-xs font-semibold text-[#F87171] transition-colors hover:bg-[#2A1517]"
                      >
                        Apagar lista
                      </button>
                    </div>
                  </div>

                  <div className="mb-4 flex items-center gap-3 rounded-xl border border-[#D4CFC9] bg-[#F0EDE8]/60 px-3 py-2.5">
                    <label className="text-xs font-semibold text-[#6B5E55] whitespace-nowrap">Follow-up desta lista</label>
                    <input
                      type="date"
                      value={selectedLista.followup_date || ""}
                      onChange={(e) => handleSetListaFollowup(selectedLista, e.target.value)}
                      className="rounded-lg border border-[#D4CFC9] bg-[#F0EDE8] px-2 py-1 text-xs text-[#1A1410] focus:outline-none focus:border-[#CC3300]"
                    />
                    {(() => {
                      const fu = followupStatus(selectedLista.followup_date);
                      if (fu === "vencido") return <span className="flex items-center gap-1 rounded-full bg-[#2D0A0A] px-2 py-1 text-[10px] font-bold text-[#F87171]"><FollowupDot status="vencido" />atrasado</span>;
                      if (fu === "hoje") return <span className="flex items-center gap-1 rounded-full bg-[#2D2000] px-2 py-1 text-[10px] font-bold text-[#F0B429]"><FollowupDot status="hoje" />hoje</span>;
                      if (fu === "proximo") return <span className="flex items-center gap-1 rounded-full bg-[#0A1A2D] px-2 py-1 text-[10px] font-bold text-[#60A5FA]"><FollowupDot status="proximo" />próximo</span>;
                      return null;
                    })()}
                  </div>

                  {loadingListaLeads ? (
                    <div className="py-16 text-center text-sm text-[#6B5E55]">Carregando cards da lista...</div>
                  ) : listaLeads.length === 0 ? (
                    <div className="rounded-xl border border-dashed border-[#D4CFC9] px-4 py-10 text-center text-sm text-[#6B5E55]">
                      Essa lista ainda nao tem leads completos. Adicione cards pelo funil ou salve uma lista com filtros.
                    </div>
                  ) : (
                    <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
                      {listaLeads.map((lead) => {
                        const badge = origemBadge(lead.origem);
                        const col = columns.find((item) => item.id === lead.coluna_id);
                        const listFuStatus = followupStatus(lead.followup_date);
                        return (
                          <div
                            key={lead.id}
                            className={`relative rounded-xl border bg-[#F0EDE8] p-3 transition-colors hover:border-[#3D4F5A] ${
                              listFuStatus === "vencido" ? "border-[#F87171] followup-glow" :
                              listFuStatus === "hoje"    ? "border-[#F0B429] followup-glow" :
                              listFuStatus === "proximo" ? "border-[#60A5FA] followup-glow" :
                                                           "border-[#D4CFC9]"
                            }`}
                            style={{
                              ["--glow-color" as string]:
                                listFuStatus === "vencido" ? "#F87171" : listFuStatus === "hoje" ? "#F0B429" : listFuStatus === "proximo" ? "#60A5FA" : undefined,
                            }}
                          >
                            <FollowupBeacon date={lead.followup_date} />
                            {/* Nome + ações */}
                            <div className="flex items-start justify-between gap-2 mb-2">
                              <div className="min-w-0">
                                <p className="text-sm font-semibold text-[#1A1410] truncate leading-tight">{lead.nome}</p>
                                <p className="text-[11px] text-[#6B5E55] truncate">{lead.empresa || lead.telefone || "—"}</p>
                              </div>
                              <div className="flex items-center gap-1 shrink-0">
                                <button onClick={() => setSelectedLead(lead)} title="Editar"
                                  className="w-6 h-6 rounded-md border border-[#D4CFC9] text-[#60A5FA] hover:border-[#60A5FA] hover:bg-[#0D2040] text-[10px] font-bold transition-colors flex items-center justify-center">
                                  ✏
                                </button>
                                <button onClick={async () => {
                                    if (!confirm(`Excluir "${lead.nome}" do CRM?`)) return;
                                    await crmDeleteLead(lead.id);
                                    await loadListaLeads(selectedLista.id);
                                    await refresh();
                                  }} title="Excluir do CRM"
                                  className="w-6 h-6 rounded-md border border-[#D4CFC9] text-[#F87171] hover:border-[#F87171] hover:bg-[#2D0A0A] text-[10px] font-bold transition-colors flex items-center justify-center">
                                  ✕
                                </button>
                                <button onClick={() => handleRemoveLeadFromLista(selectedLista, lead)} title="Remover da lista"
                                  className="w-6 h-6 rounded-md border border-[#D4CFC9] text-[#6B5E55] hover:border-[#3D4F5A] hover:text-[#1A1410] text-[10px] transition-colors flex items-center justify-center">
                                  −
                                </button>
                              </div>
                            </div>
                            {/* Badges */}
                            <div className="flex flex-wrap gap-1 mb-1.5">
                              <span className={`text-[9px] font-semibold px-1.5 py-0.5 rounded-full ${badge.bg} ${badge.text}`}>{badge.label}</span>
                              {col && <span className="text-[9px] font-semibold px-1.5 py-0.5 rounded-full bg-[#E8E4DF]" style={{ color: col.color }}>{col.name}</span>}
                              {lead.score !== null && lead.score !== undefined && (
                                <span className={`text-[9px] font-bold px-1.5 py-0.5 rounded-full bg-[#E8E4DF] ${scoreColor(lead.score)}`}>
                                  {lead.score}pts
                                </span>
                              )}
                              {lead.lead_status && <span className="text-[9px] px-1.5 py-0.5 rounded-full bg-[#0E0E18] text-[#CC3300]">{statusLabel(lead.lead_status)}</span>}
                            </div>
                            {lead.campanha && <p className="text-[10px] text-[#3B82F6] truncate">📢 {lead.campanha}</p>}
                          </div>
                        );
                      })}
                    </div>
                  )}
                </>
              )}
            </div>
          </div>
        </div>
      )}

      {/* Modais */}
      {selectedLead && (
        <LeadModal
          lead={selectedLead}
          columns={columns}
          listas={listas}
          onConfirmLista={async (listaId) => {
            await crmAddLeadToLista(listaId, selectedLead.id);
            await crmMoveLead(selectedLead.id, null);
            await refresh();
          }}
          onClose={() => setSelectedLead(null)}
          onRefresh={refresh}
        />
      )}
      {showNewCol && (
        <ColumnModal instanceId={instanceId || lastInbox || undefined} onClose={() => setShowNewCol(false)} onSaved={refresh} />
      )}
      {editingColumn && (
        <ColumnModal
          column={editingColumn}
          instanceId={instanceId || lastInbox || undefined}
          onClose={() => setEditingColumn(null)}
          onSaved={refresh}
        />
      )}
      {showNewLead && (
        <NewLeadModal columns={columns} instanceId={instanceId || lastInbox} onClose={() => setShowNewLead(false)} onCreated={refresh} />
      )}

      {showSaveLista && (
        <div className="fixed inset-0 z-[200] flex items-center justify-center bg-black/60">
          <div className="bg-[#E8E4DF] rounded-2xl p-6 w-full max-w-sm shadow-xl border border-[#D4CFC9]">
            <h3 className="text-[#1A1410] font-bold text-lg mb-1">Salvar como Lista</h3>
            <p className="text-[#6B5E55] text-sm mb-4">
              {filterOrigem ? `Filtro ativo: origem "${filterOrigem}"` : "Todos os leads do CRM serão incluídos."}
            </p>
            <input
              type="text"
              placeholder="Nome da lista (ex: Leads Meta - Maio)"
              value={listaName}
              onChange={(e) => setListaName(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && handleSaveLista()}
              className="w-full bg-[#F0EDE8] border border-[#D4CFC9] rounded-xl px-4 py-2.5 text-[#1A1410] text-sm outline-none focus:border-[#3B82F6] mb-4"
              autoFocus
            />
            <div className="flex gap-2 justify-end">
              <button
                onClick={() => { setShowSaveLista(false); setListaName(""); }}
                className="px-4 py-2 rounded-xl text-sm text-[#6B5E55] hover:text-[#1A1410] transition-colors"
              >
                Cancelar
              </button>
              <button
                onClick={handleSaveLista}
                disabled={savingLista || !listaName.trim()}
                className="bg-[#3B82F6] hover:bg-[#2563EB] text-white font-bold px-5 py-2 rounded-xl text-sm transition-colors disabled:opacity-40"
              >
                {savingLista ? "Salvando..." : "Salvar Lista"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}



export default function CrmPage() {
  return (
    <Suspense fallback={<div className="flex items-center justify-center min-h-screen bg-[#F0EDE8]"><p className="text-[#6B5E55] text-sm">Carregando...</p></div>}>
      <CrmPageInner />
    </Suspense>
  );
}
