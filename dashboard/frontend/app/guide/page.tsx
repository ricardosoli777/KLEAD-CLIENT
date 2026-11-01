"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import PageHeaderShell from "@/components/PageHeaderShell";

// ── Tipos ─────────────────────────────────────────────────────────────────────

type Step = { title: string; desc: string; code?: string; tip?: string };
type Section = { id: string; icon: string; title: string; subtitle: string; color: string; steps: Step[] };
// Vídeos passo a passo: gerenciados pelo superadmin direto na página
// (aceita URL .mp4, URL de player ou HTML de <iframe> — YouTube/Vimeo/Adilo).

// ── Dados dos guias ───────────────────────────────────────────────────────────

const SECTIONS: Section[] = [
  {
    id: "meta-flow",
    icon: "M",
    title: "Meta Lead Ads + KLEAD CRM",
    subtitle: "Receber leads, salvar no CRM e devolver sinais para Advanced Match",
    color: "#A78BFA",
    steps: [
      {
        title: "1. Separe os 2 fluxos",
        desc: "Meta -> KLEAD recebe o lead pelo webhook. KLEAD -> Meta devolve eventos pela Conversions API. São fluxos diferentes e precisam ser configurados separadamente.",
        tip: "Webhook recebe. CAPI devolve sinais para otimização e matching.",
      },
      {
        title: "2. Separe as credenciais",
        desc: "Você vai usar WEBHOOK_SECRET, meta_token, meta_app_secret, meta_page_id, meta_capi_access_token e meta_capi_event_source_id. Se usar WhatsApp Cloud API, adicione o phone_number_id.",
        tip: "phone_number_id é do WhatsApp Cloud API, não é o telefone do cliente.",
      },
      {
        title: "3. Pegue a URL pública da instância",
        desc: "O backend expõe a configuração por instância em /webhook/meta-info/{instance_id}. É daqui que você tira a URL do webhook e o verify token.",
        code: "GET /webhook/meta-info/{instance_id}\nRetorna: webhook_url, verify_token e page_id",
        tip: "Se a URL pública mudar, atualize o webhook na Meta.",
      },
      {
        title: "4. Cadastre o webhook na Meta",
        desc: "No Meta for Developers, crie a assinatura do webhook usando a URL pública do KLEAD e o Verify Token do sistema. O GET valida a URL, e o POST passa a receber os payloads.",
        code: "URL: https://seu-dominio/webhook/{instance_id}\nVerify Token: WEBHOOK_SECRET",
      },
      {
        title: "5. Salve os dados da Meta na instância",
        desc: "Na tela da instância, preencha meta_page_id, meta_token, meta_app_secret e, se necessário, phone_number_id. Isso liga a conta da Meta ao KLEAD correto.",
        tip: "O page_id define para qual instância o lead vai cair quando o webhook chegar.",
      },
      {
        title: "6. Receba o lead no CRM",
        desc: "Quando o POST chega, o KLEAD lê o JSON, identifica a instância, normaliza nome, telefone e email e grava o lead no CRM com a origem da Meta.",
        tip: "O CRM guarda também page_id, form_id, leadgen_id e campos extras.",
      },
      {
        title: "7. Ligue a Conversions API",
        desc: "Se meta_capi_enabled estiver ativo, o KLEAD envia eventos de volta para a Meta quando o lead entra, muda de etapa ou recebe contato manual.",
        code: "Eventos típicos: Lead, QualifiedLead e Contact",
      },
      {
        title: "8. Valide o retorno e o Advanced Matching",
        desc: "O KLEAD envia email, telefone, nome, sobrenome e external_id hashados para a Meta. Se o lead tiver cidade, estado, CEP, país, fbp, fbc, IP ou user agent, esses campos opcionais também podem ir junto.",
        tip: "Quanto mais dados válidos você captura no formulário, melhor tende a ser o match.",
      },
      {
        title: "9. Teste com um lead real",
        desc: "Faça um teste na Meta, confira se o lead apareceu no CRM e verifique os logs do backend. Se a CAPI estiver ativa, confirme o envio do evento de retorno.",
        code: "docker compose logs -f klead-backend",
      },
    ],
  },
  {
    id: "tokens",
    icon: "K",
    title: "Tokens, chaves e APIs",
    subtitle: "De onde cada chave vem, para onde vai e para que serve",
    color: "#F59E0B",
    steps: [
      {
        title: "1. `WEBHOOK_SECRET`",
        desc: "Vem do KLEAD e volta para a Meta como Verify Token. Ele valida a verificação do webhook e protege a entrada dos eventos.",
        tip: "É o token que a Meta usa para confirmar que o webhook é seu.",
      },
      {
        title: "2. `meta_token`",
        desc: "Vem do Meta for Developers e fica salvo na instância do KLEAD. O backend usa esse token para buscar dados do lead, página e métricas.",
      },
      {
        title: "3. `meta_app_secret`",
        desc: "Vem do Meta for Developers e fica salvo na instância do KLEAD. Ele serve para validar a assinatura X-Hub-Signature-256 dos webhooks.",
      },
      {
        title: "4. `meta_page_id`",
        desc: "Vem da Página da Meta / Lead Ads e vai para a instância do KLEAD. Ele serve para roteamento do lead para a conta certa.",
      },
      {
        title: "5. `phone_number_id`",
        desc: "Vem do WhatsApp Cloud API e vai para a instância do KLEAD. Use só se a operação também usar o WhatsApp oficial da Meta.",
      },
      {
        title: "6. `meta_capi_access_token`",
        desc: "Vem da Meta e vai para a instância do KLEAD. É o token usado para enviar eventos server-side para a Conversions API.",
      },
      {
        title: "7. `meta_capi_event_source_id`",
        desc: "Vem do Meta Events Manager e vai para a instância do KLEAD. Identifica a fonte de eventos da CAPI.",
      },
      {
        title: "8. `meta_capi_test_event_code`",
        desc: "Vem do Events Manager da Meta e vai para a instância do KLEAD. É opcional e serve para teste.",
      },
      {
        title: "9. `meta_ad_account_id`",
        desc: "Vem da conta de anúncios da Meta e vai para a instância do KLEAD. É usado para puxar métricas de campanha no dashboard.",
      },
      {
        title: "10. Tabela visual com exemplos",
        desc: "Exemplos fictícios só para referência visual. Não copie esses valores para produção.",
        code: `| Chave / API               | Vem de                    | Vai para                         | Exemplo fictício           |
|--------------------------|---------------------------|----------------------------------|----------------------------|
| WEBHOOK_SECRET           | KLEAD                    | Verify Token da Meta             | whsec_demo_9f2a7c4b       |
| meta_token               | Meta Developers           | Salvo no KLEAD                  | EAA_demo_token_123         |
| meta_app_secret          | Meta Developers           | Salvo no KLEAD                  | appsec_demo_8d51           |
| meta_page_id             | Página da Meta            | Salvo no KLEAD                  | PAGE_123456789             |
| phone_number_id          | WhatsApp Cloud API        | Salvo no KLEAD                  | 123456789012345            |
| meta_capi_access_token    | Meta / CAPI               | Salvo no KLEAD                  | EAA_demo_capi_456          |
| meta_capi_event_source_id | Meta Events Manager       | Salvo no KLEAD                  | 987654321012345            |
| meta_capi_test_event_code | Meta Events Manager       | Salvo no KLEAD                  | TEST1234                   |
| meta_ad_account_id       | Meta Ads Account          | Salvo no KLEAD                  | act_123456789012345        |`,
      },
    ],
  },
  {
    id: "meta",
    icon: "W",
    title: "Meta Cloud API",
    subtitle: "Receba e envie mensagens via API oficial do WhatsApp",
    color: "#CC3300",
    steps: [
      {
        title: "1. Crie um App no Meta for Developers",
        desc: "Acesse developers.facebook.com > Meus Apps > Criar App. Escolha o tipo 'Empresa'. Anote o App ID que aparece no painel.",
        tip: "Use uma conta Business Manager vinculada ao número que vai usar.",
      },
      {
        title: "2. Adicione o produto WhatsApp ao App",
        desc: "Dentro do App, clique em 'Adicionar produto' e selecione WhatsApp. O Meta vai criar automaticamente um número de teste gratuito para você começar.",
      },
      {
        title: "3. Copie o Phone Number ID",
        desc: "Vá em WhatsApp > Configuração da API. No campo 'De', selecione seu número. Abaixo aparece o Phone Number ID — um número de 15 dígitos. Copie esse valor.",
        tip: "É diferente do número de telefone em si. Parece com: 123456789012345",
      },
      {
        title: "4. Gere o Access Token permanente",
        desc: "Vá em Configurações > Business > Usuários do Sistema. Crie um Usuário do Sistema com papel de Admin. Gere um token com as permissões: whatsapp_business_messaging e whatsapp_business_management. Esse token não expira.",
        tip: "Tokens gerados na tela de API Setup expiram em 24h. Use sempre o Usuário do Sistema para token permanente.",
      },
      {
        title: "5. Copie o App Secret",
        desc: "Vá em Configurações > Básico. O campo 'Chave Secreta do Aplicativo' é o App Secret. Clique em 'Mostrar' e copie o valor.",
      },
      {
        title: "6. Configure o Webhook no Meta",
        desc: "Vá em WhatsApp > Configuração. Em Webhooks, clique em 'Configurar'. Cole a URL do webhook do KLEAD e o Verify Token.",
        code: "URL: https://sua-url-publica/webhook/{instance_id}\nVerify Token: valor configurado em WEBHOOK_SECRET",
        tip: "O Verify Token é o mesmo para todas as instâncias e deve ser tratado como segredo operacional.",
      },
      {
        title: "7. Ative a assinatura de mensagens",
        desc: "Após configurar o webhook, habilite a assinatura 'messages' para que o Meta envie as mensagens recebidas para o KLEAD.",
      },
      {
        title: "8. Preencha as credenciais no KLEAD",
        desc: "Crie uma nova instância, selecione 'Meta (Oficial) WABA' como provedor e preencha: Token de Acesso, App Secret e Phone Number ID com os valores coletados acima.",
      },
    ],
  },
  {
    id: "evolution",
    icon: "W",
    title: "WhatsApp Web (Evolution GO)",
    subtitle: "Conecte via QR Code sem precisar da API oficial",
    color: "#CC3300",
    steps: [
      {
        title: "1. Evolution GO já está rodando",
        desc: "O Evolution GO é um serviço separado que já vem configurado no seu VPS. Ele simula o WhatsApp Web e roda na porta 4000 internamente.",
        tip: "Use o domínio do Evolution GO escolhido durante a instalação na sua VPS.",
      },
      {
        title: "2. Crie a instância no KLEAD",
        desc: "Clique em Nova Instância, dê um nome (ex: MinhaEmpresa-SDR), selecione 'Evolution GO' como provedor e preencha o Token da Instância com a chave de API do seu Evolution GO.",
        tip: "O token fica em /root/evolution/.env no VPS, campo AUTHENTICATION_API_KEY.",
      },
      {
        title: "3. Escaneie o QR Code",
        desc: "Após criar a instância, acesse o dashboard dela e clique em 'Ver QR Code'. Abra o WhatsApp no celular > Aparelhos Conectados > Conectar Aparelho e escaneie o QR.",
        tip: "Use um número dedicado para o SDR — não use seu WhatsApp pessoal principal.",
      },
      {
        title: "4. Confirme a conexão",
        desc: "Na home do KLEAD, o card da instância vai mostrar 'WA Conectado' em verde. Se ficar em amarelo 'Conectando', aguarde 30 segundos e recarregue a página.",
      },
      {
        title: "5. Configure o webhook automaticamente",
        desc: "O KLEAD tenta configurar o webhook automaticamente ao criar a instância. Se o status do webhook estiver incorreto, use o script de recuperação.",
        code: "python scripts/fix-webhook.py",
        tip: "Execute no diretório raiz do KLEAD no VPS: cd /root/klead && python scripts/fix-webhook.py",
      },
      {
        title: "6. (Opcional) Evo Instance Name",
        desc: "Se o nome da instância no Evolution GO for diferente do nome no KLEAD, preencha o campo 'Evo Instance Name' nas configurações da instância para evitar falhas de envio.",
      },
    ],
  },
  {
    id: "inbound",
    icon: "→",
    title: "Leads Inbound para o CRM",
    subtitle: "Como os leads chegam ao CRM automaticamente ou manualmente",
    color: "#F0B429",
    steps: [
      {
        title: "Origem 1 — Meta Ads (automático)",
        desc: "Quando um lead responde um anúncio no Facebook ou Instagram e envia mensagem via WhatsApp, o Meta encaminha para o webhook do KLEAD. O lead é criado automaticamente na coluna 'Novo Lead' do CRM com a origem 'Meta Ads'.",
        tip: "Não precisa fazer nada. Desde que o webhook Meta esteja configurado, os leads aparecem automaticamente.",
      },
      {
        title: "Origem 2 — SDR Inbox (manual com 1 clique)",
        desc: "Quando um lead frio responde sua campanha de disparo e a IA qualifica ele como interessante, vá ao Inbox SDR da instância, abra a conversa e clique no botão roxo 'Enviar ao CRM'. O lead vai para a coluna 'Novo Lead' com o histórico completo da conversa.",
        tip: "O botão fica na barra de ações no topo da conversa, ao lado de 'Resumo IA'.",
      },
      {
        title: "Origem 3 — WhatsApp Business (em breve)",
        desc: "Integração com WhatsApp Business via campanha de anúncios direto no Meta Ads Manager. Os leads que clicam em 'Enviar mensagem' no anúncio chegam via webhook Meta — mesmo fluxo da Origem 1.",
      },
      {
        title: "Origem 4 — Manual",
        desc: "No CRM, clique em '+ Lead' no canto superior direito. Preencha nome, telefone e selecione a etapa inicial. Útil para leads captados fora do WhatsApp (eventos, indicações, formulários web).",
      },
      {
        title: "Depois que o lead está no CRM",
        desc: "Clique no card do lead para abrir o painel lateral. Você pode: mover entre etapas (drag & drop ou botões), adicionar notas, ver o histórico completo de conversa, adicionar tags e enviar mensagem WhatsApp direto do CRM.",
        tip: "Use tags para segmentar: 'quente', 'aguardando proposta', 'decisor', etc.",
      },
      {
        title: "Configurando a pontuação (score)",
        desc: "O score de 0 a 100 é gerado automaticamente pela IA durante a qualificação no SDR. Você pode personalizar os critérios de pontuação na aba SDR da instância, no campo 'Critérios de Pontuação'.",
        code: "Verde: 70-100 (lead quente)\nAmarelo: 40-69 (lead morno)\nVermelho: 0-39 (lead frio)",
      },
    ],
  },
  {
    id: "sdr",
    icon: "AI",
    title: "Configurando o Agente SDR",
    subtitle: "IA que qualifica leads automaticamente no WhatsApp",
    color: "#60A5FA",
    steps: [
      {
        title: "1. Acesse as configurações SDR",
        desc: "Na home, clique em 'SDR' no card da instância. Depois vá na aba 'SDR' dentro do dashboard.",
      },
      {
        title: "2. Configure o provedor de IA",
        desc: "Escolha o provedor (OpenAI, Anthropic ou Groq) e insira a API key correspondente. Selecione o modelo. Para qualificação de leads recomendamos GPT-4o-mini (custo-benefício) ou Claude Haiku (velocidade).",
        tip: "Cada provedor guarda sua própria chave — trocar de provedor não apaga a chave do outro.",
      },
      {
        title: "3. Escreva o Prompt de Qualificação",
        desc: "Esse é o roteiro que a IA segue para qualificar o lead. Inclua: qual produto/serviço você oferece, qual perfil de cliente ideal, quais perguntas fazer, quando fazer handoff para humano.",
        tip: "Seja específico. Quanto mais contexto você der, melhor a IA qualifica.",
      },
      {
        title: "4. Defina o limite de turnos",
        desc: "Max AI Turns define quantas respostas a IA dá antes de passar para um humano automaticamente. Recomendamos 5-8 turnos para não cansar o lead.",
      },
      {
        title: "5. Configure palavras-chave de handoff",
        desc: "Palavras que, se o lead disser, transferem imediatamente para humano. Ex: preco, valor, quero comprar, falar com alguem.",
        tip: "Separe por vírgula. Sem acentos para pegar variações digitadas.",
      },
      {
        title: "6. Ative o SDR",
        desc: "Toggle 'IA Inbound Ativada' deve estar ligado. A partir daí, toda mensagem recebida nessa instância passa pela IA automaticamente.",
      },
    ],
  },
];

// ── Componente de step ────────────────────────────────────────────────────────

function StepCard({ step, color }: { step: Step; color: string }) {
  return (
    <div className="bg-[#F0EDE8] rounded-xl p-4 border border-[#D4CFC9]">
      <p className="font-semibold text-[#1A1410] text-sm mb-2">{step.title}</p>
      <p className="text-[#6B5E55] text-sm leading-relaxed">{step.desc}</p>
      {step.code && (
        <pre className="mt-3 bg-[#F0EDE8] border border-[#D4CFC9] rounded-lg p-3 text-xs text-[#CC3300] font-mono overflow-x-auto whitespace-pre-wrap">
          {step.code}
        </pre>
      )}
      {step.tip && (
        <div className="mt-3 flex gap-2 bg-[#E8E4DF] rounded-lg px-3 py-2 border-l-2" style={{ borderColor: color }}>
          <span className="text-xs font-bold shrink-0" style={{ color }}>Dica</span>
          <p className="text-xs text-[#6B5E55] leading-relaxed">{step.tip}</p>
        </div>
      )}
    </div>
  );
}

// ── Componente de seção ───────────────────────────────────────────────────────

function GuideSection({ section }: { section: Section }) {
  const [open, setOpen] = useState(false);

  return (
    <div className="bg-[#E8E4DF] rounded-2xl border border-[#D4CFC9] overflow-hidden">
      {/* Header clicável */}
      <button
        onClick={() => setOpen(!open)}
        className="w-full flex items-center gap-4 p-5 text-left hover:bg-[#D4CFC9] transition-colors"
      >
        <div
          className="w-10 h-10 rounded-xl flex items-center justify-center font-bold text-sm shrink-0 text-[#F0EDE8]"
          style={{ backgroundColor: section.color }}
        >
          {section.icon}
        </div>
        <div className="flex-1 min-w-0">
          <p className="font-bold text-[#1A1410] text-base">{section.title}</p>
          <p className="text-[#6B5E55] text-xs mt-0.5">{section.subtitle}</p>
        </div>
        <div className="flex items-center gap-3 shrink-0">
          <span className="text-xs text-[#6B5E55] bg-[#F0EDE8] px-2 py-1 rounded-lg">
            {section.steps.length} passos
          </span>
          <svg
            viewBox="0 0 24 24"
            className={`w-5 h-5 fill-[#6B5E55] transition-transform ${open ? "rotate-180" : ""}`}
          >
            <path d="M7 10l5 5 5-5z" />
          </svg>
        </div>
      </button>

      {/* Conteúdo expandido */}
      {open && (
        <div className="px-5 pb-5 space-y-3 border-t border-[#D4CFC9] pt-4">
          {section.steps.map((step, i) => (
            <StepCard key={i} step={step} color={section.color} />
          ))}
        </div>
      )}
    </div>
  );
}

// ── Página principal ──────────────────────────────────────────────────────────

export default function GuidePage() {
  const [isSuperadmin, setIsSuperadmin] = useState(false);

  useEffect(() => {
    fetch("/api/proxy/auth/me")
      .then(r => (r.ok ? r.json() : null))
      .then(data => {
        if (data?.funcao === "superadmin") setIsSuperadmin(true);
      })
      .catch(() => {});
  }, []);

  return (
    <div className="min-h-screen bg-[#F0EDE8]">

      {/* Header */}
      <PageHeaderShell maxWidthClassName="max-w-3xl">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex items-start gap-4">
            <Link href="/" className="mt-0.5 text-xs font-semibold uppercase tracking-[0.22em] text-[#6B5E55] transition-colors hover:text-[#1A1410]">
              Voltar
            </Link>
            <div>
              <h1 className="text-[#1A1410] font-bold text-lg leading-tight">Como Usar</h1>
              <p className="text-[#6B5E55] text-[12px] mt-0.5">Passo a passo para conectar, receber e devolver dados no KLEAD</p>
            </div>
          </div>
          <span className="inline-flex w-fit items-center gap-2 rounded-full border border-[#D4CFC9] bg-[#F0EDE8] px-3 py-1 text-[11px] font-semibold text-[#6B5E55]">
            <span className="w-1.5 h-1.5 rounded-full bg-[#CC3300]" />
            Central de ajuda
          </span>
        </div>
      </PageHeaderShell>

      <main className="max-w-3xl mx-auto px-6 py-6 space-y-4">

        {/* Banner de acesso rapido */}
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-2">
          {SECTIONS.map((s) => (
            <a
              key={s.id}
              href={`#${s.id}`}
              onClick={(e) => {
                e.preventDefault();
                document.getElementById(s.id)?.scrollIntoView({ behavior: "smooth" });
              }}
              className="flex items-center gap-2 bg-[#E8E4DF] border border-[#D4CFC9] rounded-xl px-3 py-2.5 hover:border-[#3D4F5A] transition-colors"
            >
              <span
                className="w-6 h-6 rounded-lg flex items-center justify-center text-[10px] font-bold text-[#F0EDE8] shrink-0"
                style={{ backgroundColor: s.color }}
              >
                {s.icon}
              </span>
              <span className="text-[#1A1410] text-xs font-semibold truncate">{s.title}</span>
            </a>
          ))}
        </div>

        {/* CTA: página de vídeos */}
        <Link
          href="/guide/videos"
          className="flex items-center justify-between gap-3 bg-[#E8E4DF] border border-[#D4CFC9] rounded-2xl p-5 mb-2 hover:border-[#3D4F5A] transition-colors group"
        >
          <div className="flex items-center gap-3">
            <span className="w-9 h-9 rounded-xl flex items-center justify-center text-sm font-bold text-[#F0EDE8] bg-[#CC3300] shrink-0">▶</span>
            <div>
              <h2 className="text-[#1A1410] text-sm font-bold leading-tight">Vídeos passo a passo</h2>
              <p className="text-[#6B5E55] text-[11px] mt-0.5">Aprenda a usar o KLEAD CRM assistindo aos tutoriais</p>
            </div>
          </div>
          <span className="text-xs font-semibold px-3 py-1.5 rounded-lg border border-[#D4CFC9] bg-[#F0EDE8] text-[#1A1410] group-hover:border-[#3D4F5A] transition-colors shrink-0">
            Ver vídeos →
          </span>
        </Link>

        {/* Seções */}
        {SECTIONS.map((section) => (
          <div key={section.id} id={section.id}>
            <GuideSection section={section} />
          </div>
        ))}

        {/* Rodapé de suporte (apenas superadmin) */}
        {isSuperadmin && (
        <div className="bg-[#E8E4DF] border border-[#D4CFC9] rounded-2xl p-5 mt-6">
          <p className="text-[#6B5E55] text-xs font-semibold uppercase tracking-wide mb-3">Comandos uteis no VPS</p>
          <div className="space-y-2">
            {[
              ["Ver logs do backend em tempo real", "docker compose logs -f klead-backend"],
              ["Reiniciar backend (apos alteracoes)", "docker restart klead-backend"],
              ["Corrigir webhook (todas as instancias)", "cd /root/klead && python scripts/fix-webhook.py"],
              ["Ver credenciais da Meta na instancia", "Abrir a tela da instancia e conferir Meta / Conversions API"],
              ["Ver estado do webhook no banco", "docker exec evolution-postgres psql -U postgres -d evogo_users -c \"SELECT name, webhook FROM instances;\""],
            ].map(([label, cmd]) => (
              <div key={cmd} className="bg-[#F0EDE8] rounded-xl p-3">
                <p className="text-[#6B5E55] text-xs mb-1">{label}</p>
                <code className="text-[#CC3300] text-xs font-mono">{cmd}</code>
              </div>
            ))}
          </div>
        </div>
        )}

      </main>
    </div>
  );
}
