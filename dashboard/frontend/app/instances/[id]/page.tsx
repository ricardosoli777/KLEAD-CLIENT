"use client";

import type { ReactNode } from "react";
import { useCallback, useEffect, useRef, useState } from "react";
import Image from "next/image";
import { useParams, useRouter } from "next/navigation";
import PageHeaderShell from "@/components/PageHeaderShell";
import {
  getStatus, getLogs, getSends,
  uploadContacts, startCampaign, stopCampaign,
  getInstanceConfig, getCredentials, updateCredentials, updatePrompt,
  previewPrompt, getWhatsappStatus, testMessage,
  generateQueue, getQueue, updateQueueItem, toggleSkipQueueItem,
  stopQueueGeneration, sendQueue,
  listContacts, clearContacts,
  importContactsText,
  resetInstance, clearConversations, deleteInstance,
  updateSdrConfig,
  listKnowledge, uploadKnowledge, deleteKnowledge,
  getWebhookUrl, reconnectInstanceQrcode, validateEvolutionConnection,
  getMetaWebhookInfo, rotateWebhookSecret,
  getMetaWebhookUrls,
  getMetaHealth,
  getMetaCapiAudit,
  type MetaCapiAuditItem,
  getMetaStatus, startMetaConnect, getMetaAssets, saveMetaAssets, disconnectMeta,
  type MetaAssetsResponse, type MetaSavedAssets, type MetaStatusResponse,
  crmGetListas, crmGetListaContatos,
  type TimingConfig,
  type KnowledgeFile,
  type CrmLista,
  updateCampaignContext,
} from "@/lib/api";
// ── Types ─────────────────────────────────────────────────────────────────────

type QueueItem = { name: string; phone: string; message: string; skipped: boolean };
type Log       = { timestamp: string; name: string; phone: string; message: string; status: string };
type Status    = { instance_id: string; status: string; envios_hoje: number; total_contacts: number; logs_count: number; falhos_count: number; daily_limit: number; progress: number };
type Creds     = { api_url: string; api_key: string; global_api_key: string; openai_key: string; provider: string; evo_instance_name: string; meta_token: string; meta_app_secret: string; phone_number_id: string; template_name: string; meta_page_id: string; meta_page_access_token: string; meta_ad_account_id: string; meta_capi_enabled: boolean; meta_capi_access_token: string; meta_capi_event_source_id: string; meta_capi_test_event_code: string };
type SpeechRecognitionResultEvent = { resultIndex: number; results: ArrayLike<ArrayLike<{ transcript: string }>> };
type SpeechRecognitionInstance = {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  onresult: ((event: SpeechRecognitionResultEvent) => void) | null;
  onerror: (() => void) | null;
  onend: (() => void) | null;
  start: () => void;
  stop: () => void;
  abort: () => void;
};
type SpeechRecognitionConstructor = new () => SpeechRecognitionInstance;

function stripActPrefix(value: string) {
  return value.startsWith("act_") ? value.slice(4) : value;
}

const STATUS_COLOR: Record<string, string> = {
  idle:      "bg-[#D4CFC9] text-[#6B5E55]",
  running:   "bg-[#3D0A00] text-[#CC3300]",
  stopping:  "bg-[#2D2000] text-[#F0B429]",
  stopped:   "bg-[#2D0A0A] text-[#F87171]",
  completed: "bg-[#0E0E18] text-[#60A5FA]",
};

const STATUS_LABEL: Record<string, string> = {
  idle:      "Aguardando",
  running:   "Rodando",
  stopping:  "Parando",
  stopped:   "Parada",
  completed: "Concluída",
};

const LOG_COLOR: Record<string, string> = {
  ENVIADO:                      "text-[#CC3300]",
  FALHOU:                       "text-[#F87171]",
  ERRO_OPENAI:                  "text-orange-400",
  CAMPANHA_INICIADA:            "text-[#60A5FA] font-semibold",
  CAMPANHA_CONCLUIDA:           "text-[#60A5FA] font-semibold",
  CAMPANHA_PARADA_PELO_USUARIO: "text-[#F87171] font-semibold",
  LIMITE_DIARIO_ATINGIDO:       "text-purple-400 font-semibold",
  PAUSA_LONGA:                  "text-[#F0B429]",
  DELAY:                        "text-[#6B5E55]",
  AGUARDANDO_LIMITE_HORARIO:    "text-orange-300",
};

type HelpFieldProps = {
  label: string;
  helpTitle: string;
  helpText: string;
  children: ReactNode;
  badge?: string;
};

function HelpField({ label, helpTitle, helpText, children, badge }: HelpFieldProps) {
  return (
    <div className="group relative">
      <div className="flex items-center gap-2 mb-1.5">
        <label className="block text-sm font-medium text-[#6B5E55]">{label}</label>
        <span className="inline-flex items-center justify-center w-5 h-5 rounded-full border border-[#D4CFC9] text-[10px] text-[#6B5E55] bg-[#F0EDE8] group-hover:border-[#CC3300] group-hover:text-[#CC3300] group-focus-within:border-[#CC3300] group-focus-within:text-[#CC3300] transition-colors">
          i
        </span>
        {badge && (
          <span className="text-[10px] uppercase tracking-wide text-[#6B5E55] bg-[#E8E4DF] border border-[#D4CFC9] rounded-full px-2 py-0.5">
            {badge}
          </span>
        )}
      </div>
      {children}
      <div className="pointer-events-none absolute left-0 top-full z-30 mt-2 hidden w-full rounded-2xl border border-[#CC3300]/20 bg-[#3D0A00] p-3 shadow-2xl group-hover:block group-focus-within:block">
        <p className="text-xs font-semibold uppercase tracking-wide text-[#CC3300] mb-1">{helpTitle}</p>
        <p className="text-xs leading-relaxed text-[#D1FAE5]">{helpText}</p>
      </div>
    </div>
  );
}

// ── Countdown ─────────────────────────────────────────────────────────────────

function useCountdown(logs: Log[], isRunning: boolean) {
  const [remaining, setRemaining] = useState<number | null>(null);
  const [total, setTotal]         = useState<number>(0);
  const [type, setType]           = useState<"delay" | "pausa">("delay");

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (!isRunning || logs.length === 0) { setRemaining(null); return; }
    const last = logs[logs.length - 1];
    const isDelay = last.status.startsWith("DELAY");
    const isPausa = last.status.startsWith("PAUSA_LONGA");
    if (!isDelay && !isPausa) { setRemaining(null); return; }
    const match = last.status.match(/\| (\d+)s/);
    if (!match) { setRemaining(null); return; }
    const secs = parseInt(match[1]);
    const startedAt = new Date(last.timestamp).getTime();
    setTotal(secs);
    setType(isDelay ? "delay" : "pausa");
    const tick = () => { const elapsed = Math.floor((Date.now() - startedAt) / 1000); setRemaining(Math.max(0, secs - elapsed)); };
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, [logs, isRunning]);

  return { remaining, total, type };
}

function CountdownBar({ logs, isRunning }: { logs: Log[]; isRunning: boolean }) {
  const { remaining, total, type } = useCountdown(logs, isRunning);
  if (!isRunning || remaining === null || remaining === 0) return null;
  const pct      = total > 0 ? Math.round(((total - remaining) / total) * 100) : 0;
  const isPausa  = type === "pausa";
  const fmt = (s: number) => s >= 60 ? `${Math.floor(s / 60)}m ${s % 60}s` : `${s}s`;

  return (
    <div className={`rounded-xl border px-4 py-3 mb-4 ${isPausa ? "border-[#F0B429]/30 bg-[#2D2000]" : "border-[#CC3300]/30 bg-[#3D0A00]"}`}>
      <div className="flex items-center justify-between mb-2">
        <span className={`text-sm font-semibold ${isPausa ? "text-[#F0B429]" : "text-[#CC3300]"}`}>
          {isPausa ? "Pausa longa" : "Próxima mensagem em"}
        </span>
        <span className={`text-2xl font-bold tabular-nums ${isPausa ? "text-[#F0B429]" : "text-[#CC3300]"}`}>
          {fmt(remaining)}
        </span>
      </div>
      <div className="w-full bg-[#F0EDE8] rounded-full h-1.5 overflow-hidden">
        <div
          className={`h-1.5 rounded-full transition-none ${isPausa ? "bg-[#F0B429]" : "bg-[#CC3300]"}`}
          style={{ width: `${pct}%` }}
        />
      </div>
      <p className={`text-xs mt-1 ${isPausa ? "text-[#F0B429]" : "text-[#CC3300]"}`}>
        {fmt(total - remaining)} decorridos de {fmt(total)} totais
      </p>
    </div>
  );
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function safeApiMsg(raw: string): string {
  if (!raw) return "";
  try {
    const parsed = JSON.parse(raw);
    return parsed?.error?.message ?? parsed?.message ?? raw;
  } catch {
    return raw;
  }
}

// ── Card de Integração Meta (OAuth) ─────────────────────────────────────────────

type MetaCardState = "loading" | "disconnected" | "connecting" | "connected_no_assets" | "connected_saved" | "error";

function MetaIntegrationCard({ instanceId, provider }: { instanceId: string; provider: string }) {
  const [state, setState] = useState<MetaCardState>("loading");
  const [errorMsg, setErrorMsg] = useState("");
  const [assets, setAssets] = useState<MetaAssetsResponse | null>(null);
  const [saved, setSaved] = useState<MetaSavedAssets>(null);
  const [whatsappStatus, setWhatsappStatus] = useState<MetaStatusResponse["whatsapp"]>();
  const [leadAdsStatus, setLeadAdsStatus] = useState<MetaStatusResponse["lead_ads"]>();
  const [saving, setSaving] = useState(false);
  const [webhookWarning, setWebhookWarning] = useState("");
  const [selection, setSelection] = useState({
    business_id: "", business_name: "",
    ad_account_id: "", ad_account_name: "",
    page_id: "", page_name: "",
    instagram_id: "", instagram_username: "",
    waba_id: "", waba_name: "",
    phone_number_id: "", phone_display_number: "",
    pixel_id: "", pixel_name: "",
    whatsapp_enabled: provider === "meta",
    lead_ads_enabled: true,
    lead_ads_default: false,
    lead_forms: [] as Array<{ id: string; name: string }>,
    confirm_route_transfer: false,
  });

  const loadStatus = useCallback(async () => {
    try {
      const status = await getMetaStatus(instanceId);
      setWhatsappStatus(status.whatsapp);
      setLeadAdsStatus(status.lead_ads);
      if (!status.connected) {
        setState("disconnected");
        return;
      }
      if (status.assets && status.assets.status === "active") {
        setSaved(status.assets);
        setState("connected_saved");
      } else {
        setState("connected_no_assets");
      }
    } catch {
      setState("error");
      setErrorMsg("Não foi possível verificar o status da integração Meta.");
    }
  }, [instanceId]);

  useEffect(() => {
    loadStatus();
    // Se voltamos do redirect OAuth (?meta_connected=1), limpa a URL depois de ler.
    const params = new URLSearchParams(window.location.search);
    if (params.get("meta_connected") || params.get("meta_error")) {
      const url = new URL(window.location.href);
      const metaError = url.searchParams.get("meta_error");
      url.searchParams.delete("meta_connected");
      url.searchParams.delete("meta_error");
      window.history.replaceState({}, "", url.toString());
      if (metaError) {
        setState("error");
        setErrorMsg(`A conexão com a Meta falhou (${metaError}). Tente novamente.`);
      }
    }
  }, [loadStatus]);

  useEffect(() => {
    if (state !== "connected_no_assets") return;
    getMetaAssets(instanceId)
      .then((res) => {
        if (!res.ok) {
          setState("error");
          setErrorMsg(res.detail || "Erro ao buscar ativos da Meta.");
          return;
        }
        setAssets(res);
        if (saved) {
          setSelection((current) => ({
            ...current,
            ...saved,
            whatsapp_enabled: Boolean(saved.whatsapp_enabled),
            lead_ads_enabled: Boolean(saved.lead_ads_enabled),
            lead_ads_default: Boolean(leadAdsStatus?.default_for_page),
            lead_forms: res.lead_forms
              .filter((formAsset) => formAsset.page_id === saved.page_id && formAsset.route_instance_id === instanceId)
              .map((formAsset) => ({ id: formAsset.id, name: formAsset.name })),
          }));
        }
      })
      .catch((err: unknown) => {
        setState("error");
        const isTimeout = err instanceof DOMException
          ? err.name === "TimeoutError" || err.name === "AbortError"
          : err instanceof Error && /timeout|aborted/i.test(`${err.name} ${err.message}`);
        setErrorMsg(isTimeout
          ? "A Meta demorou mais de 2 minutos para listar os ativos. Tente novamente em alguns instantes."
          : err instanceof Error && err.message
            ? `Não foi possível buscar os ativos da Meta: ${err.message}`
            : "Erro de conexão ao buscar ativos da Meta.");
      });
  }, [state, instanceId, saved, leadAdsStatus?.default_for_page]);

  const handleConnect = async () => {
    setState("connecting");
    setErrorMsg("");
    try {
      const res = await startMetaConnect(instanceId);
      if (!res.ok || !res.authorize_url) {
        setState("error");
        setErrorMsg(res.detail || "Não foi possível iniciar a conexão com a Meta.");
        return;
      }
      if (res.csrf) {
        // Em produção o callback roda em api.example.com, então o cookie deve
        // pertencer a .example.com. Não derive com as duas últimas partes:
        // em domínios brasileiros isso produziria o domínio público .com.br,
        // que o navegador rejeita. Em dev/outros hosts, use cookie host-only.
        const hostname = window.location.hostname.toLowerCase();
        const domain = hostname === "example.com" || hostname.endsWith(".example.com")
          ? "; domain=.example.com"
          : "";
        document.cookie = `klead_meta_csrf=${res.csrf}; path=/; max-age=300; secure; samesite=lax${domain}`;
      }
      window.location.href = res.authorize_url;
    } catch {
      setState("error");
      setErrorMsg("Erro de conexão ao iniciar login com a Meta.");
    }
  };

  const handleSave = async () => {
    setSaving(true);
    try {
      let res;
      try {
        res = await saveMetaAssets(instanceId, selection);
      } catch (err) {
        const detail = err instanceof Error ? err.message : "";
        if (!detail.startsWith("transfer_confirmation_required:")) throw err;
        const conflict = detail.replace("transfer_confirmation_required:", "");
        if (!confirm(`Estes destinos já pertencem a outra instância:\n${conflict}\n\nTransferir para ${instanceId}?`)) {
          return;
        }
        res = await saveMetaAssets(instanceId, { ...selection, confirm_route_transfer: true });
      }
      if (!res.ok) {
        setState("error");
        setErrorMsg(res.detail || "Erro ao salvar a seleção de ativos.");
        return;
      }
      setWebhookWarning(selection.whatsapp_enabled && res.webhook_subscribed === false
        ? `Ativos salvos, mas a assinatura do webhook ficou pendente${res.webhook_detail ? `: ${res.webhook_detail}` : "."}`
        : "");
      await loadStatus();
    } catch {
      setState("error");
      setErrorMsg("Erro de conexão ao salvar ativos.");
    } finally {
      setSaving(false);
    }
  };

  const handleDisconnect = async () => {
    if (!confirm("Desconectar a integração Meta desta instância? Os ativos selecionados serão removidos — você pode reconectar depois.")) return;
    try {
      await disconnectMeta(instanceId);
      setSaved(null);
      setState("disconnected");
    } catch {
      setState("error");
      setErrorMsg("Erro ao desconectar a integração Meta.");
    }
  };

  const selectClass = "w-full bg-[#F0EDE8] border border-[#D4CFC9] rounded-xl px-3 py-2 text-sm text-[#1A1410] focus:outline-none focus:border-[#CC3300] transition-colors";

  return (
    <div className="rounded-2xl border border-[#D4CFC9] bg-[#F0EDE8] p-4 space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <p className="text-xs font-semibold uppercase tracking-wide text-[#CC3300]">Integração Meta</p>
          {state === "connected_saved" && <p className="text-xs text-[#6B5E55] mt-0.5">Meta conectada</p>}
        </div>
        {(state === "connected_no_assets" || state === "connected_saved") && (
          <button onClick={handleDisconnect} className="text-xs text-[#6B5E55] hover:text-[#F87171] transition-colors">
            Desconectar Meta
          </button>
        )}
      </div>

      {state === "loading" && (
        <p className="text-sm text-[#6B5E55]">Carregando...</p>
      )}

      {state === "disconnected" && (
        <div className="space-y-3">
          <p className="text-sm text-[#6B5E55] leading-relaxed">
            Conecte sua conta Meta para importar contas de anúncio, páginas, Instagram, WhatsApp Business e dados de KPI.
          </p>
          <button
            onClick={handleConnect}
            className="w-full bg-[#CC3300] hover:bg-[#991F00] text-[#F0EDE8] font-bold py-2.5 rounded-xl text-sm transition-colors"
          >
            Conectar com Meta
          </button>
        </div>
      )}

      {state === "connecting" && (
        <button disabled className="w-full bg-[#CC3300]/50 text-[#F0EDE8] font-bold py-2.5 rounded-xl text-sm cursor-not-allowed">
          Redirecionando para a Meta...
        </button>
      )}

      {state === "error" && (
        <div className="space-y-3">
          <p className="text-sm text-[#F87171] bg-[#2D0A0A] border border-[#F87171]/30 rounded-xl px-3 py-2">{errorMsg}</p>
          <button
            onClick={handleConnect}
            className="w-full bg-[#CC3300] hover:bg-[#991F00] text-[#F0EDE8] font-bold py-2.5 rounded-xl text-sm transition-colors"
          >
            Reconectar Meta
          </button>
        </div>
      )}

      {state === "connected_no_assets" && (
        <div className="space-y-3">
          {!assets ? (
            <p className="text-sm text-[#6B5E55]">Buscando ativos disponíveis na sua conta Meta...</p>
          ) : (
            <>
              <div className="grid sm:grid-cols-2 gap-3">
                <label className={`rounded-xl border p-3 cursor-pointer ${selection.lead_ads_enabled ? "border-[#CC3300] bg-[#FDE8E4]" : "border-[#D4CFC9]"}`}>
                  <input type="checkbox" className="mr-2" checked={selection.lead_ads_enabled} onChange={(e) => setSelection({ ...selection, lead_ads_enabled: e.target.checked })} />
                  <span className="text-sm font-semibold text-[#1A1410]">Receber Lead Ads</span>
                  <p className="text-xs text-[#6B5E55] mt-1">Página e formulários, sem exigir WhatsApp API.</p>
                </label>
                <label className={`rounded-xl border p-3 cursor-pointer ${selection.whatsapp_enabled ? "border-[#CC3300] bg-[#FDE8E4]" : "border-[#D4CFC9]"}`}>
                  <input type="checkbox" className="mr-2" checked={selection.whatsapp_enabled} onChange={(e) => setSelection({ ...selection, whatsapp_enabled: e.target.checked })} />
                  <span className="text-sm font-semibold text-[#1A1410]">Usar WhatsApp Oficial</span>
                  <p className="text-xs text-[#6B5E55] mt-1">Ativa WABA, número e mensagens pela Cloud API.</p>
                </label>
              </div>
              <div>
                <label className="block text-xs font-semibold text-[#6B5E55] uppercase tracking-wide mb-1">Business Manager</label>
                <select
                  className={selectClass}
                  value={selection.business_id}
                  onChange={(e) => {
                    const biz = assets.businesses.find((b) => b.id === e.target.value);
                    setSelection({ ...selection, business_id: e.target.value, business_name: biz?.name || "" });
                  }}
                >
                  <option value="">Selecione...</option>
                  {assets.businesses.map((b) => <option key={b.id} value={b.id}>{b.name}</option>)}
                </select>
              </div>
              {selection.lead_ads_enabled && selection.page_id && (
                <div className="rounded-xl border border-[#D4CFC9] p-3 space-y-2">
                  <label className="flex items-center gap-2 text-sm text-[#1A1410]">
                    <input type="checkbox" checked={selection.lead_ads_default} onChange={(e) => setSelection({ ...selection, lead_ads_default: e.target.checked })} />
                    Esta instância é o destino padrão desta Página
                  </label>
                  <p className="text-xs text-[#6B5E55]">Formulários sem regra específica usarão este destino.</p>
                  <div className="space-y-1.5 pt-1">
                    {assets.lead_forms.filter((f) => f.page_id === selection.page_id).map((formAsset) => {
                      const selected = selection.lead_forms.some((f) => f.id === formAsset.id);
                      return (
                        <label key={formAsset.id} className="flex items-start gap-2 rounded-lg bg-[#E8E4DF] px-3 py-2 text-sm">
                          <input
                            type="checkbox"
                            className="mt-0.5"
                            checked={selected}
                            onChange={(e) => setSelection({
                              ...selection,
                              lead_forms: e.target.checked
                                ? [...selection.lead_forms, { id: formAsset.id, name: formAsset.name }]
                                : selection.lead_forms.filter((f) => f.id !== formAsset.id),
                            })}
                          />
                          <span className="text-[#1A1410]">
                            {formAsset.name}
                            {formAsset.route_instance_id && formAsset.route_instance_id !== instanceId && (
                              <span className="block text-xs text-[#F0B429]">Atualmente: {formAsset.route_instance_id}</span>
                            )}
                          </span>
                        </label>
                      );
                    })}
                    {assets.lead_forms.filter((f) => f.page_id === selection.page_id).length === 0 && (
                      <p className={assets.pages.find((p) => p.id === selection.page_id)?.lead_forms_error ? "text-xs text-[#F0B429]" : "text-xs text-[#6B5E55]"}>
                        {assets.pages.find((p) => p.id === selection.page_id)?.lead_forms_error || "Nenhum formulário disponível nesta Página."}
                      </p>
                    )}
                  </div>
                </div>
              )}
              <div>
                <label className="block text-xs font-semibold text-[#6B5E55] uppercase tracking-wide mb-1">Conta de anúncio</label>
                <select
                  className={selectClass}
                  value={selection.ad_account_id}
                  onChange={(e) => {
                    const acc = assets.ad_accounts.find((a) => a.id === e.target.value);
                    setSelection({ ...selection, ad_account_id: e.target.value, ad_account_name: acc?.name || "", pixel_id: "", pixel_name: "" });
                  }}
                >
                  <option value="">Selecione...</option>
                  {assets.ad_accounts.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
                </select>
              </div>
              <div>
                <label className="block text-xs font-semibold text-[#6B5E55] uppercase tracking-wide mb-1">
                  Pixel / Dataset <span className="normal-case font-normal text-[#6B5E55]">— usado para enviar KPIs de volta à Meta</span>
                </label>
                <select
                  className={selectClass}
                  value={selection.pixel_id}
                  disabled={!selection.ad_account_id}
                  onChange={(e) => {
                    const px = assets.pixels.find((p) => p.id === e.target.value);
                    setSelection({ ...selection, pixel_id: e.target.value, pixel_name: px?.name || "" });
                  }}
                >
                  <option value="">{selection.ad_account_id ? "Selecione (opcional)..." : "Selecione uma conta de anúncio primeiro"}</option>
                  {assets.pixels.filter((p) => p.ad_account_id === selection.ad_account_id).map((p) => (
                    <option key={p.id} value={p.id}>{p.name}</option>
                  ))}
                </select>
              </div>
              <div>
                <label className="block text-xs font-semibold text-[#6B5E55] uppercase tracking-wide mb-1">Página</label>
                <select
                  className={selectClass}
                  value={selection.page_id}
                  onChange={(e) => {
                    const page = assets.pages.find((p) => p.id === e.target.value);
                    setSelection({
                      ...selection,
                      page_id: e.target.value,
                      page_name: page?.name || "",
                      instagram_id: page?.instagram_business_account_id || "",
                      instagram_username: page?.instagram_username || "",
                      lead_forms: [],
                      lead_ads_default: false,
                    });
                  }}
                >
                  <option value="">Selecione...</option>
                  {assets.pages.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
                </select>
              </div>
              <div>
                <label className="block text-xs font-semibold text-[#6B5E55] uppercase tracking-wide mb-1">Instagram conectado</label>
                <input
                  readOnly
                  value={selection.instagram_id
                    ? `${selection.instagram_username ? `@${selection.instagram_username} — ` : ""}ID: ${selection.instagram_id}`
                    : "Nenhum vinculado à página selecionada"}
                  className={`${selectClass} opacity-70 cursor-not-allowed`}
                />
              </div>
              {selection.whatsapp_enabled && <div>
                <label className="block text-xs font-semibold text-[#6B5E55] uppercase tracking-wide mb-1">WhatsApp Business Account</label>
                <select
                  className={selectClass}
                  value={selection.waba_id}
                  onChange={(e) => {
                    const waba = assets.whatsapp_business_accounts.find((w) => w.id === e.target.value);
                    setSelection({ ...selection, waba_id: e.target.value, waba_name: waba?.name || "", phone_number_id: "", phone_display_number: "" });
                  }}
                >
                  <option value="">Selecione...</option>
                  {assets.whatsapp_business_accounts.map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
                </select>
              </div>}
              {selection.whatsapp_enabled && <div>
                <label className="block text-xs font-semibold text-[#6B5E55] uppercase tracking-wide mb-1">Número WhatsApp</label>
                <select
                  className={selectClass}
                  value={selection.phone_number_id}
                  disabled={!selection.waba_id}
                  onChange={(e) => {
                    const phone = assets.phone_numbers.find((p) => p.id === e.target.value);
                    setSelection({ ...selection, phone_number_id: e.target.value, phone_display_number: phone?.display_phone_number || "" });
                  }}
                >
                  <option value="">{selection.waba_id ? "Selecione..." : "Selecione um WABA primeiro"}</option>
                  {assets.phone_numbers.filter((p) => p.waba_id === selection.waba_id).map((p) => (
                    <option key={p.id} value={p.id}>{p.display_phone_number}</option>
                  ))}
                </select>
              </div>}
              <button
                onClick={handleSave}
                disabled={saving || (!selection.lead_ads_enabled && !selection.whatsapp_enabled) || (selection.lead_ads_enabled && !selection.page_id) || (selection.whatsapp_enabled && (!selection.waba_id || !selection.phone_number_id))}
                className="w-full bg-[#CC3300] hover:bg-[#991F00] disabled:bg-[#CC3300]/40 text-[#F0EDE8] font-bold py-2.5 rounded-xl text-sm transition-colors"
              >
                {saving ? "Salvando..." : "Salvar Integração Meta"}
              </button>
              <p className="text-xs text-[#6B5E55]">Lead Ads exige Página. WhatsApp Oficial exige WABA e número. Os dois modos são independentes.</p>
              {webhookWarning && <p className="text-xs text-[#F0B429]">{webhookWarning}</p>}
            </>
          )}
        </div>
      )}

      {state === "connected_saved" && saved && (
        <div className="space-y-2 text-sm">
          {provider === "meta" && whatsappStatus?.enabled && (
            <div className={`rounded-xl border p-3 ${whatsappStatus.configured && whatsappStatus.webhook_subscribed ? "border-[#CC3300]/30 bg-[#FDE8E4]" : "border-[#F0B429]/30 bg-[#FFF7E0]"}`}>
              <p className="font-semibold text-[#1A1410]">
                {whatsappStatus.configured && whatsappStatus.webhook_subscribed ? "WhatsApp Oficial conectado" : "WhatsApp Oficial com configuração pendente"}
              </p>
              <p className="text-xs text-[#6B5E55] mt-1">Credencial: {whatsappStatus.credential_source === "oauth" ? "conexão automática" : "manual"}</p>
              {!whatsappStatus.webhook_subscribed && <p className="text-xs text-[#F0B429] mt-1">Assinatura do WABA pendente. Reconecte ou salve os ativos novamente.</p>}
              <p className="text-[11px] text-[#6B5E55] mt-1 break-all">Callback: {whatsappStatus.webhook_url}</p>
            </div>
          )}
          {provider === "meta" && whatsappStatus && !whatsappStatus.enabled && (
            <div className="rounded-xl border border-[#D4CFC9] bg-[#E8E4DF] p-3">
              <p className="font-semibold text-[#1A1410]">WhatsApp Oficial não ativado</p>
              <p className="text-xs text-[#6B5E55] mt-1">
                A integração Meta continua disponível para Lead Ads, anúncios e CAPI. Para enviar pela Cloud API, altere os ativos e selecione um WABA e um número.
              </p>
            </div>
          )}
          {saved.lead_ads_enabled && (
            <div className="rounded-xl border border-[#CC3300]/30 bg-[#FDE8E4] p-3">
              <p className="font-semibold text-[#1A1410]">Lead Ads ativo</p>
              <p className="text-xs text-[#6B5E55] mt-1">Os formulários selecionados serão encaminhados por regra.</p>
            </div>
          )}
          <div className="rounded-xl border border-[#D4CFC9] bg-[#E8E4DF] p-3 space-y-1.5">
            <p><span className="text-[#6B5E55]">Business:</span> <span className="text-[#1A1410]">{saved.business_name || "—"}</span></p>
            <p><span className="text-[#6B5E55]">Conta de anúncio:</span> <span className="text-[#1A1410]">{saved.ad_account_name || "—"}</span></p>
            <p><span className="text-[#6B5E55]">Página:</span> <span className="text-[#1A1410]">{saved.page_name || "—"}</span></p>
            <p><span className="text-[#6B5E55]">Instagram:</span> <span className="text-[#1A1410]">{saved.instagram_id || "Nenhum"}</span></p>
            {saved.whatsapp_enabled && <p><span className="text-[#6B5E55]">Número WhatsApp:</span> <span className="text-[#1A1410]">{saved.phone_display_number || "—"}</span></p>}
            <p><span className="text-[#6B5E55]">Pixel/Dataset (KPIs):</span> <span className="text-[#1A1410]">{saved.pixel_name || "Nenhum — KPIs não serão enviados"}</span></p>
          </div>
          <button
            onClick={() => setState("connected_no_assets")}
            className="w-full border border-[#D4CFC9] text-[#6B5E55] hover:text-[#1A1410] font-medium py-2 rounded-xl text-xs transition-colors"
          >
            Alterar ativos
          </button>
        </div>
      )}
    </div>
  );
}

// ── Modal de Credenciais ───────────────────────────────────────────────────────

function CredentialsModal({ instanceId, onClose }: { instanceId: string; onClose: () => void }) {
  const EVOLUTION_API_URL = "https://go.seudominio.com";
  const [form, setForm] = useState<Creds>({ api_url: "", api_key: "", global_api_key: "", openai_key: "", provider: "evolution", evo_instance_name: "", meta_token: "", meta_app_secret: "", phone_number_id: "", template_name: "", meta_page_id: "", meta_page_access_token: "", meta_ad_account_id: "", meta_capi_enabled: false, meta_capi_access_token: "", meta_capi_event_source_id: "", meta_capi_test_event_code: "" });
  const [loading, setLoading] = useState(true);
  const [saving, setSaving]   = useState(false);
  const [validating, setValidating] = useState(false);
  const [error, setError]     = useState("");
  const [saved, setSaved]     = useState(false);
  const [validationInfo, setValidationInfo] = useState("");
  const [qrPreview, setQrPreview] = useState("");
  const [activationStatus, setActivationStatus] = useState<null | {
    token_valid: boolean; token_name: string; token_error: string;
    page_subscribed: boolean; page_subscribed_error: string;
    capi_auto_enabled: boolean; capi_token_valid: boolean; capi_token_error: string; webhook_waba_pending: boolean;
  }>(null);
  const [capiLogs, setCapiLogs] = useState<MetaCapiAuditItem[]>([]);
  const [showCapiLogs, setShowCapiLogs] = useState(false);
  const [webhookUrls, setWebhookUrls] = useState<{ waba_url: string; leads_url: string; verify_token: string } | null>(null);
  const [webhookCopied, setWebhookCopied] = useState<"waba" | "leads" | "token" | null>(null);
  const [showEvolutionBlock, setShowEvolutionBlock] = useState(false);
  const [showMetaManualBlock, setShowMetaManualBlock] = useState(false);
  const [showWebhookUrlsBlock, setShowWebhookUrlsBlock] = useState(false);

  useEffect(() => {
    getCredentials(instanceId).then((data) => {
      setForm((prev) => ({
        ...prev,
        ...data,
        meta_ad_account_id: stripActPrefix(data.meta_ad_account_id ?? ""),
      }));
      if (data.provider === "meta" && data.meta_capi_enabled) {
        getMetaCapiAudit(instanceId, 20).then(setCapiLogs).catch(() => {});
      }
    }).catch(() => {
      setError("Erro ao carregar credenciais.");
    }).finally(() => {
      setLoading(false);
    });
    getMetaWebhookUrls(instanceId).then(setWebhookUrls).catch(() => {});
  }, [instanceId]);

  const handleSave = async () => {
    setSaving(true); setError(""); setActivationStatus(null);
    try {
      const res = await updateCredentials(instanceId, form);
      if (res.ok) {
        setSaved(true);
        if (form.provider === "meta" && res.activation) {
          setActivationStatus(res.activation);
        } else {
          setTimeout(onClose, 800);
        }
      } else {
        setError(res.detail ?? "Erro ao salvar.");
      }
    } catch {
      setError("Erro ao conectar. Tente novamente.");
    } finally {
      setSaving(false);
    }
  };

  const handleValidate = async () => {
    setError("");
    setValidationInfo("");
    setQrPreview("");
    setActivationStatus(null);
    setValidating(true);

    try {
      const saveRes = await updateCredentials(instanceId, form);
      if (!saveRes.ok) {
        const errText = saveRes.detail ?? "Erro ao salvar as credenciais.";
        try { setError(JSON.parse(errText)?.detail ?? errText); } catch { setError(errText); }
        return;
      }

      if (form.provider === "meta") {
        const [health, oauthStatus] = await Promise.all([
          getMetaHealth(instanceId),
          getMetaStatus(instanceId),
        ]);
        if (!health || !health.ok) {
          setError("As credenciais foram salvas, mas não foi possível consultar a saúde da integração Meta.");
          return;
        }

        setSaved(true);
        if (saveRes.activation) setActivationStatus(saveRes.activation);

        const leadAdsActive = Boolean(oauthStatus.connected && oauthStatus.lead_ads?.enabled);
        const whatsappEnabled = Boolean(oauthStatus.whatsapp?.enabled);
        const whatsappReady = Boolean(
          whatsappEnabled
          && oauthStatus.whatsapp?.configured
          && health.token_valid
        );
        const capiActive = Boolean(health.capi_enabled);
        const results = [
          leadAdsActive ? "Lead Ads conectado" : "Lead Ads não ativado",
          capiActive ? "CAPI ativa" : "CAPI não ativada",
          whatsappReady
            ? "WhatsApp Oficial conectado"
            : whatsappEnabled
              ? "WhatsApp Oficial com configuração pendente"
              : "WhatsApp Oficial não ativado",
        ];
        setValidationInfo(`Verificação concluída: ${results.join("; ")}.`);
        return;
      }

      const validation = await validateEvolutionConnection(instanceId);
      if (!validation.ok) {
        setError(validation.detail ?? "Erro ao validar a conexão da Evolution.");
        return;
      }

      if (validation.connected) {
        setSaved(true);
        setValidationInfo(`Conexão Evolution validada: ${validation.evo_instance_name || instanceId} está online.`);
        setTimeout(onClose, 1100);
        return;
      }

      if (validation.qr_code) {
        const qr = validation.qr_code.startsWith("data:") ? validation.qr_code : `data:image/png;base64,${validation.qr_code}`;
        setSaved(true);
        setValidationInfo(`Reconexão iniciada para ${validation.evo_instance_name || instanceId}. Escaneie o novo QR abaixo.`);
        setQrPreview(qr);
        return;
      }

      setSaved(true);
      setValidationInfo(`Credenciais salvas, mas a Evolution não retornou QR para ${validation.evo_instance_name || instanceId}.`);
    } catch {
      setError(form.provider === "meta" ? "Erro ao validar a conexão da Meta." : "Erro ao validar a conexão da Evolution.");
    } finally {
      setValidating(false);
    }
  };

  const inputClass = "w-full bg-[#F0EDE8] border border-[#D4CFC9] rounded-xl px-4 py-2.5 text-sm text-[#1A1410] focus:outline-none focus:border-[#CC3300] transition-colors";
  const secretInputClass = `${inputClass} secret-input`;

  return (
    <div className="fixed inset-0 bg-black/70 z-[200] flex items-center justify-center p-4">
      <div className="bg-[#E8E4DF] rounded-2xl shadow-2xl w-full max-w-2xl border border-[#D4CFC9] max-h-[90vh] overflow-y-auto">
        <div className="flex items-center justify-between px-5 py-4 border-b border-[#D4CFC9]">
          <h2 className="text-[#1A1410] font-bold text-base">Editar Credenciais — {instanceId}</h2>
          <button onClick={onClose} aria-label="Fechar" className="text-[#6B5E55] hover:text-[#1A1410] text-xl leading-none">✕</button>
        </div>

        {loading ? (
          <div className="p-6 text-[#6B5E55] text-sm">Carregando...</div>
        ) : (
          <div className="p-5 space-y-4">

            {/* Status de ativação Meta */}
            {activationStatus && (
              <div className="rounded-2xl border border-[#D4CFC9] bg-[#F0EDE8] p-4 space-y-3">
                <p className="text-xs font-semibold uppercase tracking-wide text-[#1A1410]">Status de ativação Meta</p>
                <div className="space-y-2 text-sm">
                  <div className="flex items-center gap-2">
                    <span className={activationStatus.token_valid ? "text-[#CC3300]" : "text-[#F87171]"}>
                      {activationStatus.token_valid ? "✓" : "✗"}
                    </span>
                    <span className="text-[#1A1410]">Token de acesso</span>
                    {activationStatus.token_name && <span className="text-[#6B5E55]">({activationStatus.token_name})</span>}
                    {activationStatus.token_error && <span className="text-[#F87171] text-xs">{safeApiMsg(activationStatus.token_error)}</span>}
                  </div>
                  <div className="flex items-center gap-2">
                    <span className={activationStatus.page_subscribed ? "text-[#CC3300]" : "text-[#F0B429]"}>
                      {activationStatus.page_subscribed ? "✓" : "⚠"}
                    </span>
                    <span className="text-[#1A1410]">Lead Ads subscritos</span>
                    {activationStatus.page_subscribed_error && <span className="text-[#F0B429] text-xs">{safeApiMsg(activationStatus.page_subscribed_error)}</span>}
                  </div>
                  <div className="flex items-center gap-2">
                    <span className={
                      activationStatus.capi_token_valid
                        ? "text-[#CC3300]"
                        : activationStatus.capi_auto_enabled
                          ? "text-[#F87171]"
                          : "text-[#6B5E55]"
                    }>
                      {activationStatus.capi_token_valid ? "✓" : activationStatus.capi_auto_enabled ? "✗" : "—"}
                    </span>
                    <span className="text-[#1A1410]">Conversions API</span>
                    {activationStatus.capi_token_error && (
                      <span className="text-[#F87171] text-xs">{safeApiMsg(activationStatus.capi_token_error)}</span>
                    )}
                    {!activationStatus.capi_auto_enabled && !activationStatus.capi_token_error && (
                      <span className="text-[#6B5E55] text-xs">preencha Event Source ID e Access Token CAPI</span>
                    )}
                  </div>
                </div>
                <button onClick={onClose} className="w-full mt-2 py-2 rounded-xl text-sm font-semibold bg-[#CC3300] text-[#F0EDE8] hover:bg-[#991F00] transition-colors">
                  Fechar
                </button>
              </div>
            )}

            <div className="grid gap-4">
              <MetaIntegrationCard instanceId={instanceId} provider={form.provider} />

              <div className="rounded-2xl border border-[#D4CFC9] bg-[#F0EDE8]">
                <button
                  type="button"
                  onClick={() => setShowEvolutionBlock((v) => !v)}
                  className="w-full flex items-center justify-between px-4 py-3 text-left"
                >
                  <div>
                    <p className="text-xs font-semibold uppercase tracking-wide text-[#CC3300]">Evolution GO</p>
                    <p className="text-xs text-[#6B5E55] mt-0.5">Clique para {showEvolutionBlock ? "ocultar" : "ver"} credenciais</p>
                  </div>
                  <span className="text-[#6B5E55] text-lg">{showEvolutionBlock ? "▲" : "▼"}</span>
                </button>
                {showEvolutionBlock && (
                  <div className="px-4 pb-4 space-y-4 border-t border-[#D4CFC9] pt-4">
                    <p className="text-xs text-[#6B5E55]">Use a API pública da Evolution, não a URL do painel /manager.</p>
                    <HelpField
                      label="Evolution GO API URL"
                      helpTitle="Onde pegar"
                      helpText="Use o domínio do Evolution GO configurado na instalação da sua VPS."
                    >
                      <input
                        type="text"
                        placeholder={EVOLUTION_API_URL}
                        value={form.api_url}
                        onChange={(e) => setForm({ ...form, api_url: e.target.value })}
                        className={inputClass}
                      />
                    </HelpField>
                    <HelpField
                      label="Evolution Instance Token"
                      helpTitle="Onde pegar"
                      helpText="Esse token vem da própria Evolution GO, no cadastro da instância. Se você recriar a instância na Evolution, esse valor muda e precisa ser substituído no KLEAD."
                      badge="seu token"
                    >
                      <input type="text" autoComplete="off" spellCheck={false} data-lpignore="true" data-form-type="other" value={form.api_key} onChange={(e) => setForm({ ...form, api_key: e.target.value })} className={secretInputClass} />
                    </HelpField>
                    <HelpField
                      label="Evolution Instance Name"
                      helpTitle="Onde pegar"
                      helpText="É o nome da instância no Evolution GO. Se você deixar vazio, o KLEAD usa o nome da instância do dashboard."
                    >
                      <input type="text" placeholder={instanceId} value={form.evo_instance_name} onChange={(e) => setForm({ ...form, evo_instance_name: e.target.value })} className={inputClass} />
                    </HelpField>
                  </div>
                )}
              </div>

              <div className="rounded-2xl border border-[#D4CFC9] bg-[#F0EDE8]">
                <button
                  type="button"
                  onClick={() => setShowMetaManualBlock((v) => !v)}
                  className="w-full flex items-center justify-between px-4 py-3 text-left"
                >
                  <div>
                    <p className="text-xs font-semibold uppercase tracking-wide text-[#CC3300]">Configuração manual avançada</p>
                    <p className="text-xs text-[#6B5E55] mt-0.5">Cole credenciais Meta diretamente, sem usar o OAuth acima. Clique para {showMetaManualBlock ? "ocultar" : "ver"}.</p>
                  </div>
                  <span className="text-[#6B5E55] text-lg">{showMetaManualBlock ? "▲" : "▼"}</span>
                </button>
                {showMetaManualBlock && (
                <div className="px-4 pb-4 space-y-4 border-t border-[#D4CFC9] pt-4">
                <p className="text-xs text-[#6B5E55]">Esses campos servem para WhatsApp Cloud API, Lead Ads e Conversions API.</p>
                <HelpField
                  label="Token de Acesso Meta"
                  helpTitle="Como gerar (token permanente)"
                  helpText="Como gerar o token permanente no Meta Business Manager:&#10;&#10;1. Acesse business.facebook.com&#10;2. Menu: Configurações → Usuários → Usuários do Sistema&#10;3. Selecione o usuário (ou crie um novo com função Admin)&#10;4. Clique em 'Gerar novo token'&#10;5. Selecione o seu App Meta&#10;6. Na lista de permissões, ative:&#10;   • whatsapp_business_messaging&#10;   • leads_retrieval&#10;   • pages_show_list&#10;   • pages_manage_metadata&#10;7. Clique em Gerar token e copie o valor&#10;&#10;Este token não expira enquanto o Usuário do Sistema existir.&#10;&#10;Dica: pages_show_list e pages_manage_metadata ficam na seção 'Páginas' da lista de permissões."
                  badge="obrigatório"
                >
                  <input type="text" autoComplete="off" spellCheck={false} data-lpignore="true" data-form-type="other" placeholder="EAAxxxxxxxxx..." value={form.meta_token} onChange={(e) => setForm({ ...form, meta_token: e.target.value })} className={secretInputClass} />
                </HelpField>
                <HelpField
                  label="App Secret"
                  helpTitle="Como pegar"
                  helpText="1. Acesse developers.facebook.com&#10;2. Selecione seu app no menu superior&#10;3. Menu lateral → Configurações → Básico&#10;4. Campo 'Chave Secreta do Aplicativo' → clique em Mostrar&#10;5. Copie o valor&#10;&#10;Usado para validar a assinatura dos webhooks recebidos da Meta."
                  badge="obrigatório"
                >
                  <input type="text" autoComplete="off" spellCheck={false} data-lpignore="true" data-form-type="other" placeholder="Cole o App Secret aqui" value={form.meta_app_secret} onChange={(e) => setForm({ ...form, meta_app_secret: e.target.value })} className={secretInputClass} />
                </HelpField>
                <HelpField
                  label="Phone Number ID"
                  helpTitle="Como pegar"
                  helpText="1. Acesse developers.facebook.com&#10;2. Selecione seu app&#10;3. Menu lateral → WhatsApp → Configuração de API&#10;4. Seção 'Número de telefone' → copie o 'ID do número de telefone'&#10;&#10;Atenção: é o ID numérico do número na API, não o número de telefone em si."
                  badge="whatsapp"
                >
                  <input type="text" placeholder="123456789012345" value={form.phone_number_id} onChange={(e) => setForm({ ...form, phone_number_id: e.target.value })} className={inputClass} />
                </HelpField>
                <HelpField
                  label="Nome do Template"
                  helpTitle="Quando usar"
                  helpText="Preencha apenas se precisar enviar mensagens fora da janela de 24h e já tiver um template aprovado na Meta.&#10;&#10;Se vazio, o KLEAD envia texto livre (só funciona dentro da janela de 24h após o último contato do cliente).&#10;&#10;Para ver seus templates: developers.facebook.com → WhatsApp → Modelos de mensagem."
                >
                  <input type="text" placeholder="ex: hello_world" value={form.template_name} onChange={(e) => setForm({ ...form, template_name: e.target.value })} className={inputClass} />
                </HelpField>
                <HelpField
                  label="Page ID"
                  helpTitle="Como pegar"
                  helpText="É o ID da sua Página do Facebook que tem os formulários de Lead Ads.&#10;&#10;1. Acesse developers.facebook.com → Ferramentas → Graph API Explorer&#10;2. Execute: /me/accounts&#10;3. Na lista de páginas, copie o 'id' da página correta&#10;&#10;Usado para rotear os leads recebidos via webhook para esta instância."
                  badge="lead ads"
                >
                  <input type="text" placeholder="ex: 123456789012345" value={form.meta_page_id} onChange={(e) => setForm({ ...form, meta_page_id: e.target.value })} className={inputClass} />
                </HelpField>
                <HelpField
                  label="Ad Account ID"
                  helpTitle="Como pegar"
                  helpText="1. Acesse business.facebook.com&#10;2. Configurações → Contas de anúncios&#10;3. Copie o número da conta (ex: 503804883944812)&#10;&#10;Cole só o número, sem 'act_'. O KLEAD monta o formato correto automaticamente.&#10;Usado para exibir métricas de anúncios no painel."
                  badge="ads"
                >
                  <input type="text" placeholder="ex: 123456789" value={form.meta_ad_account_id} onChange={(e) => setForm({ ...form, meta_ad_account_id: stripActPrefix(e.target.value) })} className={inputClass} />
                </HelpField>
                <div className="rounded-xl border border-[#D4CFC9] bg-[#F0EDE8] p-4 space-y-3">
                  <HelpField
                    label="Conversions API"
                    helpTitle="O que é"
                    helpText="Quando ativado, o KLEAD envia eventos do CRM de volta para a Meta (ex: Lead, Contact, QualifiedLead).&#10;&#10;Isso melhora a otimização das campanhas de anúncios e permite rastrear a qualidade dos leads dentro do Gerenciador de Anúncios.&#10;&#10;Só ative se já tiver um Pixel ou Dataset configurado no Events Manager."
                    badge="retorno"
                  >
                    <label className="flex items-center gap-3 text-sm text-[#1A1410]">
                      <input
                        type="checkbox"
                        checked={form.meta_capi_enabled}
                        onChange={(e) => setForm({ ...form, meta_capi_enabled: e.target.checked })}
                        className="h-4 w-4 rounded border-[#D4CFC9] bg-[#F0EDE8] accent-[#CC3300]"
                      />
                      Enviar eventos do CRM para a Meta
                    </label>
                  </HelpField>
                  <HelpField
                    label="Event Source ID"
                    helpTitle="Como pegar"
                    helpText="1. Acesse business.facebook.com → Events Manager&#10;2. Selecione o Pixel ou Dataset que vai receber os eventos&#10;3. Copie o ID numérico exibido&#10;&#10;Pode ser o mesmo ID da sua Página se estiver usando um Dataset vinculado."
                    badge="capi"
                  >
                    <input type="text" placeholder="ex: 123456789012345" value={form.meta_capi_event_source_id} onChange={(e) => setForm({ ...form, meta_capi_event_source_id: e.target.value })} className={inputClass} />
                  </HelpField>
                  <HelpField
                    label="Access Token CAPI"
                    helpTitle="Como gerar"
                    helpText="1. Acesse business.facebook.com → Events Manager&#10;2. Selecione o Pixel/Dataset&#10;3. Aba 'Configurações' → seção 'Conversions API'&#10;4. Clique em 'Gerar token de acesso'&#10;&#10;Este token é específico para envio de eventos e é diferente do Token de Acesso Meta principal."
                    badge="capi"
                  >
                    <input type="text" autoComplete="off" spellCheck={false} data-lpignore="true" data-form-type="other" placeholder="EAAxxxxxxxxx..." value={form.meta_capi_access_token} onChange={(e) => setForm({ ...form, meta_capi_access_token: e.target.value })} className={secretInputClass} />
                  </HelpField>
                  <HelpField
                    label="Test Event Code"
                    helpTitle="Quando usar"
                    helpText="1. Acesse business.facebook.com → Events Manager&#10;2. Selecione o Pixel/Dataset → aba 'Testar eventos'&#10;3. Copie o código exibido (ex: TEST12345)&#10;&#10;Use apenas durante testes para validar a integração. Remova ao ir para produção."
                    badge="opcional"
                  >
                    <input type="text" placeholder="opcional" value={form.meta_capi_test_event_code} onChange={(e) => setForm({ ...form, meta_capi_test_event_code: e.target.value })} className={inputClass} />
                  </HelpField>
                </div>
                </div>
                )}
              </div>

              {/* Card: URLs de Webhook Meta */}
              {webhookUrls && (
                <div className="rounded-2xl border border-[#30363D] bg-[#F0EDE8]">
                  <button
                    type="button"
                    onClick={() => setShowWebhookUrlsBlock((v) => !v)}
                    className="w-full flex items-center justify-between px-4 py-3 text-left"
                  >
                    <div>
                      <p className="text-xs font-semibold uppercase tracking-wide text-[#F0B429]">URLs de Webhook — configure no Meta</p>
                      <p className="text-xs text-[#6B5E55] mt-0.5">Clique para {showWebhookUrlsBlock ? "ocultar" : "ver"}. Cole cada URL no lugar indicado no Meta for Developers.</p>
                    </div>
                    <span className="text-[#6B5E55] text-lg">{showWebhookUrlsBlock ? "▲" : "▼"}</span>
                  </button>
                  {showWebhookUrlsBlock && (
                  <div className="px-4 pb-4 space-y-4 border-t border-[#D4CFC9] pt-4">

                  {/* Verify Token */}
                  <div>
                    <p className="text-xs font-semibold text-[#6B5E55] uppercase tracking-wide mb-1">Verify Token <span className="normal-case font-normal text-[#6B5E55]">— usado nos dois webhooks abaixo</span></p>
                    <div className="flex gap-2">
                      <code className="flex-1 bg-[#E8E4DF] border border-[#D4CFC9] rounded-xl px-3 py-2 text-xs text-[#F0B429] font-mono break-all">{webhookUrls.verify_token}</code>
                      <button onClick={() => { navigator.clipboard.writeText(webhookUrls.verify_token); setWebhookCopied("token"); setTimeout(() => setWebhookCopied(null), 2000); }} className="shrink-0 border border-[#D4CFC9] text-[#6B5E55] hover:text-[#1A1410] px-3 py-2 rounded-xl text-xs transition-colors">{webhookCopied === "token" ? "✓" : "Copiar"}</button>
                    </div>
                  </div>

                  {/* Webhook 1: WhatsApp mensagens */}
                  <div className="rounded-xl border border-[#D4CFC9] bg-[#E8E4DF] p-3 space-y-2">
                    <span className="text-xs font-bold text-[#CC3300]">① WhatsApp — Mensagens recebidas</span>
                    <p className="text-xs text-[#6B5E55]">Configure em: <span className="text-[#1A1410]">Meta for Developers → app → WhatsApp → Configuração → Webhook</span></p>
                    <p className="text-xs text-[#6B5E55]">Campo a assinar: <code className="text-[#CC3300]">messages</code></p>
                    <div className="flex gap-2 mt-1">
                      <code className="flex-1 bg-[#F0EDE8] border border-[#D4CFC9] rounded-xl px-3 py-2 text-xs text-[#CC3300] font-mono break-all">{webhookUrls.waba_url}</code>
                      <button onClick={() => { navigator.clipboard.writeText(webhookUrls.waba_url); setWebhookCopied("waba"); setTimeout(() => setWebhookCopied(null), 2000); }} className="shrink-0 border border-[#D4CFC9] text-[#6B5E55] hover:text-[#1A1410] px-3 py-2 rounded-xl text-xs transition-colors">{webhookCopied === "waba" ? "✓" : "Copiar"}</button>
                    </div>
                  </div>

                  {/* Webhook 2: Lead Ads */}
                  <div className="rounded-xl border border-[#D4CFC9] bg-[#E8E4DF] p-3 space-y-2">
                    <span className="text-xs font-bold text-[#58A6FF]">② Lead Ads — Formulários de anúncios</span>
                    <p className="text-xs text-[#6B5E55]">Configure em: <span className="text-[#1A1410]">Meta for Developers → Webhooks → Page → campo leadgen</span></p>
                    <p className="text-xs text-[#6B5E55]">Campo a assinar: <code className="text-[#CC3300]">leadgen</code></p>
                    <div className="flex gap-2 mt-1">
                      <code className="flex-1 bg-[#F0EDE8] border border-[#D4CFC9] rounded-xl px-3 py-2 text-xs text-[#CC3300] font-mono break-all">{webhookUrls.leads_url}</code>
                      <button onClick={() => { navigator.clipboard.writeText(webhookUrls.leads_url); setWebhookCopied("leads"); setTimeout(() => setWebhookCopied(null), 2000); }} className="shrink-0 border border-[#D4CFC9] text-[#6B5E55] hover:text-[#1A1410] px-3 py-2 rounded-xl text-xs transition-colors">{webhookCopied === "leads" ? "✓" : "Copiar"}</button>
                    </div>
                  </div>
                  </div>
                  )}
                </div>
              )}
            </div>

            {error && <p className="text-sm text-[#F87171] bg-[#2D0A0A] border border-[#F87171]/30 rounded-xl px-3 py-2">{error}</p>}
            {validationInfo && <p className="text-sm text-[#D1FAE5] bg-[#3D0A00] border border-[#CC3300]/20 rounded-xl px-3 py-2">{validationInfo}</p>}
            {qrPreview && (
              <div className="rounded-2xl border border-[#D4CFC9] bg-[#F0EDE8] p-4 space-y-3">
                <div>
                  <p className="text-xs font-semibold uppercase tracking-wide text-[#CC3300]">QR de reconexão</p>
                  <p className="text-xs text-[#6B5E55]">Escaneie este QR no WhatsApp do celular para religar a instância.</p>
                </div>
                <div className="flex justify-center">
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img src={qrPreview} alt="QR Code da Evolution" className="w-56 h-56 object-contain bg-white rounded-xl p-3" />
                </div>
              </div>
            )}
            {/* Badge FOC */}
            {form.meta_capi_enabled && form.meta_capi_event_source_id && (
              <div className="rounded-2xl border border-[#CC3300]/30 bg-[#3D0A00] px-4 py-3">
                <div className="flex items-center justify-between mb-2">
                  <span className="text-xs font-bold uppercase tracking-widest text-[#CC3300]">● FOC Ativo</span>
                  <span className="text-xs text-[#6B5E55]">Funil de Otimização CAPI</span>
                </div>
                <div className="flex items-center gap-1 flex-wrap text-xs text-[#6B5E55]">
                  {["Lead","Contact","SubmitApplication","Schedule","Purchase"].map((e, i, arr) => (
                    <span key={e} className="flex items-center gap-1">
                      <span className="text-[#CC3300] font-medium">{e}</span>
                      {i < arr.length - 1 && <span>→</span>}
                    </span>
                  ))}
                </div>
              </div>
            )}

            {/* Painel de logs CAPI */}
            {capiLogs.length > 0 && (
              <div className="rounded-2xl border border-[#D4CFC9] bg-[#F0EDE8]">
                <button
                  onClick={() => setShowCapiLogs(v => !v)}
                  className="w-full flex items-center justify-between px-4 py-3 text-xs font-semibold text-[#6B5E55] hover:text-[#1A1410] transition-colors"
                >
                  <span className="uppercase tracking-wide">Últimos eventos CAPI ({capiLogs.length})</span>
                  <span>{showCapiLogs ? "▲" : "▼"}</span>
                </button>
                {showCapiLogs && (
                  <div className="px-4 pb-4 space-y-2 max-h-64 overflow-y-auto">
                    {capiLogs.map((log, i) => {
                      const ud = ((log.payload as Record<string, unknown>)?.data as Record<string, unknown>[])?.[0]?.user_data as Record<string, unknown> | undefined;
                      const fields = ud ? Object.keys(ud) : [];
                      return (
                        <div key={i} className={`rounded-xl border px-3 py-2 text-xs ${log.ok ? "border-[#CC3300]/20 bg-[#3D0A00]" : "border-[#F87171]/20 bg-[#2D0A0A]"}`}>
                          <div className="flex items-center justify-between gap-2">
                            <span className={`font-semibold ${log.ok ? "text-[#CC3300]" : "text-[#F87171]"}`}>
                              {log.ok ? "✓" : "✗"} {log.event_name}
                            </span>
                            <div className="flex items-center gap-2">
                              {log.credential_source && (
                                <span className={`px-1.5 py-0.5 rounded text-[10px] font-semibold uppercase tracking-wide ${log.credential_source === "oauth" ? "bg-[#1447E6]/20 text-[#6C8CFF]" : "bg-[#6B5E55]/20 text-[#6B5E55]"}`}>
                                  {log.credential_source === "oauth" ? "OAuth" : "Manual"}
                                </span>
                              )}
                              <span className="text-[#6B5E55]">{log.timestamp?.slice(0, 16).replace("T", " ")}</span>
                            </div>
                          </div>
                          <div className="text-[#6B5E55] mt-0.5">
                            {log.lead_nome || log.lead_telefone || "—"}
                          </div>
                          {fields.length > 0 && (
                            <div className="text-[#A7F3D0] mt-0.5">{fields.join(", ")}</div>
                          )}
                          {!log.ok && log.detail && (
                            <div className="text-[#F87171] mt-0.5 truncate">{log.detail}</div>
                          )}
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>
            )}

            <div className="flex gap-3 pt-1">
              <button onClick={onClose} className="flex-1 border border-[#D4CFC9] text-[#6B5E55] hover:text-[#1A1410] font-medium py-2.5 rounded-xl text-sm transition-colors">
                Cancelar
              </button>
              <button
                onClick={handleSave}
                disabled={saving || saved || validating}
                className="flex-1 bg-[#D4CFC9] hover:bg-[#30363D] disabled:bg-[#D4CFC9]/60 text-[#1A1410] font-bold py-2.5 rounded-xl text-sm transition-colors"
              >
                {saved ? "Salvo!" : saving ? "Salvando..." : "Salvar"}
              </button>
              <button
                onClick={handleValidate}
                disabled={saving || validating}
                className="flex-1 bg-[#CC3300] hover:bg-[#991F00] disabled:bg-[#CC3300]/40 text-[#F0EDE8] font-bold py-2.5 rounded-xl text-sm transition-colors"
              >
                {validating ? "Validando..." : form.provider === "meta" ? "Salvar e Validar Meta" : "Salvar e Validar Evolution"}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

// ── Modal de Preview de Campanha ───────────────────────────────────────────────

function CampaignPreviewModal({
  instanceId,
  context,
  onApprove,
  onCancel,
}: {
  instanceId: string;
  context: string;
  onApprove: () => void;
  onCancel: () => void;
}) {
  const [previews, setPreviews] = useState<{ name: string; phone: string; message: string }[]>([]);
  const [loading, setLoading]   = useState(false);
  const [error, setError]       = useState("");

  const loadPreviews = useCallback(async () => {
    setLoading(true); setError("");
    try {
      const res = await previewPrompt(instanceId, context, 5);
      if (res.ok) setPreviews(res.previews ?? []);
      else setError(res.detail ?? "Erro ao gerar prévia.");
    } catch {
      setError("Erro de conexão com o backend.");
    } finally {
      setLoading(false);
    }
  }, [context, instanceId]);

  useEffect(() => { loadPreviews(); }, [loadPreviews]);

  const now = new Date().toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" });

  return (
    <div className="fixed inset-0 bg-black/75 z-50 flex items-center justify-center p-4">
      <div className="bg-[#E8E4DF] rounded-2xl shadow-2xl w-full max-w-lg border border-[#D4CFC9] flex flex-col overflow-hidden" style={{ maxHeight: "88vh" }}>

        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-[#D4CFC9] shrink-0">
          <div>
            <h2 className="text-[#1A1410] font-bold text-base">Prévia da Campanha</h2>
            <p className="text-[#6B5E55] text-xs mt-0.5">
              {loading ? "Gerando..." : `${previews.length} mensagens geradas com contatos reais`}
            </p>
          </div>
          <button onClick={onCancel} aria-label="Fechar prévia" className="text-[#6B5E55] hover:text-[#1A1410] text-xl leading-none transition-colors">✕</button>
        </div>

        {/* Messages area */}
        <div
          className="flex-1 min-h-0 overflow-y-auto p-4 space-y-5 bg-[#0B141A]"
        >
          {loading ? (
            <div className="py-12 text-center">
              <div className="inline-block w-8 h-8 border-2 border-[#CC3300]/30 border-t-[#CC3300] rounded-full animate-spin mb-3" />
              <p className="text-[#6B5E55] text-sm">Gerando mensagens personalizadas...</p>
            </div>
          ) : error ? (
            <div className="py-12 text-center">
              <p className="text-[#F87171] text-sm">{error}</p>
            </div>
          ) : (
            previews.map((p, i) => (
              <div key={i} className="space-y-1.5">
                {/* Contact info */}
                <div className="flex items-center gap-2 px-1">
                  <div className="w-7 h-7 rounded-full bg-[#0E0E18] flex items-center justify-center text-[#CC3300] text-xs font-bold">
                    {(p.name || "?").charAt(0).toUpperCase()}
                  </div>
                  <span className="text-[#6B5E55] text-xs font-medium">
                    {p.name || "Contato"}
                    {p.phone ? <span className="text-[#6B5E55]/60"> · {p.phone}</span> : null}
                  </span>
                </div>
                {/* WhatsApp bubble */}
                <div className="flex justify-end">
                  <div className="bg-[#DCF8C6] rounded-[18px_18px_4px_18px] px-4 py-2.5 max-w-[85%] shadow-sm">
                    <p className="text-[#F0EDE8] text-sm whitespace-pre-wrap leading-relaxed">{p.message}</p>
                    <div className="flex items-center justify-end gap-1 mt-1.5">
                      <span className="text-[#667781] text-[10px]">{now}</span>
                      <svg viewBox="0 0 16 11" className="w-4 h-3 fill-[#53BDEB]">
                        <path d="M11.071.653a.75.75 0 0 0-1.06 1.06L11.071.653zm-7.43 5.963.53-.53.001.001-.53.53zm1.44 1.44-.53.53-.001-.001.53-.529zm6.52-6.343-7.5 7.5-1.06-1.06 7.5-7.5 1.06 1.06zm-8.56 7.5-.97-.97 1.06-1.06.97.97-1.06 1.06zm1.06 0a1.5 1.5 0 0 1-2.12 0l1.06-1.06a.75.75 0 0 0 1.06 0l1.06 1.06z"/>
                        <path d="M14.571.653a.75.75 0 0 0-1.06 1.06L14.571.653zm-7.5 7.5-.53.53-.001-.001.53-.53zm1.5 0 .53.53-.001.001-.53-.53z"/>
                      </svg>
                    </div>
                  </div>
                </div>
              </div>
            ))
          )}
        </div>

        {/* Actions */}
        <div className="px-5 py-4 border-t border-[#D4CFC9] flex gap-2 shrink-0">
          <button
            onClick={loadPreviews}
            disabled={loading}
            className="flex-1 border border-[#D4CFC9] text-[#6B5E55] hover:text-[#1A1410] hover:border-[#3D4F5A] font-medium py-2.5 rounded-xl text-sm transition-colors disabled:opacity-40"
          >
            Regenerar
          </button>
          <button
            onClick={onCancel}
            className="px-4 border border-[#D4CFC9] text-[#6B5E55] hover:text-[#1A1410] hover:border-[#3D4F5A] font-medium py-2.5 rounded-xl text-sm transition-colors"
          >
            Cancelar
          </button>
          <button
            onClick={onApprove}
            disabled={loading || previews.length === 0}
            className="flex-1 bg-[#CC3300] hover:bg-[#991F00] disabled:bg-[#CC3300]/30 disabled:cursor-not-allowed text-[#F0EDE8] font-bold py-2.5 rounded-xl text-sm transition-colors"
          >
            Aprovar e iniciar
          </button>
        </div>
      </div>
    </div>
  );
}

// ── NumberField ───────────────────────────────────────────────────────────────

function NumberField({
  label,
  placeholder,
  value,
  onChange,
  disabled,
}: {
  label: string;
  placeholder: string;
  value: number;
  onChange: (v: number) => void;
  disabled?: boolean;
}) {
  return (
    <div>
      <label className="block text-xs font-medium text-[#6B5E55] mb-1.5">{label}</label>
      <input
        type="number"
        min={0}
        placeholder={placeholder}
        value={value || ""}
        onChange={(e) => onChange(Number(e.target.value))}
        disabled={disabled}
        className="w-full bg-[#F0EDE8] border border-[#D4CFC9] rounded-xl px-3 py-2 text-sm text-[#1A1410] placeholder-[#6B5E55] focus:outline-none focus:border-[#CC3300] transition-colors disabled:opacity-50"
      />
    </div>
  );
}

// ── Página principal ───────────────────────────────────────────────────────────

export default function InstancePage() {
  const params = useParams();
  const id = params.id as string;

  const [status, setStatus]       = useState<Status | null>(null);
  const [logs, setLogs]           = useState<Log[]>([]);
  const [sends, setSends]         = useState<Log[]>([]);
  const [context, setContext]     = useState("");
  const [file, setFile]           = useState<File | null>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadMsg, setUploadMsg] = useState("");
  const [tab, setTab]             = useState<"contatos" | "timing" | "contexto" | "campanha" | "sdr" | "inbox">("contexto");
  // Plano Lite: sem disparador nem SDR autônomo. Lido do cookie klead_plano
  // (não-httpOnly) após montar — evita mismatch de hidratação (começa false).
  const [isLite, setIsLite] = useState(false);
  useEffect(() => {
    setIsLite(document.cookie.split("; ").some(c => c === "klead_plano=lite"));
  }, []);
  const router                    = useRouter();
  const [contactsList, setContactsList]   = useState<{ name: string; number: string }[]>([]);
  const [dailyLimit, setDailyLimit]         = useState<number>(0);
  const [useCustomTiming, setUseCustomTiming] = useState(false);
  const [hourlyLimit, setHourlyLimit]       = useState<number>(0);
  const [delayMin, setDelayMin]             = useState<number>(0);
  const [delayMax, setDelayMax]             = useState<number>(0);
  const [longPauseEvery, setLongPauseEvery] = useState<number>(0);
  const [longPauseMin, setLongPauseMin]     = useState<number>(0);
  const [longPauseMax, setLongPauseMax]     = useState<number>(0);
  const [showCreds, setShowCreds]           = useState(false);
  const [systemPrompt, setSystemPrompt]   = useState("");
  const [canManageCredentials, setCanManageCredentials] = useState(false);

  // SDR — Configuração de IA Inbound
  const [sdrEnabled, setSdrEnabled]                   = useState(false);
  const [qualificationPrompt, setQualificationPrompt] = useState<string>("");
  const [maxAiTurns, setMaxAiTurns]                   = useState(8);
  const [aiResponseDelay, setAiResponseDelay]         = useState(3);
  const [handoffKeywords, setHandoffKeywords]         = useState<string>("");
  const [sdrModel, setSdrModel]                       = useState<string>("openai/gpt-oss-120b");
  const [llmProvider, setLlmProvider]                 = useState<string>("groq");
  const [llmApiKey, setLlmApiKey]                     = useState<string>("");
  const [keyOpenai, setKeyOpenai]                     = useState<string>("");
  const [keyAnthropic, setKeyAnthropic]               = useState<string>("");
  const [keyGroq, setKeyGroq]                         = useState<string>("");
  const [followupEnabled, setFollowupEnabled]         = useState(false);
  const [followupDelayHours, setFollowupDelayHours]   = useState(24);
  const [followupMax, setFollowupMax]                 = useState(3);
  const [autoReleaseMinutes, setAutoReleaseMinutes]   = useState(3);
  const [ttsEnabled, setTtsEnabled]                   = useState(false);
  const [scoringCriteria, setScoringCriteria]         = useState("");
  const [knowledgeFiles, setKnowledgeFiles]           = useState<KnowledgeFile[]>([]);
  const [kbUploading, setKbUploading]                 = useState(false);
  const [sdrSaving, setSdrSaving]                     = useState(false);
  const [sdrSaved, setSdrSaved]                       = useState(false);
  const [sdrSaveError, setSdrSaveError]               = useState(false);
  const [promptSaving, setPromptSaving]   = useState(false);
  const [promptSaved, setPromptSaved]     = useState(false);
  const [promptSaveError, setPromptSaveError] = useState(false);

  const [showQrCodeModal, setShowQrCodeModal] = useState(false);
  const [qrCodeData, setQrCodeData] = useState("");
  const [qrCodeError, setQrCodeError] = useState("");

  const [webhookInfo, setWebhookInfo]     = useState<{ webhook_url: string; fix_command: string } | null>(null);
  const [webhookCopied, setWebhookCopied] = useState(false);
  const [showWebhookMeta, setShowWebhookMeta]       = useState(false);
  const [showCloudflareWorker, setShowCloudflareWorker] = useState(false);
  const [showWebhookEvo, setShowWebhookEvo]         = useState(false);
  const [showContextSystemPrompt, setShowContextSystemPrompt] = useState(false);
  const [showQualificationSettings, setShowQualificationSettings] = useState(false);
  const [showFollowupSettings, setShowFollowupSettings] = useState(false);
  const [showLlmSettings, setShowLlmSettings] = useState(false);
  const [showScoringSettings, setShowScoringSettings] = useState(false);
  const [showKnowledgeSettings, setShowKnowledgeSettings] = useState(false);
  const [showWaAdvanced, setShowWaAdvanced]         = useState(false);
  const [showWebhookEvents, setShowWebhookEvents]   = useState(false);
  const [showDangerZone, setShowDangerZone]         = useState(false);
  const [deletingInstance, setDeletingInstance]     = useState(false);
  const [metaWebhookInfo, setMetaWebhookInfo]   = useState<{ webhook_url: string; verify_token: string; page_id: string } | null>(null);
  const [metaCopied, setMetaCopied]             = useState<"url" | "token" | "page" | null>(null);
  const [rotatingSecret, setRotatingSecret]     = useState(false);

  // WhatsApp Info (número + perfil)
  const [waInfo, setWaInfo] = useState<{ connected: boolean; number: string; profile_name: string } | null>(null);
  const [reconnecting, setReconnecting] = useState(false);

  // Webhook Events
  const ALL_WEBHOOK_EVENTS = ["ALL","MESSAGE","READ_RECEIPT","PRESENCE","HISTORY_SYNC","CHAT_PRESENCE","CALL","CONNECTION","QRCODE","LABEL","CONTACT","GROUP","NEWSLETTER"] as const;
  const [webhookEvents, setWebhookEvents]         = useState<string[]>(["MESSAGE"]);
  const [webhookEventsSaving, setWebhookEventsSaving] = useState(false);
  const [webhookEventsSaved, setWebhookEventsSaved]   = useState(false);

  // WhatsApp Advanced Settings (Evolution GO)
  const [waSettings, setWaSettings] = useState({
    alwaysOnline:    true,
    rejectCall:      true,
    msgCall:         "",
    readMessages:    true,
    readStatus:      true,
    groupsIgnore:    true,
    syncFullHistory: true,
  });
  const [waSettingsSaving, setWaSettingsSaving]   = useState(false);
  const [waSettingsSaved, setWaSettingsSaved]     = useState(false);
  const [waSettingsError, setWaSettingsError]     = useState("");

  // Refs para controlar auto-save (evita salvar no carregamento inicial)
  const promptLoadedRef  = useRef(false);
  const sdrLoadedRef     = useRef(false);
  const timingLoadedRef  = useRef(false);
  const [queue, setQueue]                 = useState<QueueItem[]>([]);
  const [queueStatus, setQueueStatus]     = useState<"idle" | "generating" | "ready">("idle");
  const [queueProgress, setQueueProgress] = useState(0);
  const [queueTotal, setQueueTotal]       = useState(0);
  const [editingIndex, setEditingIndex]   = useState<number | null>(null);
  const [editingText, setEditingText]     = useState("");
  const logsEndRef       = useRef<HTMLDivElement>(null);
  const logsContainerRef = useRef<HTMLDivElement>(null);

  // Status WhatsApp
  const [waState, setWaState] = useState<"open" | "close" | "connecting" | "unknown">("unknown");

  // Modal de preview de campanha
  const [showCampaignPreview, setShowCampaignPreview]   = useState(false);
  const [pendingAction, setPendingAction]               = useState<"direct" | "queue" | null>(null);

  // Microfone para contexto
  const [isRecording, setIsRecording] = useState(false);
  const recognitionRef = useRef<{ stop: () => void; abort: () => void } | null>(null);

  const handleToggleMic = () => {
    if (isRecording) {
      recognitionRef.current?.stop();
      return;
    }
    const speechWindow = window as Window & {
      SpeechRecognition?: SpeechRecognitionConstructor;
      webkitSpeechRecognition?: SpeechRecognitionConstructor;
    };
    const SR = speechWindow.SpeechRecognition || speechWindow.webkitSpeechRecognition;
    if (!SR) { alert("Seu navegador não suporta reconhecimento de voz."); return; }
    const rec = new SR();
    rec.lang = "pt-BR";
    rec.continuous = true;
    rec.interimResults = false;
    rec.onresult = (e) => {
      const transcript = Array.from(e.results)
        .slice(e.resultIndex)
        .map((r) => r[0].transcript)
        .join(" ");
      setContext((prev) => { setInlinePreviews([]); return prev ? prev + " " + transcript : transcript; });
    };
    rec.onerror = () => { setIsRecording(false); };
    rec.onend   = () => { setIsRecording(false); };
    rec.start();
    recognitionRef.current = rec;
    setIsRecording(true);
  };

  // Formulário de teste inline (dentro do card de exemplos)
  const [testName, setTestName]     = useState("");
  const [testPhone, setTestPhone]   = useState("");
  const [testLoading, setTestLoading] = useState(false);
  const [testResult, setTestResult] = useState<{ ok: boolean; message?: string; detail?: string } | null>(null);
  const [inlinePreviews, setInlinePreviews]         = useState<{ name: string; phone: string; message: string }[]>([]);
  const [inlinePreviewLoading, setInlinePreviewLoading] = useState(false);
  const [inlinePreviewError, setInlinePreviewError]   = useState("");
  const [contextoPreviewMsgs, setContextoPreviewMsgs]   = useState<string[]>([]);
  const [contextoPreviewLoading, setContextoPreviewLoading] = useState(false);

  // Import do CRM
  const [showImportCrm, setShowImportCrm]   = useState(false);
  const [crmListas, setCrmListas]           = useState<CrmLista[]>([]);
  const [loadingListas, setLoadingListas]   = useState(false);
  const [importingLista, setImportingLista] = useState(false);

  const handleOpenImportCrm = async () => {
    setShowImportCrm(true);
    setLoadingListas(true);
    try {
      const listas = await crmGetListas();
      setCrmListas(listas);
    } finally {
      setLoadingListas(false);
    }
  };

  const handleImportLista = async (lista: CrmLista) => {
    setImportingLista(true);
    try {
      const { contacts } = await crmGetListaContatos(lista.id);
      if (!contacts.length) { alert("Lista vazia."); return; }
      const lines = contacts.map((c) => `${c.name},${c.phone}`).join("\n");
      await importContactsText(id as string, lines);
      await loadContacts();
      setShowImportCrm(false);
    } finally {
      setImportingLista(false);
    }
  };

  // Auto-save contexto da campanha no backend (debounce 1.5s)
  useEffect(() => {
    if (!timingLoadedRef.current) return;
    const timer = setTimeout(() => { updateCampaignContext(id, context); }, 1500);
    return () => clearTimeout(timer);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [context]);

  // Auto-save timing no backend (debounce 1.5s)
  useEffect(() => {
    if (!timingLoadedRef.current) return;
    const timer = setTimeout(() => {
      updateCampaignContext(id, context, {
        useCustomTiming, dailyLimit, hourlyLimit,
        delayMin, delayMax, longPauseEvery, longPauseMin, longPauseMax,
      });
    }, 1500);
    return () => clearTimeout(timer);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [useCustomTiming, dailyLimit, hourlyLimit, delayMin, delayMax, longPauseEvery, longPauseMin, longPauseMax]);

  // ── Data loading ──────────────────────────────────────────────────────────

  const loadStatus   = useCallback(async () => { const s = await getStatus(id); setStatus(s); }, [id]);
  const loadLogs     = useCallback(async () => { const d = await getLogs(id);   setLogs(d.logs ?? []); }, [id]);
  const loadSends    = useCallback(async () => { const d = await getSends(id);  setSends(d.sends ?? []); }, [id]);
  const loadContacts = useCallback(async () => { const d = await listContacts(id).catch(() => null); if (d) setContactsList(d.contacts ?? []); }, [id]);
  const loadWaStatus = useCallback(async () => { const d = await getWhatsappStatus(id).catch(() => null); if (d) setWaState(d.state ?? "unknown"); }, [id]);

  const loadQueue = useCallback(async () => {
    const d = await getQueue(id).catch(() => null);
    if (!d) return;
    setQueue(d.queue ?? []);
    setQueueStatus(d.queue_status ?? "idle");
    setQueueProgress(d.queue_progress ?? 0);
    setQueueTotal(d.total_contacts ?? 0);
  }, [id]);

  useEffect(() => {
    getInstanceConfig(id).then((d) => {
      if (d?.prompt !== undefined) setSystemPrompt(d.prompt);
      if (d?.inbound_ai_enabled !== undefined) setSdrEnabled(d.inbound_ai_enabled);
      if (d?.qualification_prompt !== undefined) setQualificationPrompt(d.qualification_prompt);
      if (d?.max_ai_turns !== undefined) setMaxAiTurns(d.max_ai_turns);
      if (d?.ai_response_delay !== undefined) setAiResponseDelay(d.ai_response_delay);
      if (d?.auto_handoff_keywords !== undefined) setHandoffKeywords((d.auto_handoff_keywords ?? []).join(", "));
      if (d?.model) setSdrModel(d.model);
      if (d?.llm_provider) setLlmProvider(d.llm_provider);
      if (d?.followup_enabled !== undefined) setFollowupEnabled(d.followup_enabled);
      if (d?.followup_delay_hours !== undefined) setFollowupDelayHours(d.followup_delay_hours);
      if (d?.followup_max !== undefined) setFollowupMax(d.followup_max);
      if (d?.auto_release_minutes !== undefined) setAutoReleaseMinutes(d.auto_release_minutes);
      if (d?.tts_enabled !== undefined) setTtsEnabled(d.tts_enabled);
      if (d?.scoring_criteria !== undefined) setScoringCriteria(d.scoring_criteria);
      if (d?.campaign_context) setContext(d.campaign_context);
      if (d?.campaign_timing && Object.keys(d.campaign_timing).length > 0) {
        const t = d.campaign_timing as Record<string, number | boolean>;
        if (t.useCustomTiming !== undefined) setUseCustomTiming(!!t.useCustomTiming);
        if (t.dailyLimit      !== undefined) setDailyLimit(t.dailyLimit as number);
        if (t.hourlyLimit     !== undefined) setHourlyLimit(t.hourlyLimit as number);
        if (t.delayMin        !== undefined) setDelayMin(t.delayMin as number);
        if (t.delayMax        !== undefined) setDelayMax(t.delayMax as number);
        if (t.longPauseEvery  !== undefined) setLongPauseEvery(t.longPauseEvery as number);
        if (t.longPauseMin    !== undefined) setLongPauseMin(t.longPauseMin as number);
        if (t.longPauseMax    !== undefined) setLongPauseMax(t.longPauseMax as number);
      }
      setCanManageCredentials(!!d?.can_manage_credentials);
      promptLoadedRef.current = true;
      sdrLoadedRef.current    = true;
      timingLoadedRef.current = true;
       if (d?.can_manage_credentials) {
          getCredentials(id).then((cred) => {
            if (cred?.llm_api_key !== undefined) setLlmApiKey(cred.llm_api_key);
            if (cred?.key_openai !== undefined) setKeyOpenai(cred.key_openai);
            if (cred?.key_anthropic !== undefined) setKeyAnthropic(cred.key_anthropic);
            if (cred?.key_groq !== undefined) setKeyGroq(cred.key_groq);
          }).catch(() => {});
         getMetaWebhookInfo(id).then(setMetaWebhookInfo).catch(() => {});
         getWebhookUrl(id).then(setWebhookInfo).catch(() => {});
      } else {
        setLlmApiKey("");
        setKeyOpenai("");
        setKeyAnthropic("");
        setKeyGroq("");
        setMetaWebhookInfo(null);
        setWebhookInfo(null);
      }
    });
    listKnowledge(id).then((d) => {
      if (d?.files) setKnowledgeFiles(d.files);
    });
    const fast = setInterval(() => {
      loadStatus(); loadLogs(); loadSends();
      if (queueStatus === "generating") loadQueue();
    }, 5000);
    const slow = setInterval(loadWaStatus, 10000);
    return () => { clearInterval(fast); clearInterval(slow); };
  }, [id, queueStatus, loadLogs, loadQueue, loadSends, loadStatus, loadWaStatus]);

  useEffect(() => {
    fetch(`/api/proxy/instance/${id}/whatsapp-settings`)
      .then(r => r.ok ? r.json() : null)
      .then(d => {
        if (d?.settings && Object.keys(d.settings).length > 0) {
          setWaSettings(prev => ({ ...prev, ...d.settings }));
        }
      })
      .catch(() => {});
    fetch(`/api/proxy/instance/${id}/whatsapp-info`)
      .then(r => r.ok ? r.json() : null)
      .then(d => { if (d) setWaInfo(d); })
      .catch(() => {});
  }, [id]);

  useEffect(() => {
    if (tab === "campanha" && logsContainerRef.current) {
      logsContainerRef.current.scrollTop = logsContainerRef.current.scrollHeight;
    }
  }, [logs, tab]);

  // Auto-save: Prompt do sistema (1.5s debounce)
  useEffect(() => {
    if (!promptLoadedRef.current) return;
    setPromptSaved(false);
    setPromptSaveError(false);
    const timer = setTimeout(async () => {
      setPromptSaving(true);
      try {
        await updatePrompt(id, systemPrompt);
        setPromptSaved(true);
        setTimeout(() => setPromptSaved(false), 2500);
      } catch {
        setPromptSaveError(true);
      } finally {
        setPromptSaving(false);
      }
    }, 1500);
    return () => clearTimeout(timer);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [systemPrompt]);

  // Auto-save: Prompt de qualificação e critérios de pontuação (1.5s debounce)
  useEffect(() => {
    if (!sdrLoadedRef.current) return;
    setSdrSaved(false);
    const timer = setTimeout(() => { handleSaveSdr(); }, 1500);
    return () => clearTimeout(timer);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [qualificationPrompt, scoringCriteria]);

  // Auto-save: Configuração de IA (provedor, modelo, chaves) — 1.5s debounce
  useEffect(() => {
    if (!sdrLoadedRef.current) return;
    const timer = setTimeout(() => { handleSaveSdr(); }, 1500);
    return () => clearTimeout(timer);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [llmProvider, sdrModel, keyOpenai, keyAnthropic, keyGroq, autoReleaseMinutes]);


  const saveWaSettings = async (override?: typeof waSettings) => {
    setWaSettingsSaving(true);
    setWaSettingsError("");
    try {
      const r = await fetch(`/api/proxy/instance/${id}/whatsapp-settings`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(override ?? waSettings),
      });
      if (r.ok) {
        setWaSettingsSaved(true);
        setTimeout(() => setWaSettingsSaved(false), 2000);
      } else {
        const data = await r.json().catch(() => ({}));
        setWaSettingsError(data.detail ?? "Erro ao salvar configurações.");
      }
    } catch {
      setWaSettingsError("Erro de conexão ao salvar.");
    } finally {
      setWaSettingsSaving(false);
    }
  };

  // Auto-prévia de campanha: gera 3 exemplos quando context muda (systemPrompt opcional)
  useEffect(() => {
    if (!context.trim()) { setContextoPreviewMsgs([]); return; }
    setContextoPreviewMsgs([]);
    const timer = setTimeout(async () => {
      setContextoPreviewLoading(true);
      try {
        const res = await previewPrompt(id, context, 3);
        if (res.ok) setContextoPreviewMsgs((res.previews ?? []).map((p: { message?: string }) => p.message ?? ""));
      } catch { /* silencioso */ }
      setContextoPreviewLoading(false);
    }, 2000);
    return () => clearTimeout(timer);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [systemPrompt, context]);

  // Auto-gera preview quando context + testName + testPhone estão preenchidos
  useEffect(() => {
    if (!context.trim() || !testName.trim() || !testPhone.trim()) return;
    setInlinePreviews([]);
    const timer = setTimeout(() => { handleLoadInlinePreviews(); }, 700);
    return () => clearTimeout(timer);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [testPhone, testName, context]);

  // ── Handlers ──────────────────────────────────────────────────────────────

  const handleDownload = () => {
    if (contactsList.length === 0) return;
    const lines = contactsList.map(c => c.name ? `${c.name}, ${c.number}` : c.number);
    const blob = new Blob([lines.join("\n")], { type: "text/plain;charset=utf-8" });
    const url  = URL.createObjectURL(blob);
    const a    = document.createElement("a");
    a.href = url;
    a.download = `contatos_${id}.txt`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const handleGenerateQueue = async () => {
    if (!context.trim()) return alert("Descreva o contexto antes de gerar a fila.");
    try {
      await generateQueue(id, context);
    } catch {
      alert("Erro ao iniciar geração da fila. Tente novamente.");
      return;
    }
    setQueueStatus("generating");
    setQueue([]); setQueueProgress(0);
    setTab("campanha");
    const poll = setInterval(async () => {
      const d = await getQueue(id).catch(() => null);
      if (!d) return;
      setQueue(d.queue ?? []); setQueueProgress(d.queue_progress ?? 0); setQueueTotal(d.total_contacts ?? 0);
      if (d.queue_status !== "generating") { setQueueStatus(d.queue_status); clearInterval(poll); }
    }, 1500);
  };

  const handleStopGeneration = async () => {
    try {
      await stopQueueGeneration(id);
      setQueueStatus("idle");
    } catch {
      alert("Erro ao parar geração. Tente novamente.");
    }
  };

  const handleToggleSkip = async (index: number) => {
    try {
      await toggleSkipQueueItem(id, index);
      setQueue(q => q.map((item, i) => i === index ? { ...item, skipped: !item.skipped } : item));
    } catch {
      alert("Erro ao atualizar item da fila. Tente novamente.");
    }
  };

  const handleStartEdit = (index: number) => { setEditingIndex(index); setEditingText(queue[index].message); };

  const handleSaveEdit = async () => {
    if (editingIndex === null) return;
    try {
      await updateQueueItem(id, editingIndex, editingText);
      setQueue(q => q.map((item, i) => i === editingIndex ? { ...item, message: editingText } : item));
      setEditingIndex(null);
    } catch {
      alert("Erro ao salvar edição. Tente novamente.");
    }
  };

  // Abre o modal de preview antes de iniciar
  const handleStartWithPreview = () => {
    if (!context.trim()) return alert("Descreva o contexto/assunto das mensagens.");
    setPendingAction("direct");
    setShowCampaignPreview(true);
  };

  const handleSendQueueWithPreview = () => {
    const active = queue.filter(i => !i.skipped && i.message.trim()).length;
    if (active === 0) return alert("Nenhuma mensagem ativa na fila.");
    setPendingAction("queue");
    setShowCampaignPreview(true);
  };

  // Chamado quando o usuário aprova no modal
  const handleApprovePreview = async () => {
    setShowCampaignPreview(false);
    try {
      if (pendingAction === "direct") {
        await startCampaign(id, context, true, timingCfg);
        loadStatus(); setTab("campanha");
      } else if (pendingAction === "queue") {
        const active = queue.filter(i => !i.skipped && i.message.trim()).length;
        if (!confirm(`Enviar ${active} mensagens da fila?`)) return;
        await sendQueue(id, timingCfg);
        loadStatus(); setTab("campanha");
      }
    } catch {
      alert("Erro ao iniciar campanha. Tente novamente.");
    } finally {
      setPendingAction(null);
    }
  };

  const handleStop = async () => {
    try {
      await stopCampaign(id);
    } catch {
      alert("Erro ao parar campanha. Tente novamente.");
    } finally {
      loadStatus();
    }
  };

  const timingCfg: TimingConfig = {
    daily_limit:      dailyLimit || undefined,
    hourly_limit:     hourlyLimit || undefined,
    delay_min:        delayMin || undefined,
    delay_max:        delayMax || undefined,
    long_pause_every: longPauseEvery || undefined,
    long_pause_min:   longPauseMin ? longPauseMin * 60 : undefined,
    long_pause_max:   longPauseMax ? longPauseMax * 60 : undefined,
  };

  const handleReset = async () => {
    if (!confirm("Limpar contatos, logs, fila, progresso e conversas do SDR Inbox?\n\nOs prompts e contexto da campanha serão mantidos.")) return;
    try {
      const [res] = await Promise.all([
        resetInstance(id),
        clearConversations(id),
      ]);
      if (res.ok) {
        setContactsList([]); setLogs([]); setSends([]);
        setQueue([]); setQueueStatus("idle"); setQueueProgress(0);
        setUploadMsg("");
        loadStatus();
      } else {
        alert("Erro ao resetar instância. Tente novamente.");
      }
    } catch {
      alert("Erro ao resetar instância. Tente novamente.");
    }
  };

  const handleLoadInlinePreviews = async () => {
    if (!context.trim()) return;
    if (!testPhone.trim()) return;
    setInlinePreviewLoading(true); setInlinePreviewError("");
    try {
      const res = await previewPrompt(id, context, 1, testName.trim(), testPhone.trim());
      if (res.ok) setInlinePreviews(res.previews ?? []);
      else setInlinePreviewError(res.detail ?? "Erro ao gerar exemplos.");
    } catch {
      setInlinePreviewError("Erro de conexão com o backend.");
    } finally {
      setInlinePreviewLoading(false);
    }
  };

  const handleTestDirect = async () => {
    if (!testPhone.trim())  return alert("Digite o número de teste.");
    if (!context.trim())    return alert("Preencha o Contexto da Campanha na aba Contexto.");
    setTestLoading(true); setTestResult(null);
    try {
      const preview = inlinePreviews?.[0]?.message ?? "";
      const res = await testMessage(id, testPhone.trim(), context, testName.trim(), preview);
      if (res.ok && res.message) {
        // Atualiza o preview com a mensagem que foi realmente enviada
        setInlinePreviews([{ name: testName.trim(), phone: testPhone.trim(), message: res.message }]);
      }
      setTestResult({ ok: res.ok, detail: res.ok ? undefined : res.detail });
    } catch {
      setTestResult({ ok: false, detail: "Erro de conexão com o backend." });
    } finally {
      setTestLoading(false);
    }
  };

  const handleUpload = async () => {
    if (!file) return;
    setUploading(true); setUploadMsg("");
    try {
      const res = await uploadContacts(id, file);
      if (res.ok) {
        setUploadMsg(
          `${res.adicionados ?? 0} adicionados - ${res.total ?? 0} total - ${res.duplicados ?? 0} duplicados - ${res.removidos ?? 0} invalidos - ${res.lidos ?? 0} lidos`
        );
        loadStatus(); loadContacts();
      } else {
        setUploadMsg(res.detail ?? "Erro no upload.");
      }
    } catch {
      setUploadMsg("Erro de conexão com o backend.");
    } finally {
      setUploading(false);
    }
  };

  // Auto-save cuida do prompt; este handler é reserva para uso manual futuro
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  const handleSavePrompt = async () => {
    setPromptSaving(true); setPromptSaved(false); setPromptSaveError(false);
    try {
      await updatePrompt(id, systemPrompt);
      setPromptSaved(true);
      setTimeout(() => setPromptSaved(false), 2500);
    } catch {
      setPromptSaveError(true);
    } finally {
      setPromptSaving(false);
    }
  };

  const handleSaveSdr = async () => {
    setSdrSaving(true); setSdrSaved(false); setSdrSaveError(false);
    const keywords = handoffKeywords.split(",").map((k: string) => k.trim()).filter(Boolean);
    const payload = {
      inbound_ai_enabled: sdrEnabled,
      qualification_prompt: qualificationPrompt,
      max_ai_turns: maxAiTurns,
      ai_response_delay: aiResponseDelay,
      auto_handoff_keywords: keywords,
      model: sdrModel,
      followup_enabled: followupEnabled,
      followup_delay_hours: followupDelayHours,
      followup_max: followupMax,
      auto_release_minutes: autoReleaseMinutes,
      scoring_criteria: scoringCriteria,
      tts_enabled: ttsEnabled,
      ...(canManageCredentials ? {
        llm_provider: llmProvider,
        llm_api_key: llmApiKey,
        key_openai: keyOpenai,
        key_anthropic: keyAnthropic,
        key_groq: keyGroq,
      } : {}),
    };
    try {
      await updateSdrConfig(id, payload);
      setSdrSaved(true);
      setTimeout(() => setSdrSaved(false), 2000);
    } catch {
      setSdrSaveError(true);
    } finally {
      setSdrSaving(false);
    }
  };

  const handleKnowledgeUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setKbUploading(true);
    try {
      await uploadKnowledge(id, file);
      const d = await listKnowledge(id);
      if (d?.files) setKnowledgeFiles(d.files);
    } catch {
      alert("Erro ao enviar documento. Tente novamente.");
    } finally {
      setKbUploading(false);
      e.target.value = "";
    }
  };

  const handleKnowledgeDelete = async (filename: string) => {
    try {
      await deleteKnowledge(id, filename);
      setKnowledgeFiles((prev) => prev.filter((f) => f.filename !== filename));
    } catch {
      alert("Erro ao remover documento. Tente novamente.");
    }
  };

  const handleClearContacts = async () => {
    if (!confirm("Limpar contatos e histórico de envios desta instância?")) return;
    try {
      const res = await clearContacts(id);
      if (res.ok) { setContactsList([]); setLogs([]); setSends([]); await loadStatus(); }
      else alert(res.detail ?? "Erro ao limpar contatos.");
    } catch {
      alert("Erro de conexão ao limpar contatos.");
    }
  };

  // ── Derived state ─────────────────────────────────────────────────────────

  const isRunning   = status?.status === "running";
  const isStopping  = status?.status === "stopping";
  const getLogColor = (s: string) => {
    for (const key of Object.keys(LOG_COLOR)) { if (s.startsWith(key)) return LOG_COLOR[key]; }
    return "text-[#6B5E55]";
  };

  const step1ok = !!status && status.total_contacts > 0;
  const step2ok = true; // timing sempre ok (tem padrão)
  const step3ok = !!context.trim();
  const stepAllOk = step1ok && step3ok;

  // No Lite as abas de disparador (contatos/timing/contexto/campanha) não
  // aparecem; sobram Config. (chaves de IA + conhecimento) e Inbox SDR.
  const TABS: readonly { key: "contatos" | "timing" | "contexto" | "campanha" | "sdr" | "inbox"; label: string; ok: boolean }[] = isLite
    ? [
        { key: "sdr",   label: "Configuracao", ok: false },
        { key: "inbox", label: "Inbox SDR",    ok: false },
      ]
    : [
        { key: "contatos", label: `① Contatos${contactsList.length > 0 ? ` (${contactsList.length})` : ""}`, ok: step1ok },
        { key: "timing",   label: "② Timing",   ok: step2ok },
        { key: "contexto", label: "③ Contexto", ok: step3ok },
        { key: "campanha", label: `④ Campanha${sends.length > 0 ? ` (${sends.length})` : ""}`, ok: false },
        { key: "sdr",     label: "Config. SDR",       ok: false },
        { key: "inbox",   label: "Inbox SDR", ok: false },
      ];

  // Lite entra numa aba de campanha por padrão (contexto) — redireciona pra
  // Configuração, a primeira aba visível pra ele.
  useEffect(() => {
    if (isLite && (tab === "contexto" || tab === "contatos" || tab === "timing" || tab === "campanha")) {
      setTab("sdr");
    }
  }, [isLite, tab]);

  // ── Render ────────────────────────────────────────────────────────────────

  return (
    <div className="min-h-screen bg-[#F0EDE8]">
      {showCreds && <CredentialsModal instanceId={id} onClose={() => setShowCreds(false)} />}
      {showCampaignPreview && (
        <CampaignPreviewModal
          instanceId={id}
          context={context}
          onApprove={handleApprovePreview}
          onCancel={() => { setShowCampaignPreview(false); setPendingAction(null); }}
        />
      )}

      {/* Header */}
      <PageHeaderShell maxWidthClassName="max-w-4xl">
        <div className="flex items-start sm:items-center justify-between gap-2 sm:gap-4">
          <div className="flex items-center gap-3 min-w-0">
            <div className="w-9 h-9 shrink-0 rounded-full bg-[#CC3300] flex items-center justify-center text-[#F0EDE8] font-bold text-base">
              {id.charAt(0).toUpperCase()}
            </div>
            <div className="min-w-0">
              <h1 className="text-[#1A1410] font-bold text-[15px] leading-tight truncate">{id}</h1>
              <p className="text-[#6B5E55] text-[11px]">Instância WhatsApp</p>
            </div>
          </div>
          <div className="flex items-center gap-1.5 sm:gap-2 flex-wrap justify-end">
            {/* Badge de conexão WhatsApp */}
            <span className={`flex items-center gap-1.5 text-xs font-semibold px-2.5 sm:px-3 py-1 sm:py-1.5 rounded-full ${
              waState === "open"       ? "bg-[#3D0A00] text-[#CC3300]" :
              waState === "connecting" ? "bg-[#2D2000] text-[#F0B429]" :
                                        "bg-[#2D0A0A] text-[#F87171]"
            }`}>
              <span className={`w-2 h-2 shrink-0 rounded-full ${
                waState === "open"       ? "bg-[#CC3300] animate-pulse" :
                waState === "connecting" ? "bg-[#F0B429] animate-pulse" :
                                          "bg-[#F87171]"
              }`} />
              <span className="hidden sm:inline">{waState === "open" ? "WA Conectado" : waState === "unknown" ? "WA ?" : "WA Desconectado"}</span>
              <span className="sm:hidden">{waState === "open" ? "WA" : "WA ✗"}</span>
            </span>

            {/* Botão de reconexão */}
            {waState !== "open" && (
              <button
                onClick={() => {
                  setQrCodeData("");
                  setQrCodeError("");
                  setShowQrCodeModal(true);
                  reconnectInstanceQrcode(id).then(res => {
                    if (res.already_connected) {
                      setShowQrCodeModal(false);
                      setWaState("open");
                    } else if (res.ok && res.qr_code) {
                      setQrCodeData(res.qr_code.includes("data:image") ? res.qr_code : `data:image/png;base64,${res.qr_code}`);
                    } else {
                      setQrCodeError(res.detail ?? "Erro ao obter QR Code");
                    }
                  }).catch(() => setQrCodeError("Erro de conexão."));
                }}
                className="text-xs sm:text-sm border border-[#CC3300]/30 text-[#CC3300] hover:bg-[#3D0A00] px-2.5 sm:px-3 py-1 sm:py-1.5 rounded-lg transition-colors font-medium"
              >
                <span className="hidden sm:inline">Reconectar WA</span>
                <span className="sm:hidden">Reconectar</span>
              </button>
            )}

            {/* Badge de status da campanha */}
            {status && (
              <span className={`text-xs font-semibold px-2.5 sm:px-3 py-1 sm:py-1.5 rounded-full ${STATUS_COLOR[status.status] ?? "bg-[#D4CFC9] text-[#6B5E55]"}`}>
                {STATUS_LABEL[status.status] ?? status.status}
              </span>
            )}

            {canManageCredentials && (
              <button
                onClick={() => setShowCreds(true)}
                className="text-xs sm:text-sm border border-[#6B5E55]/30 text-[#6B5E55] hover:bg-[#D4CFC9] px-2.5 sm:px-3 py-1 sm:py-1.5 rounded-lg transition-colors font-medium"
              >
                Credenciais
              </button>
            )}

            <button
              onClick={handleReset}
              className="text-xs sm:text-sm border border-[#F87171]/30 text-[#F87171] hover:bg-[#2D0A0A] px-2.5 sm:px-3 py-1 sm:py-1.5 rounded-lg transition-colors font-medium"
            >
              Limpar
            </button>
          </div>
        </div>
      </PageHeaderShell>


      {/* Modal do QR Code */}
      {showQrCodeModal && (
        <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/60 backdrop-blur-sm p-4">
          <div className="bg-[#F0EDE8] rounded-2xl border border-[#D4CFC9] w-full max-w-md shadow-2xl p-6">
            <h2 className="text-xl font-bold text-[#1A1410] mb-4">Reconectar WhatsApp</h2>
            <p className="text-[#6B5E55] text-sm mb-6">Escaneie o QR Code abaixo com o WhatsApp do seu celular.</p>

            <div className="flex justify-center mb-6">
              {!qrCodeData && !qrCodeError ? (
                <div className="w-64 h-64 bg-[#E8E4DF] animate-pulse rounded-xl flex items-center justify-center">
                  <span className="text-[#6B5E55] text-sm">Carregando...</span>
                </div>
              ) : qrCodeError ? (
                <div className="w-64 h-64 bg-[#2D0A0A] border border-[#F87171]/30 rounded-xl flex items-center justify-center p-4 text-center">
                  <span className="text-[#F87171] text-sm">{qrCodeError}</span>
                </div>
              ) : (
                <div className="bg-white p-4 rounded-xl inline-block">
                  <Image src={qrCodeData} alt="WhatsApp QR Code" width={224} height={224} className="w-56 h-56 object-contain" unoptimized />
                </div>
              )}
            </div>

            <div className="flex justify-end">
              <button onClick={() => setShowQrCodeModal(false)} className="px-5 py-2.5 bg-[#D4CFC9] hover:bg-[#3D4F5A] text-[#1A1410] font-medium rounded-xl transition-colors">
                Fechar
              </button>
            </div>
          </div>
        </div>
      )}

      <main className="max-w-4xl mx-auto px-4 sm:px-6 py-6">



        {/* Tabs */}
        <div className="overflow-x-auto mb-5 -mx-4 sm:mx-0 px-4 sm:px-0">
          <div className="flex gap-1 bg-[#E8E4DF] rounded-xl p-1.5 border border-[#D4CFC9] min-w-max sm:min-w-0">
            {TABS.map((t) => (
              <button
                key={t.key}
                onClick={() => t.key === "inbox" ? router.push(`/instances/${id}/inbox`) : setTab(t.key)}
                className={`shrink-0 sm:flex-1 px-2.5 py-2 rounded-lg text-xs font-semibold transition-colors flex items-center justify-center gap-1 whitespace-nowrap ${
                  tab === t.key
                    ? "bg-[#0E0E18] text-[#CC3300]"
                    : t.ok
                    ? "text-[#CC3300] hover:text-[#991F00]"
                    : "text-[#6B5E55] hover:text-[#1A1410]"
                }`}
              >
                {t.label}
                {t.ok && tab !== t.key && <span className="text-[#CC3300] text-[10px]">✓</span>}
              </button>
            ))}
          </div>
        </div>

        {/* ── Tab: ② Timing ──────────────────────────────────────────────────── */}
        {tab === "timing" && (
          <div className="space-y-4">
            <div className="bg-[#E8E4DF] rounded-2xl border border-[#D4CFC9] p-5">
              <h2 className="font-bold text-[#1A1410] mb-1">Controles de Timing</h2>
              <p className="text-sm text-[#6B5E55] mb-5">Configure o ritmo de envio ou use os valores padrão, que já simulam comportamento humano.</p>

              <div className="flex gap-2 mb-5">
                <button onClick={() => setUseCustomTiming(false)}
                  className={`flex-1 py-2.5 rounded-xl text-sm font-semibold border transition-colors ${!useCustomTiming ? "bg-[#0E0E18] text-[#CC3300] border-[#CC3300]/30" : "bg-[#F0EDE8] text-[#6B5E55] border-[#D4CFC9] hover:text-[#1A1410]"}`}>
                  Usar padrão (recomendado)
                </button>
                <button onClick={() => setUseCustomTiming(true)}
                  className={`flex-1 py-2.5 rounded-xl text-sm font-semibold border transition-colors ${useCustomTiming ? "bg-[#0E0E18] text-[#CC3300] border-[#CC3300]/30" : "bg-[#F0EDE8] text-[#6B5E55] border-[#D4CFC9] hover:text-[#1A1410]"}`}>
                  Personalizar
                </button>
              </div>

              {!useCustomTiming ? (
                <div className="bg-[#F0EDE8] rounded-xl p-4 space-y-2 text-sm text-[#6B5E55]">
                  {[
                    ["Limite diário",       "200–300 mensagens (aleatório)"],
                    ["Limite por hora",     "25–38 mensagens (aleatório)"],
                    ["Delay entre envios",  "95–145 segundos (aleatório)"],
                    ["Pausa longa",         "A cada 20 envios, pausa de 5–15 min"],
                  ].map(([k, v]) => (
                    <div key={k} className="flex justify-between">
                      <span>{k}</span>
                      <span className="text-[#1A1410] font-medium">{v}</span>
                    </div>
                  ))}
                </div>
              ) : (
                <div className="grid grid-cols-2 gap-4">
                  <NumberField label="Limite diário" placeholder="Padrão: 200–300" value={dailyLimit} onChange={setDailyLimit} disabled={isRunning || isStopping} />
                  <NumberField label="Limite por hora" placeholder="Padrão: 25–38" value={hourlyLimit} onChange={setHourlyLimit} disabled={isRunning || isStopping} />
                  <NumberField label="Delay mín (seg)" placeholder="Padrão: 95s" value={delayMin} onChange={setDelayMin} disabled={isRunning || isStopping} />
                  <NumberField label="Delay máx (seg)" placeholder="Padrão: 145s" value={delayMax} onChange={setDelayMax} disabled={isRunning || isStopping} />
                  <NumberField label="Pausa longa a cada N" placeholder="Padrão: 20 envios" value={longPauseEvery} onChange={setLongPauseEvery} disabled={isRunning || isStopping} />
                  <div className="col-span-2 grid grid-cols-2 gap-4">
                    <NumberField label="Pausa longa mín (min)" placeholder="Padrão: 5 min" value={longPauseMin} onChange={setLongPauseMin} disabled={isRunning || isStopping} />
                    <NumberField label="Pausa longa máx (min)" placeholder="Padrão: 15 min" value={longPauseMax} onChange={setLongPauseMax} disabled={isRunning || isStopping} />
                  </div>
                  <p className="col-span-2 text-[10px] text-[#6B5E55]/60">Deixe 0 para usar o padrão em cada campo.</p>
                </div>
              )}
            </div>
            <button onClick={() => setTab("contexto")}
              className="w-full bg-[#D4CFC9] hover:bg-[#3D4F5A] text-[#1A1410] font-semibold py-3 rounded-xl text-sm transition-colors">
              Próximo: ③ Contexto →
            </button>
          </div>
        )}

        {/* ── Tab: ③ Contexto ────────────────────────────────────────────────── */}
        {tab === "contexto" && (
          <div className="space-y-4">

            <div className="bg-[#E8E4DF] rounded-2xl border border-[#D4CFC9] p-5">
              {/* Prompt do sistema */}
              <div className="mb-5 pb-5 border-b border-[#D4CFC9]">
                <div className="flex items-center justify-between mb-1">
                  <h2 className="font-bold text-[#1A1410]">Prompt do Sistema</h2>
                  <div className="flex items-center gap-2">
                  <span className={`text-xs px-2 py-1 rounded-lg transition-colors ${
                    promptSaveError ? "text-[#F87171]" : promptSaving ? "text-[#F0B429]" : promptSaved ? "text-[#CC3300]" : "text-[#3D4F5A]"
                  }`}>
                    {promptSaveError ? "Erro ao salvar" : promptSaving ? "Salvando..." : promptSaved ? "✓ Salvo" : "Auto-save"}
                  </span>
                    <button
                      type="button"
                      onClick={() => setShowContextSystemPrompt(v => !v)}
                      className="text-xs font-semibold px-3 py-1.5 rounded-lg border border-[#D4CFC9] text-[#6B5E55] hover:text-[#1A1410] hover:border-[#3D4F5A] transition-colors"
                    >
                      {showContextSystemPrompt ? "Fechar" : "Abrir"}
                    </button>
                </div>
                </div>
                {showContextSystemPrompt && (
                <>
                <p className="text-sm text-[#6B5E55] mb-3">Instrução fixa da IA — define o tom e comportamento para todos os envios. Salvo automaticamente.</p>
                <textarea
                  rows={4}
                  placeholder={`Ex: Você é um consultor simpático da [Empresa]. Escreva mensagens curtas, diretas e calorosas. Use o nome do cliente. Máximo 4 linhas. 1 emoji no máximo. Nunca use linguagem corporativa ou formal.`}
                  value={systemPrompt}
                  onChange={(e) => setSystemPrompt(e.target.value)}
                  disabled={isRunning || isStopping}
                  className="w-full bg-[#F0EDE8] border border-[#D4CFC9] rounded-xl px-4 py-3 text-sm text-[#1A1410] placeholder-[#6B5E55] focus:outline-none focus:border-[#CC3300] transition-colors resize-none disabled:opacity-50"
                />
                </>
                )}
              </div>

            <div className="flex items-center justify-between mb-1">
                <h2 className="font-bold text-[#1A1410]">Contexto da Campanha</h2>
                <button
                  type="button"
                  onClick={handleToggleMic}
                  disabled={isRunning || isStopping}
                  title={isRecording ? "Parar gravação" : "Gravar com microfone"}
                  className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium border transition-colors disabled:opacity-40 ${
                    isRecording
                      ? "bg-[#F87171]/10 border-[#F87171]/50 text-[#F87171] animate-pulse"
                      : "border-[#D4CFC9] text-[#6B5E55] hover:text-[#1A1410] hover:border-[#3D4F5A]"
                  }`}
                >
                  <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="currentColor" className="w-3.5 h-3.5">
                    <path d="M8.25 4.5a3.75 3.75 0 1 1 7.5 0v8.25a3.75 3.75 0 1 1-7.5 0V4.5Z" />
                    <path d="M6 10.5a.75.75 0 0 1 .75.75v1.5a5.25 5.25 0 1 0 10.5 0v-1.5a.75.75 0 0 1 1.5 0v1.5a6.751 6.751 0 0 1-6 6.709v2.291h3a.75.75 0 0 1 0 1.5h-7.5a.75.75 0 0 1 0-1.5h3v-2.291a6.751 6.751 0 0 1-6-6.709v-1.5A.75.75 0 0 1 6 10.5Z" />
                  </svg>
                  {isRecording ? "Gravando..." : "Microfone"}
                </button>
              </div>
              <p className="text-sm text-[#6B5E55] mb-4">Descreva o propósito das mensagens. A IA usará isso para personalizar cada envio.</p>
              <textarea
                rows={5}
                placeholder="Ex: Promoção de pisos vinílicos com 20% de desconto esta semana. Entrega grátis para pedidos acima de R$500. Somos a Loja X, referência em revestimentos no estado."
                value={context}
                onChange={(e) => setContext(e.target.value)}
                disabled={isRunning || isStopping}
                className="w-full bg-[#F0EDE8] border border-[#D4CFC9] rounded-xl px-4 py-3 text-sm text-[#1A1410] placeholder-[#6B5E55] focus:outline-none focus:border-[#CC3300] transition-colors resize-none disabled:opacity-50"
              />
              {context.trim() && (
                <p className="text-xs text-[#CC3300] mt-2 font-medium">✓ Contexto preenchido</p>
              )}

            </div>

            {/* Prévias automáticas */}
            {(context.trim() || contextoPreviewLoading || contextoPreviewMsgs.length > 0) && (
              <div className="bg-[#E8E4DF] rounded-2xl border border-[#D4CFC9] p-5">
                <div className="flex items-center justify-between mb-3">
                  <div>
                    <h2 className="font-bold text-[#1A1410]">Prévia das Mensagens</h2>
                    <p className="text-xs text-[#6B5E55] mt-0.5">Exemplos gerados com base no seu prompt e contexto — cada contato recebe uma versão única.</p>
                  </div>
                  {contextoPreviewLoading && (
                    <span className="text-xs text-[#F0B429] animate-pulse">Gerando...</span>
                  )}
                </div>
                {contextoPreviewMsgs.length > 0 ? (
                  <div className="space-y-3">
                    {contextoPreviewMsgs.map((msg, i) => (
                      <div key={i} className="bg-[#F0EDE8] rounded-xl p-4">
                        <p className="text-[10px] text-[#6B5E55] uppercase tracking-wide font-semibold mb-2">Exemplo {i + 1}</p>
                        <p className="text-sm text-[#1A1410] whitespace-pre-wrap leading-relaxed">{msg}</p>
                      </div>
                    ))}
                  </div>
                ) : !contextoPreviewLoading ? (
                  <p className="text-sm text-[#6B5E55]">Preencha o contexto da campanha para ver os exemplos.</p>
                ) : null}
              </div>
            )}

            {/* Enviar teste real */}
            <div className="bg-[#E8E4DF] rounded-2xl border border-[#D4CFC9] p-5 space-y-4">
              <h2 className="font-bold text-[#1A1410]">Enviar Teste Real</h2>

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block text-xs font-medium text-[#6B5E55] mb-1.5">Nome</label>
                  <input
                    type="text"
                    placeholder="Ex: João Silva"
                    value={testName}
                    onChange={(e) => { setTestName(e.target.value); setTestResult(null); }}
                    className="w-full bg-[#F0EDE8] border border-[#D4CFC9] rounded-xl px-3 py-2.5 text-sm text-[#1A1410] placeholder-[#6B5E55] focus:outline-none focus:border-[#CC3300] transition-colors"
                  />
                </div>
                <div>
                  <label className="block text-xs font-medium text-[#6B5E55] mb-1.5">Telefone</label>
                  <input
                    type="text"
                    placeholder="5511987654321"
                    value={testPhone}
                    onChange={(e) => { setTestPhone(e.target.value); setTestResult(null); }}
                    className="w-full bg-[#F0EDE8] border border-[#D4CFC9] rounded-xl px-3 py-2.5 text-sm text-[#1A1410] placeholder-[#6B5E55] focus:outline-none focus:border-[#CC3300] transition-colors font-mono"
                  />
                </div>
              </div>

              {/* Prévia personalizada */}
              <div>
                <div className="flex items-center justify-between mb-2">
                  <p className="text-xs font-medium text-[#6B5E55]">
                    {testName || testPhone ? `Prévia para ${testName ? `${testName} ` : ""}${testPhone}` : "Prévia da mensagem"}
                  </p>
                  <button
                    onClick={handleLoadInlinePreviews}
                    disabled={inlinePreviewLoading || !context.trim() || !testPhone.trim()}
                    className="text-xs border border-[#D4CFC9] text-[#6B5E55] hover:text-[#1A1410] hover:border-[#3D4F5A] px-3 py-1.5 rounded-lg transition-colors disabled:opacity-40"
                  >
                    {inlinePreviewLoading ? "Gerando..." : "↻ Regerar"}
                  </button>
                </div>

                {!context.trim() || !testPhone.trim() ? (
                  <div className="bg-[#F0EDE8] rounded-xl p-4 text-center text-[#6B5E55] text-sm">
                    {!context.trim() ? "Preencha o contexto acima para gerar a prévia." : "Preencha o telefone para gerar a prévia."}
                  </div>
                ) : inlinePreviewLoading ? (
                  <div className="flex items-center gap-3 py-6 justify-center">
                    <div className="w-4 h-4 border-2 border-[#CC3300]/30 border-t-[#CC3300] rounded-full animate-spin" />
                    <span className="text-[#6B5E55] text-sm">Gerando prévia personalizada...</span>
                  </div>
                ) : inlinePreviewError ? (
                  <p className="text-[#F87171] text-sm">{inlinePreviewError}</p>
                ) : inlinePreviews.length > 0 ? (
                  <div className="bg-[#E8E4DF] rounded-xl p-4">
                    {inlinePreviews.map((p, i) => (
                      <div key={i} className="flex justify-end">
                        <div className="bg-[#DCF8C6] rounded-[18px_18px_4px_18px] px-4 py-2.5 max-w-[90%] shadow-sm">
                          <p className="text-[#1A1410] text-sm whitespace-pre-wrap leading-relaxed">{p.message}</p>
                          <div className="flex justify-end mt-1">
                            <span className="text-[#667781] text-[10px]">{new Date().toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" })}</span>
                          </div>
                        </div>
                      </div>
                    ))}
                  </div>
                ) : (
                  <div className="bg-[#F0EDE8] rounded-xl p-4 text-center text-[#6B5E55] text-sm">
                    Aguardando...
                  </div>
                )}
              </div>

              <button
                onClick={handleTestDirect}
                disabled={testLoading || !testPhone.trim() || !context.trim()}
                className="w-full bg-[#CC3300] hover:bg-[#991F00] disabled:bg-[#CC3300]/30 disabled:cursor-not-allowed text-[#F0EDE8] font-bold py-3 rounded-xl text-sm transition-colors"
              >
                {testLoading
                  ? "Gerando e enviando..."
                  : `Enviar teste para ${testName ? `${testName} ` : ""}${testPhone || "..."} ↗`}
              </button>

              {testResult && (
                <div className={`rounded-xl px-4 py-3 border text-sm ${testResult.ok ? "bg-[#3D0A00] border-[#CC3300]/30" : "bg-[#2D0A0A] border-[#F87171]/30"}`}>
                  {testResult.ok
                    ? <p className="font-semibold text-[#CC3300]">✓ Enviado para {testName ? `${testName} (${testPhone})` : testPhone}</p>
                    : <p className="text-[#F87171]">✗ {testResult.detail ?? "Falha ao enviar."}</p>}
                </div>
              )}
            </div>

            <button
              onClick={() => setTab("campanha")}
              disabled={!context.trim()}
              className="w-full bg-[#CC3300] hover:bg-[#991F00] disabled:bg-[#CC3300]/30 disabled:cursor-not-allowed text-[#F0EDE8] font-bold py-3 rounded-xl text-sm transition-colors">
              Próximo: ④ Campanha →
            </button>
          </div>
        )}

        {/* ── Tab: ① Contatos ───────────────────────────────────────────────── */}
        {tab === "contatos" && (
          <div className="space-y-4">

            {/* Upload de arquivo */}
            <div className="bg-[#E8E4DF] rounded-2xl border border-[#D4CFC9] p-5">
              <div className="flex items-center justify-between mb-1">
                <h2 className="font-bold text-[#1A1410]">Importar Contatos</h2>
                <div className="flex gap-2">
                  <button
                    onClick={handleOpenImportCrm}
                    className="text-xs border border-[#3B82F6]/40 text-[#3B82F6] hover:bg-[#0D1A3A] font-medium px-3 py-1.5 rounded-lg transition-colors"
                  >
                    Importar do CRM
                  </button>
                  {contactsList.length > 0 && (
                    <button
                      onClick={handleClearContacts}
                      className="text-xs border border-[#F87171]/30 text-[#F87171] hover:bg-[#2D0A0A] font-medium px-3 py-1.5 rounded-lg transition-colors"
                    >
                      Remover Lista
                    </button>
                  )}
                </div>
              </div>
              <p className="text-sm text-[#6B5E55] mb-4">Formatos aceitos: CSV, TXT, XLSX, PDF</p>
              <div className="flex gap-3 items-center flex-wrap">
                <input
                  type="file"
                  accept=".csv,.txt,.xlsx,.xls,.pdf"
                  onChange={(e) => setFile(e.target.files?.[0] ?? null)}
                  className="text-sm text-[#6B5E55] file:mr-3 file:py-1.5 file:px-3 file:rounded-lg file:border-0 file:bg-[#D4CFC9] file:text-[#1A1410] file:text-sm file:font-medium"
                />
                <button
                  onClick={handleUpload}
                  disabled={!file || uploading}
                  className="bg-[#60A5FA]/10 border border-[#60A5FA]/40 text-[#60A5FA] hover:bg-[#0E0E18] disabled:opacity-40 text-sm font-semibold px-4 py-2 rounded-xl transition-colors"
                >
                  {uploading ? "Enviando..." : "Carregar arquivo"}
                </button>
              </div>
              {uploadMsg && <p className="text-sm text-[#CC3300] font-medium mt-3">{uploadMsg}</p>}
            </div>

            {/* Lista atual */}
            <div className="bg-[#E8E4DF] rounded-2xl border border-[#D4CFC9] overflow-hidden">
              <div className="flex items-center justify-between px-4 py-3 border-b border-[#D4CFC9]">
                <span className="text-sm font-bold text-[#1A1410]">
                  Contatos na instância — {contactsList.length}
                </span>
                <div className="flex gap-2">
                  {contactsList.length > 0 && (
                    <>
                      <button
                        onClick={handleDownload}
                        className="text-sm bg-[#D4CFC9] hover:bg-[#3D4F5A] text-[#1A1410] font-medium px-3 py-1.5 rounded-lg transition-colors"
                      >
                        Baixar TXT
                      </button>
                      <button
                        onClick={handleClearContacts}
                        className="text-sm border border-[#F87171]/30 text-[#F87171] hover:bg-[#2D0A0A] font-medium px-3 py-1.5 rounded-lg transition-colors"
                      >
                        Limpar Tudo
                      </button>
                    </>
                  )}
                </div>
              </div>

              {contactsList.length === 0 ? (
                <div className="py-16 text-center text-[#6B5E55] text-sm">
                  Nenhum contato carregado. Importe usando os métodos acima.
                </div>
              ) : (
                <div className="divide-y divide-[#D4CFC9] max-h-[400px] overflow-y-auto">
                  {contactsList.map((c, i) => (
                    <div key={i} className="px-4 py-2.5 flex items-center gap-4 hover:bg-[#D4CFC9]/40 transition-colors">
                      <span className="text-xs text-[#6B5E55] w-8 shrink-0 text-right tabular-nums">{i + 1}</span>
                      <div className="w-7 h-7 rounded-full bg-[#0E0E18] flex items-center justify-center text-[#CC3300] text-xs font-bold shrink-0">
                        {(c.name || "?").charAt(0).toUpperCase()}
                      </div>
                      <span className="text-sm font-medium text-[#1A1410] w-36 truncate shrink-0">{c.name || "—"}</span>
                      <span className="text-sm text-[#6B5E55] font-mono">{c.number}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
            <button onClick={() => setTab("timing")}
              className="w-full bg-[#D4CFC9] hover:bg-[#3D4F5A] text-[#1A1410] font-semibold py-3 rounded-xl text-sm transition-colors">
              Próximo: ② Timing →
            </button>
          </div>
        )}

        {/* ── Tab: ⑤ Campanha ────────────────────────────────────────────────── */}
        {tab === "campanha" && (
          <div className="space-y-4">
            <CountdownBar logs={logs} isRunning={isRunning} />

            {/* Checklist + botões de ação */}
            <div className="bg-[#E8E4DF] rounded-2xl border border-[#D4CFC9] p-5">
              <h2 className="font-bold text-[#1A1410] mb-4">Disparar</h2>

              {!isRunning && !isStopping && (
                <div className="flex gap-2 mb-4 flex-wrap">
                  <span className={`flex items-center gap-1.5 text-xs font-medium px-3 py-1.5 rounded-full border ${step1ok ? "bg-[#3D0A00] text-[#CC3300] border-[#CC3300]/30" : "bg-[#2D2000] text-[#F0B429] border-[#F0B429]/30"}`}>
                    {step1ok ? "✓" : "!"} {status?.total_contacts ?? 0} contatos
                  </span>
                  <span className={`flex items-center gap-1.5 text-xs font-medium px-3 py-1.5 rounded-full border ${step3ok ? "bg-[#3D0A00] text-[#CC3300] border-[#CC3300]/30" : "bg-[#2D2000] text-[#F0B429] border-[#F0B429]/30"}`}>
                    {step3ok ? "✓" : "!"} Contexto
                  </span>
                  <span className={`flex items-center gap-1.5 text-xs font-medium px-3 py-1.5 rounded-full border ${waState === "open" ? "bg-[#3D0A00] text-[#CC3300] border-[#CC3300]/30" : "bg-[#2D0A0A] text-[#F87171] border-[#F87171]/30"}`}>
                    {waState === "open" ? "✓" : "✗"} WA
                  </span>
                </div>
              )}

              <div className="space-y-2">
                {/* Botões secundários (fila) */}
                {(queueStatus !== "idle" || false) && (
                  <div className="flex gap-2 flex-wrap">
                    {queueStatus === "generating" && (
                      <button onClick={handleStopGeneration}
                        className="border border-[#F0B429]/40 text-[#F0B429] hover:bg-[#2D2000] font-semibold py-2.5 px-4 rounded-xl transition-colors text-sm whitespace-nowrap">
                        Parar Geração ({queueProgress}/{queueTotal})
                      </button>
                    )}
                    {queueStatus === "ready" && (
                      <>
                        <button onClick={handleGenerateQueue} disabled={isRunning || isStopping}
                          className="border border-[#D4CFC9] text-[#6B5E55] hover:text-[#1A1410] hover:border-[#3D4F5A] disabled:opacity-40 font-semibold py-2.5 px-4 rounded-xl transition-colors text-sm whitespace-nowrap">
                          Regerar Fila
                        </button>
                        <button onClick={handleSendQueueWithPreview} disabled={isRunning || isStopping}
                          className="bg-[#60A5FA]/10 border border-[#60A5FA]/40 text-[#60A5FA] hover:bg-[#0E0E18] disabled:opacity-40 font-semibold py-2.5 px-4 rounded-xl transition-colors text-sm whitespace-nowrap">
                          Enviar Fila ({queue.filter(i => !i.skipped).length})
                        </button>
                      </>
                    )}
                  </div>
                )}
                {/* Botão primário — linha separada no mobile */}
                <div className="flex gap-2">
                  {queueStatus === "idle" && (
                    <button onClick={handleGenerateQueue} disabled={isRunning || isStopping || !stepAllOk}
                      className="border border-[#60A5FA]/40 text-[#60A5FA] hover:bg-[#0E0E18] disabled:opacity-40 disabled:cursor-not-allowed font-semibold py-2.5 px-4 rounded-xl transition-colors text-sm whitespace-nowrap">
                      Gerar Fila
                    </button>
                  )}
                  <button onClick={handleStartWithPreview} disabled={isRunning || isStopping || !stepAllOk}
                    className={`flex-1 font-bold py-2.5 rounded-xl transition-colors text-sm disabled:cursor-not-allowed ${
                      status?.status === "stopped"
                        ? "bg-[#F0B429] hover:bg-[#D4A017] disabled:bg-[#F0B429]/30 text-[#F0EDE8]"
                        : "bg-[#CC3300] hover:bg-[#991F00] disabled:bg-[#CC3300]/30 text-[#F0EDE8]"
                    }`}>
                    {isRunning ? "Enviando..." : status?.status === "stopped" ? "↺ Retomar Campanha" : "▶ Iniciar Campanha"}
                  </button>
                  <button onClick={handleStop} disabled={!isRunning && !isStopping}
                    className="bg-[#F87171]/10 border border-[#F87171]/30 text-[#F87171] hover:bg-[#2D0A0A] disabled:opacity-30 disabled:cursor-not-allowed font-bold py-2.5 px-4 rounded-xl transition-colors text-sm whitespace-nowrap">
                    {isStopping ? "Parando..." : "■ Parar"}
                  </button>
                </div>
              </div>
            </div>

            {/* Fila de revisão */}
            {(queueStatus === "generating" || queueStatus === "ready") && (
              <div className="bg-[#E8E4DF] rounded-2xl border border-[#D4CFC9] overflow-hidden">
                <div className="flex items-center justify-between px-4 py-3 border-b border-[#D4CFC9]">
                  <span className="text-sm font-bold text-[#1A1410]">
                    {queueStatus === "generating"
                      ? `Gerando mensagens... ${queueProgress}/${queueTotal}`
                      : `Fila de Revisão — ${queue.filter(i => !i.skipped).length} aprovadas / ${queue.filter(i => i.skipped).length} puladas`}
                  </span>
                  {queueStatus === "generating" && (
                    <div className="w-32 bg-[#F0EDE8] rounded-full h-1.5 overflow-hidden">
                      <div
                        className="h-1.5 bg-[#60A5FA] rounded-full transition-all duration-500"
                        style={{ width: `${queueTotal > 0 ? Math.round(queueProgress / queueTotal * 100) : 0}%` }}
                      />
                    </div>
                  )}
                </div>

                {queue.length === 0 && queueStatus === "generating" ? (
                  <div className="py-12 text-center text-[#6B5E55] text-sm">Gerando mensagens...</div>
                ) : (
                  <div className="divide-y divide-[#D4CFC9] max-h-[600px] overflow-y-auto">
                    {queue.map((item, i) => (
                      <div key={i} className={`px-4 py-3 transition-colors ${item.skipped ? "opacity-40" : "hover:bg-[#D4CFC9]/40"}`}>
                        <div className="flex items-start gap-3">
                          <div className="flex-1 min-w-0">
                            <div className="flex items-center gap-2 mb-2">
                              <span className="font-semibold text-sm text-[#1A1410]">{item.name || "—"}</span>
                              <span className="text-xs text-[#6B5E55] font-mono">{item.phone}</span>
                              {item.skipped && (
                                <span className="text-xs bg-[#D4CFC9] text-[#6B5E55] px-2 py-0.5 rounded-full font-medium">pulado</span>
                              )}
                            </div>

                            {editingIndex === i ? (
                              <div className="space-y-2">
                                <textarea
                                  rows={4}
                                  value={editingText}
                                  onChange={(e) => setEditingText(e.target.value)}
                                  className="w-full bg-[#F0EDE8] border border-[#CC3300]/40 rounded-xl px-3 py-2 text-sm text-[#1A1410] focus:outline-none focus:border-[#CC3300] resize-none"
                                />
                                <div className="flex gap-2">
                                  <button
                                    onClick={handleSaveEdit}
                                    className="bg-[#CC3300] hover:bg-[#991F00] text-[#F0EDE8] text-xs font-bold px-3 py-1.5 rounded-lg transition-colors"
                                  >
                                    Salvar
                                  </button>
                                  <button
                                    onClick={() => setEditingIndex(null)}
                                    className="border border-[#D4CFC9] text-[#6B5E55] hover:text-[#1A1410] text-xs font-medium px-3 py-1.5 rounded-lg transition-colors"
                                  >
                                    Cancelar
                                  </button>
                                </div>
                              </div>
                            ) : (
                              <p className="text-sm text-[#6B5E55] leading-snug whitespace-pre-wrap">{item.message || "(sem mensagem)"}</p>
                            )}
                          </div>

                          {editingIndex !== i && (
                            <div className="flex flex-col gap-1 shrink-0">
                              <button
                                onClick={() => handleStartEdit(i)}
                                disabled={item.skipped}
                                className="text-xs border border-[#D4CFC9] text-[#6B5E55] hover:text-[#1A1410] hover:border-[#3D4F5A] disabled:opacity-30 px-2 py-1 rounded-lg transition-colors"
                              >
                                Editar
                              </button>
                              <button
                                onClick={() => handleToggleSkip(i)}
                                className={`text-xs px-2 py-1 rounded-lg transition-colors border ${
                                  item.skipped
                                    ? "border-[#CC3300]/40 text-[#CC3300] hover:bg-[#3D0A00]"
                                    : "border-[#F87171]/30 text-[#F87171] hover:bg-[#2D0A0A]"
                                }`}
                              >
                                {item.skipped ? "Incluir" : "Pular"}
                              </button>
                            </div>
                          )}
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}

            {/* Histórico de envios */}
            {sends.length > 0 && (
              <div className="bg-[#E8E4DF] rounded-2xl border border-[#D4CFC9] overflow-hidden">
                <div className="flex items-center justify-between px-4 py-3 border-b border-[#D4CFC9]">
                  <span className="text-sm font-bold text-[#1A1410]">Histórico de Envios</span>
                  <span className="text-sm text-[#6B5E55] font-medium">{sends.length} mensagens</span>
                </div>
                <div className="divide-y divide-[#D4CFC9] max-h-[520px] overflow-y-auto">
                  {[...sends].reverse().map((s, i) => (
                    <div key={i} className="px-4 py-3 flex gap-4 items-start hover:bg-[#D4CFC9]/40 transition-colors">
                      <div className="shrink-0 mt-1.5">
                        <div className={`w-2 h-2 rounded-full ${s.status.startsWith("ENVIADO") ? "bg-[#CC3300]" : "bg-[#F87171]"}`} />
                      </div>
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2 mb-1">
                          <span className="font-semibold text-sm text-[#1A1410]">{s.name || "—"}</span>
                          <span className="text-xs text-[#6B5E55] font-mono">{s.phone}</span>
                          <span className={`text-xs font-semibold ml-auto shrink-0 ${s.status.startsWith("ENVIADO") ? "text-[#CC3300]" : "text-[#F87171]"}`}>
                            {s.status.startsWith("ENVIADO") ? "Enviado" : s.status.startsWith("ERRO") ? "Erro IA" : "Falhou"}
                          </span>
                        </div>
                        {s.message && <p className="text-sm text-[#6B5E55] leading-snug line-clamp-2">{s.message}</p>}
                        <p className="text-xs text-[#6B5E55]/60 mt-1">{new Date(s.timestamp).toLocaleString("pt-BR")}</p>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Logs */}
            <div className="bg-[#E8E4DF] rounded-2xl border border-[#D4CFC9] overflow-hidden">
              <div className="flex items-center justify-between px-4 py-3 border-b border-[#D4CFC9]">
                <span className="text-sm font-bold text-[#1A1410]">Logs brutos</span>
                <span className="text-sm text-[#6B5E55] font-medium">{logs.length} entradas</span>
              </div>
              <div ref={logsContainerRef} className="h-[520px] overflow-y-auto p-4 font-mono text-xs space-y-1 bg-[#0B141A]">
                {logs.length === 0 ? (
                  <p className="text-[#6B5E55]">Sem logs. Inicie uma campanha.</p>
                ) : (
                  logs.map((log, i) => (
                    <div key={i} className="flex gap-3">
                      <span className="text-[#6B5E55]/60 shrink-0">
                        {new Date(log.timestamp).toLocaleTimeString("pt-BR")}
                      </span>
                      <span className={`shrink-0 w-36 truncate ${getLogColor(log.status)}`}>
                        {log.status}
                      </span>
                      {log.name  && <span className="text-[#1A1410] shrink-0">{log.name}</span>}
                      {log.phone && <span className="text-[#6B5E55] shrink-0">{log.phone}</span>}
                      {log.message && <span className="text-[#6B5E55]/80 truncate">{log.message}</span>}
                    </div>
                  ))
                )}
                <div ref={logsEndRef} />
              </div>
            </div>
          </div>
        )}

        {/* ── Tab: SDR ──────────────────────────────────────────────────────── */}
        {tab === "sdr" && (
          <div className="space-y-5">

            {/* Card: Chaves de API LLM */}
            <div className="bg-[#E8E4DF] rounded-2xl border border-[#D4CFC9] p-5">
              <button
                type="button"
                onClick={() => setShowLlmSettings(v => !v)}
                className="w-full flex items-center justify-between gap-4 text-left"
              >
                <div>
                  <h2 className="font-bold text-[#1A1410] mb-1">Chaves de API LLM</h2>
                  <p className="text-sm text-[#6B5E55]">
                    Clique para {showLlmSettings ? "ocultar" : "ver"} provedor, modelo e chave de IA
                  </p>
                </div>
                <span className="text-[#6B5E55] text-lg">{showLlmSettings ? "▲" : "▼"}</span>
              </button>

              {showLlmSettings && <div className="mt-5 pt-5 border-t border-[#D4CFC9]">

              {/* Provedor LLM */}
              <div className="mb-4">
                <label className="block text-sm font-medium text-[#6B5E55] mb-1.5">Provedor de IA</label>
                <select
                  value={llmProvider}
                  onChange={(e) => {
                    if (!canManageCredentials) return;
                    setLlmProvider(e.target.value);
                    setSdrModel(e.target.value === "groq" ? "openai/gpt-oss-120b" : "gpt-5.5");
                  }}
                  disabled={!canManageCredentials}
                  className="w-full bg-[#F0EDE8] border border-[#D4CFC9] text-[#1A1410] rounded-xl px-4 py-2.5 text-sm outline-none focus:border-[#CC3300] disabled:opacity-50"
                >
                  <option value="openai">OpenAI</option>
                  <option value="groq">Groq (ultra-rápido)</option>
                </select>
                {!canManageCredentials && (
                  <p className="text-[#6B5E55] text-xs mt-1">Provedor e chaves são gerenciados por admin.</p>
                )}
              </div>

              {/* API Key do provedor */}
              <div className="mb-4">
                <label className="block text-sm font-medium text-[#6B5E55] mb-1.5">
                  API Key — {llmProvider === "anthropic" ? "Anthropic" : llmProvider === "groq" ? "Groq" : "OpenAI"}
                </label>
                {llmProvider === "openai" && (
                  <input type="text" autoComplete="off" spellCheck={false} data-lpignore="true" data-form-type="other" value={keyOpenai} onChange={(e) => setKeyOpenai(e.target.value)}
                    placeholder="sk-proj-..."
                    disabled={!canManageCredentials}
                    className="w-full bg-[#F0EDE8] border border-[#D4CFC9] text-[#1A1410] placeholder-[#3D4F5A] rounded-xl px-4 py-2.5 text-sm outline-none focus:border-[#CC3300] disabled:opacity-50"
                  />
                )}
                {llmProvider === "anthropic" && (
                  <input type="text" autoComplete="off" spellCheck={false} data-lpignore="true" data-form-type="other" value={keyAnthropic} onChange={(e) => setKeyAnthropic(e.target.value)}
                    placeholder="sk-ant-..."
                    disabled={!canManageCredentials}
                    className="w-full bg-[#F0EDE8] border border-[#D4CFC9] text-[#1A1410] placeholder-[#3D4F5A] rounded-xl px-4 py-2.5 text-sm outline-none focus:border-[#CC3300] disabled:opacity-50"
                  />
                )}
                {llmProvider === "groq" && (
                  <input type="text" autoComplete="off" spellCheck={false} data-lpignore="true" data-form-type="other" value={keyGroq} onChange={(e) => setKeyGroq(e.target.value)}
                    placeholder="gsk_..."
                    disabled={!canManageCredentials}
                    className="w-full bg-[#F0EDE8] border border-[#D4CFC9] text-[#1A1410] placeholder-[#3D4F5A] rounded-xl px-4 py-2.5 text-sm outline-none focus:border-[#CC3300] disabled:opacity-50"
                  />
                )}
                <p className="text-[#6B5E55] text-xs mt-1">Chave própria desta instância. Se vazio, usa a chave da IA Global (conta mestre) como reserva.</p>
              </div>

              {/* Modelo */}
              <div className="mb-4">
                <label className="block text-sm font-medium text-[#6B5E55] mb-1.5">Modelo</label>
                <select
                  value={sdrModel}
                  onChange={(e) => { if (canManageCredentials) setSdrModel(e.target.value); }}
                  disabled={!canManageCredentials}
                  className="w-full bg-[#F0EDE8] border border-[#D4CFC9] text-[#1A1410] rounded-xl px-4 py-2.5 text-sm outline-none focus:border-[#CC3300] disabled:opacity-50"
                >
                  {llmProvider === "openai" && (<>
                    <option value="gpt-4.1-nano">GPT-4.1 Nano (mais econômico)</option>
                    <option value="gpt-5.5">GPT-5.5 (máxima qualidade)</option>
                  </>)}
                  {llmProvider === "anthropic" && (<>
                    <option value="claude-opus-4-8">Claude Opus 4.8 (máxima qualidade)</option>
                  </>)}
                  {llmProvider === "groq" && (<>
                    <option value="openai/gpt-oss-120b">GPT-OSS 120B (recomendado)</option>
                  </>)}
                </select>
                <p className="text-[#6B5E55] text-xs mt-1">Modelo usado pelo agente desta instância.</p>
              </div>

              <div className="flex items-center gap-3 pt-1">
                <button
                  onClick={handleSaveSdr}
                  disabled={sdrSaving}
                  className="bg-[#CC3300] hover:bg-[#991F00] disabled:bg-[#D4CFC9] text-[#F0EDE8] disabled:text-[#6B5E55] font-bold px-6 py-2.5 rounded-xl transition-colors text-sm"
                >
                  {sdrSaving ? "Salvando..." : "Salvar configurações"}
                </button>
                {sdrSaved && <span className="text-xs text-[#CC3300]">✓ Salvo</span>}
                {sdrSaveError && <span className="text-xs text-[#F87171]">Erro ao salvar — tente novamente</span>}
              </div>
              </div>}
            </div>

            {/* Card: Informações da Instância WhatsApp */}
            {waInfo && (
              <div className="bg-[#E8E4DF] rounded-2xl border border-[#D4CFC9] p-5">
                <h2 className="font-bold text-[#1A1410] mb-3">Informações WhatsApp</h2>
                <div className="grid grid-cols-2 gap-3">
                  <div className="bg-[#F0EDE8] rounded-xl px-4 py-3">
                    <p className="text-xs text-[#6B5E55] uppercase tracking-wide mb-1">Status</p>
                    <p className={`text-sm font-semibold ${waInfo.connected ? "text-[#25D366]" : "text-[#CC3300]"}`}>
                      {waInfo.connected ? "Conectado" : "Desconectado"}
                    </p>
                  </div>
                  <div className="bg-[#F0EDE8] rounded-xl px-4 py-3">
                    <p className="text-xs text-[#6B5E55] uppercase tracking-wide mb-1">Nome do Perfil</p>
                    <p className="text-sm font-semibold text-[#1A1410]">{waInfo.profile_name || "—"}</p>
                  </div>
                  <div className="bg-[#F0EDE8] rounded-xl px-4 py-3 col-span-2">
                    <p className="text-xs text-[#6B5E55] uppercase tracking-wide mb-1">Número</p>
                    <p className="text-sm font-semibold text-[#1A1410] font-mono">{waInfo.number || "—"}</p>
                  </div>
                </div>
                {!waInfo.connected && (
                  <div className="mt-4 pt-4 border-t border-[#D4CFC9]">
                    <div className="flex items-center gap-3 flex-wrap">
                      <button
                        onClick={async () => {
                          setReconnecting(true);
                          try {
                            const r = await fetch(`/api/proxy/instance/${id}/reconnect`, { method: "POST" });
                            if (r.ok) {
                              let info = null;
                              for (let attempt = 0; attempt < 5; attempt += 1) {
                                await new Promise(res => setTimeout(res, attempt === 0 ? 1500 : 2000));
                                info = await fetch(`/api/proxy/instance/${id}/whatsapp-info`)
                                  .then(r2 => r2.ok ? r2.json() : null)
                                  .catch(() => null);
                                if (info?.connected) break;
                              }
                              if (info?.connected) {
                                setWaInfo(info);
                                setWaState("open");
                                alert("Reconectado com sucesso!");
                              } else {
                                alert("Não reconectou automaticamente. Se o WhatsApp foi desconectado pelo celular, gere um novo QR Code abaixo.");
                              }
                            } else {
                              const d = await r.json().catch(() => ({}));
                              alert(d.detail || "Não foi possível reconectar automaticamente. Gere um novo QR Code.");
                            }
                          } finally {
                            setReconnecting(false);
                          }
                        }}
                        disabled={reconnecting}
                        className="bg-[#25D366] hover:bg-[#1ea952] disabled:opacity-50 text-white font-bold px-4 py-2 rounded-xl text-sm transition-colors"
                      >
                        {reconnecting ? "Reconectando..." : "Reconectar"}
                      </button>
                      <p className="text-xs text-[#6B5E55] flex-1 min-w-[220px]">
                        Tenta recuperar a sessão existente, sem precisar escanear QR Code. Se o WhatsApp foi
                        <strong> deslogado de verdade pelo celular</strong> (removido de &quot;Aparelhos conectados&quot;),
                        isso não vai funcionar — nesse caso, clique em <strong>&quot;Reconectar WA&quot;</strong> no topo da página
                        pra gerar um QR Code novo.
                      </p>
                    </div>
                  </div>
                )}
              </div>
            )}

            {/* Card: WhatsApp Advanced Settings */}
            <div className="bg-[#E8E4DF] rounded-2xl border border-[#D4CFC9] p-5">
              <button
                type="button"
                onClick={() => setShowWaAdvanced(v => !v)}
                className="w-full flex items-center justify-between gap-4 text-left"
              >
                <div>
                  <h2 className="font-bold text-[#1A1410]">Configurações WhatsApp</h2>
                  <p className="text-xs text-[#6B5E55] mt-0.5">
                    Clique para {showWaAdvanced ? "ocultar" : "ver"} — comportamento da instância no WhatsApp.
                  </p>
                </div>
                <span className="text-[#6B5E55] text-lg">{showWaAdvanced ? "▲" : "▼"}</span>
              </button>

              {showWaAdvanced && <div className="mt-4 pt-4 border-t border-[#D4CFC9]">
              <div className="space-y-2">
                {([
                  { key: "alwaysOnline",    label: "Sempre Online",              desc: "Mantém o número sempre exibido como online" },
                  { key: "rejectCall",      label: "Rejeitar Chamadas",          desc: "Rejeita chamadas recebidas automaticamente" },
                  { key: "readMessages",    label: "Marcar Mensagens como Lidas", desc: "Marca mensagens como lidas ao receber" },
                  { key: "readStatus",      label: "Marcar Status como Lido",    desc: "Marca status/stories como vistos automaticamente" },
                  { key: "groupsIgnore",    label: "Ignorar Grupos",             desc: "Ignora mensagens recebidas em grupos" },
                  { key: "syncFullHistory", label: "Sincronizar Histórico",       desc: "Sincroniza histórico completo ao conectar" },
                ] as { key: keyof typeof waSettings; label: string; desc: string }[]).map(({ key, label, desc }) => (
                  <label key={key} className="flex items-start gap-3 py-2 border-b border-[#D4CFC9] last:border-0 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={!!waSettings[key]}
                      onChange={() => {
                        const next = { ...waSettings, [key]: !waSettings[key] };
                        setWaSettings(next);
                        saveWaSettings(next);
                      }}
                      className="mt-0.5 w-4 h-4 accent-[#CC3300] cursor-pointer"
                    />
                    <div>
                      <p className="text-sm font-medium text-[#1A1410]">{label}</p>
                      <p className="text-xs text-[#6B5E55]">{desc}</p>
                    </div>
                  </label>
                ))}
                {waSettings.rejectCall && (
                  <div className="pt-1">
                    <label className="text-xs font-semibold text-[#6B5E55] uppercase tracking-wide mb-1 block">Mensagem ao rejeitar chamada</label>
                    <input
                      type="text"
                      value={waSettings.msgCall}
                      onChange={e => {
                        const next = { ...waSettings, msgCall: e.target.value };
                        setWaSettings(next);
                        clearTimeout((window as unknown as Record<string, ReturnType<typeof setTimeout>>)._waMsgCallTimer);
                        (window as unknown as Record<string, ReturnType<typeof setTimeout>>)._waMsgCallTimer = setTimeout(() => saveWaSettings(next), 1500);
                      }}
                      placeholder="Ex: Não estou disponível para chamadas, envie uma mensagem."
                      className="w-full bg-[#F0EDE8] border border-[#D4CFC9] rounded-xl px-3 py-2 text-sm text-[#1A1410] placeholder-[#A09080] outline-none focus:border-[#CC3300]"
                    />
                  </div>
                )}
              </div>
              <div className="flex items-center gap-2 mt-3">
                {waSettingsSaving && <span className="text-xs text-[#6B5E55]">Salvando...</span>}
                {waSettingsSaved && <span className="text-xs text-[#25D366] font-medium">Salvo ✓</span>}
                {waSettingsError && <span className="text-xs text-[#F85149] font-medium">{waSettingsError}</span>}
              </div>
              </div>}
            </div>

            {/* Card: Eventos do Webhook */}
            <div className="bg-[#E8E4DF] rounded-2xl border border-[#D4CFC9] p-5">
              <button
                type="button"
                onClick={() => setShowWebhookEvents(v => !v)}
                className="w-full flex items-center justify-between gap-4 text-left mb-4"
              >
                <div>
                  <h2 className="font-bold text-[#1A1410]">Eventos do Webhook</h2>
                  <p className="text-xs text-[#6B5E55] mt-0.5">
                    Clique para {showWebhookEvents ? "ocultar" : "ver"} — quais eventos o Evolution GO enviará para o KLEAD.
                  </p>
                </div>
                <span className="text-[#6B5E55] text-lg">{showWebhookEvents ? "▲" : "▼"}</span>
              </button>

              {showWebhookEvents && <div className="mt-4 pt-4 border-t border-[#D4CFC9]">
                <div className="flex items-center gap-2 mb-2">
                  {webhookEventsSaving && <span className="text-xs text-[#6B5E55]">Salvando...</span>}
                  {webhookEventsSaved  && <span className="text-xs text-[#25D366]">Salvo ✓</span>}
                </div>
                <div className="grid grid-cols-2 sm:grid-cols-3 gap-2 mb-4">
                  {ALL_WEBHOOK_EVENTS.map(evt => (
                    <label key={evt} className="flex items-center gap-2 cursor-pointer select-none">
                      <input
                        type="checkbox"
                        checked={webhookEvents.includes(evt)}
                        onChange={() => setWebhookEvents(prev =>
                          prev.includes(evt) ? prev.filter(e => e !== evt) : [...prev, evt]
                        )}
                        className="w-4 h-4 accent-[#CC3300]"
                      />
                      <span className="text-xs text-[#1A1410] font-mono">{evt}</span>
                    </label>
                  ))}
                </div>
                <button
                  onClick={async () => {
                    setWebhookEventsSaving(true);
                    try {
                      const r = await fetch(`/api/proxy/instance/${id}/webhook-events`, {
                        method: "PUT",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({ events: webhookEvents }),
                      });
                      if (r.ok) { setWebhookEventsSaved(true); setTimeout(() => setWebhookEventsSaved(false), 2000); }
                    } catch { /* silencioso */ } finally { setWebhookEventsSaving(false); }
                  }}
                  className="bg-[#CC3300] hover:bg-[#991F00] text-[#F0EDE8] font-bold px-4 py-2 rounded-xl text-sm transition-colors"
                >
                  Salvar Eventos
                </button>
              </div>}
            </div>

            {/* Card: Webhook Meta */}
            {canManageCredentials && (
              <div className="bg-[#E8E4DF] rounded-2xl border border-[#D4CFC9]">
                <button
                  onClick={() => setShowWebhookMeta(v => !v)}
                  className="w-full flex items-center justify-between px-5 py-4 text-left"
                >
                  <div>
                    <h2 className="font-bold text-[#1A1410]">Configuração do Webhook Meta</h2>
                    <p className="text-xs text-[#6B5E55] mt-0.5">Clique para {showWebhookMeta ? "ocultar" : "ver"} credenciais</p>
                  </div>
                  <span className="text-[#6B5E55] text-lg">{showWebhookMeta ? "▲" : "▼"}</span>
                </button>
                {showWebhookMeta && <div className="px-5 pb-5 border-t border-[#D4CFC9] pt-4">
                <p className="text-sm text-[#6B5E55] mb-4">
                  Cole esses valores no painel Meta for Developers em WhatsApp {">"} Configuracao {">"} Webhooks.
                  Depois habilite a assinatura <code className="text-[#CC3300]">messages</code>.
                </p>
                {metaWebhookInfo ? (
                  <div className="space-y-3">
                    <div>
                      <p className="text-xs font-semibold text-[#6B5E55] uppercase tracking-wide mb-1.5">URL do Webhook</p>
                      <div className="flex gap-2">
                        <code className="flex-1 bg-[#F0EDE8] border border-[#D4CFC9] rounded-xl px-3 py-2 text-xs text-[#CC3300] font-mono break-all">
                          {metaWebhookInfo.webhook_url}
                        </code>
                        <button
                          onClick={() => { navigator.clipboard.writeText(metaWebhookInfo.webhook_url); setMetaCopied("url"); setTimeout(() => setMetaCopied(null), 2000); }}
                          className="shrink-0 border border-[#D4CFC9] text-[#6B5E55] hover:text-[#1A1410] px-3 py-2 rounded-xl text-xs transition-colors"
                        >
                          {metaCopied === "url" ? "✓ Copiado" : "Copiar"}
                        </button>
                      </div>
                    </div>
                      <div>
                        <p className="text-xs font-semibold text-[#6B5E55] uppercase tracking-wide mb-1.5">Verify Token</p>
                        <div className="flex gap-2">
                          <code className="flex-1 bg-[#F0EDE8] border border-[#D4CFC9] rounded-xl px-3 py-2 text-xs text-[#F0B429] font-mono break-all">
                            {metaWebhookInfo.verify_token}
                        </code>
                        <button
                          onClick={() => { navigator.clipboard.writeText(metaWebhookInfo.verify_token); setMetaCopied("token"); setTimeout(() => setMetaCopied(null), 2000); }}
                          className="shrink-0 border border-[#D4CFC9] text-[#6B5E55] hover:text-[#1A1410] px-3 py-2 rounded-xl text-xs transition-colors"
                          >
                            {metaCopied === "token" ? "✓ Copiado" : "Copiar"}
                          </button>
                          <button
                            onClick={async () => {
                              if (!confirm("Gerar um novo Verify Token invalida o atual. Você precisará atualizar o webhook no painel Meta (e reconfigurar o Evolution GO, se aplicável) com o novo valor. Continuar?")) return;
                              setRotatingSecret(true);
                              try {
                                const res = await rotateWebhookSecret(id);
                                if (res.ok) {
                                  setMetaWebhookInfo(prev => prev ? { ...prev, verify_token: res.webhook_secret } : prev);
                                  getWebhookUrl(id).then(setWebhookInfo).catch(() => {});
                                }
                              } finally {
                                setRotatingSecret(false);
                              }
                            }}
                            disabled={rotatingSecret}
                            className="shrink-0 border border-[#D4CFC9] text-[#6B5E55] hover:text-[#1A1410] px-3 py-2 rounded-xl text-xs transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                          >
                            {rotatingSecret ? "Gerando..." : "Gerar novo"}
                          </button>
                        </div>
                      </div>
                      <div>
                        <p className="text-xs font-semibold text-[#6B5E55] uppercase tracking-wide mb-1.5">Page ID da Página</p>
                        <div className="flex gap-2">
                          <code className="flex-1 bg-[#F0EDE8] border border-[#D4CFC9] rounded-xl px-3 py-2 text-xs text-[#1A1410] font-mono break-all">
                            {metaWebhookInfo.page_id || "Não configurado"}
                          </code>
                          <button
                            onClick={() => {
                              if (!metaWebhookInfo.page_id) return;
                              navigator.clipboard.writeText(metaWebhookInfo.page_id);
                              setMetaCopied("page");
                              setTimeout(() => setMetaCopied(null), 2000);
                            }}
                            disabled={!metaWebhookInfo.page_id}
                            className="shrink-0 border border-[#D4CFC9] text-[#6B5E55] hover:text-[#1A1410] px-3 py-2 rounded-xl text-xs transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                          >
                            {metaCopied === "page" ? "✓ Copiado" : "Copiar"}
                          </button>
                        </div>
                      </div>
                    </div>
                  ) : (
                  <div className="bg-[#F0EDE8] rounded-xl p-4 text-sm text-[#6B5E55]">Carregando informações do webhook...</div>
                )}
                </div>}
              </div>
            )}

            {/* Card: Cloudflare Worker — secrets KLEAD */}
            {canManageCredentials && metaWebhookInfo && (
              <div className="bg-[#E8E4DF] rounded-2xl border border-[#D4CFC9]">
                <button
                  onClick={() => setShowCloudflareWorker(v => !v)}
                  className="w-full flex items-center justify-between px-5 py-4 text-left"
                >
                  <div>
                    <h2 className="font-bold text-[#1A1410]">Configuração do Cloudflare Worker</h2>
                    <p className="text-xs text-[#6B5E55] mt-0.5">Clique para {showCloudflareWorker ? "ocultar" : "ver"} credenciais</p>
                  </div>
                  <span className="text-[#6B5E55] text-lg">{showCloudflareWorker ? "▲" : "▼"}</span>
                </button>
                {showCloudflareWorker && <div className="px-5 pb-5 border-t border-[#D4CFC9] pt-4">
                <p className="text-sm text-[#6B5E55] mb-4">
                  Configure os secrets abaixo no Worker da Cloudflare para que leads recebidos via WhatsApp sejam enviados automaticamente ao KLEAD CRM e ao SDR.
                </p>
                <div className="space-y-4">

                  {/* Secret 1: KLEAD_WEBHOOK_URL */}
                  <div>
                    <p className="text-xs font-semibold text-[#6B5E55] uppercase tracking-wide mb-1">Secret <code className="text-[#CC3300] normal-case">KLEAD_WEBHOOK_URL</code> <span className="normal-case font-normal">— encaminha mensagens para o SDR</span></p>
                    <div className="flex gap-2">
                      <code className="flex-1 bg-[#F0EDE8] border border-[#D4CFC9] rounded-xl px-3 py-2 text-xs text-[#CC3300] font-mono break-all">
                        {metaWebhookInfo.webhook_url}
                      </code>
                      <button
                        onClick={() => { navigator.clipboard.writeText(metaWebhookInfo.webhook_url); setMetaCopied("url"); setTimeout(() => setMetaCopied(null), 2000); }}
                        className="shrink-0 border border-[#D4CFC9] text-[#6B5E55] hover:text-[#1A1410] px-3 py-2 rounded-xl text-xs transition-colors"
                      >
                        {metaCopied === "url" ? "✓ Copiado" : "Copiar"}
                      </button>
                    </div>
                  </div>

                  {/* Secret 2: KLEAD_API_URL */}
                  <div>
                    <p className="text-xs font-semibold text-[#6B5E55] uppercase tracking-wide mb-1">Secret <code className="text-[#CC3300] normal-case">KLEAD_API_URL</code> <span className="normal-case font-normal">— base URL do KLEAD</span></p>
                    <div className="flex gap-2">
                      <code className="flex-1 bg-[#F0EDE8] border border-[#D4CFC9] rounded-xl px-3 py-2 text-xs text-[#CC3300] font-mono break-all">
                        https://api.example.com
                      </code>
                      <button
                        onClick={() => { navigator.clipboard.writeText("https://api.example.com"); setMetaCopied("token"); setTimeout(() => setMetaCopied(null), 2000); }}
                        className="shrink-0 border border-[#D4CFC9] text-[#6B5E55] hover:text-[#1A1410] px-3 py-2 rounded-xl text-xs transition-colors"
                      >
                        {metaCopied === "token" ? "✓ Copiado" : "Copiar"}
                      </button>
                    </div>
                  </div>

                  {/* Secret 3: KLEAD_API_KEY */}
                  <div>
                    <p className="text-xs font-semibold text-[#6B5E55] uppercase tracking-wide mb-1">Secret <code className="text-[#CC3300] normal-case">KLEAD_API_KEY</code> <span className="normal-case font-normal">— mesmo valor do Verify Token acima</span></p>
                    <div className="flex gap-2">
                      <code className="flex-1 bg-[#F0EDE8] border border-[#D4CFC9] rounded-xl px-3 py-2 text-xs text-[#F0B429] font-mono break-all">
                        {metaWebhookInfo.verify_token}
                      </code>
                      <button
                        onClick={() => { navigator.clipboard.writeText(metaWebhookInfo.verify_token); setMetaCopied("token"); setTimeout(() => setMetaCopied(null), 2000); }}
                        className="shrink-0 border border-[#D4CFC9] text-[#6B5E55] hover:text-[#1A1410] px-3 py-2 rounded-xl text-xs transition-colors"
                      >
                        {metaCopied === "token" ? "✓ Copiado" : "Copiar"}
                      </button>
                    </div>
                  </div>

                  {/* Secret 4: KLEAD_CRM_INSTANCE */}
                  <div>
                    <p className="text-xs font-semibold text-[#6B5E55] uppercase tracking-wide mb-1">Secret <code className="text-[#CC3300] normal-case">KLEAD_CRM_INSTANCE</code> <span className="normal-case font-normal">— nome desta instância</span></p>
                    <div className="flex gap-2">
                      <code className="flex-1 bg-[#F0EDE8] border border-[#D4CFC9] rounded-xl px-3 py-2 text-xs text-[#CC3300] font-mono break-all">
                        {id}
                      </code>
                      <button
                        onClick={() => { navigator.clipboard.writeText(id); setMetaCopied(null); }}
                        className="shrink-0 border border-[#D4CFC9] text-[#6B5E55] hover:text-[#1A1410] px-3 py-2 rounded-xl text-xs transition-colors"
                      >
                        Copiar
                      </button>
                    </div>
                  </div>

                  {/* Instruções */}
                  <div className="bg-[#F0EDE8] border border-[#D4CFC9] rounded-xl p-4 space-y-2 text-xs text-[#6B5E55]">
                    <p className="font-semibold text-[#1A1410]">Como configurar:</p>
                    <p>1. Acesse <span className="text-[#CC3300]">dash.cloudflare.com</span> → Workers &amp; Pages → <span className="text-[#CC3300]">server-edge-tracker</span></p>
                    <p>2. Vá em <span className="text-[#1A1410]">Settings → Variables and Secrets</span></p>
                    <p>3. Para cada secret acima: clique em <span className="text-[#1A1410]">Add</span> → tipo <span className="text-[#F0B429]">Secret</span> → cole o nome e valor</p>
                    <p>4. Salve — leads do WhatsApp irão direto para o CRM e SDR automaticamente</p>
                  </div>
                </div>
                </div>}
              </div>
            )}

            {/* Card: Webhook Setup (Evolution GO) */}
            {canManageCredentials && (
            <div className="bg-[#E8E4DF] rounded-2xl border border-[#D4CFC9]">
              <button
                onClick={() => setShowWebhookEvo(v => !v)}
                className="w-full flex items-center justify-between px-5 py-4 text-left"
              >
                <div>
                  <h2 className="font-bold text-[#1A1410]">Configuração do Webhook</h2>
                  <p className="text-xs text-[#6B5E55] mt-0.5">Clique para {showWebhookEvo ? "ocultar" : "ver"} credenciais</p>
                </div>
                <span className="text-[#6B5E55] text-lg">{showWebhookEvo ? "▲" : "▼"}</span>
              </button>
              {showWebhookEvo && <div className="px-5 pb-5 border-t border-[#D4CFC9] pt-4">
              <p className="text-sm text-[#6B5E55] mb-4">
                O webhook é o canal pelo qual o WhatsApp notifica o KLEAD quando um lead responde.
                Deve ser configurado uma vez no banco do Evolution GO. Se o SDR parar de responder, reconfigure aqui.
              </p>

              {webhookInfo ? (
                <div className="space-y-3">
                  <div>
                    <p className="text-xs font-semibold text-[#6B5E55] uppercase tracking-wide mb-1.5">URL do Webhook (com token)</p>
                    <div className="flex gap-2">
                      <code className="flex-1 bg-[#F0EDE8] border border-[#D4CFC9] rounded-xl px-3 py-2 text-xs text-[#CC3300] font-mono break-all">
                        {webhookInfo.webhook_url}
                      </code>
                      <button
                        onClick={() => { navigator.clipboard.writeText(webhookInfo.webhook_url); setWebhookCopied(true); setTimeout(() => setWebhookCopied(false), 2000); }}
                        className="shrink-0 border border-[#D4CFC9] text-[#6B5E55] hover:text-[#1A1410] px-3 py-2 rounded-xl text-xs transition-colors"
                      >
                        {webhookCopied ? "✓ Copiado" : "Copiar"}
                      </button>
                    </div>
                  </div>
                  <div>
                    <p className="text-xs font-semibold text-[#6B5E55] uppercase tracking-wide mb-1.5">Comando para reconfigurar (rode no terminal)</p>
                    <div className="flex gap-2">
                      <code className="flex-1 bg-[#F0EDE8] border border-[#D4CFC9] rounded-xl px-3 py-2 text-xs text-[#60A5FA] font-mono break-all">
                        {webhookInfo.fix_command}
                      </code>
                      <button
                        onClick={() => navigator.clipboard.writeText(webhookInfo.fix_command)}
                        className="shrink-0 border border-[#D4CFC9] text-[#6B5E55] hover:text-[#1A1410] px-3 py-2 rounded-xl text-xs transition-colors"
                      >
                        Copiar
                      </button>
                    </div>
                  </div>
                  <p className="text-xs text-[#6B5E55]/70">
                    Ou rode <code className="text-[#60A5FA]">python scripts/fix-webhook.py</code> na raiz do projeto para reconfigurar todas as instâncias de uma vez.
                  </p>
                </div>
              ) : (
                <div className="bg-[#F0EDE8] rounded-xl p-4 text-sm text-[#6B5E55]">Carregando informações do webhook...</div>
              )}
              </div>}
            </div>
            )}

            {/* Cards do SDR autônomo (Agente de Qualificação, Follow-up,
                Critérios de Pontuação) — ocultos no plano Lite, que não tem
                SDR autônomo. Chaves de IA e Base de Conhecimento seguem visíveis. */}
            {!isLite && (<>
            {/* Card: Configuração do Agente */}
            <div className="bg-[#E8E4DF] rounded-2xl border border-[#D4CFC9] p-5">
              <button
                type="button"
                onClick={() => setShowQualificationSettings(v => !v)}
                className="w-full flex items-center justify-between gap-4 text-left"
              >
                <div>
                  <h2 className="font-bold text-[#1A1410] mb-1">Agente de Qualificação</h2>
                  <p className="text-sm text-[#6B5E55]">
                    Clique para {showQualificationSettings ? "ocultar" : "ver"} configurações avançadas
                  </p>
                </div>
                <span className="text-[#6B5E55] text-lg">{showQualificationSettings ? "▲" : "▼"}</span>
              </button>

              {showQualificationSettings && <div className="mt-5 pt-5 border-t border-[#D4CFC9]">

              {/* Toggle */}
              <div className="flex items-center justify-between bg-[#F0EDE8] rounded-xl px-4 py-3 mb-4">
                <div>
                  <p className="text-[#1A1410] font-semibold text-sm">IA Inbound Ativada</p>
                  <p className="text-[#6B5E55] text-xs mt-0.5">Responde automaticamente quando um lead te mandar mensagem</p>
                </div>
                <button
                  onClick={async () => {
                    const v = !sdrEnabled;
                    setSdrEnabled(v);
                    const keywords = handoffKeywords.split(",").map((k: string) => k.trim()).filter(Boolean);
                    try {
                      await updateSdrConfig(id, { inbound_ai_enabled: v, qualification_prompt: qualificationPrompt, max_ai_turns: maxAiTurns, ai_response_delay: aiResponseDelay, auto_handoff_keywords: keywords, model: sdrModel, llm_provider: llmProvider, llm_api_key: llmApiKey, key_openai: keyOpenai, key_anthropic: keyAnthropic, key_groq: keyGroq, followup_enabled: followupEnabled, followup_delay_hours: followupDelayHours, followup_max: followupMax, auto_release_minutes: autoReleaseMinutes, scoring_criteria: scoringCriteria });
                      setSdrSaved(true); setTimeout(() => setSdrSaved(false), 2000);
                    } catch {
                      setSdrEnabled(!v);
                      alert("Erro ao salvar. Tente novamente.");
                    }
                  }}
                  className={`w-12 h-6 rounded-full transition-colors relative ${sdrEnabled ? "bg-[#CC3300]" : "bg-[#D4CFC9]"}`}
                >
                  <span className={`absolute top-0.5 left-0.5 w-5 h-5 bg-white rounded-full shadow transition-transform ${sdrEnabled ? "translate-x-6" : "translate-x-0"}`} />
                </button>
              </div>

              {/* Contexto automático */}
              <div className="flex gap-3 mb-4">
                <div className="flex-1 bg-[#F0EDE8] rounded-xl px-4 py-3 flex items-start gap-2">
                  <span className="text-lg leading-none mt-0.5">{(() => { const h = new Date().getHours(); return h >= 6 && h < 12 ? "🌅" : h >= 12 && h < 18 ? "☀️" : h >= 18 && h < 24 ? "🌙" : "🌚"; })()}</span>
                  <div>
                    <p className="text-[#1A1410] text-xs font-semibold">Período atual</p>
                    <p className="text-[#CC3300] text-xs mt-0.5">{(() => { const h = new Date().getHours(); return h >= 6 && h < 12 ? "Manhã — IA usa \"bom dia\"" : h >= 12 && h < 18 ? "Tarde — IA usa \"boa tarde\"" : h >= 18 && h < 24 ? "Noite — IA usa \"boa noite\"" : "Madrugada — IA não inicia, atende se o lead escrever"; })()}</p>
                  </div>
                </div>
                <div className="flex-1 bg-[#F0EDE8] rounded-xl px-4 py-3 flex items-start gap-2">
                  <span className="text-lg leading-none mt-0.5">👤</span>
                  <div>
                    <p className="text-[#1A1410] text-xs font-semibold">Nome do lead</p>
                    <p className="text-[#CC3300] text-xs mt-0.5">Injetado automaticamente — a IA usa o primeiro nome do lead ao longo da conversa</p>
                  </div>
                </div>
              </div>

              {/* Prompt */}
              <div className="mb-4">
                <div className="flex items-center justify-between mb-1.5">
                  <label className="text-sm font-medium text-[#6B5E55]">Prompt de Qualificação</label>
                  <span className={`text-xs px-2 py-1 rounded-lg transition-colors ${
                    sdrSaveError ? "text-[#F87171]" : sdrSaving ? "text-[#F0B429]" : sdrSaved ? "text-[#CC3300]" : "text-[#3D4F5A]"
                  }`}>
                    {sdrSaveError ? "Erro ao salvar" : sdrSaving ? "Salvando..." : sdrSaved ? "✓ Salvo" : "Auto-save"}
                  </span>
                </div>
                <textarea
                  value={qualificationPrompt}
                  onChange={(e) => setQualificationPrompt(e.target.value)}
                  rows={8}
                  placeholder={`Você é um SDR da [Empresa], especialista em [produto/serviço].

Seu objetivo é qualificar leads que responderam nossa campanha.
Conduza a conversa de forma natural, como um humano faria.

Faça até 4 perguntas — uma por mensagem, nunca todas de uma vez:
1. Qual é o principal desafio que você enfrenta hoje com [tema]?
2. Você já tem alguma solução em uso ou está buscando do zero?
3. Qual seria o prazo ideal para resolver isso?
4. Você é o responsável pela decisão de compra?

Critérios para handoff (transferir para humano):
- Lead demonstrou interesse real e tem prazo/budget definidos
- Lead pediu mais informações, preço ou uma demonstração
- Score >= 70

Critérios para disqualify:
- Lead claramente sem interesse após 3 mensagens
- Não é o decisor e não pode indicar quem é
- Score <= 20`}
                  className="w-full bg-[#F0EDE8] border border-[#D4CFC9] text-[#1A1410] placeholder-[#3D4F5A] rounded-xl px-4 py-3 text-sm outline-none focus:border-[#CC3300] resize-y leading-relaxed"
                />
              </div>

              <div className="grid grid-cols-2 gap-4 mb-4">
                <div>
                  <label className="block text-sm font-medium text-[#6B5E55] mb-1.5">Máx. turnos da IA</label>
                  <input
                    type="number" min={1} max={20}
                    value={maxAiTurns}
                    onChange={(e) => setMaxAiTurns(Number(e.target.value))}
                    className="w-full bg-[#F0EDE8] border border-[#D4CFC9] text-[#1A1410] rounded-xl px-4 py-2.5 text-sm outline-none focus:border-[#CC3300]"
                  />
                  <p className="text-[#6B5E55] text-xs mt-1">Após este número de trocas, transfere para humano</p>
                </div>
                <div>
                  <label className="block text-sm font-medium text-[#6B5E55] mb-1.5">Delay de resposta (seg)</label>
                  <input
                    type="number" min={0} max={30}
                    value={aiResponseDelay}
                    onChange={(e) => setAiResponseDelay(Number(e.target.value))}
                    className="w-full bg-[#F0EDE8] border border-[#D4CFC9] text-[#1A1410] rounded-xl px-4 py-2.5 text-sm outline-none focus:border-[#CC3300]"
                  />
                  <p className="text-[#6B5E55] text-xs mt-1">Segundos de espera antes de responder (humaniza)</p>
                </div>
              </div>

              {/* Keywords */}
              <div className="mb-4">
                <label className="block text-sm font-medium text-[#6B5E55] mb-1.5">Keywords de handoff imediato</label>
                <input
                  type="text"
                  value={handoffKeywords}
                  onChange={(e) => setHandoffKeywords(e.target.value)}
                  placeholder="quero comprar, quanto custa, me liga, valor"
                  className="w-full bg-[#F0EDE8] border border-[#D4CFC9] text-[#1A1410] placeholder-[#3D4F5A] rounded-xl px-4 py-2.5 text-sm outline-none focus:border-[#CC3300]"
                />
                <p className="text-[#6B5E55] text-xs mt-1">Palavras separadas por vírgula — ao detectar qualquer uma, transfere imediatamente para humano</p>
              </div>

              <div className="flex items-center gap-3 pt-1">
                <button
                  onClick={handleSaveSdr}
                  disabled={sdrSaving}
                  className="bg-[#CC3300] hover:bg-[#991F00] disabled:bg-[#D4CFC9] text-[#F0EDE8] disabled:text-[#6B5E55] font-bold px-6 py-2.5 rounded-xl transition-colors text-sm"
                >
                  {sdrSaving ? "Salvando..." : "Salvar configurações"}
                </button>
                {sdrSaved && <span className="text-xs text-[#CC3300]">✓ Salvo</span>}
                {sdrSaveError && <span className="text-xs text-[#F87171]">Erro ao salvar — tente novamente</span>}
                <span className="text-xs text-[#3D4F5A] ml-auto">Prompts salvam automaticamente ao digitar</span>
              </div>
              </div>}
            </div>

            {/* Card: Follow-up Automático */}
            <div className="bg-[#E8E4DF] rounded-2xl border border-[#D4CFC9] p-5">
              <button
                type="button"
                onClick={() => setShowFollowupSettings(v => !v)}
                className="w-full flex items-center justify-between gap-4 text-left"
              >
                <div>
                  <h2 className="font-bold text-[#1A1410] mb-1">Follow-up Automático</h2>
                  <p className="text-sm text-[#6B5E55]">
                    Clique para {showFollowupSettings ? "ocultar" : "ver"} configurações avançadas
                  </p>
                </div>
                <span className="text-[#6B5E55] text-lg">{showFollowupSettings ? "▲" : "▼"}</span>
              </button>

              {showFollowupSettings && <div className="mt-5 pt-5 border-t border-[#D4CFC9]">

              <div className="flex items-center justify-between bg-[#F0EDE8] rounded-xl px-4 py-3 mb-4">
                <div>
                  <p className="text-[#1A1410] font-semibold text-sm">Follow-up ativado</p>
                  <p className="text-[#6B5E55] text-xs mt-0.5">IA recontata leads silenciosos automaticamente</p>
                </div>
                <button
                  onClick={async () => {
                    const v = !followupEnabled;
                    setFollowupEnabled(v);
                    const keywords = handoffKeywords.split(",").map((k: string) => k.trim()).filter(Boolean);
                    try {
                      await updateSdrConfig(id, { inbound_ai_enabled: sdrEnabled, qualification_prompt: qualificationPrompt, max_ai_turns: maxAiTurns, ai_response_delay: aiResponseDelay, auto_handoff_keywords: keywords, model: sdrModel, llm_provider: llmProvider, llm_api_key: llmApiKey, key_openai: keyOpenai, key_anthropic: keyAnthropic, key_groq: keyGroq, followup_enabled: v, followup_delay_hours: followupDelayHours, followup_max: followupMax, auto_release_minutes: autoReleaseMinutes, scoring_criteria: scoringCriteria });
                      setSdrSaved(true); setTimeout(() => setSdrSaved(false), 2000);
                    } catch {
                      setFollowupEnabled(!v);
                      alert("Erro ao salvar. Tente novamente.");
                    }
                  }}
                  className={`w-12 h-6 rounded-full transition-colors relative ${followupEnabled ? "bg-[#CC3300]" : "bg-[#D4CFC9]"}`}
                >
                  <span className={`absolute top-0.5 left-0.5 w-5 h-5 bg-white rounded-full shadow transition-transform ${followupEnabled ? "translate-x-6" : "translate-x-0"}`} />
                </button>
              </div>

              {followupEnabled && (
                <div className="grid grid-cols-2 gap-4">
                  <div>
                    <label className="block text-sm font-medium text-[#6B5E55] mb-1.5">Aguardar (horas)</label>
                    <input
                      type="number" min={1} max={168}
                      value={followupDelayHours}
                      onChange={(e) => setFollowupDelayHours(Number(e.target.value))}
                      className="w-full bg-[#F0EDE8] border border-[#D4CFC9] text-[#1A1410] rounded-xl px-4 py-2.5 text-sm outline-none focus:border-[#CC3300]"
                    />
                    <p className="text-[#6B5E55] text-xs mt-1">Horas sem resposta para recontato</p>
                  </div>
                  <div>
                    <label className="block text-sm font-medium text-[#6B5E55] mb-1.5">Máx. follow-ups</label>
                    <input
                      type="number" min={1} max={10}
                      value={followupMax}
                      onChange={(e) => setFollowupMax(Number(e.target.value))}
                      className="w-full bg-[#F0EDE8] border border-[#D4CFC9] text-[#1A1410] rounded-xl px-4 py-2.5 text-sm outline-none focus:border-[#CC3300]"
                    />
                    <p className="text-[#6B5E55] text-xs mt-1">Total de tentativas por lead</p>
                  </div>
                </div>
              )}

              {/* Auto-retomada da IA */}
              <div className="mt-4 grid grid-cols-2 gap-4">
                <div>
                  <label className="block text-sm font-medium text-[#6B5E55] mb-1.5">Retomar IA após (minutos)</label>
                  <input
                    type="number" min={0} max={60}
                    value={autoReleaseMinutes}
                    onChange={(e) => setAutoReleaseMinutes(Number(e.target.value))}
                    className="w-full bg-[#F0EDE8] border border-[#D4CFC9] text-[#1A1410] rounded-xl px-4 py-2.5 text-sm outline-none focus:border-[#CC3300]"
                  />
                  <p className="text-[#6B5E55] text-xs mt-1">Se o operador ficar X min sem enviar mensagem, a IA retoma automaticamente. 0 = desabilitado.</p>
                </div>
              </div>

              {/* TTS — resposta em áudio */}
              <div className="mt-4 flex items-start justify-between gap-4 bg-[#F0EDE8] rounded-xl p-4 border border-[#D4CFC9]">
                <div>
                  <p className="text-[#1A1410] text-sm font-semibold flex items-center gap-2">
                    <svg viewBox="0 0 24 24" className="w-4 h-4 fill-[#CC3300]"><path d="M12 14c1.66 0 3-1.34 3-3V5c0-1.66-1.34-3-3-3S9 3.34 9 5v6c0 1.66 1.34 3 3 3zm-1-9c0-.55.45-1 1-1s1 .45 1 1v6c0 .55-.45 1-1 1s-1-.45-1-1V5zm6 6c0 2.76-2.24 5-5 5s-5-2.24-5-5H5c0 3.53 2.61 6.43 6 6.92V21h2v-3.08c3.39-.49 6-3.39 6-6.92h-2z"/></svg>
                    Responder áudio com áudio (TTS)
                  </p>
                  <p className="text-[#6B5E55] text-xs mt-1">
                    Quando o lead enviar um áudio, a IA responde em texto <span className="text-[#1A1410]">e</span> também em voz (OpenAI TTS). Requer chave OpenAI.
                  </p>
                </div>
                <button
                  type="button"
                  onClick={() => setTtsEnabled(v => !v)}
                  className={`relative w-12 h-6 rounded-full transition-colors flex-shrink-0 ${ttsEnabled ? "bg-[#CC3300]" : "bg-[#D4CFC9]"}`}
                >
                  <span className={`absolute top-0.5 w-5 h-5 rounded-full bg-white shadow transition-transform ${ttsEnabled ? "translate-x-6" : "translate-x-0.5"}`} />
                </button>
              </div>
              </div>}
            </div>

            {/* Card: Critérios de Pontuação */}
            <div className="bg-[#E8E4DF] rounded-2xl border border-[#D4CFC9] p-5">
              <button
                type="button"
                onClick={() => setShowScoringSettings(v => !v)}
                className="w-full flex items-center justify-between gap-4 text-left"
              >
                <div>
                  <h2 className="font-bold text-[#1A1410] mb-1">Critérios de Pontuação</h2>
                  <p className="text-sm text-[#6B5E55]">
                    Clique para {showScoringSettings ? "ocultar" : "ver"} configurações avançadas
                  </p>
                </div>
                <span className="text-[#6B5E55] text-lg">{showScoringSettings ? "▲" : "▼"}</span>
              </button>

              {showScoringSettings && <div className="mt-5 pt-5 border-t border-[#D4CFC9]">
              <textarea
                value={scoringCriteria}
                onChange={(e) => setScoringCriteria(e.target.value)}
                rows={10}
                placeholder={`Exemplo:\n\nPontos positivos:\n- Tem equipe > 5 pessoas: +30\n- Confirmou orçamento disponível: +25\n- Quer agendar demo: +20\n- É o decisor: +15\n\nPontos negativos:\n- Sem orçamento: -40\n- Não é o decisor: -20\n- Apenas curioso: -30\n\nAções automáticas:\n- Score >= 70: handoff\n- Score <= 20 após 3 mensagens: disqualify`}
                className="w-full bg-[#F0EDE8] border border-[#D4CFC9] text-[#1A1410] placeholder-[#3D4F5A] rounded-xl px-4 py-3 text-sm resize-none outline-none focus:border-[#CC3300] font-mono leading-relaxed"
              />
              <p className="text-[#6B5E55] text-xs mt-2">
                Os critérios são injetados no system prompt da IA. Salve na seção acima para aplicar.
              </p>
              </div>}
            </div>
            </>)}

            {/* Card: Base de Conhecimento */}
            <div className="bg-[#E8E4DF] rounded-2xl border border-[#D4CFC9] p-5">
              <button
                type="button"
                onClick={() => setShowKnowledgeSettings(v => !v)}
                className="w-full flex items-center justify-between gap-4 text-left"
              >
                <div>
                  <h2 className="font-bold text-[#1A1410] mb-1">Base de Conhecimento</h2>
                  <p className="text-sm text-[#6B5E55]">
                    Clique para {showKnowledgeSettings ? "ocultar" : "ver"} documentos e configuração
                  </p>
                </div>
                <span className="text-[#6B5E55] text-lg">{showKnowledgeSettings ? "▲" : "▼"}</span>
              </button>

              {showKnowledgeSettings && <div className="mt-5 pt-5 border-t border-[#D4CFC9]">

              {/* Upload */}
              <label className={`flex items-center gap-3 bg-[#F0EDE8] border-2 border-dashed border-[#D4CFC9] hover:border-[#CC3300] rounded-xl px-4 py-3 cursor-pointer transition-colors mb-4 ${kbUploading ? "opacity-50 pointer-events-none" : ""}`}>
                <span className="text-[#CC3300] text-lg">+</span>
                <div>
                  <p className="text-[#1A1410] text-sm font-medium">{kbUploading ? "Enviando..." : "Adicionar documento"}</p>
                  <p className="text-[#6B5E55] text-xs">PDF, TXT, DOCX — máx. injeção: 20 000 caracteres</p>
                </div>
                <input type="file" accept=".pdf,.txt,.md,.docx" className="hidden" onChange={handleKnowledgeUpload} disabled={kbUploading} />
              </label>

              {/* Lista de arquivos */}
              {knowledgeFiles.length === 0 ? (
                <p className="text-[#3D4F5A] text-sm text-center py-2">Nenhum documento ainda.</p>
              ) : (
                <div className="space-y-2">
                  {knowledgeFiles.map((f) => (
                    <div key={f.filename} className="flex items-center justify-between bg-[#F0EDE8] rounded-xl px-4 py-2.5">
                      <div>
                        <p className="text-[#1A1410] text-sm">{f.filename}</p>
                        <p className="text-[#6B5E55] text-xs">{(f.size / 1024).toFixed(1)} KB</p>
                      </div>
                      <button
                        onClick={() => handleKnowledgeDelete(f.filename)}
                        className="text-red-700 hover:text-red-800 text-sm px-2 py-1 transition-colors"
                      >
                        Remover
                      </button>
                    </div>
                  ))}
                </div>
              )}
              </div>}
            </div>

            {/* Card: Zona de Perigo */}
            <div className="bg-[#E8E4DF] rounded-2xl border border-[#CC3300]/30 p-5">
              <button
                type="button"
                onClick={() => setShowDangerZone(v => !v)}
                className="w-full flex items-center justify-between gap-4 text-left"
              >
                <div>
                  <h2 className="font-bold text-[#CC3300] mb-1">Zona de Perigo</h2>
                  <p className="text-sm text-[#6B5E55]">
                    Clique para {showDangerZone ? "ocultar" : "ver"} desconexão e exclusão da instância
                  </p>
                </div>
                <span className="text-[#CC3300] text-lg">{showDangerZone ? "▲" : "▼"}</span>
              </button>

              {showDangerZone && <div className="mt-4 pt-4 border-t border-[#D4CFC9] space-y-3">
                <div className="flex items-center justify-between gap-4 py-3 border-b border-[#D4CFC9]">
                  <div>
                    <p className="text-sm font-medium text-[#1A1410]">Desconectar Instância</p>
                    <p className="text-xs text-[#6B5E55]">Desconecta do WhatsApp sem apagar os dados. Será necessário escanear QR novamente.</p>
                  </div>
                  <button
                    onClick={async () => {
                      if (!confirm("Desconectar esta instância do WhatsApp?")) return;
                      const r = await fetch(`/api/proxy/instance/${id}/disconnect`, { method: "POST" });
                      if (r.ok) { setWaInfo(prev => prev ? { ...prev, connected: false, number: "", profile_name: "" } : null); alert("Instância desconectada."); }
                      else { const d = await r.json().catch(() => ({})); alert(d.detail || "Erro ao desconectar."); }
                    }}
                    className="shrink-0 border border-[#CC3300] text-[#CC3300] hover:bg-[#CC3300] hover:text-white font-bold px-4 py-2 rounded-xl text-sm transition-colors"
                  >
                    Desconectar
                  </button>
                </div>
                <div className="flex items-center justify-between gap-4 py-3">
                  <div>
                    <p className="text-sm font-medium text-[#1A1410]">Apagar Instância</p>
                    <p className="text-xs text-[#6B5E55]">Remove permanentemente esta instância e todas as configurações. Ação irreversível.</p>
                  </div>
                  <button
                    disabled={deletingInstance}
                    onClick={async () => {
                      if (!confirm(`Apagar a instância "${id}" permanentemente?\n\nEsta ação não pode ser desfeita.`)) return;
                      if (!confirm(`Tem certeza? Todos os dados de "${id}" serão perdidos.`)) return;
                      setDeletingInstance(true);
                      try {
                        await deleteInstance(id);
                        router.push("/");
                      } catch (error) {
                        const detail = error instanceof Error ? error.message : "";
                        alert(detail || "Erro ao apagar instância. Tente novamente.");
                        setDeletingInstance(false);
                      }
                    }}
                    className="shrink-0 bg-[#3D0A0A] border border-[#F87171]/40 text-[#F87171] hover:bg-[#F87171] hover:text-white disabled:opacity-50 font-bold px-4 py-2 rounded-xl text-sm transition-colors"
                  >
                    {deletingInstance ? "Apagando..." : "Apagar"}
                  </button>
                </div>
              </div>}
            </div>
          </div>
        )}

      </main>

      {/* Modal: Importar do CRM */}
      {showImportCrm && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
          <div className="bg-[#E8E4DF] rounded-2xl p-6 w-full max-w-md shadow-xl border border-[#D4CFC9]">
            <h3 className="text-[#1A1410] font-bold text-lg mb-1">Importar do CRM</h3>
            <p className="text-[#6B5E55] text-sm mb-4">Selecione uma lista salva no CRM para importar como contatos.</p>
            {loadingListas ? (
              <p className="text-[#6B5E55] text-sm text-center py-6">Carregando listas...</p>
            ) : crmListas.length === 0 ? (
              <p className="text-[#6B5E55] text-sm text-center py-6">Nenhuma lista salva no CRM ainda.<br/>Vá ao CRM, filtre os leads e clique em &quot;+ Lista&quot;.</p>
            ) : (
              <div className="space-y-2 max-h-64 overflow-y-auto mb-4">
                {crmListas.map((lista) => (
                  <button
                    key={lista.id}
                    onClick={() => handleImportLista(lista)}
                    disabled={importingLista}
                    className="w-full text-left bg-[#F0EDE8] hover:bg-[#D4CFC9] border border-[#D4CFC9] rounded-xl px-4 py-3 transition-colors disabled:opacity-40"
                  >
                    <p className="text-[#1A1410] font-semibold text-sm">{lista.name}</p>
                    <p className="text-[#6B5E55] text-xs mt-0.5">
                      {lista.filtros.origem ? `Origem: ${lista.filtros.origem}` : "Todos os leads"}
                      {" · "}criada em {lista.created_at.slice(0, 10)}
                    </p>
                  </button>
                ))}
              </div>
            )}
            <div className="flex justify-end">
              <button
                onClick={() => setShowImportCrm(false)}
                className="px-4 py-2 rounded-xl text-sm text-[#6B5E55] hover:text-[#1A1410] transition-colors"
              >
                Fechar
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
