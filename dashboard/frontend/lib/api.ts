const API = "/api/proxy";
const FETCH_TIMEOUT_MS = 30000;
const FETCH_TIMEOUT_UPLOAD_MS = 200000; // > timeout do backend (180s) pro envio de mídia via Evolution GO
const FETCH_TIMEOUT_AI_MS = 120000; // análise/chat Meta Ads: Graph API (~25s pior caso) + geração LLM passam fácil dos 30s

function getToken(): string {
  if (typeof document === "undefined") return "";
  const match = document.cookie.match(/(?:^|;\s*)klead_token=([^;]*)/);
  return match ? decodeURIComponent(match[1]) : "";
}

function extraHeaders(extra?: Record<string, string>): Record<string, string> {
  const token = getToken();
  return { ...(token ? { "X-Auth-Token": token } : {}), ...extra };
}

async function apiFetch(input: string, init?: RequestInit, timeoutMs = FETCH_TIMEOUT_MS): Promise<Response> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(new DOMException("Timeout", "TimeoutError")), timeoutMs);
  try {
    const r = await fetch(input, { ...init, signal: controller.signal });
    if (!r.ok) {
      let detail = "";
      try {
        const body = (await r.clone().json()).detail;
        // FastAPI manda string em erros normais, mas em erro de validação (422)
        // "detail" é uma lista de objetos {loc, msg, type} — sem isso vira
        // "[object Object]" quando convertido pra string direto.
        if (typeof body === "string") detail = body;
        else if (Array.isArray(body)) detail = body.map(e => e?.msg ?? JSON.stringify(e)).join("; ");
        else if (body) detail = JSON.stringify(body);
      } catch { /* ignore */ }
      throw new Error(detail || `HTTP ${r.status}`);
    }
    return r;
  } finally {
    clearTimeout(timer);
  }
}

export async function getInstances() {
  const r = await apiFetch(`${API}/instances`, { headers: extraHeaders() });
  return r.json();
}

let _instancesCache: { data: Record<string, unknown>; ts: number } | null = null;
let _instancesInflight: Promise<Record<string, unknown>> | null = null;
const INSTANCES_CACHE_TTL_MS = 60000;

/**
 * Igual getInstances(), mas com cache curto (60s) + coalescing de requests em
 * voo. Usado onde vários componentes pequenos (um por card, por exemplo)
 * precisam só da lista de instâncias pra popular um dropdown — sem isso, N
 * cards na tela disparavam N requests idênticos ao mesmo tempo.
 * Não usar em telas que precisam do dado garantidamente fresco (ex: logo
 * após criar/apagar uma instância) — nesses casos use getInstances() direto.
 */
export async function getInstancesCached(): Promise<Record<string, unknown>> {
  const now = Date.now();
  if (_instancesCache && now - _instancesCache.ts < INSTANCES_CACHE_TTL_MS) {
    return _instancesCache.data;
  }
  if (_instancesInflight) return _instancesInflight;
  _instancesInflight = getInstances()
    .then((data) => {
      _instancesCache = { data, ts: Date.now() };
      _instancesInflight = null;
      return data;
    })
    .catch((err) => {
      _instancesInflight = null;
      throw err;
    });
  return _instancesInflight;
}

export type CurrentUser = {
  email: string;
  nome: string;
  avatar?: string;
  funcao: string;
  plano?: string;
  max_instances?: number;
  allowed_instances?: string[];
  owned_instances?: string[];
};

export async function getMe(): Promise<CurrentUser> {
  const r = await apiFetch(`${API}/auth/me`, { headers: extraHeaders() });
  return r.json();
}

export type AdminOverviewInstance = {
  instance_id: string;
  owner: string;
  provider: string;
  inbound_ai_enabled: boolean;
  lead_count: number;
  conversation_count: number;
  messages_7d: number;
  last_activity: string;
};

export type AdminOverview = {
  instances: AdminOverviewInstance[];
  totals: { instance_count: number; lead_count: number; messages_7d: number };
};

export async function getAdminOverview(): Promise<AdminOverview> {
  const r = await apiFetch(`${API}/admin/overview`, { headers: extraHeaders() });
  return r.json();
}

export type AuditEvent = {
  at: string;
  actor_email: string;
  actor_funcao: string;
  action: string;
  target_type: string;
  target_id: string;
  instancia_id: string;
  detail: string;
  meta: unknown;
  ip: string;
};

export async function getAuditEvents(params: {
  actor_email?: string;
  action?: string;
  target_id?: string;
  limit?: number;
} = {}): Promise<{ entries: AuditEvent[] }> {
  const qs = new URLSearchParams();
  if (params.actor_email) qs.set("actor_email", params.actor_email);
  if (params.action) qs.set("action", params.action);
  if (params.target_id) qs.set("target_id", params.target_id);
  qs.set("limit", String(params.limit ?? 100));
  const r = await apiFetch(`${API}/admin/audit?${qs}`, { headers: extraHeaders() });
  return r.json();
}

export async function createInstance(data: {
  instance_id: string;
  api_url?: string;
  api_key?: string;
  openai_key: string;
  prompt: string;
  provider?: string;
  meta_token?: string;
  meta_app_secret?: string;
  meta_capi_enabled?: boolean;
  meta_capi_access_token?: string;
  meta_capi_event_source_id?: string;
  meta_capi_test_event_code?: string;
  phone_number_id?: string;
  template_name?: string;
}) {
  const r = await apiFetch(`${API}/create-instance`, {
    method: "POST",
    headers: extraHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(data),
  });
  return r.json();
}

export async function createChannel(data: {
  instance_id: string;
  global_api_key: string;
  api_url?: string;
}) {
  const r = await apiFetch(`${API}/create-channel`, {
    method: "POST",
    headers: extraHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(data),
  });
  return r.json();
}

export async function getInstanceQrcode(instance_id: string) {
  const r = await apiFetch(`${API}/instance/${instance_id}/qrcode`, { headers: extraHeaders() });
  return r.json();
}

export async function reconnectInstanceQrcode(instance_id: string) {
  const r = await apiFetch(`${API}/instance/${instance_id}/qrcode/reconnect`, { headers: extraHeaders() });
  return r.json();
}

export async function validateEvolutionConnection(instance_id: string) {
  const r = await apiFetch(`${API}/instance/${instance_id}/evolution/validate`, {
    method: "POST",
    headers: extraHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ reconnect: true }),
  });
  return r.json();
}

export type InstanceConfig = {
  api_url: string;
  prompt: string;
  provider: string;
  inbound_ai_enabled: boolean;
  qualification_prompt: string;
  max_ai_turns: number;
  ai_response_delay: number;
  auto_handoff_keywords: string[];
  model: string;
  llm_model: string;
  llm_provider: string;
  followup_enabled: boolean;
  followup_delay_hours: number;
  followup_max: number;
  scoring_criteria: string;
  evo_instance_name: string;
  auto_release_minutes: number;
  tts_enabled: boolean;
  auto_suggest_enabled: boolean;
  phone_number_id: string;
  template_name: string;
  meta_page_id: string;
  meta_ad_account_id: string;
  can_manage_credentials: boolean;
  campaign_context: string;
  campaign_timing: Record<string, unknown>;
};

export async function getInstanceConfig(instance_id: string) {
  const r = await apiFetch(`${API}/instance/${instance_id}/config`, { headers: extraHeaders() });
  return r.json() as Promise<InstanceConfig>;
}

export async function updateAutoSuggest(instance_id: string, enabled: boolean) {
  const r = await apiFetch(`${API}/instance/${instance_id}/auto-suggest`, {
    method: "PATCH",
    headers: extraHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ enabled }),
  });
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail ?? `Erro ${r.status}`);
  return r.json() as Promise<{ ok: boolean; auto_suggest_enabled: boolean }>;
}

export async function getCredentials(instance_id: string) {
  const r = await apiFetch(`${API}/instance/${instance_id}/credentials`, { headers: extraHeaders() });
  return r.json();
}

export async function updateCredentials(instance_id: string, data: {
  api_url?: string;
  api_key?: string;
  openai_key: string;
  provider?: string;
  meta_token?: string;
  meta_app_secret?: string;
  phone_number_id?: string;
  template_name?: string;
  llm_provider?: string;
  llm_api_key?: string;
  llm_model?: string;
  evo_instance_name?: string;
  meta_page_id?: string;
  meta_page_access_token?: string;
  meta_ad_account_id?: string;
  meta_capi_enabled?: boolean;
  meta_capi_access_token?: string;
  meta_capi_event_source_id?: string;
  meta_capi_test_event_code?: string;
}) {
  const r = await apiFetch(`${API}/instance/${instance_id}/credentials`, {
    method: "PUT",
    headers: extraHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(data),
  });
  return r.json();
}

// ── Integração Meta via OAuth ───────────────────────────────────────────────

export type MetaAsset = { id: string; name: string; account_id?: string };
export type MetaPageAsset = MetaAsset & { instagram_business_account_id?: string; instagram_username?: string; lead_forms_error?: string };
export type MetaPhoneAsset = { id: string; display_phone_number: string; verified_name?: string; waba_id: string; waba_name?: string };
export type MetaPixelAsset = { id: string; name: string; ad_account_id: string; ad_account_name?: string };
export type MetaLeadFormAsset = { id: string; name: string; status?: string; page_id: string; route_instance_id?: string };

export type MetaAssetsResponse = {
  ok: boolean;
  businesses: MetaAsset[];
  ad_accounts: MetaAsset[];
  pages: MetaPageAsset[];
  lead_forms: MetaLeadFormAsset[];
  whatsapp_business_accounts: MetaAsset[];
  phone_numbers: MetaPhoneAsset[];
  pixels: MetaPixelAsset[];
  detail?: string;
};

export type MetaSavedAssets = {
  business_id: string; business_name: string;
  ad_account_id: string; ad_account_name: string;
  page_id: string; page_name: string;
  instagram_id: string; waba_id: string;
  phone_number_id: string; phone_display_number: string;
  pixel_id: string; pixel_name: string;
  whatsapp_enabled: boolean; lead_ads_enabled: boolean;
  status: string;
} | null;

export type MetaStatusResponse = {
  ok: boolean;
  connected: boolean;
  connection: { meta_user_id: string; updated_at: string } | null;
  assets: MetaSavedAssets;
  lead_ads?: { enabled: boolean; default_for_page: boolean };
  whatsapp?: {
    enabled: boolean;
    configured: boolean;
    waba_id: string;
    phone_number_id: string;
    webhook_subscribed: boolean;
    credential_source: "oauth" | "manual";
    webhook_url: string;
  };
};

export async function getMetaStatus(instance_id: string): Promise<MetaStatusResponse> {
  const r = await apiFetch(`${API}/meta/status/${instance_id}`, { headers: extraHeaders() });
  return r.json();
}

export async function startMetaConnect(instance_id: string): Promise<{ ok: boolean; authorize_url?: string; csrf?: string; detail?: string }> {
  const r = await apiFetch(`${API}/meta/connect/${instance_id}`, { headers: extraHeaders() });
  return r.json();
}

export async function getMetaAssets(instance_id: string): Promise<MetaAssetsResponse> {
  // A descoberta percorre BMs, contas, páginas, formulários, WABAs, números e
  // pixels na Graph API. Em contas com muitos ativos isso pode passar do
  // timeout padrão de 30s mesmo quando o backend conclui com sucesso.
  const r = await apiFetch(
    `${API}/meta/assets/${instance_id}`,
    { headers: extraHeaders() },
    FETCH_TIMEOUT_AI_MS,
  );
  return r.json();
}

export type MetaAssetSelection = Record<string, string | boolean | Array<{ id: string; name: string }>>;

export async function saveMetaAssets(instance_id: string, selection: MetaAssetSelection): Promise<{ ok: boolean; detail?: string; whatsapp_configured?: boolean; webhook_subscribed?: boolean; webhook_detail?: string; lead_ads_subscribed?: boolean; lead_ads_detail?: string; lead_forms_routed?: number }> {
  const r = await apiFetch(`${API}/meta/assets/${instance_id}`, {
    method: "POST",
    headers: extraHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(selection),
  });
  return r.json();
}

export async function disconnectMeta(instance_id: string): Promise<{ ok: boolean; detail?: string }> {
  const r = await apiFetch(`${API}/meta/disconnect/${instance_id}`, {
    method: "DELETE",
    headers: extraHeaders(),
  });
  return r.json();
}

export type AiGlobalConfig = {
  llm_provider: string;
  llm_model: string;
  llm_api_key: string;
  key_openai?: string;
  key_anthropic?: string;
  key_groq?: string;
  evo_global_api_key?: string;
};

export async function getAiGlobalConfig() {
  const r = await apiFetch(`${API}/settings/ai`, { headers: extraHeaders() });
  return r.json() as Promise<AiGlobalConfig>;
}

export async function updateAiGlobalConfig(data: AiGlobalConfig) {
  const r = await apiFetch(`${API}/settings/ai`, {
    method: "PUT",
    headers: extraHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(data),
  });
  return r.json();
}

export async function updatePrompt(instance_id: string, prompt: string) {
  const r = await apiFetch(`${API}/instance/${instance_id}/prompt`, {
    method: "PATCH",
    headers: extraHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ prompt }),
  });
  return r.json();
}

export async function updateCampaignContext(instance_id: string, campaign_context: string, campaign_timing?: object) {
  const r = await apiFetch(`${API}/instance/${instance_id}/campaign-context`, {
    method: "PATCH",
    headers: extraHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ campaign_context, campaign_timing: campaign_timing ?? {} }),
  });
  return r.json();
}

export async function uploadContacts(instance_id: string, file: File): Promise<UploadContactsResult> {
  const form = new FormData();
  form.append("file", file);
  const r = await apiFetch(`${API}/upload-contacts/${instance_id}`, {
    method: "POST",
    headers: extraHeaders(),
    body: form,
  });
  return r.json();
}

export type UploadContactsResult = {
  ok: boolean;
  instance_id?: string;
  total?: number;
  adicionados?: number;
  duplicados?: number;
  removidos?: number;
  lidos?: number;
  detail?: string;
};

export async function listContacts(instance_id: string) {
  const r = await apiFetch(`${API}/contacts/${instance_id}`, { headers: extraHeaders() });
  return r.json();
}

export async function clearContacts(instance_id: string) {
  const r = await apiFetch(`${API}/contacts/${instance_id}`, {
    method: "DELETE",
    headers: extraHeaders(),
  });
  return r.json();
}

export async function downloadContacts(instance_id: string): Promise<void> {
  const r = await apiFetch(`${API}/contacts/${instance_id}/download`, { headers: extraHeaders() });
  const blob = await r.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `contatos_${instance_id}.csv`;
  a.click();
  URL.revokeObjectURL(url);
}

export type TimingConfig = {
  daily_limit?: number;
  hourly_limit?: number;
  delay_min?: number;
  delay_max?: number;
  long_pause_every?: number;
  long_pause_min?: number;
  long_pause_max?: number;
};

export async function startCampaign(instance_id: string, context: string, resume = false, cfg: TimingConfig = {}) {
  const r = await apiFetch(`${API}/start-campaign`, {
    method: "POST",
    headers: extraHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ instance_id, context, resume, ...cfg }),
  });
  return r.json();
}

export async function stopCampaign(instance_id: string) {
  const r = await apiFetch(`${API}/stop-campaign/${instance_id}`, {
    method: "POST",
    headers: extraHeaders(),
  });
  return r.json();
}

export async function getStatus(instance_id: string) {
  const r = await apiFetch(`${API}/status/${instance_id}`, { headers: extraHeaders() });
  return r.json();
}

export async function getLogs(instance_id: string, offset = 0) {
  const r = await apiFetch(`${API}/logs/${instance_id}?offset=${offset}`, { headers: extraHeaders() });
  return r.json();
}

export async function getSends(instance_id: string) {
  const r = await apiFetch(`${API}/sends/${instance_id}`, { headers: extraHeaders() });
  return r.json();
}

export async function testMessage(instance_id: string, phone: string, context: string, name = "", message = "") {
  const r = await apiFetch(`${API}/test-message`, {
    method: "POST",
    headers: extraHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ instance_id, phone, context, name, message }),
  });
  return r.json();
}

export async function previewPrompt(instance_id: string, context: string, count = 3, name = "", phone = "") {
  const r = await apiFetch(`${API}/preview-prompt/${instance_id}`, {
    method: "POST",
    headers: extraHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ instance_id, context, count, name, phone }),
  });
  return r.json();
}

export async function retryFailed(instance_id: string, context: string) {
  const r = await apiFetch(`${API}/retry-failed/${instance_id}`, {
    method: "POST",
    headers: extraHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ instance_id, context }),
  });
  return r.json();
}

export async function getHistory() {
  const r = await apiFetch(`${API}/history`, { headers: extraHeaders() });
  return r.json();
}

export async function generateQueue(instance_id: string, context: string) {
  const r = await apiFetch(`${API}/generate-queue/${instance_id}`, {
    method: "POST",
    headers: extraHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ context }),
  });
  return r.json();
}

export async function getQueue(instance_id: string) {
  const r = await apiFetch(`${API}/queue/${instance_id}`, { headers: extraHeaders() });
  return r.json();
}

export async function updateQueueItem(instance_id: string, index: number, message: string) {
  const r = await apiFetch(`${API}/queue/${instance_id}/${index}`, {
    method: "PUT",
    headers: extraHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ message }),
  });
  return r.json();
}

export async function toggleSkipQueueItem(instance_id: string, index: number) {
  const r = await apiFetch(`${API}/queue/${instance_id}/${index}/skip`, {
    method: "PATCH",
    headers: extraHeaders(),
  });
  return r.json();
}

export async function stopQueueGeneration(instance_id: string) {
  const r = await apiFetch(`${API}/stop-queue-generation/${instance_id}`, {
    method: "POST",
    headers: extraHeaders(),
  });
  return r.json();
}

export async function sendQueue(instance_id: string, cfg: TimingConfig = {}) {
  const r = await apiFetch(`${API}/send-queue/${instance_id}`, {
    method: "POST",
    headers: extraHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(cfg),
  });
  return r.json();
}

export async function resetInstance(instance_id: string) {
  const r = await apiFetch(`${API}/reset-instance/${instance_id}`, {
    method: "POST",
    headers: extraHeaders(),
  });
  return r.json();
}

export async function sendDirect(instance_id: string, phone: string, message: string) {
  const r = await apiFetch(`${API}/send-direct`, {
    method: "POST",
    headers: extraHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ instance_id, phone, message }),
  });
  return r.json();
}

export async function getWhatsappStatus(instance_id: string) {
  const r = await apiFetch(`${API}/instance/${instance_id}/whatsapp-status`, { headers: extraHeaders() });
  return r.json();
}

export async function getWaStates(): Promise<Record<string, string>> {
  // Estado de conexão de todas as instâncias do usuário em UMA chamada —
  // substitui N chamadas de getWhatsappStatus no polling da home.
  const r = await apiFetch(`${API}/instances/wa-states`, { headers: extraHeaders() });
  const data = await r.json();
  return data?.states ?? {};
}

export async function previewContactsImport(rawText: string) {
  const r = await apiFetch(`${API}/preview-contacts-import`, {
    method: "POST",
    headers: extraHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ raw_text: rawText }),
  });
  return r.json();
}

export async function importContactsText(instance_id: string, rawText: string) {
  const r = await apiFetch(`${API}/import-contacts-text/${instance_id}`, {
    method: "POST",
    headers: extraHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ raw_text: rawText }),
  });
  return r.json();
}

export async function getInvalidContacts(instance_id: string) {
  const r = await apiFetch(`${API}/contacts/${instance_id}/invalid`, { headers: extraHeaders() });
  return r.json() as Promise<{ instance_id: string; invalid_count: number; invalid_numbers: string[] }>;
}

export async function cleanInvalidContacts(instance_id: string) {
  const r = await apiFetch(`${API}/contacts/${instance_id}/invalid`, {
    method: "DELETE",
    headers: extraHeaders(),
  });
  return r.json() as Promise<{ removed: number; remaining: number }>;
}

export async function deleteInstance(instance_id: string) {
  const r = await apiFetch(`${API}/instance/${instance_id}`, {
    method: "DELETE",
    headers: extraHeaders(),
  });
  return r.json();
}

// ── SDR — Configuração ────────────────────────────────────────────────────────

export type SdrConfig = {
  inbound_ai_enabled: boolean;
  qualification_prompt: string;
  max_ai_turns: number;
  ai_response_delay: number;
  auto_handoff_keywords: string[];
  model: string;
  llm_provider?: string;
  llm_api_key?: string;
  key_openai?: string;
  key_anthropic?: string;
  key_groq?: string;
  followup_enabled: boolean;
  followup_delay_hours: number;
  followup_max: number;
  auto_release_minutes: number;
  scoring_criteria: string;
};

export type ConversationSummaryAI = {
  nome: string;
  interesse: string;
  principais_temas: string[];
  perguntas_feitas: string[];
  sentimento: "positivo" | "neutro" | "negativo";
  proxima_acao: string;
  score_atual: number | null;
  score_estimado: number;
};

export async function getConversationSummary(instance_id: string, phone: string) {
  const r = await apiFetch(`${API}/conversations/${encodeURIComponent(instance_id)}/${encodeURIComponent(phone)}/summary`, {
    headers: extraHeaders(),
  });
  return r.json() as Promise<ConversationSummaryAI>;
}

export type KnowledgeFile = { filename: string; size: number };

export async function listKnowledge(instance_id: string) {
  const r = await apiFetch(`${API}/instance/${instance_id}/knowledge`, { headers: extraHeaders() });
  return r.json() as Promise<{ files: KnowledgeFile[] }>;
}

export async function uploadKnowledge(instance_id: string, file: File) {
  const form = new FormData();
  form.append("file", file);
  const r = await apiFetch(`${API}/instance/${instance_id}/knowledge`, {
    method: "POST",
    headers: {},
    body: form,
  });
  return r.json();
}

export async function deleteKnowledge(instance_id: string, filename: string) {
  const r = await apiFetch(`${API}/instance/${instance_id}/knowledge/${encodeURIComponent(filename)}`, {
    method: "DELETE",
    headers: extraHeaders(),
  });
  return r.json();
}

// ── Inbox Knowledge Base ──────────────────────────────────────────────────────

export async function listInboxKnowledge(instance_id: string) {
  const r = await apiFetch(`${API}/instance/${encodeURIComponent(instance_id)}/inbox-knowledge`, { headers: extraHeaders() });
  return r.json() as Promise<{ files: KnowledgeFile[] }>;
}

export async function uploadInboxKnowledge(instance_id: string, file: File) {
  const form = new FormData();
  form.append("file", file);
  const r = await apiFetch(`${API}/instance/${encodeURIComponent(instance_id)}/inbox-knowledge`, {
    method: "POST", headers: {}, body: form,
  });
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail ?? `Erro ${r.status}`);
  return r.json();
}

export async function deleteInboxKnowledge(instance_id: string, filename: string) {
  const r = await apiFetch(`${API}/instance/${encodeURIComponent(instance_id)}/inbox-knowledge/${encodeURIComponent(filename)}`, {
    method: "DELETE", headers: extraHeaders(),
  });
  return r.json();
}

export async function exportConversations(instance_id: string) {
  const r = await apiFetch(`${API}/conversations/${encodeURIComponent(instance_id)}/export`, { headers: extraHeaders() });
  if (!r.ok) throw new Error(`Erro ${r.status}`);
  const blob = await r.blob();
  const url  = URL.createObjectURL(blob);
  const a    = document.createElement("a");
  a.href     = url;
  a.download = `inbox_${instance_id}.zip`;
  a.click();
  URL.revokeObjectURL(url);
}

export async function autoGeneratePatterns(instance_id: string): Promise<{ ok: boolean; filename: string; conversas_analisadas: number }> {
  const r = await apiFetch(`${API}/instance/${encodeURIComponent(instance_id)}/inbox-knowledge/auto-generate`, {
    method: "POST", headers: extraHeaders(),
  });
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail ?? `Erro ${r.status}`);
  return r.json();
}

export async function synthesizeAgentKb(instance_id: string): Promise<{ ok: boolean; filename: string; conversas_analisadas: number; kb_usada: boolean }> {
  const r = await apiFetch(`${API}/instance/${encodeURIComponent(instance_id)}/inbox-knowledge/synthesize`, {
    method: "POST", headers: extraHeaders(),
  });
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail ?? `Erro ${r.status}`);
  return r.json();
}

export async function suggestReplies(instance_id: string, phone: string): Promise<{ sugestoes: string[]; analise?: string }> {
  const r = await apiFetch(`${API}/conversations/${encodeURIComponent(instance_id)}/${encodeURIComponent(phone)}/suggest`, {
    method: "POST", headers: extraHeaders(),
  });
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail ?? `Erro ${r.status}`);
  return r.json();
}

export async function updateSdrConfig(instance_id: string, data: SdrConfig) {
  const r = await apiFetch(`${API}/instance/${instance_id}/sdr`, {
    method: "PATCH",
    headers: extraHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(data),
  });
  return r.json();
}

export async function getWebhookUrl(instance_id: string) {
  const r = await apiFetch(`${API}/webhook/url/${instance_id}`, { headers: extraHeaders() });
  return r.json() as Promise<{ webhook_url: string; evo_instance_name: string; fix_command: string; fix_script: string }>;
}

export async function getMetaWebhookInfo(instance_id: string): Promise<{ webhook_url: string; verify_token: string; page_id: string }> {
  const r = await apiFetch(`${API}/webhook/meta-info/${instance_id}`, { headers: extraHeaders() });
  return r.json();
}

export async function rotateWebhookSecret(instance_id: string): Promise<{ ok: boolean; webhook_secret: string }> {
  const r = await apiFetch(`${API}/instance/${instance_id}/webhook-secret/rotate`, {
    method: "POST",
    headers: extraHeaders(),
  });
  return r.json();
}

export type Alert = {
  id: string;
  type: "lead_sem_telefone" | "lead_sem_campos" | "capi_falha" | string;
  instance_id: string;
  message: string;
  detail: string;
  at: string;
};

export async function getAlerts(instance_id = "", limit = 50): Promise<Alert[]> {
  try {
    const params = new URLSearchParams({ limit: String(limit) });
    if (instance_id) params.set("instance_id", instance_id);
    const r = await apiFetch(`${API}/alerts?${params}`, { headers: extraHeaders() });
    if (!r.ok) return [];
    const data = await r.json();
    return data.alerts ?? [];
  } catch {
    return [];
  }
}

export async function clearAlerts(instance_id = ""): Promise<void> {
  const params = instance_id ? `?instance_id=${encodeURIComponent(instance_id)}` : "";
  await fetch(`${API}/alerts${params}`, { method: "DELETE", headers: extraHeaders() });
}

export type MetaHealthStatus = {
  ok: boolean;
  token_valid: boolean;
  token_name: string;
  token_error: string;
  page_subscribed: boolean;
  capi_enabled: boolean;
  last_capi_at: string | null;
  last_capi_ok: boolean | null;
  last_capi_event: string | null;
  cached: boolean;
};

export async function getMetaHealth(instance_id: string): Promise<MetaHealthStatus | null> {
  try {
    const r = await apiFetch(`${API}/instance/${encodeURIComponent(instance_id)}/meta-health`, {
      headers: extraHeaders(),
    });
    if (!r.ok) return null;
    return await r.json();
  } catch {
    return null;
  }
}

export type MetaCapiAuditItem = {
  timestamp: string;
  ok: boolean;
  event_name: string;
  lead_nome: string;
  lead_telefone: string;
  detail: string;
  credential_source: "oauth" | "manual" | "";
  payload: Record<string, unknown>;
};

export async function getMetaCapiAudit(instance_id: string, limit = 30): Promise<MetaCapiAuditItem[]> {
  const r = await apiFetch(`${API}/crm/meta-capi/audit?instance_id=${encodeURIComponent(instance_id)}&limit=${limit}`, {
    headers: extraHeaders(),
  });
  if (!r.ok) return [];
  const data = await r.json();
  return data.items ?? [];
}

export async function getLeadMetaCapiAudit(lead_id: string, limit = 5): Promise<MetaCapiAuditItem[]> {
  try {
    const r = await apiFetch(`${API}/crm/leads/${encodeURIComponent(lead_id)}/meta-capi/audit?limit=${limit}`, {
      headers: extraHeaders(),
    });
    if (!r.ok) return [];
    const data = await r.json();
    return data.items ?? [];
  } catch {
    return [];
  }
}

export async function getMetaWebhookUrls(instance_id: string): Promise<{ waba_url: string; leads_url: string; verify_token: string }> {
  const r = await apiFetch(`${API}/webhook/meta-urls?instance_id=${encodeURIComponent(instance_id)}`, { headers: extraHeaders() });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  const data = await r.json();
  const token: string = data.verify_token ?? "";
  const leadsEntry = (data.webhooks ?? []).find((w: { tipo: string }) => w.tipo === "Lead Ads");
  const leadsUrl: string = leadsEntry?.url ?? "";
  const base = leadsUrl.replace(/\/webhook\/meta-leads.*$/, "");
  return { waba_url: `${base}/webhook/${instance_id}`, leads_url: leadsUrl, verify_token: token };
}

export async function setupWebhook(instance_id: string, webhookUrl: string) {
  const r = await apiFetch(
    `${API}/webhook/setup/${instance_id}?webhook_url=${encodeURIComponent(webhookUrl)}`,
    { method: "POST", headers: extraHeaders() }
  );
  return r.json();
}

// ── SDR — Conversas ───────────────────────────────────────────────────────────

export type ConversationSummary = {
  phone: string;
  name: string;
  stage: string;
  ai_paused: boolean;
  unread: boolean;
  score: number | null;
  score_reason: string;
  turn_count: number;
  needs_human?: boolean;
  handoff_reason?: string;
  handoff_at?: string | null;
  handoff_source?: string;
  handoff_handled_at?: string | null;
  msg_count: number;
  last_message: string;
  last_activity: string;
  created_at: string;
  lead_campanha?: string;
  lead_origem?: string;
  roleta_active?: boolean;
};

export type ConversationMessage = {
  role: "user" | "assistant" | "human";
  content: string;
  timestamp: string;
  is_audio?: boolean;
  is_media?: boolean;
  media_type?: "image" | "video" | "document" | "audio";
  media_url?: string;
  filename?: string;
  is_poll?: boolean;
  poll_send_id?: string;
  poll_options?: string[];
  poll_allow_multiple_answers?: boolean;
  poll_question_index?: number;
  poll_question_count?: number;
  poll_batch_id?: string;
  provider_message_id?: string;
};

export type InboxPollQuestion = {
  question: string;
  options: string[];
};

export type InboxPollTemplate = {
  id: string;
  name: string;
  question: string;
  options_json: string[];
  allow_multiple_answers: boolean;
  questions: InboxPollQuestion[];
  question_count: number;
  is_default: boolean;
  created_at: string;
  updated_at: string;
};

export type InboxPollInput = {
  name: string;
  question: string;
  options: string[];
  allow_multiple_answers: boolean;
  is_default: boolean;
  questions: InboxPollQuestion[];
};

export type ConversationDetail = ConversationSummary & {
  messages: ConversationMessage[];
  source_meta?: Record<string, unknown>;
  leadgen_id?: string;
  ctwa_clid?: string;
  lead_email?: string;
};

export type MetricsData = {
  total: number;
  qualifying: number;
  handoff: number;
  disqualified: number;
  initiated: number;
  conversion_rate: number;
  disqualify_rate: number;
  avg_score: number | null;
  avg_turns: number;
  score_distribution: { hot: number; warm: number; cold: number };
  unscored: number;
  needs_human: number;
  ai_paused: number;
  by_hour: { hour: number; count: number }[];
};

export async function getMetrics(instance_id: string) {
  const r = await apiFetch(`${API}/metrics/${instance_id}`, { headers: extraHeaders() });
  return r.json() as Promise<MetricsData>;
}

export type MetaAdsCampaign = {
  campaign: string;
  impressions: number;
  reach: number;
  frequency: number;
  quality_ranking: string;
  clicks: number;
  spend: number;
  leads: number;
  cpm: number;
  cpc: number;
  cpl: number | null;
  daily_budget: number | null;
  status?: string;
};

export type MetaAdsMetrics = {
  campaigns: MetaAdsCampaign[];
  total_spend: number;
  total_leads: number;
  total_clicks: number;
  total_reach: number;
  avg_cpl: number | null;
};

export type MetaAdsAd = {
  ad_id: string;
  ad_name: string;
  adset_name: string;
  campaign_name: string;
  impressions: number;
  reach: number;
  frequency: number;
  quality_ranking: string;
  clicks: number;
  spend: number;
  leads: number;
  cpm: number;
  cpc: number;
  ctr: number;
  cpl: number | null;
  thumbnail_url: string | null;
  creative_body: string;
  creative_title: string;
  creative_type: "video" | "image" | "unknown";
};

export type MetaAdsAdMetrics = {
  ads: MetaAdsAd[];
  total_spend: number;
  total_leads: number;
  avg_cpl: number | null;
};

// Opções do explorador Meta Ads — conta/período diferentes do padrão da instância
export type MetaAdsExplorerOpts = { ad_account?: string; date_preset?: string };

function explorerQuery(opts?: MetaAdsExplorerOpts, extra: Record<string, string> = {}): string {
  const params = new URLSearchParams();
  if (opts?.ad_account) params.set("ad_account", opts.ad_account);
  if (opts?.date_preset) params.set("date_preset", opts.date_preset);
  for (const [k, v] of Object.entries(extra)) if (v) params.set(k, v);
  const qs = params.toString();
  return qs ? `?${qs}` : "";
}

export async function getMetaAdsByAd(instance_id: string, opts?: MetaAdsExplorerOpts): Promise<MetaAdsAdMetrics> {
  const r = await apiFetch(`${API}/metrics/meta-ads/${encodeURIComponent(instance_id)}/ads${explorerQuery(opts)}`, { headers: extraHeaders() });
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail ?? `Erro ${r.status}`);
  return r.json();
}

export type MetaAdsBusinessTree = { id: string; name: string; ad_accounts: MetaAsset[] };

export async function getMetaAdsAccountsTree(instance_id: string): Promise<{ ok: boolean; businesses: MetaAdsBusinessTree[] }> {
  const r = await apiFetch(`${API}/meta/ads/${encodeURIComponent(instance_id)}/accounts`, { headers: extraHeaders() });
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail ?? `Erro ${r.status}`);
  return r.json();
}

export type MetaAdsCampaignInfo = {
  id: string;
  name: string;
  status: string;
  effective_status: string;
  objective: string;
  daily_budget: number | null;
};

export async function getMetaAdsCampaigns(instance_id: string, ad_account = ""): Promise<{ ok: boolean; ad_account: string; campaigns: MetaAdsCampaignInfo[] }> {
  const qs = ad_account ? `?ad_account=${encodeURIComponent(ad_account)}` : "";
  const r = await apiFetch(`${API}/meta/ads/${encodeURIComponent(instance_id)}/campaigns${qs}`, { headers: extraHeaders() });
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail ?? `Erro ${r.status}`);
  return r.json();
}

export async function getMetaAdsExplorerMetrics(instance_id: string, opts?: MetaAdsExplorerOpts, campaign_id = ""): Promise<MetaAdsMetrics> {
  const r = await apiFetch(
    `${API}/metrics/meta-ads/${encodeURIComponent(instance_id)}/explorer${explorerQuery(opts, { campaign_id })}`,
    { headers: extraHeaders() }
  );
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail ?? `Erro ${r.status}`);
  return r.json();
}

export type WeeklyPeriod = {
  spend: number; leads: number; clicks: number;
  impressions: number; reach: number; ctr: number;
  cpl: number | null; cpm: number;
};

export type WeeklyComparison = {
  this_week: WeeklyPeriod;
  last_week: WeeklyPeriod;
  delta_pct: { spend: number | null; leads: number | null; clicks: number | null; ctr: number | null; cpl: number | null; cpm: number | null };
};

export async function getMetaAdsComparison(instance_id: string): Promise<WeeklyComparison> {
  const r = await apiFetch(`${API}/metrics/meta-ads/${encodeURIComponent(instance_id)}/comparison`, { headers: extraHeaders() });
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail ?? `Erro ${r.status}`);
  return r.json();
}

export type AdsAnalysisAlert = {
  nivel: "critico" | "atencao" | "ok";
  categoria: "criativo" | "audiencia" | "orcamento" | "desempenho";
  titulo: string;
  detalhe: string;
};

export type AdsAnalysisSection = {
  status: "bom" | "atencao" | "critico";
  diagnostico: string;
  acoes: string[];
};

export type AdsAnalysisCampaign = {
  nome: string;
  status: "bom" | "atencao" | "critico";
  acao_prioritaria: string;
};

export type AdsAnalysisResult = {
  status_geral: "bom" | "atencao" | "critico";
  resumo: string;
  alertas: AdsAnalysisAlert[];
  analise: {
    criativo: AdsAnalysisSection;
    audiencia: AdsAnalysisSection;
    orcamento: AdsAnalysisSection;
  };
  proximas_acoes: string[];
  por_campanha: AdsAnalysisCampaign[];
  _meta?: { provider: string; model: string; campaigns_analyzed: number; campaign_filter: string | null };
};

export async function analyzeMetaAds(instance_id: string, campaign = "", opts?: MetaAdsExplorerOpts): Promise<AdsAnalysisResult> {
  const params = explorerQuery(opts, { campaign });
  const r = await apiFetch(`${API}/dashboard/${encodeURIComponent(instance_id)}/analyze-meta-ads${params}`, {
    method: "POST",
    headers: extraHeaders(),
  }, FETCH_TIMEOUT_AI_MS);
  if (!r.ok) {
    const err = await r.json().catch(() => ({}));
    throw new Error(err.detail ?? `Erro ${r.status}`);
  }
  return r.json();
}

export type ChatMessage = { role: "user" | "assistant"; content: string };

export async function chatMetaAds(
  instance_id: string,
  messages: ChatMessage[],
  campaign = "",
  opts?: MetaAdsExplorerOpts
): Promise<{ reply: string; provider: string; model: string }> {
  const r = await apiFetch(
    `${API}/dashboard/${encodeURIComponent(instance_id)}/chat-meta-ads`,
    {
      method: "POST",
      headers: extraHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ messages, campaign, ad_account: opts?.ad_account ?? "", date_preset: opts?.date_preset ?? "" }),
    },
    FETCH_TIMEOUT_AI_MS
  );
  if (!r.ok) {
    const err = await r.json().catch(() => ({}));
    throw new Error(err.detail ?? `Erro ${r.status}`);
  }
  return r.json();
}

export async function getMetaAdsMetrics(instance_id: string) {
  const r = await apiFetch(`${API}/metrics/meta-ads/${instance_id}`, { headers: extraHeaders() });
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail ?? `Erro ${r.status}`);
  return r.json() as Promise<MetaAdsMetrics>;
}

export function connectSSE(instance_id: string, onEvent: (type: string, data: Record<string, unknown>) => void): (() => void) & { isConnected: () => boolean } {
  // Cookie klead_token é enviado automaticamente (same-origin) — sem token na URL
  const url = `${API}/events/${instance_id}`;
  const es = new EventSource(url);
  let connected = false;

  const handler = (type: string) => (e: MessageEvent) => {
    try { onEvent(type, JSON.parse(e.data)); } catch { /* ignore */ }
  };

  es.addEventListener("new_message", handler("new_message"));
  es.addEventListener("ai_reply",    handler("ai_reply"));
  es.addEventListener("handoff",     handler("handoff"));
  es.addEventListener("connected",   handler("connected"));
  // Usado pelo inbox pra pular o poll de fallback quando o SSE já está entregando
  // eventos em tempo real — connected começa false, então qualquer falha nessa
  // detecção só faz voltar ao comportamento de sempre pollar (sem regressão).
  es.onopen = () => { connected = true; };
  es.onerror = () => { connected = false; };

  const disconnect = () => es.close();
  return Object.assign(disconnect, { isConnected: () => connected });
}

export async function listConversations(instance_id: string) {
  const r = await apiFetch(`${API}/conversations/${instance_id}`, { headers: extraHeaders() });
  return r.json() as Promise<{ instance_id: string; total: number; conversations: ConversationSummary[] }>;
}

export async function getConversation(instance_id: string, phone: string) {
  const r = await apiFetch(`${API}/conversations/${encodeURIComponent(instance_id)}/${encodeURIComponent(phone)}`, { headers: extraHeaders() });
  return r.json() as Promise<ConversationDetail>;
}

export async function takeoverConversation(instance_id: string, phone: string) {
  const r = await apiFetch(`${API}/conversations/${encodeURIComponent(instance_id)}/${encodeURIComponent(phone)}/takeover`, {
    method: "PATCH",
    headers: extraHeaders(),
  });
  return r.json();
}

export async function releaseConversation(instance_id: string, phone: string) {
  const r = await apiFetch(`${API}/conversations/${encodeURIComponent(instance_id)}/${encodeURIComponent(phone)}/release`, {
    method: "PATCH",
    headers: extraHeaders(),
  });
  return r.json();
}

export async function humanSendMessage(instance_id: string, phone: string, message: string) {
  const r = await apiFetch(`${API}/conversations/${encodeURIComponent(instance_id)}/${encodeURIComponent(phone)}/send`, {
    method: "POST",
    headers: extraHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ message }),
  });
  return r.json();
}

export async function listInboxPolls(): Promise<InboxPollTemplate[]> {
  const r = await apiFetch(`${API}/inbox-polls/templates`, { headers: extraHeaders() });
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail ?? `Erro ${r.status}`);
  return r.json();
}

export async function getInboxPollCapabilities(instance_id: string): Promise<{ channel: string; native_poll: boolean }> {
  const r = await apiFetch(`${API}/inbox-polls/${encodeURIComponent(instance_id)}/capabilities`, { headers: extraHeaders() });
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail ?? `Erro ${r.status}`);
  return r.json();
}

export async function createInboxPoll(data: InboxPollInput): Promise<InboxPollTemplate> {
  const r = await apiFetch(`${API}/inbox-polls/templates`, {
    method: "POST", headers: extraHeaders({ "Content-Type": "application/json" }), body: JSON.stringify(data),
  });
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail ?? `Erro ${r.status}`);
  return r.json();
}

export async function updateInboxPoll(id: string, data: Partial<InboxPollInput>): Promise<InboxPollTemplate> {
  const r = await apiFetch(`${API}/inbox-polls/templates/${encodeURIComponent(id)}`, {
    method: "PATCH", headers: extraHeaders({ "Content-Type": "application/json" }), body: JSON.stringify(data),
  });
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail ?? `Erro ${r.status}`);
  return r.json();
}

export async function duplicateInboxPoll(id: string): Promise<InboxPollTemplate> {
  const r = await apiFetch(`${API}/inbox-polls/templates/${encodeURIComponent(id)}/duplicate`, {
    method: "POST", headers: extraHeaders(),
  });
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail ?? `Erro ${r.status}`);
  return r.json();
}

export async function deleteInboxPoll(id: string): Promise<void> {
  const r = await apiFetch(`${API}/inbox-polls/templates/${encodeURIComponent(id)}`, {
    method: "DELETE", headers: extraHeaders(),
  });
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail ?? `Erro ${r.status}`);
}

export async function sendInboxPoll(instance_id: string, phone: string, template_id: string, request_id: string) {
  const r = await apiFetch(`${API}/inbox-polls/${encodeURIComponent(instance_id)}/${encodeURIComponent(phone)}/send`, {
    method: "POST", headers: extraHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ template_id, request_id }),
  });
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail ?? `Erro ${r.status}`);
  return r.json();
}

export type InboxPollVote = {
  voter_key: string;
  voter_name: string;
  selected_options_json: string[];
  vote_message_id: string;
  voted_at: string | null;
};

export async function getInboxPollResults(instance_id: string, phone: string, send_id: string): Promise<{ votes: InboxPollVote[]; provider_available: boolean }> {
  const r = await apiFetch(`${API}/inbox-polls/${encodeURIComponent(instance_id)}/${encodeURIComponent(phone)}/sends/${encodeURIComponent(send_id)}/results`, { headers: extraHeaders() });
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail ?? `Erro ${r.status}`);
  return r.json();
}

export async function humanSendAudio(instance_id: string, phone: string, audioBlob: Blob) {
  const form = new FormData();
  form.append("file", audioBlob, "audio.webm");
  const r = await apiFetch(`${API}/conversations/${encodeURIComponent(instance_id)}/${encodeURIComponent(phone)}/send-audio`, {
    method: "POST",
    headers: extraHeaders(),
    body: form,
  }, FETCH_TIMEOUT_UPLOAD_MS);
  return r.json();
}

export async function humanSendMedia(instance_id: string, phone: string, file: File, caption = "") {
  const form = new FormData();
  form.append("file", file, file.name);
  form.append("caption", caption);
  const r = await apiFetch(`${API}/conversations/${encodeURIComponent(instance_id)}/${encodeURIComponent(phone)}/send-media`, {
    method: "POST",
    headers: extraHeaders(),
    body: form,
  }, FETCH_TIMEOUT_UPLOAD_MS);
  return r.json();
}

export async function createNewLead(instance_id: string, name: string, phone: string, message = "", campanha = "", email = "", leadgen_id = "", cep = ""): Promise<{ ok: boolean; phone: string; name: string }> {
  const r = await apiFetch(`${API}/conversations/${encodeURIComponent(instance_id)}/new-lead`, {
    method: "POST",
    headers: extraHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ name, phone, message, campanha, email, leadgen_id, cep }),
  });
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail ?? `Erro ${r.status}`);
  return r.json();
}

export async function updateConversationCampanha(instance_id: string, phone: string, campanha: string, dismiss = false) {
  const r = await apiFetch(`${API}/conversations/${encodeURIComponent(instance_id)}/${encodeURIComponent(phone)}/campanha`, {
    method: "PATCH",
    headers: extraHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ campanha, dismiss }),
  });
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail ?? `Erro ${r.status}`);
  return r.json();
}

export interface LeadMetaPayload {
  nome?: string;
  email?: string;
  leadgen_id?: string;
  cidade?: string;
  estado?: string;
  pais?: string;
  cep?: string;
}

export async function updateConversationMetadata(instance_id: string, phone: string, data: LeadMetaPayload) {
  const r = await apiFetch(`${API}/conversations/${encodeURIComponent(instance_id)}/${encodeURIComponent(phone)}/metadata`, {
    method: "PATCH",
    headers: extraHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(data),
  });
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail ?? `Erro ${r.status}`);
  return r.json();
}

export async function deleteConversation(instance_id: string, phone: string) {
  const r = await apiFetch(`${API}/conversations/${encodeURIComponent(instance_id)}/${encodeURIComponent(phone)}`, {
    method: "DELETE",
    headers: extraHeaders(),
  });
  return r.json();
}

export async function deleteConversationMessage(instance_id: string, phone: string, index: number) {
  const r = await apiFetch(`${API}/conversations/${encodeURIComponent(instance_id)}/${encodeURIComponent(phone)}/messages/${index}`, {
    method: "DELETE",
    headers: extraHeaders(),
  });
  return r.json();
}

export async function clearConversations(instance_id: string, stage?: string) {
  const url = stage
    ? `${API}/conversations/${instance_id}?stage=${encodeURIComponent(stage)}`
    : `${API}/conversations/${instance_id}`;
  const r = await apiFetch(url, { method: "DELETE", headers: extraHeaders() });
  return r.json();
}

// ── CRM ───────────────────────────────────────────────────────────────────────

export type CrmColumn = {
  id: string;
  name: string;
  order: number;
  color: string;
  capi_event?: string | null;
  instancia_id?: string;
};

export type CrmTimeline = { action: string; detail: string; at: string };

export type CrmContactLog = {
  tipo: string;
  nota: string;
  at: string;
};

export type CrmLead = {
  id: string;
  nome: string;
  telefone: string;
  email: string;
  empresa: string;
  cidade: string;
  estado: string;
  cep: string;
  pais: string;
  campanha: string;
  valor_estimado: string;
  followup_date: string;
  origem: string;
  instancia_id: string;
  coluna_id: string;
  score: number | null;
  score_reason: string;
  lead_status: string;
  historico: { role: string; content: string }[];
  notas: { text: string; at: string }[];
  contatos: CrmContactLog[];
  segmento: string;
  poder_aquisitivo: string;
  tags: string[];
  assigned_to: string;
  assigned_to_name: string;
  assigned_at: string;
  assignment_status: string;
  timeline: CrmTimeline[];
  leadgen_id?: string;
  ctwa_clid?: string;
  bsuid?: string;
  source_meta?: Record<string, unknown> & {
    evolution_identity?: {
      raw_jid?: string;
      jid_type?: "pn" | "lid" | "group" | "unknown";
      pn_jid?: string;
      lid_jid?: string;
      jid_aliases?: string[];
      mapping_observed?: boolean;
    };
  };
  created_at: string;
  updated_at: string;
};

export type CrmStats = {
  total: number;
  avg_score: number | null;
  conversion_pct: number;
  by_col: { name: string; color: string; count: number }[];
  by_origem: Record<string, number>;
};

export type ConfirmationOption = {
  id: string;
  label: string;
  emoji: string;
  synonyms: string[];
  auto_reply?: string;
  position?: number;
};

export type ConfirmationTemplate = {
  id: string;
  name: string;
  message_text: string;
  options_json: ConfirmationOption[];
  active: boolean;
  is_system_default: boolean;
};

export async function crmGetConfirmationTemplates(): Promise<ConfirmationTemplate[]> {
  const r = await apiFetch(`${API}/crm/confirmation-templates`, { headers: extraHeaders() });
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail ?? `Erro ${r.status}`);
  return r.json();
}

export async function crmUpdateConfirmationTemplate(template_id: string, data: {
  name?: string;
  message_text?: string;
  options?: ConfirmationOption[];
  active?: boolean;
}): Promise<ConfirmationTemplate> {
  const r = await apiFetch(`${API}/crm/confirmation-templates/${encodeURIComponent(template_id)}`, {
    method: "PATCH",
    headers: extraHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(data),
  });
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail ?? `Erro ${r.status}`);
  return r.json();
}

export async function crmSendConfirmation(lead_id: string, data: {
  template_id?: string;
  message_text?: string;
  options?: ConfirmationOption[];
  variables?: Record<string, string>;
  include_public_link?: boolean;
  delivery_format?: "text" | "poll";
}) {
  const r = await apiFetch(`${API}/crm/leads/${lead_id}/confirmations`, {
    method: "POST",
    headers: extraHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(data),
  });
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail ?? `Erro ${r.status}`);
  return r.json();
}

export async function getPublicConfirmation(token: string): Promise<{ status: string; options: ConfirmationOption[]; response_option_id: string; scheduled_for?: string }> {
  const r = await apiFetch(`${API}/public/confirmations/${encodeURIComponent(token)}`);
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail ?? "Confirmação inválida.");
  return r.json();
}

export async function respondPublicConfirmation(token: string, option_id: string): Promise<{ ok: boolean; option_id: string; requires_schedule?: boolean }> {
  const r = await apiFetch(`${API}/public/confirmations/${encodeURIComponent(token)}/respond`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ option_id }),
  });
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail ?? "Não foi possível registrar.");
  return r.json();
}

export async function schedulePublicConfirmation(token: string, scheduled_for: string): Promise<{ ok: boolean; scheduled_for: string; message: string }> {
  const r = await apiFetch(`${API}/public/confirmations/${encodeURIComponent(token)}/schedule`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ scheduled_for }),
  });
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail ?? "Não foi possível reservar o horário.");
  return r.json();
}

export async function crmGetStats(instancia_id?: string): Promise<CrmStats> {
  const params = instancia_id ? `?instancia_id=${encodeURIComponent(instancia_id)}` : "";
  const r = await apiFetch(`${API}/crm/stats${params}`, { headers: extraHeaders() });
  return r.json();
}

export async function crmSetTags(lead_id: string, tags: string[]): Promise<CrmLead> {
  const r = await apiFetch(`${API}/crm/leads/${lead_id}/tags`, {
    method: "PATCH",
    headers: extraHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ tags }),
  });
  return r.json();
}

export async function crmGetColumns(instancia_id?: string): Promise<CrmColumn[]> {
  const qs = instancia_id ? `?instancia_id=${encodeURIComponent(instancia_id)}` : "";
  const r = await apiFetch(`${API}/crm/columns${qs}`, { headers: extraHeaders() });
  return r.json();
}

export async function crmCreateColumn(instancia_id: string, name: string, color: string, capi_event?: string): Promise<CrmColumn> {
  const r = await apiFetch(`${API}/crm/columns`, {
    method: "POST",
    headers: extraHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ instancia_id, name, color, capi_event }),
  });
  return r.json();
}

export async function crmUpdateColumn(instancia_id: string, id: string, name: string, color: string, order?: number, capi_event?: string): Promise<CrmColumn> {
  const r = await apiFetch(`${API}/crm/columns/${id}`, {
    method: "PATCH",
    headers: extraHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ instancia_id, name, color, order, capi_event }),
  });
  return r.json();
}

export async function crmBackfillLeadgen(): Promise<{ ok: boolean; updated: number; skipped_already_have: number; total: number }> {
  const r = await apiFetch(`${API}/crm/backfill-leadgen`, {
    method: "POST",
    headers: extraHeaders(),
  });
  return r.json();
}

export async function crmDeleteColumn(instancia_id: string, id: string) {
  const qs = `?instancia_id=${encodeURIComponent(instancia_id)}`;
  const r = await apiFetch(`${API}/crm/columns/${id}${qs}`, { method: "DELETE", headers: extraHeaders() });
  return r.json();
}

export async function crmGetLeads(coluna_id?: string, origem?: string, search?: string, instancia_id?: string): Promise<CrmLead[]> {
  const params = new URLSearchParams();
  if (coluna_id)   params.set("coluna_id",   coluna_id);
  if (origem)      params.set("origem",      origem);
  if (search)      params.set("search",      search);
  if (instancia_id) params.set("instancia_id", instancia_id);
  const r = await apiFetch(`${API}/crm/leads?${params}`, { headers: extraHeaders() });
  return r.json();
}

export async function crmCreateLead(data: {
  nome: string;
  telefone: string;
  origem?: string;
  instancia_id?: string;
  coluna_id?: string;
  score?: number;
  score_reason?: string;
  lead_status?: string;
  email?: string;
  empresa?: string;
  cidade?: string;
  estado?: string;
  cep?: string;
  pais?: string;
  campanha?: string;
  valor_estimado?: string;
  followup_date?: string;
  segmento?: string;
  poder_aquisitivo?: string;
  tags?: string[];
  observacao?: string;
}): Promise<CrmLead> {
  const r = await apiFetch(`${API}/crm/leads`, {
    method: "POST",
    headers: extraHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(data),
  });
  return r.json();
}

export async function crmMoveLead(lead_id: string, coluna_id: string | null): Promise<CrmLead> {
  const r = await apiFetch(`${API}/crm/leads/${lead_id}/move`, {
    method: "PATCH",
    headers: extraHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ coluna_id }),
  });
  return r.json();
}

export async function crmAddContactLog(lead_id: string, tipo: string, nota: string, proximo_contato?: string): Promise<CrmLead> {
  const r = await apiFetch(`${API}/crm/leads/${lead_id}/contatos`, {
    method: "POST",
    headers: extraHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ tipo, nota, proximo_contato: proximo_contato || null }),
  });
  return r.json();
}

export async function crmAddNote(lead_id: string, text: string): Promise<CrmLead> {
  const r = await apiFetch(`${API}/crm/leads/${lead_id}/note`, {
    method: "POST",
    headers: extraHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ text }),
  });
  return r.json();
}

export async function crmSendMessage(lead_id: string, message: string) {
  const r = await apiFetch(`${API}/crm/leads/${lead_id}/message`, {
    method: "POST",
    headers: extraHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ message }),
  });
  return r.json();
}

export async function crmSendMedia(lead_id: string, file: File, caption = "") {
  const form = new FormData();
  form.append("file", file, file.name);
  form.append("caption", caption);
  const r = await apiFetch(`${API}/crm/leads/${lead_id}/send-media`, {
    method: "POST",
    headers: extraHeaders(),
    body: form,
  }, FETCH_TIMEOUT_UPLOAD_MS);
  return r.json();
}

export async function crmDeleteLead(lead_id: string) {
  const r = await apiFetch(`${API}/crm/leads/${lead_id}`, { method: "DELETE", headers: extraHeaders() });
  return r.json();
}

export async function crmSeedConversation(lead_id: string): Promise<{ ok: boolean; instance_id: string; phone: string; already_existed: boolean }> {
  const r = await apiFetch(`${API}/crm/leads/${lead_id}/seed-conversation`, { method: "POST", headers: extraHeaders() });
  if (!r.ok) {
    const err = await r.json().catch(() => ({}));
    throw new Error(err.detail ?? `Erro ${r.status}`);
  }
  return r.json();
}

export async function crmUpdateFields(lead_id: string, data: {
  nome?: string;
  telefone?: string;
  email?: string;
  empresa?: string;
  cidade?: string;
  estado?: string;
  cep?: string;
  pais?: string;
  valor_estimado?: string;
  followup_date?: string;
  segmento?: string;
  poder_aquisitivo?: string;
  score?: number;
  score_reason?: string;
  lead_status?: string;
  instancia_id?: string;
  leadgen_id?: string;
}): Promise<CrmLead> {
  const r = await apiFetch(`${API}/crm/leads/${lead_id}/fields`, {
    method: "PATCH",
    headers: extraHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(data),
  });
  return r.json();
}

export async function crmExportCsv(coluna_id?: string, origem?: string, search?: string): Promise<void> {
  const params = new URLSearchParams();
  if (coluna_id) params.set("coluna_id", coluna_id);
  if (origem)    params.set("origem", origem);
  if (search)    params.set("search", search);
  const r = await apiFetch(`${API}/crm/export?${params}`, { headers: extraHeaders() });
  const blob = await r.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "crm_leads.csv";
  a.click();
  URL.revokeObjectURL(url);
}

// ── Listas CRM ────────────────────────────────────────────────────────────────

export type CrmLista = {
  id: string;
  name: string;
  filtros: {
    coluna_id?: string;
    origem?: string;
    score_min?: number;
    tags?: string[];
  };
  lead_ids: string[];
  followup_date: string;
  created_at: string;
};

export async function crmGetListas(): Promise<CrmLista[]> {
  const r = await apiFetch(`${API}/crm/listas`, { headers: extraHeaders() });
  return r.json();
}

export async function crmCreateLista(name: string, filtros: CrmLista["filtros"]): Promise<CrmLista> {
  const r = await apiFetch(`${API}/crm/listas`, {
    method: "POST",
    headers: extraHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ name, filtros }),
  });
  return r.json();
}

export async function crmDeleteLista(lista_id: string): Promise<void> {
  await fetch(`${API}/crm/listas/${lista_id}`, { method: "DELETE", headers: extraHeaders() });
}

export async function crmSetListaFollowup(lista_id: string, followup_date: string): Promise<CrmLista> {
  const r = await apiFetch(`${API}/crm/listas/${lista_id}/followup`, {
    method: "PATCH",
    headers: extraHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ followup_date }),
  });
  return r.json();
}

export async function crmAddLeadToLista(lista_id: string, lead_id: string): Promise<CrmLista> {
  const r = await apiFetch(`${API}/crm/listas/${lista_id}/leads/${lead_id}`, { method: "POST", headers: extraHeaders() });
  return r.json();
}

export async function crmRemoveLeadFromLista(lista_id: string, lead_id: string): Promise<CrmLista> {
  const r = await apiFetch(`${API}/crm/listas/${lista_id}/leads/${lead_id}`, { method: "DELETE", headers: extraHeaders() });
  return r.json();
}

export async function crmGetListaContatos(lista_id: string): Promise<{ contacts: { phone: string; name: string }[]; total: number }> {
  const r = await apiFetch(`${API}/crm/listas/${lista_id}/contatos`, { headers: extraHeaders() });
  return r.json();
}

export async function crmGetListaLeads(lista_id: string): Promise<{ leads: CrmLead[]; total: number }> {
  const r = await apiFetch(`${API}/crm/listas/${lista_id}/leads`, { headers: extraHeaders() });
  return r.json();
}

export async function inboxSendToCrm(instance_id: string, phone: string, coluna_id?: string): Promise<CrmLead> {
  const params = coluna_id ? `?coluna_id=${coluna_id}` : "";
  const r = await apiFetch(`${API}/conversations/${encodeURIComponent(instance_id)}/${encodeURIComponent(phone)}/send-to-crm${params}`, {
    method: "POST",
    headers: extraHeaders(),
  });
  if (!r.ok) {
    const err = await r.json().catch(() => ({}));
    throw new Error(err.detail ?? `HTTP ${r.status}`);
  }
  return r.json();
}

// Roleta de leads

export type AssignmentSettings = {
  enabled: boolean;
  mode: "automatic" | "manual";
  pointer: number;
  queue_generated_at: string;
  queue_count: number;
};

export type AssignmentRule = {
  id: string;
  name: string;
  enabled: boolean;
  origem: string;
  campanha: string;
  instancia_id: string;
  created_at: string;
  updated_at: string;
};

export type AssignmentAttendant = {
  instance_id: string;
  user_id: string;
  email: string;
  nome: string;
  funcao: string;
  active: boolean;
  status: "available" | "paused" | "absent";
  weight: number;
  order: number;
  last_assigned_at: string;
  total_assigned: number;
  channel_status: string;
  evo_instance_name: string;
};

export async function getAssignmentSettings(): Promise<AssignmentSettings> {
  const r = await apiFetch(`${API}/assignments/settings`, { headers: extraHeaders() });
  return r.json();
}

export async function updateAssignmentSettings(data: Partial<Pick<AssignmentSettings, "enabled" | "mode">>): Promise<AssignmentSettings> {
  const r = await apiFetch(`${API}/assignments/settings`, {
    method: "PATCH",
    headers: extraHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(data),
  });
  return r.json();
}

export async function getAssignmentAttendants(): Promise<AssignmentAttendant[]> {
  const r = await apiFetch(`${API}/assignments/attendants`, { headers: extraHeaders() });
  return r.json();
}

export async function updateAssignmentAttendant(email: string, data: {
  active?: boolean;
  status?: AssignmentAttendant["status"];
  weight?: number;
  order?: number;
}): Promise<AssignmentAttendant> {
  const r = await apiFetch(`${API}/assignments/attendants/${encodeURIComponent(email)}`, {
    method: "PATCH",
    headers: extraHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(data),
  });
  return r.json();
}

export async function reorderAssignmentAttendants(emails: string[]): Promise<AssignmentAttendant[]> {
  const r = await apiFetch(`${API}/assignments/reorder`, {
    method: "POST",
    headers: extraHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ emails }),
  });
  return r.json();
}

export async function getNextAssignmentAttendant(): Promise<{ next: AssignmentAttendant | null }> {
  const r = await apiFetch(`${API}/assignments/next`, { headers: extraHeaders() });
  return r.json();
}

export async function getAssignmentQueue(): Promise<{ queue: AssignmentAttendant[] }> {
  const r = await apiFetch(`${API}/assignments/queue`, { headers: extraHeaders() });
  return r.json();
}

export async function generateAssignmentQueue(): Promise<{ queue: AssignmentAttendant[]; settings: AssignmentSettings }> {
  const r = await apiFetch(`${API}/assignments/generate-queue`, {
    method: "POST",
    headers: extraHeaders(),
  });
  return r.json();
}

export async function shuffleAssignmentQueue(): Promise<{ queue: AssignmentAttendant[]; settings: AssignmentSettings }> {
  const r = await apiFetch(`${API}/assignments/shuffle-queue`, {
    method: "POST",
    headers: extraHeaders(),
  });
  return r.json();
}

export async function startAssignmentDistribution(): Promise<{ ok: boolean; reason?: string; settings: AssignmentSettings }> {
  const r = await apiFetch(`${API}/assignments/start`, {
    method: "POST",
    headers: extraHeaders(),
  });
  return r.json();
}

export async function stopAssignmentDistribution(): Promise<{ ok: boolean; settings: AssignmentSettings }> {
  const r = await apiFetch(`${API}/assignments/stop`, {
    method: "POST",
    headers: extraHeaders(),
  });
  return r.json();
}

export async function getUnassignedLeadCount(): Promise<{ count: number }> {
  const r = await apiFetch(`${API}/assignments/unassigned-count`, { headers: extraHeaders() });
  return r.json();
}

export async function distributeUnassignedLeads(limit?: number): Promise<{
  ok: boolean;
  total: number;
  remaining: number;
  distributed: { lead: CrmLead; attendant: AssignmentAttendant }[];
  next: AssignmentAttendant | null;
}> {
  const r = await apiFetch(`${API}/assignments/distribute-unassigned`, {
    method: "POST",
    headers: extraHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ limit }),
  });
  if (!r.ok) {
    const err = await r.json().catch(() => ({}));
    throw new Error(err.detail ?? `HTTP ${r.status}`);
  }
  return r.json();
}

export async function assignConversation(
  instance_id: string,
  phone: string,
  name: string,
  assigned_to: string,
  assigned_to_name: string,
): Promise<CrmLead> {
  const r = await apiFetch(`${API}/assignments/assign-conversation`, {
    method: "POST",
    headers: extraHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ instance_id, phone, name, assigned_to, assigned_to_name }),
  });
  if (!r.ok) {
    const err = await r.json().catch(() => ({}));
    throw new Error(err.detail ?? `HTTP ${r.status}`);
  }
  return r.json();
}

export async function retroactiveAssign(): Promise<{ ok: boolean; assigned: number; skipped: number }> {
  const r = await apiFetch(`${API}/assignments/retroactive-assign`, {
    method: "POST",
    headers: extraHeaders(),
  });
  if (!r.ok) {
    const err = await r.json().catch(() => ({}));
    throw new Error(err.detail ?? `HTTP ${r.status}`);
  }
  return r.json();
}

export async function getAssignmentRules(): Promise<{ rules: AssignmentRule[] }> {
  const r = await apiFetch(`${API}/assignments/rules`, { headers: extraHeaders() });
  return r.json();
}

export async function saveAssignmentRule(data: Partial<AssignmentRule>): Promise<AssignmentRule> {
  const r = await apiFetch(`${API}/assignments/rules`, {
    method: "POST",
    headers: extraHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(data),
  });
  if (!r.ok) {
    const err = await r.json().catch(() => ({}));
    throw new Error(err.detail ?? `HTTP ${r.status}`);
  }
  return r.json();
}

export async function deleteAssignmentRule(ruleId: string): Promise<{ ok: boolean }> {
  const r = await apiFetch(`${API}/assignments/rules/${encodeURIComponent(ruleId)}`, {
    method: "DELETE",
    headers: extraHeaders(),
  });
  if (!r.ok) {
    const err = await r.json().catch(() => ({}));
    throw new Error(err.detail ?? `HTTP ${r.status}`);
  }
  return r.json();
}

// ── Guia "Como Usar": vídeos ──────────────────────────────────────────────────

export type GuideVideo = {
  id: string;
  title: string;
  description: string;
  kind: "mp4" | "iframe";
  url: string;
  module?: number;
  order?: number;
};

export async function listGuideVideos(): Promise<GuideVideo[]> {
  const r = await apiFetch(`${API}/guide/videos`, { headers: extraHeaders() });
  if (!r.ok) return [];
  return (await r.json()).videos ?? [];
}

export async function createGuideVideo(title: string, description: string, source: string, module = 1, order = 1): Promise<GuideVideo> {
  const r = await apiFetch(`${API}/guide/videos`, {
    method: "POST",
    headers: extraHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ title, description, source, module, order }),
  });
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail ?? `HTTP ${r.status}`);
  return r.json();
}

export async function updateGuideVideo(id: string, title: string, description: string, source: string, module = 1, order = 1): Promise<GuideVideo> {
  const r = await apiFetch(`${API}/guide/videos/${encodeURIComponent(id)}`, {
    method: "PUT",
    headers: extraHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ title, description, source, module, order }),
  });
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail ?? `HTTP ${r.status}`);
  return r.json();
}

export async function deleteGuideVideo(id: string): Promise<{ ok: boolean }> {
  const r = await apiFetch(`${API}/guide/videos/${encodeURIComponent(id)}`, {
    method: "DELETE",
    headers: extraHeaders(),
  });
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail ?? `HTTP ${r.status}`);
  return r.json();
}
