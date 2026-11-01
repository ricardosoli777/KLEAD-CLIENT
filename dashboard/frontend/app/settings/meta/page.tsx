"use client";

import { useEffect, useState } from "react";
import PageHeaderShell from "@/components/PageHeaderShell";

type WebhookEntry = { tipo: string; descricao: string; url: string; campo: string };
type MetaUrls = { verify_token: string; webhooks: WebhookEntry[] };

function InfoCard({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="bg-[#3D0A00] border border-[#CC3300]/20 rounded-2xl p-4">
      <p className="text-[#CC3300] text-xs font-bold uppercase tracking-wide mb-2 flex items-center gap-2">
        <svg viewBox="0 0 24 24" className="w-3.5 h-3.5 fill-current flex-shrink-0"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm1 15h-2v-2h2v2zm0-4h-2V7h2v6z"/></svg>
        {title}
      </p>
      <div className="text-[#6B5E55] text-xs leading-relaxed space-y-1">{children}</div>
    </div>
  );
}

export default function MetaSettingsPage() {
  const [data, setData]     = useState<MetaUrls | null>(null);
  const [copied, setCopied] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const [restricted, setRestricted] = useState(false);

  useEffect(() => {
    fetch("/api/proxy/webhook/meta-urls", { headers: { "Content-Type": "application/json" } })
      .then(r => {
        if (!r.ok) {
          // Token global é restrito à conta mestre — admin de tenant usa o
          // token da própria instância, exibido no card de credenciais.
          setRestricted(true);
          return null;
        }
        return r.json();
      })
      .then(d => { if (d) setData(d); })
      .catch(() => setRestricted(true))
      .finally(() => setLoading(false));
  }, []);

  function copy(text: string, key: string) {
    navigator.clipboard.writeText(text);
    setCopied(key);
    setTimeout(() => setCopied(null), 2000);
  }

  return (
    <div className="min-h-screen bg-[#F0EDE8]">
      <PageHeaderShell maxWidthClassName="max-w-3xl">
        <div className="text-center">
          <h1 className="text-[#1A1410] text-lg font-bold leading-tight">Integração Meta</h1>
          <p className="text-[#6B5E55] text-[12px] mt-0.5">Configure webhooks e anúncios do Facebook/Instagram no KLEAD.</p>
        </div>
      </PageHeaderShell>
      <div className="max-w-3xl mx-auto px-4 py-8 space-y-6">

        <div>
          <h1 className="text-xl font-bold text-[#1A1410]">Integração Meta</h1>
          <p className="text-sm text-[#6B5E55] mt-1">Configure webhooks e anúncios do Facebook/Instagram no KLEAD.</p>
        </div>

        <InfoCard title="O que essa integração faz">
          <p>• <span className="text-[#CC3300]">Lead Ads:</span> quando alguém preenche um formulário de anúncio no Facebook ou Instagram, o lead cai automaticamente no CRM e o SDR dispara o WhatsApp em segundos.</p>
          <p>• <span className="text-[#CC3300]">Ads Manager:</span> veja impressões, cliques, custo por lead e gasto de cada campanha direto no Dashboard — sem sair do KLEAD.</p>
          <p>• <span className="text-[#CC3300]">Roteamento automático:</span> configure o Page ID na instância para que leads do anúncio caiam na instância SDR certa.</p>
        </InfoCard>

        {loading && <div className="text-[#6B5E55] text-sm">Carregando...</div>}

        {restricted && !loading && (
          <div className="bg-[#E8E4DF] rounded-2xl border border-[#D4CFC9] p-5">
            <p className="text-sm font-bold text-[#1A1410] mb-2">URLs de webhook da sua instância</p>
            <p className="text-xs text-[#6B5E55] leading-relaxed">
              As URLs e o Verify Token do seu webhook ficam no painel da sua instância:
              abra <span className="text-[#CC3300]">Instâncias → sua instância → Credenciais → URLs de Webhook</span>.
              Cada instância tem um token próprio — use o seu ao cadastrar o webhook no painel Meta for Developers.
            </p>
          </div>
        )}

        {data && (
          <>
            {/* Verify Token */}
            <div className="bg-[#E8E4DF] rounded-2xl border border-[#D4CFC9] p-5 space-y-3">
              <p className="text-sm font-bold text-[#1A1410]">Verify Token</p>
              <InfoCard title="Para que serve">
                <p>Cole este token no campo <span className="text-[#CC3300]">Verify Token</span> ao cadastrar qualquer webhook no painel Meta for Developers. É o mesmo token para todos os webhooks.</p>
              </InfoCard>
              <div className="flex items-center gap-2">
                <code className="flex-1 bg-[#F0EDE8] rounded-xl px-4 py-3 text-[#CC3300] text-sm font-mono break-all">{data.verify_token}</code>
                <button onClick={() => copy(data.verify_token, "verify")} className="shrink-0 bg-[#CC3300] hover:bg-[#991F00] text-white text-xs font-semibold px-4 py-3 rounded-xl transition-colors">
                  {copied === "verify" ? "✓ Copiado" : "Copiar"}
                </button>
              </div>
            </div>

            {/* Webhooks */}
            <div className="bg-[#E8E4DF] rounded-2xl border border-[#D4CFC9] p-5 space-y-4">
              <p className="text-sm font-bold text-[#1A1410]">URLs de Webhook</p>
              <InfoCard title="Como usar">
                <p>1. No painel Meta for Developers → seu App → <span className="text-[#CC3300]">Webhooks</span></p>
                <p>2. Clique em <span className="text-[#CC3300]">Add Subscription</span> → selecione <span className="text-[#CC3300]">leadgen</span></p>
                <p>3. Cole a URL abaixo e o Verify Token acima → clique <span className="text-[#CC3300]">Verify and Save</span></p>
                <p>4. Pronto — leads chegam no CRM em tempo real. Se a instância tiver Page ID configurado, o SDR dispara automaticamente.</p>
              </InfoCard>
              {data.webhooks.map((wh, i) => (
                <div key={i} className="space-y-2">
                  <div className="flex items-center justify-between">
                    <p className="text-sm font-semibold text-[#1A1410]">{wh.tipo}</p>
                    <span className="text-xs bg-[#CC3300]/10 text-[#CC3300] border border-[#CC3300]/20 rounded-lg px-2 py-1">campo: {wh.campo}</span>
                  </div>
                  <p className="text-xs text-[#6B5E55]">{wh.descricao}</p>
                  <div className="flex items-center gap-2">
                    <code className="flex-1 bg-[#F0EDE8] rounded-xl px-4 py-3 text-[#6B5E55] text-xs font-mono break-all">{wh.url}</code>
                    <button onClick={() => copy(wh.url, `wh-${i}`)} className="shrink-0 bg-[#D4CFC9] hover:bg-[#3B4A54] text-[#1A1410] text-xs font-semibold px-4 py-3 rounded-xl transition-colors">
                      {copied === `wh-${i}` ? "✓ Copiado" : "Copiar"}
                    </button>
                  </div>
                </div>
              ))}
            </div>

            {/* Ads Manager */}
            <div className="bg-[#E8E4DF] rounded-2xl border border-[#D4CFC9] p-5 space-y-3">
              <p className="text-sm font-bold text-[#1A1410]">Ads Manager — Configuração por Instância</p>
              <InfoCard title="Como ativar métricas de anúncios">
                <p>1. Vá em <span className="text-[#CC3300]">Instâncias → selecione uma → Credenciais</span></p>
                <p>2. Preencha o <span className="text-[#CC3300]">Ad Account ID</span> (ex: <code className="text-[#CC3300]">act_123456789</code>) e o <span className="text-[#CC3300]">Page ID</span></p>
                <p>3. O Dashboard da instância mostrará um card com métricas das campanhas dos últimos 30 dias</p>
                <p>4. O token Meta já configurado na instância será usado — deve ter permissão <span className="text-[#CC3300]">ads_read</span></p>
              </InfoCard>
              <div className="bg-[#F0EDE8] rounded-xl p-4 text-xs text-[#6B5E55] space-y-1">
                <p className="text-[#1A1410] font-semibold mb-2">Onde encontrar o Ad Account ID:</p>
                <p>• Acesse <span className="text-[#1A1410]">business.facebook.com → Configurações → Contas de Anúncios</span></p>
                <p>• O ID aparece no formato <code className="text-[#CC3300]">act_XXXXXXXXX</code></p>
                <p className="mt-2 text-[#1A1410] font-semibold">Onde encontrar o Page ID:</p>
                <p>• Acesse sua Página no Facebook → <span className="text-[#1A1410]">Sobre</span> → role até o final → <span className="text-[#1A1410]">ID da Página</span></p>
              </div>
            </div>

            {/* Roteamento */}
            <div className="bg-[#E8E4DF] rounded-2xl border border-[#D4CFC9] p-5 space-y-3">
              <p className="text-sm font-bold text-[#1A1410]">Roteamento Automático de Leads</p>
              <InfoCard title="Como funciona o roteamento">
                <p>Cada anúncio tem um Page ID. Quando um lead chega, o KLEAD verifica qual instância tem aquele Page ID configurado e roteia o lead automaticamente.</p>
                <p className="mt-1">• Sem Page ID configurado → lead vai ao CRM sem disparar SDR</p>
                <p>• Com Page ID configurado → lead vai ao CRM <span className="text-[#CC3300]">e</span> o SDR dispara o WhatsApp imediatamente</p>
              </InfoCard>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
