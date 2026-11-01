"use client";

import { useState, useEffect, useCallback, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { createChannel, createInstance, getInstanceQrcode, getMetaWebhookInfo, getAiGlobalConfig, getMe, startMetaConnect, type CurrentUser } from "@/lib/api";

type Step = "form" | "qrcode" | "meta-webhook" | "done";

export default function NewInstance() {
  const EVOLUTION_API_URL = "https://go.seudominio.com";
  const router = useRouter();
  const [provider, setProvider] = useState<"evolution" | "meta">("evolution");
  const [metaMode, setMetaMode] = useState<"oauth" | "manual">("oauth");
  const [step, setStep] = useState<Step>("form");
  const [createdId, setCreatedId] = useState("");
  const [qrCode, setQrCode] = useState("");
  const [metaWebhookUrl, setMetaWebhookUrl] = useState("");
  const [metaVerifyToken, setMetaVerifyToken] = useState("");
  const [copied, setCopied] = useState<"url" | "token" | null>(null);

  const [form, setForm] = useState({
    instance_id:     "",
    global_api_key:  "",
    api_url:         "",
    meta_token:      "",
    meta_app_secret: "",
    phone_number_id: "",
    template_name:   "",
  });
  const [error, setError]       = useState("");
  const [loading, setLoading]   = useState(false);
  const [savedEvoKey, setSavedEvoKey] = useState(false);
  const [me, setMe] = useState<CurrentUser | null>(null);

  useEffect(() => {
    fetch("/api/installation", { cache: "no-store" })
      .then((response) => response.ok ? response.json() : null)
      .then((cfg) => {
        if (cfg?.evolution_api_url) {
          setForm((previous) => ({ ...previous, api_url: previous.api_url || cfg.evolution_api_url }));
        }
      }).catch(() => {});
    getAiGlobalConfig().then((cfg) => {
      if (cfg.evo_global_api_key) {
        setSavedEvoKey(true);
      }
    }).catch(() => {});
    getMe().then(setMe).catch(() => {});
  }, []);

  const instCount = me?.owned_instances?.length ?? 0;
  const maxInstances = me?.max_instances ?? 1;
  const atLimit = instCount >= maxInstances;

  // Atualiza QR code a cada 20s enquanto aguarda conexão
  const refreshQr = useCallback(async (iid: string) => {
    const res = await getInstanceQrcode(iid);
    if (res?.qr_code) setQrCode(res.qr_code);
  }, []);

  useEffect(() => {
    if (step !== "qrcode" || !createdId) return;
    const interval = setInterval(() => refreshQr(createdId), 20000);
    return () => clearInterval(interval);
  }, [step, createdId, refreshQr]);

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setError("");

    if (!form.instance_id.trim()) return setError("Nome da instância é obrigatório.");

    // apiFetch lança em não-2xx (ex: 403 sem permissão) — sem try/catch o botão
    // ficava em "Criando..." pra sempre, sem mensagem nenhuma.
    const permissionMsg = (e: unknown, fallback: string) =>
      e instanceof Error && e.message.includes("403")
        ? "Seu usuário não tem permissão para criar instâncias. Peça ao administrador para liberar."
        : fallback;

    if (provider === "evolution") {
      if (!form.api_url.trim()) return setError("Informe a URL do Evolution GO da sua instalação.");
      // Gerente/sdr não têm acesso à API Key Global (endpoint restrito a
      // admin) — pra eles, o backend cai no fallback já configurado pelo
      // admin/superadmin, então não bloqueia o formulário por causa dela.
      const canManageEvoKey = me?.funcao === "admin" || me?.funcao === "superadmin";
      if (canManageEvoKey && !form.global_api_key.trim() && !savedEvoKey) {
        return setError("API Key Global do Evolution GO é obrigatória.");
      }

      setLoading(true);
      try {
        const res = await createChannel({
          instance_id:    form.instance_id.trim(),
          global_api_key: form.global_api_key.trim(),
          api_url:        form.api_url,
        });
        if (!res.ok) return setError(res.detail || "Erro ao criar canal no Evolution GO.");
        setCreatedId(res.instance_id);
        setQrCode(res.qr_code || "");
        setStep("qrcode");
      } catch (e) {
        setError(permissionMsg(e, "Erro ao criar canal no Evolution GO. Tente novamente."));
      } finally {
        setLoading(false);
      }
    } else {
      if (metaMode === "manual" && !form.meta_token.trim()) return setError("Token da Meta é obrigatório.");
      if (metaMode === "manual" && !form.phone_number_id.trim()) return setError("Phone Number ID é obrigatório.");

      setLoading(true);
      try {
        const res = await createInstance({
          instance_id:    form.instance_id.trim(),
          provider:       "meta",
          meta_token:      form.meta_token,
          meta_app_secret: form.meta_app_secret,
          phone_number_id: form.phone_number_id,
          template_name:   form.template_name,
          openai_key:     "",
          prompt:         "Você cria mensagens de WhatsApp curtas, calorosas e naturais. Use o nome do contato. Máximo 5 linhas. 1 a 2 emojis. Sem linguagem corporativa.",
        });
        if (res.ok) {
          const iid = res.instance_id || form.instance_id.trim();
          setCreatedId(iid);
          if (metaMode === "oauth") {
            const connect = await startMetaConnect(iid);
            if (!connect.ok || !connect.authorize_url) {
              router.push(`/instances/${encodeURIComponent(iid)}?meta_error=${encodeURIComponent(connect.detail || "falha_ao_iniciar_oauth")}`);
              return;
            }
            if (connect.csrf) {
              const hostname = window.location.hostname.toLowerCase();
              const domain = hostname === "example.com" || hostname.endsWith(".example.com")
                ? "; domain=.example.com"
                : "";
              document.cookie = `klead_meta_csrf=${connect.csrf}; path=/; max-age=300; secure; samesite=lax${domain}`;
            }
            window.location.href = connect.authorize_url;
            return;
          }
          try {
            const info = await getMetaWebhookInfo(iid);
            setMetaWebhookUrl(info.webhook_url);
            setMetaVerifyToken(info.verify_token);
          } catch { /* silently ignore, user can still proceed */ }
          setStep("meta-webhook");
        } else setError(res.detail || "Erro ao criar instância.");
      } catch (e) {
        setError(permissionMsg(e, "Erro ao criar instância. Tente novamente."));
      } finally {
        setLoading(false);
      }
    }
  };

  const copyText = (text: string, which: "url" | "token") => {
    navigator.clipboard.writeText(text);
    setCopied(which);
    setTimeout(() => setCopied(null), 2000);
  };

  const inputClass = "w-full bg-[#F0EDE8] border border-[#D4CFC9] rounded-xl px-4 py-3 text-sm text-[#1A1410] placeholder-[#6B5E55] focus:outline-none focus:border-[#CC3300] transition-colors";
  const secretInputClass = `${inputClass} secret-input`;

  // ── Tela de QR code ──────────────────────────────────────────────────────────
  if (step === "qrcode") {
    return (
      <div className="min-h-screen bg-[#F0EDE8]">
        <header className="bg-[#0E0E18] px-6 py-4 shadow-lg">
          <div className="max-w-xl mx-auto flex items-center gap-4">
            <button onClick={() => setStep("form")} className="text-[#CC3300] hover:text-[#991F00] transition-colors">
              <svg viewBox="0 0 24 24" className="w-5 h-5 fill-current">
                <path d="M20 11H7.83l5.59-5.59L12 4l-8 8 8 8 1.41-1.41L7.83 13H20v-2z"/>
              </svg>
            </button>
            <div>
              <h1 className="text-[#1A1410] font-bold text-lg">Conectar WhatsApp</h1>
              <p className="text-[#6B5E55] text-xs">Instância criada — escaneie o QR code</p>
            </div>
          </div>
        </header>

        <main className="max-w-xl mx-auto px-6 py-8 space-y-5">
          {/* Card QR */}
          <div className="bg-[#E8E4DF] rounded-2xl border border-[#D4CFC9] p-6 flex flex-col items-center gap-4">
            <div className="flex items-center gap-2 text-[#CC3300] font-semibold text-sm">
              <span className="w-2 h-2 rounded-full bg-[#CC3300] animate-pulse inline-block"/>
              Aguardando conexão
            </div>

            {qrCode ? (
              <div className="bg-white p-3 rounded-xl">
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img
                  src={qrCode.startsWith("data:") ? qrCode : `data:image/png;base64,${qrCode}`}
                  alt="QR Code WhatsApp"
                  className="w-56 h-56 object-contain"
                />
              </div>
            ) : (
              <div className="w-56 h-56 bg-[#F0EDE8] rounded-xl flex items-center justify-center text-[#6B5E55] text-sm">
                Gerando QR code...
              </div>
            )}

            <p className="text-[#6B5E55] text-xs text-center leading-relaxed">
              Abra o WhatsApp no celular → Dispositivos conectados → Conectar dispositivo → Escaneie este código
            </p>

            <button
              onClick={() => refreshQr(createdId)}
              className="text-xs text-[#CC3300] hover:text-[#991F00] transition-colors underline"
            >
              Atualizar QR code
            </button>
          </div>

          {/* Info webhook */}
          <div className="bg-[#E8E4DF] rounded-2xl border border-[#D4CFC9] p-4">
            <p className="text-[#6B5E55] text-xs font-semibold uppercase tracking-wide mb-2">Instância criada</p>
            <p className="text-[#1A1410] text-sm font-mono">{createdId}</p>
            <p className="text-[#6B5E55] text-xs mt-1">Webhook configurado automaticamente.</p>
          </div>

          <button
            onClick={() => router.push(`/instances/${createdId}`)}
            className="w-full bg-[#CC3300] hover:bg-[#991F00] text-[#F0EDE8] font-bold py-3.5 rounded-xl transition-colors text-sm"
          >
            Ir para o Dashboard →
          </button>
        </main>
      </div>
    );
  }

  // ── Tela webhook Meta ────────────────────────────────────────────────────────
  if (step === "meta-webhook") {
    return (
      <div className="min-h-screen bg-[#F0EDE8]">
        <header className="bg-[#0E0E18] px-6 py-4 shadow-lg">
          <div className="max-w-xl mx-auto flex items-center gap-4">
            <div>
              <h1 className="text-[#1A1410] font-bold text-lg">Instância criada!</h1>
              <p className="text-[#6B5E55] text-xs">Configure o webhook no painel Meta</p>
            </div>
          </div>
        </header>

        <main className="max-w-xl mx-auto px-6 py-8 space-y-5">
          <div className="bg-[#E8E4DF] rounded-2xl border border-[#D4CFC9] p-5 space-y-4">
            <div className="flex items-center gap-2">
              <div className="w-2 h-2 rounded-full bg-[#A78BFA]" />
              <p className="text-[#1A1410] font-bold text-sm">Webhook para configurar no Meta</p>
            </div>
            <p className="text-[#6B5E55] text-xs leading-relaxed">
              No painel Meta for Developers, va em WhatsApp {">"} Configuracao {">"} Webhooks e cole os valores abaixo:
            </p>

            <div>
              <p className="text-[#6B5E55] text-xs font-semibold uppercase tracking-wide mb-1.5">URL do Webhook</p>
              <div className="flex gap-2 items-center">
                <code className="flex-1 bg-[#F0EDE8] border border-[#D4CFC9] rounded-xl px-3 py-2 text-[#CC3300] text-xs font-mono break-all">
                  {metaWebhookUrl || `https://SEU-DOMINIO/webhook/${createdId}`}
                </code>
                <button
                  onClick={() => copyText(metaWebhookUrl, "url")}
                  className="shrink-0 bg-[#D4CFC9] hover:bg-[#3D4F5A] text-[#1A1410] px-3 py-2 rounded-xl text-xs font-semibold transition-colors"
                >
                  {copied === "url" ? "Copiado!" : "Copiar"}
                </button>
              </div>
            </div>

            <div>
              <p className="text-[#6B5E55] text-xs font-semibold uppercase tracking-wide mb-1.5">Verify Token</p>
              <div className="flex gap-2 items-center">
                <code className="flex-1 bg-[#F0EDE8] border border-[#D4CFC9] rounded-xl px-3 py-2 text-[#F0B429] text-xs font-mono break-all">
                  {metaVerifyToken || "(configure WEBHOOK_SECRET no .env)"}
                </code>
                <button
                  onClick={() => copyText(metaVerifyToken, "token")}
                  className="shrink-0 bg-[#D4CFC9] hover:bg-[#3D4F5A] text-[#1A1410] px-3 py-2 rounded-xl text-xs font-semibold transition-colors"
                >
                  {copied === "token" ? "Copiado!" : "Copiar"}
                </button>
              </div>
            </div>

            <div className="bg-[#F0EDE8] rounded-xl p-3 border-l-2 border-[#A78BFA]">
              <p className="text-xs text-[#6B5E55] leading-relaxed">
                Apos configurar o webhook no Meta, habilite a assinatura <span className="text-[#1A1410] font-mono">messages</span> para receber as mensagens. O Verify Token e o mesmo para todas as instancias.
              </p>
            </div>
          </div>

          <div className="bg-[#E8E4DF] rounded-2xl border border-[#D4CFC9] p-4">
            <p className="text-[#6B5E55] text-xs font-semibold uppercase tracking-wide mb-1">Instancia criada</p>
            <p className="text-[#1A1410] text-sm font-mono">{createdId}</p>
            <p className="text-[#6B5E55] text-xs mt-1">Provedor: Meta Cloud API</p>
          </div>

          <button
            onClick={() => router.push(`/instances/${createdId}`)}
            className="w-full bg-[#CC3300] hover:bg-[#991F00] text-[#F0EDE8] font-bold py-3.5 rounded-xl transition-colors text-sm"
          >
            Ir para o Dashboard {"→"}
          </button>
        </main>
      </div>
    );
  }

  // ── Formulário ───────────────────────────────────────────────────────────────
  return (
    <div className="min-h-screen bg-[#F0EDE8]">
      <header className="bg-[#0E0E18] px-6 py-4 shadow-lg">
        <div className="max-w-xl mx-auto flex items-center gap-4">
          <Link href="/" className="text-[#CC3300] hover:text-[#991F00] transition-colors">
            <svg viewBox="0 0 24 24" className="w-5 h-5 fill-current">
              <path d="M20 11H7.83l5.59-5.59L12 4l-8 8 8 8 1.41-1.41L7.83 13H20v-2z"/>
            </svg>
          </Link>
          <div>
            <h1 className="text-[#1A1410] font-bold text-lg">Nova Instância</h1>
            <p className="text-[#6B5E55] text-xs">Configure uma nova conta WhatsApp</p>
          </div>
        </div>
      </header>

      <main className="max-w-xl mx-auto px-6 py-8">
        {me?.funcao === "admin" && (
          <div className={`mb-4 rounded-xl border px-4 py-3 text-sm ${
            atLimit ? "bg-red-950/30 border-red-700/40 text-red-400" : "bg-[#E8E4DF] border-[#D4CFC9] text-[#1A1410]"
          }`}>
            {atLimit ? (
              <>Limite do seu plano atingido: <span className="font-semibold">{instCount}/{maxInstances}</span> instâncias. Fale com o suporte para aumentar seu plano antes de criar uma nova.</>
            ) : (
              <>Você usa <span className="text-[#CC3300] font-semibold">{instCount}/{maxInstances}</span> instâncias do seu plano.</>
            )}
          </div>
        )}
        <form onSubmit={handleSubmit} className="space-y-4">

          {/* Nome */}
          <div className="bg-[#E8E4DF] rounded-2xl border border-[#D4CFC9] p-5">
            <label className="block text-[#6B5E55] text-xs font-semibold uppercase tracking-wide mb-3">
              Identificação
            </label>
            <input
              type="text"
              placeholder="ex: Ramon, numero_sp_01"
              value={form.instance_id}
              onChange={(e) => setForm({ ...form, instance_id: e.target.value })}
              className={inputClass}
            />
            <p className="text-xs text-[#6B5E55] mt-1.5">
              {provider === "evolution" ? "Este nome será usado no Evolution GO e no KLEAD." : "Nome interno para organizar conversas, CRM e configurações no KLEAD."}
            </p>
          </div>

          {/* Provider */}
          <div className="bg-[#E8E4DF] rounded-2xl border border-[#D4CFC9] p-5">
            <label className="block text-[#6B5E55] text-xs font-semibold uppercase tracking-wide mb-3">
              Provedor WhatsApp
            </label>
            <div className="flex gap-3">
              {(["evolution", "meta"] as const).map((p) => (
                <button
                  key={p}
                  type="button"
                  onClick={() => setProvider(p)}
                  className={`flex-1 py-2.5 rounded-xl text-sm font-semibold border transition-colors ${
                    provider === p
                      ? "bg-[#CC3300] border-[#CC3300] text-[#F0EDE8]"
                      : "bg-[#F0EDE8] border-[#D4CFC9] text-[#6B5E55] hover:border-[#CC3300] hover:text-[#CC3300]"
                  }`}
                >
                  {p === "evolution" ? "Evolution GO" : "Meta (Oficial) WABA"}
                </button>
              ))}
            </div>
          </div>

          {/* Credenciais Evolution — fluxo automático */}
          {provider === "evolution" && (
            <div className="bg-[#E8E4DF] rounded-2xl border border-[#D4CFC9] p-5 space-y-4">
              <div>
                <label className="block text-[#6B5E55] text-xs font-semibold uppercase tracking-wide mb-1">
                  Credenciais Evolution GO
                </label>
                <p className="text-xs text-[#CC3300] mt-1">
                  O KLEAD cria a instância no Evolution GO e configura o webhook automaticamente.
                </p>
              </div>
              <div>
                <label className="block text-sm font-medium text-[#1A1410] mb-1.5">URL do Evolution GO</label>
                <input
                  type="text"
                  placeholder={EVOLUTION_API_URL}
                  value={form.api_url}
                  onChange={(e) => setForm({ ...form, api_url: e.target.value })}
                  className={inputClass}
                />
                <p className="text-xs text-[#6B5E55] mt-1.5">
                  Domínio do Evolution GO configurado durante a instalação na VPS.
                </p>
              </div>
              <div>
                <label className="block text-sm font-medium text-[#1A1410] mb-1.5">
                  API Key Global <span className="text-[#CC3300]">*</span>
                </label>
                <input
                  type="text"
                  autoComplete="off"
                  spellCheck={false}
                  data-lpignore="true"
                  data-form-type="other"
                  placeholder={savedEvoKey ? "Chave já salva — deixe em branco para reutilizar" : "Chave mestra do servidor Evolution GO"}
                  value={form.global_api_key}
                  onChange={(e) => setForm({ ...form, global_api_key: e.target.value })}
                  className={secretInputClass}
                />
                <p className="text-xs text-[#6B5E55] mt-1.5">
                  {savedEvoKey ? "Chave global já configurada. Preencha apenas se quiser alterá-la." : "Encontrada no painel do Evolution GO ou no arquivo de configuração do servidor."}
                </p>
              </div>
            </div>
          )}

          {/* Credenciais Meta */}
          {provider === "meta" && (
            <div className="bg-[#E8E4DF] rounded-2xl border border-[#D4CFC9] p-5 space-y-4">
              <label className="block text-[#6B5E55] text-xs font-semibold uppercase tracking-wide mb-1">
                Forma de conexão Meta Cloud API
              </label>
              <div className="grid grid-cols-2 gap-2">
                <button type="button" onClick={() => setMetaMode("oauth")} className={`rounded-xl border px-3 py-2.5 text-sm font-semibold ${metaMode === "oauth" ? "bg-[#CC3300] border-[#CC3300] text-white" : "bg-[#F0EDE8] border-[#D4CFC9] text-[#6B5E55]"}`}>
                  Conectar com Meta
                </button>
                <button type="button" onClick={() => setMetaMode("manual")} className={`rounded-xl border px-3 py-2.5 text-sm font-semibold ${metaMode === "manual" ? "bg-[#CC3300] border-[#CC3300] text-white" : "bg-[#F0EDE8] border-[#D4CFC9] text-[#6B5E55]"}`}>
                  Configuração manual
                </button>
              </div>
              {metaMode === "oauth" ? (
                <div className="rounded-xl border border-[#CC3300]/30 bg-[#FDE8E4] p-4 text-sm text-[#6B5E55] leading-relaxed">
                  Após criar, você autoriza a conta Meta e seleciona o WABA e o número. Token, Phone Number ID e webhook serão configurados pelo KLEAD. Conta de anúncio, página e Pixel são opcionais.
                </div>
              ) : (
                <>
              <div>
                <label className="block text-sm font-medium text-[#1A1410] mb-1.5">
                  Token de Acesso <span className="text-[#CC3300]">*</span>
                </label>
                <input
                  type="text"
                  autoComplete="off"
                  spellCheck={false}
                  data-lpignore="true"
                  data-form-type="other"
                  placeholder="EAAxxxxxxxxx..."
                  value={form.meta_token}
                  onChange={(e) => setForm({ ...form, meta_token: e.target.value })}
                  className={secretInputClass}
                />
                <p className="text-xs text-[#6B5E55] mt-1.5">Token permanente gerado no Meta for Developers.</p>
              </div>
              <div>
                <label className="block text-sm font-medium text-[#1A1410] mb-1.5">
                  Phone Number ID <span className="text-[#CC3300]">*</span>
                </label>
                <input
                  type="text"
                  placeholder="123456789012345"
                  value={form.phone_number_id}
                  onChange={(e) => setForm({ ...form, phone_number_id: e.target.value })}
                  className={inputClass}
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-[#1A1410] mb-1.5">
                  App Secret <span className="text-[#6B5E55] text-xs">(opcional com o app KLEAD)</span>
                </label>
                <input
                  type="text"
                  autoComplete="off"
                  spellCheck={false}
                  data-lpignore="true"
                  data-form-type="other"
                  placeholder="Cole o App Secret aqui"
                  value={form.meta_app_secret}
                  onChange={(e) => setForm({ ...form, meta_app_secret: e.target.value })}
                  className={secretInputClass}
                />
                <p className="text-xs text-[#6B5E55] mt-1.5">App Settings → Basic → App Secret no Meta for Developers.</p>
              </div>
              <div>
                <label className="block text-sm font-medium text-[#1A1410] mb-1.5">
                  Nome do Template
                </label>
                <input
                  type="text"
                  placeholder="ex: hello_world (deixe vazio para texto livre)"
                  value={form.template_name}
                  onChange={(e) => setForm({ ...form, template_name: e.target.value })}
                  className={inputClass}
                />
                <p className="text-xs text-[#6B5E55] mt-1.5">Deixe vazio para texto livre (janela 24h).</p>
              </div>
                </>
              )}
            </div>
          )}

          {error && (
            <div className="bg-[#2D0A0A] border border-[#F87171]/30 text-[#F87171] text-sm rounded-xl px-4 py-3">
              {error}
            </div>
          )}

          <button
            type="submit"
            disabled={loading}
            className="w-full bg-[#CC3300] hover:bg-[#991F00] disabled:bg-[#CC3300]/40 disabled:cursor-not-allowed text-[#F0EDE8] font-bold py-3.5 rounded-xl transition-colors text-sm"
          >
            {loading
              ? provider === "evolution" ? "Criando canal..." : "Criando instância..."
              : provider === "evolution" ? "Criar Canal e Conectar WhatsApp" : metaMode === "oauth" ? "Criar e Conectar com Meta" : "Criar Instância"
            }
          </button>
        </form>
      </main>
    </div>
  );
}
