"use client";
import { Suspense, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";

interface Plan {
  id: string;
  label: string;
  max_instances: number;
}

const PLAN_INFO: Record<string, { price: string; tagline: string; features: string[]; highlight?: boolean }> = {
  user: {
    price: "R$ 69,88",
    tagline: "Pra quem quer testar o KLEAD com uma linha de WhatsApp.",
    features: ["1 instância de WhatsApp", "IA qualificando leads 24h", "Disparo de campanhas", "CRM com Kanban", "Inbox com histórico de conversas"],
  },
  pro: {
    price: "R$ 199,88",
    tagline: "Pra times que já rodam várias campanhas em paralelo.",
    features: ["5 instâncias de WhatsApp", "Tudo do plano User", "Roleta de distribuição de leads"],
    highlight: true,
  },
};

// As 4 formas de pagar o Lite — cards separados na vitrine. ciclo vazio =
// mensal recorrente (price padrão do lite); mensal_avulso é pagamento ÚNICO
// ("Experimente por um mês"): 30 dias de acesso, sem renovação automática.
const LITE_VARIANTS: { ciclo: string; nome: string; price: string; per: string; note: string; badge?: string }[] = [
  { ciclo: "",              nome: "Lite Mensal",    price: "R$ 29,88",  per: "por mês",         note: "renova todo mês — cancele quando quiser" },
  { ciclo: "mensal_avulso", nome: "Lite Avulso",    price: "R$ 39,88",  per: "pagamento único", note: "30 dias de acesso — sem renovação automática", badge: "Experimente" },
  { ciclo: "semestral",     nome: "Lite Semestral", price: "R$ 149,88", per: "a cada 6 meses",  note: "sai por R$ 24,98/mês", badge: "-16%" },
  { ciclo: "anual",         nome: "Lite Anual",     price: "R$ 249,88", per: "por ano",         note: "sai por R$ 20,82/mês", badge: "-30%" },
];

// WhatsApp do suporte pro Enterprise sob consulta. O número vem do telefone
// cadastrado no PERFIL do superadmin (exposto em /auth/plans-public) — troque
// lá e a landing atualiza sozinha. Se o perfil não tiver telefone, usa o fallback.
const WHATSAPP_FALLBACK = "5511947576690";
const waSuporteLink = (numero: string) =>
  `https://wa.me/${numero || WHATSAPP_FALLBACK}?text=` +
  encodeURIComponent("Olá! Quero saber mais sobre o plano Enterprise do KLEAD (mais de 5 instâncias).");

export default function PlansPage() {
  return (
    <Suspense fallback={null}>
      <PlansPageInner />
    </Suspense>
  );
}

function PlansPageInner() {
  const searchParams = useSearchParams();
  const planoParam = searchParams.get("plano") || "";
  const cicloParam = searchParams.get("ciclo") || "";
  const directCheckoutPlan = ["lite", "user", "pro"].includes(planoParam)
    ? planoParam
    : "";
  const directCheckoutCycle =
    directCheckoutPlan === "lite" && ["mensal_avulso", "semestral", "anual"].includes(cicloParam)
      ? cicloParam
      : "";

  const [plans, setPlans] = useState<Plan[]>([]);
  const [loadingPlans, setLoadingPlans] = useState(true);
  const [whatsSuporte, setWhatsSuporte] = useState("");

  const [showModal, setShowModal] = useState(() => Boolean(directCheckoutPlan));
  const [selectedPlan, setSelectedPlan] = useState<string>(() => directCheckoutPlan);
  const [ciclo, setCiclo] = useState<string>(() => directCheckoutCycle);
  const [nome, setNome] = useState("");
  const [email, setEmail] = useState("");
  const [error, setError] = useState("");
  const [sending, setSending] = useState(false);

  const checkoutStatus = searchParams.get("checkout");

  useEffect(() => {
    fetch("/api/proxy/auth/plans-public")
      .then(r => r.json())
      .then(data => {
        setPlans(data.plans || []);
        setWhatsSuporte(data.whatsapp_suporte || "");
      })
      .finally(() => setLoadingPlans(false));
  }, []);

  function openModal(planId: string, cicloSel: string = "") {
    setSelectedPlan(planId);
    setCiclo(cicloSel);
    setError("");
    setShowModal(true);
  }

  async function handleCheckout(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    if (!selectedPlan) { setError("Escolha um plano acima."); return; }
    setSending(true);
    try {
      const res = await fetch("/api/proxy/billing/checkout", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          email, nome, plano: selectedPlan,
          ...(selectedPlan === "lite" && ciclo ? { ciclo } : {}),
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || !data.url) { setError(data.detail || "Erro ao iniciar pagamento."); setSending(false); return; }
      window.location.href = data.url;
    } catch {
      setError("Erro ao conectar. Tente novamente.");
      setSending(false);
    }
  }

  return (
    <div className="min-h-screen bg-[#F0EDE8] flex flex-col">
      {/* Header simples */}
      <header className="border-b border-[#D4CFC9] px-4 py-4">
        <div className="max-w-5xl mx-auto">
          <span className="text-2xl font-bold text-[#CC3300]">KLEAD</span>
        </div>
      </header>

      <main className="flex-1">
        {/* Hero */}
        <section className="px-4 pt-16 pb-12 text-center">
          <div className="max-w-3xl mx-auto">
            <span className="inline-block bg-[#CC3300]/10 text-[#CC3300] text-xs font-bold uppercase tracking-wide px-3 py-1.5 rounded-full mb-5">
              IA de qualificação de leads no WhatsApp
            </span>
            <h1 className="text-[#1A1410] text-3xl sm:text-5xl font-bold leading-tight">
              Seu WhatsApp respondendo, qualificando e pontuando leads — 24h por dia, sem contratar ninguém.
            </h1>
            <p className="text-[#6B5E55] text-base sm:text-lg mt-5 max-w-2xl mx-auto">
              O KLEAD conecta ao seu WhatsApp em minutos, conversa com quem responde suas campanhas,
              dá uma nota de 0 a 100 pra cada lead e entrega só os quentes pro seu time fechar.
              Você dispara, a IA filtra, você vende.
            </p>
            <div className="flex flex-col sm:flex-row items-center justify-center gap-3 mt-8">
              <a
                href="#planos"
                className="w-full sm:w-auto bg-[#CC3300] hover:bg-[#991F00] text-white font-semibold px-8 py-3 rounded-xl transition-colors text-sm"
              >
                Ver planos e preços
              </a>
              <a
                href="#planos"
                className="w-full sm:w-auto border border-[#CC3300] text-[#CC3300] hover:bg-[#CC3300] hover:text-white font-semibold px-8 py-3 rounded-xl transition-colors text-sm"
              >
                Experimentar por 30 dias
              </a>
            </div>
            <p className="text-[#6B5E55] text-xs mt-5">
              Conecta por QR Code em minutos · IA com o tom da sua empresa · Cancele quando quiser
            </p>
          </div>
        </section>

        {/* Dor */}
        <section className="px-4 pb-16">
          <div className="max-w-4xl mx-auto">
            <h2 className="text-[#1A1410] text-2xl font-bold text-center mb-2">
              O problema não é gerar leads. É atender todos eles.
            </h2>
            <p className="text-[#6B5E55] text-sm text-center mb-10">
              Você investe em tráfego, os leads chegam... e aí o funil fura:
            </p>
            <div className="grid gap-6 sm:grid-cols-3">
              <div className="rounded-2xl border border-[#D4CFC9] bg-[#E8E4DF] p-6">
                <p className="text-[#CC3300] font-bold text-sm mb-2">Lead esfria em minutos</p>
                <p className="text-[#6B5E55] text-xs leading-relaxed">
                  Quem responde primeiro leva. Se o &quot;oi, quero saber mais&quot; fica horas sem resposta,
                  o concorrente já fechou.
                </p>
              </div>
              <div className="rounded-2xl border border-[#D4CFC9] bg-[#E8E4DF] p-6">
                <p className="text-[#CC3300] font-bold text-sm mb-2">Time afogado em curioso</p>
                <p className="text-[#6B5E55] text-xs leading-relaxed">
                  Seu vendedor passa o dia repetindo as mesmas perguntas pra quem nunca vai comprar —
                  enquanto o lead bom espera na fila.
                </p>
              </div>
              <div className="rounded-2xl border border-[#D4CFC9] bg-[#E8E4DF] p-6">
                <p className="text-[#CC3300] font-bold text-sm mb-2">Ninguém atende de madrugada</p>
                <p className="text-[#6B5E55] text-xs leading-relaxed">
                  A campanha roda 24h, mas seu time não. O lead das 23h só recebe resposta às 9h
                  do dia seguinte — se receber.
                </p>
              </div>
            </div>
          </div>
        </section>

        {/* Como funciona */}
        <section className="px-4 pb-16">
          <div className="max-w-5xl mx-auto">
            <h2 className="text-[#1A1410] text-2xl font-bold text-center mb-2">
              Do clique no anúncio ao lead quente no seu CRM
            </h2>
            <p className="text-[#6B5E55] text-sm text-center mb-10">
              Quatro passos, zero planilha, zero lead esquecido.
            </p>
            <div className="grid gap-6 sm:grid-cols-2 lg:grid-cols-4 text-center">
              <div>
                <p className="text-[#CC3300] font-bold text-2xl mb-1">1</p>
                <p className="text-[#1A1410] font-semibold text-sm mb-1">Conecte seu WhatsApp</p>
                <p className="text-[#6B5E55] text-xs">QR Code ou WhatsApp oficial da Meta. Em minutos, sem instalar nada.</p>
              </div>
              <div>
                <p className="text-[#CC3300] font-bold text-2xl mb-1">2</p>
                <p className="text-[#1A1410] font-semibold text-sm mb-1">A IA assume o atendimento</p>
                <p className="text-[#6B5E55] text-xs">Responde na hora, no seu tom, uma pergunta por vez — como um SDR treinado por você.</p>
              </div>
              <div>
                <p className="text-[#CC3300] font-bold text-2xl mb-1">3</p>
                <p className="text-[#1A1410] font-semibold text-sm mb-1">Cada lead ganha nota de 0 a 100</p>
                <p className="text-[#6B5E55] text-xs">Seus critérios viram pontuação automática. Curioso é descartado sozinho.</p>
              </div>
              <div>
                <p className="text-[#CC3300] font-bold text-2xl mb-1">4</p>
                <p className="text-[#1A1410] font-semibold text-sm mb-1">Você fecha os quentes</p>
                <p className="text-[#6B5E55] text-xs">Score alto vira handoff no Inbox e no CRM. Seu time só fala com quem pode comprar.</p>
              </div>
            </div>
          </div>
        </section>

        {/* Recursos */}
        <section className="px-4 pb-16">
          <div className="max-w-5xl mx-auto">
            <h2 className="text-[#1A1410] text-2xl font-bold text-center mb-2">
              Uma máquina de qualificação completa — não só um chatbot
            </h2>
            <p className="text-[#6B5E55] text-sm text-center mb-10">
              Chatbot responde. O KLEAD qualifica, pontua, organiza e ainda devolve dados pras suas campanhas renderem mais.
            </p>
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
              {[
                { t: "SDR de IA 24/7", d: "Qualifica com as SUAS perguntas e passa o lead pro humano sozinho quando ele esquenta." },
                { t: "Pontuação 0–100", d: "Regras suas (“tem orçamento: +25”) viram score consistente. Sem achismo na fila de vendas." },
                { t: "Inbox em tempo real", d: "Toda conversa num só lugar. Assuma da IA a qualquer momento, com o histórico completo." },
                { t: "CRM Kanban", d: "O card acompanha a conversa ao vivo — do primeiro oi ao fechamento, sem digitação manual." },
                { t: "Disparo de campanhas", d: "Dispare pra base inteira e deixe a IA segurar a enxurrada de respostas por você." },
                { t: "Roleta de leads", d: "Distribui leads automaticamente entre os vendedores. Sem briga, sem lead esquecido." },
                { t: "Meta Ads integrado", d: "Lead Ads caem direto no CRM e a Conversions API devolve sinais pra Meta otimizar suas campanhas." },
                { t: "Base de conhecimento", d: "A IA responde dúvidas com as informações do SEU negócio — nada de resposta inventada." },
              ].map(f => (
                <div key={f.t} className="rounded-2xl border border-[#D4CFC9] bg-[#E8E4DF] p-5">
                  <p className="text-[#1A1410] font-semibold text-sm mb-1.5">{f.t}</p>
                  <p className="text-[#6B5E55] text-xs leading-relaxed">{f.d}</p>
                </div>
              ))}
            </div>
            <p className="text-[#6B5E55] text-xs text-center mt-6">
              Você escolhe o motor de IA: OpenAI, Anthropic ou Groq (com opção gratuita) — sempre com a sua própria chave.
            </p>
          </div>
        </section>

        {/* Planos */}
        <section id="planos" className="px-4 pb-16 scroll-mt-6">
          <div className="max-w-6xl mx-auto">
            <h2 className="text-[#1A1410] text-2xl font-bold text-center mb-2">Planos</h2>
            <p className="text-[#6B5E55] text-sm text-center mb-10">Do apoio de IA no atendimento manual à automação completa — escolha o que faz sentido pra sua operação.</p>

            {loadingPlans ? (
              <p className="text-center text-[#6B5E55] text-sm">Carregando planos...</p>
            ) : (
              <>
                {/* Família Lite — 4 formas de pagar, cards separados */}
                <div className="mb-12">
                  <p className="text-center text-[#1A1410] font-bold text-base mb-1">
                    Lite — atendimento com IA de apoio
                  </p>
                  <p className="text-center text-[#6B5E55] text-xs mb-6 max-w-2xl mx-auto">
                    1 instância de WhatsApp · Inbox com sugestões de resposta por IA · CRM Kanban ·
                    Dashboard e análise de Meta Ads · Base de conhecimento
                  </p>
                  <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
                    {LITE_VARIANTS.map(v => (
                      <div
                        key={v.nome}
                        className={`rounded-2xl border p-5 flex flex-col bg-[#E8E4DF] ${
                          v.badge === "Experimente" ? "border-[#CC3300] ring-2 ring-[#CC3300]/20" : "border-[#D4CFC9]"
                        }`}
                      >
                        {v.badge ? (
                          <span className="self-start bg-[#CC3300] text-white text-[10px] font-bold uppercase tracking-wide px-2 py-0.5 rounded-full mb-2">
                            {v.badge}
                          </span>
                        ) : (
                          <span className="h-[26px]" />
                        )}
                        <p className="text-[#1A1410] font-bold text-sm">{v.nome}</p>
                        <p className="text-[#1A1410] font-bold text-2xl mt-2">{v.price}</p>
                        <p className="text-[#6B5E55] text-xs">{v.per}</p>
                        <p className="text-[#6B5E55] text-[11px] mt-2 mb-4 flex-1">{v.note}</p>
                        <button
                          onClick={() => openModal("lite", v.ciclo)}
                          className={`w-full font-semibold py-2.5 rounded-xl transition-colors text-xs ${
                            v.badge === "Experimente"
                              ? "bg-[#CC3300] hover:bg-[#991F00] text-white"
                              : "border border-[#CC3300] text-[#CC3300] hover:bg-[#CC3300] hover:text-white"
                          }`}
                        >
                          {v.ciclo === "mensal_avulso" ? "Experimentar 1 mês" : `Assinar ${v.nome}`}
                        </button>
                      </div>
                    ))}
                  </div>
                </div>

                {/* User e Pro */}
                <div className="grid gap-6 sm:grid-cols-2 max-w-2xl mx-auto">
                {plans.filter(p => p.id !== "lite").map(plan => {
                  const info = PLAN_INFO[plan.id];
                  if (!info) return null;
                  return (
                    <div
                      key={plan.id}
                      className={`rounded-2xl border p-6 flex flex-col bg-[#E8E4DF] ${
                        info.highlight ? "border-[#CC3300] ring-2 ring-[#CC3300]/20" : "border-[#D4CFC9]"
                      }`}
                    >
                      {info.highlight && (
                        <span className="self-start bg-[#CC3300] text-white text-[10px] font-bold uppercase tracking-wide px-2 py-1 rounded-full mb-3">
                          Mais popular
                        </span>
                      )}
                      <p className="text-[#1A1410] font-bold text-lg">{plan.label}</p>
                      <p className="text-[#6B5E55] text-xs mt-1 mb-4">{info.tagline}</p>
                      <p className="text-[#1A1410] font-bold text-3xl mb-1">{info.price}</p>
                      <p className="text-[#6B5E55] text-xs mb-5">por mês</p>

                      <ul className="space-y-2 mb-6 flex-1">
                        {info.features.map(f => (
                          <li key={f} className="flex items-start gap-2 text-[#1A1410] text-xs">
                            <span className="text-[#CC3300] font-bold mt-0.5">✓</span>
                            <span>{f}</span>
                          </li>
                        ))}
                      </ul>

                      <button
                        onClick={() => openModal(plan.id)}
                        className={`w-full font-semibold py-3 rounded-xl transition-colors text-sm ${
                          info.highlight
                            ? "bg-[#CC3300] hover:bg-[#991F00] text-white"
                            : "border border-[#CC3300] text-[#CC3300] hover:bg-[#CC3300] hover:text-white"
                        }`}
                      >
                        Assinar {plan.label}
                      </button>
                    </div>
                  );
                })}
                </div>

                {/* Enterprise sob consulta */}
                <div className="mt-10 rounded-2xl border border-[#D4CFC9] bg-[#E8E4DF] p-6 sm:p-8 flex flex-col sm:flex-row items-center justify-between gap-4 text-center sm:text-left">
                  <div>
                    <p className="text-[#1A1410] font-bold text-lg">Precisa de mais de 5 instâncias?</p>
                    <p className="text-[#6B5E55] text-xs mt-1 max-w-md">
                      Plano Enterprise sob medida — 10, 20, 50 instâncias de WhatsApp, com condições
                      negociadas pro tamanho da sua operação.
                    </p>
                  </div>
                  <a
                    href={waSuporteLink(whatsSuporte)}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="shrink-0 bg-[#CC3300] hover:bg-[#991F00] text-white font-semibold px-6 py-3 rounded-xl transition-colors text-sm"
                  >
                    Falar com a gente
                  </a>
                </div>
              </>
            )}
          </div>
        </section>

        {/* Comparativo */}
        <section className="px-4 pb-16">
          <div className="max-w-5xl mx-auto">
            <h2 className="text-[#1A1410] text-2xl font-bold text-center mb-2">Compare os planos</h2>
            <p className="text-[#6B5E55] text-sm text-center mb-8">
              Todos os planos incluem Inbox, CRM e análise de Meta Ads. A diferença está na automação e no volume.
            </p>
            <div className="overflow-x-auto rounded-2xl border border-[#D4CFC9]">
              <table className="w-full text-xs border-collapse min-w-[640px] bg-[#E8E4DF]">
                <thead>
                  <tr className="border-b border-[#D4CFC9]">
                    <th className="text-left text-[#1A1410] font-semibold p-3">Recurso</th>
                    <th className="text-center text-[#1A1410] font-semibold p-3">Lite</th>
                    <th className="text-center text-[#1A1410] font-semibold p-3">User</th>
                    <th className="text-center text-[#1A1410] font-semibold p-3">Pro</th>
                  </tr>
                </thead>
                <tbody>
                  {[
                    ["Instâncias de WhatsApp", "1", "1", "5"],
                    ["Inbox + CRM Kanban", "✓", "✓", "✓"],
                    ["Dashboard e análise de Meta Ads", "✓", "✓", "✓"],
                    ["Sugestões de resposta por IA (co-piloto)", "✓", "✓", "✓"],
                    ["Opção de pagamento único (30 dias)", "✓", "—", "—"],
                    ["Ciclos semestral e anual com desconto", "✓", "—", "—"],
                    ["SDR de IA autônomo 24/7", "—", "✓", "✓"],
                    ["Pontuação de leads 0–100", "—", "✓", "✓"],
                    ["Disparo de campanhas", "—", "✓", "✓"],
                    ["Roleta de distribuição de leads", "—", "✓", "✓"],
                    ["Supervisão de atendentes", "—", "✓", "✓"],
                  ].map(row => (
                    <tr key={row[0]} className="border-b border-[#D4CFC9] last:border-0">
                      <td className="text-[#1A1410] p-3">{row[0]}</td>
                      {row.slice(1).map((cell, i) => (
                        <td key={i} className={`text-center p-3 font-semibold ${cell === "—" ? "text-[#6B5E55]" : "text-[#CC3300]"}`}>
                          {cell}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="text-[#6B5E55] text-xs text-center mt-4">
              Precisa de mais volume?{" "}
              <a href={waSuporteLink(whatsSuporte)} target="_blank" rel="noopener noreferrer" className="text-[#CC3300] font-semibold underline">
                Enterprise sob medida — fale com a gente
              </a>
            </p>
          </div>
        </section>

        {/* FAQ */}
        <section className="px-4 pb-16">
          <div className="max-w-3xl mx-auto">
            <h2 className="text-[#1A1410] text-2xl font-bold text-center mb-8">Perguntas frequentes</h2>
            <div className="space-y-3">
              {[
                {
                  q: "Posso testar antes de assinar?",
                  a: "Sim. O Lite Avulso é exatamente isso: pagamento único de R$ 39,88, 30 dias de acesso e nenhuma renovação automática. Gostou? Aí você assina o mensal, o semestral ou o anual (bem mais baratos por mês).",
                },
                {
                  q: "Posso cancelar quando quiser?",
                  a: "Pode. As assinaturas renovam automaticamente, mas você cancela a qualquer momento, sem multa e sem burocracia. E a compra avulsa nem renova — acabou os 30 dias, acabou a cobrança.",
                },
                {
                  q: "Funciona com o meu número de WhatsApp atual?",
                  a: "Sim. Você conecta escaneando um QR Code (como o WhatsApp Web) ou, se preferir, usa o WhatsApp oficial da Meta (Cloud API) — inclusive com integração de Lead Ads.",
                },
                {
                  q: "A IA vai responder como um robô?",
                  a: "Não. Você escreve o prompt com o tom da sua empresa e as perguntas de qualificação do seu negócio. A IA responde curto, natural e uma pergunta por vez — e você pode assumir a conversa a qualquer momento pelo Inbox.",
                },
                {
                  q: "Qual inteligência artificial o KLEAD usa?",
                  a: "Você escolhe: OpenAI, Anthropic ou Groq (que tem opção gratuita). A chave de API é sua, o que te dá controle total de custo e de modelo.",
                },
                {
                  q: "Como recebo o acesso depois de pagar?",
                  a: "Na hora. Assim que o pagamento é confirmado no checkout seguro do Stripe, sua senha chega por email e você já conecta o WhatsApp em minutos.",
                },
              ].map(item => (
                <details key={item.q} className="rounded-2xl border border-[#D4CFC9] bg-[#E8E4DF] px-5 py-4 group">
                  <summary className="text-[#1A1410] font-semibold text-sm cursor-pointer list-none flex items-center justify-between gap-4">
                    {item.q}
                    <span className="text-[#CC3300] font-bold group-open:rotate-45 transition-transform">+</span>
                  </summary>
                  <p className="text-[#6B5E55] text-xs leading-relaxed mt-3">{item.a}</p>
                </details>
              ))}
            </div>
          </div>
        </section>

        {/* CTA final */}
        <section className="px-4 pb-20">
          <div className="max-w-3xl mx-auto rounded-2xl border border-[#CC3300] bg-[#E8E4DF] p-10 text-center">
            <h2 className="text-[#1A1410] text-2xl sm:text-3xl font-bold leading-tight">
              Cada minuto sem resposta é um lead esfriando.
            </h2>
            <p className="text-[#6B5E55] text-sm mt-4 max-w-xl mx-auto">
              Coloque a IA pra atender hoje: conexão por QR Code em minutos, acesso liberado na hora
              e 30 dias avulsos pra provar que funciona — sem renovação automática.
            </p>
            <a
              href="#planos"
              className="inline-block mt-7 bg-[#CC3300] hover:bg-[#991F00] text-white font-semibold px-10 py-3.5 rounded-xl transition-colors text-sm"
            >
              Escolher meu plano
            </a>
          </div>
        </section>

        {checkoutStatus === "cancelado" && (
          <div className="max-w-md mx-auto px-4 mb-8">
            <div className="bg-[#2D2000] border border-[#F0B429]/40 rounded-xl px-4 py-3 text-[#F0B429] text-sm text-center">
              Pagamento cancelado. Pode tentar de novo quando quiser.
            </div>
          </div>
        )}
      </main>

      {/* Footer */}
      <footer className="border-t border-[#D4CFC9] px-4 py-8">
        <div className="max-w-5xl mx-auto flex flex-col sm:flex-row items-center justify-between gap-4 text-center sm:text-left">
          <span className="text-lg font-bold text-[#CC3300]">KLEAD</span>
          <p className="text-[#6B5E55] text-xs">
            Qualificação automática de leads via WhatsApp com IA.
          </p>
          <p className="text-[#6B5E55] text-xs">
            © {new Date().getFullYear()} KLEAD. Todos os direitos reservados.
          </p>
        </div>
      </footer>

      {/* Popup: dados pra checkout */}
      {showModal && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 px-4"
          onClick={() => !sending && setShowModal(false)}
        >
          <div
            className="w-full max-w-md bg-[#E8E4DF] rounded-2xl border border-[#D4CFC9] p-6 shadow-xl"
            onClick={e => e.stopPropagation()}
          >
            <form onSubmit={handleCheckout} className="space-y-4">
              <div className="flex items-start justify-between gap-4">
                <div>
                  <h2 className="text-[#1A1410] font-bold text-lg">Assinar KLEAD</h2>
                  <p className="text-[#6B5E55] text-xs mt-1">
                    Plano:{" "}
                    <span className="text-[#CC3300] font-semibold">
                      {selectedPlan === "lite"
                        ? (() => {
                            const v = LITE_VARIANTS.find(v => v.ciclo === ciclo);
                            return v ? `${v.nome} (${v.price})` : "Lite";
                          })()
                        : plans.find(p => p.id === selectedPlan)?.label}
                    </span>
                  </p>
                </div>
                <button
                  type="button"
                  onClick={() => setShowModal(false)}
                  className="text-[#6B5E55] hover:text-[#1A1410] text-xl leading-none"
                  aria-label="Fechar"
                >
                  ×
                </button>
              </div>

              <div>
                <label className="block text-xs font-semibold text-[#6B5E55] uppercase tracking-wide mb-1.5">Nome</label>
                <input
                  type="text" value={nome} onChange={e => setNome(e.target.value)} required autoFocus
                  placeholder="Seu nome"
                  className="w-full bg-[#F0EDE8] border border-[#D4CFC9] rounded-xl px-4 py-3 text-[#1A1410] text-sm placeholder-[#6B5E55] focus:outline-none focus:border-[#CC3300] transition-colors"
                />
              </div>
              <div>
                <label className="block text-xs font-semibold text-[#6B5E55] uppercase tracking-wide mb-1.5">Email</label>
                <input
                  type="email" value={email} onChange={e => setEmail(e.target.value)} required
                  placeholder="seu@email.com"
                  className="w-full bg-[#F0EDE8] border border-[#D4CFC9] rounded-xl px-4 py-3 text-[#1A1410] text-sm placeholder-[#6B5E55] focus:outline-none focus:border-[#CC3300] transition-colors"
                />
              </div>

              {error && (
                <div className="bg-red-900/30 border border-red-700/50 rounded-xl px-4 py-3 text-red-400 text-sm">
                  {error}
                </div>
              )}

              <button
                type="submit"
                disabled={sending}
                className="w-full bg-[#CC3300] hover:bg-[#991F00] disabled:opacity-50 text-white font-semibold py-3 rounded-xl transition-colors text-sm"
              >
                {sending ? "Redirecionando..." : "Ir para pagamento"}
              </button>

              <p className="text-[#6B5E55] text-[11px] text-center">
                Você será redirecionado pro checkout seguro do Stripe. Sua senha de acesso é enviada por
                email assim que o pagamento for confirmado.
              </p>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
