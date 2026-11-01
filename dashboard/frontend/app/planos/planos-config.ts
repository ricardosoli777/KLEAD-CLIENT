// Config central das páginas individuais de plano (/planos/[slug]).
// Preço mudou? Edita AQUI (e no PLAN_INFO/LITE_VARIANTS de app/plans/page.tsx).
// Cada entrada vira uma landing focada com checkout embutido e metadata própria
// (preview bonito ao colar o link no WhatsApp/Instagram).

export type PlanPage = {
  plano: "lite" | "user" | "pro";
  ciclo: "" | "mensal_avulso" | "semestral" | "anual";
  nome: string;
  price: string;
  per: string;
  note: string;
  badge?: string;
  headline: string;
  sub: string;
  metaDescription: string;
  cta: string;
  features: string[];
  faq: { q: string; a: string }[];
};

const LITE_FEATURES = [
  "1 instância de WhatsApp",
  "Inbox com sugestões de resposta por IA (co-piloto)",
  "CRM com Kanban acompanhando a conversa ao vivo",
  "Dashboard e análise de anúncios Meta",
  "Base de conhecimento pra IA responder com dados do SEU negócio",
];

const USER_FEATURES = [
  "1 instância de WhatsApp",
  "SDR de IA autônomo respondendo 24/7 no seu tom",
  "Pontuação de leads 0–100 com os seus critérios",
  "Disparo de campanhas pra base inteira",
  "Roleta de distribuição de leads entre vendedores",
  "Inbox + CRM Kanban + dashboard de Meta Ads",
];

const PRO_FEATURES = [
  "5 instâncias de WhatsApp",
  "Tudo do plano User em cada instância",
  "SDR de IA autônomo 24/7 + pontuação 0–100",
  "Disparo de campanhas + roleta de leads",
  "Supervisão de atendentes",
];

const FAQ_LITE_BASE = [
  {
    q: "O que a IA faz no plano Lite?",
    a: "No Lite a IA é seu co-piloto: sugere respostas prontas no Inbox pra você aprovar e enviar, e responde dúvidas com a base de conhecimento do seu negócio. Quem conduz a conversa é você. Pra IA atender sozinha 24/7, veja o plano User.",
  },
  {
    q: "Como recebo o acesso?",
    a: "Na hora. Assim que o pagamento é confirmado no checkout seguro do Stripe, sua senha chega por email e você conecta seu WhatsApp escaneando um QR Code — em minutos.",
  },
];

export const PLAN_PAGES: Record<string, PlanPage> = {
  "lite-mensal": {
    plano: "lite",
    ciclo: "",
    nome: "Lite Mensal",
    price: "R$ 29,88",
    per: "por mês",
    note: "Renova todo mês. Cancele quando quiser, sem multa.",
    headline: "IA de apoio no seu WhatsApp por menos de R$ 1 por dia.",
    sub: "Inbox inteligente, CRM Kanban e análise dos seus anúncios Meta — com a IA sugerindo a resposta certa pra cada lead.",
    metaDescription: "Plano Lite do KLEAD por R$ 29,88/mês: Inbox com sugestões de IA, CRM Kanban e dashboard de Meta Ads. Cancele quando quiser.",
    cta: "Assinar Lite Mensal",
    features: LITE_FEATURES,
    faq: [
      ...FAQ_LITE_BASE,
      { q: "Posso cancelar quando quiser?", a: "Sim. A assinatura renova todo mês, mas você cancela a qualquer momento, sem multa e sem burocracia." },
    ],
  },
  "lite-avulso": {
    plano: "lite",
    ciclo: "mensal_avulso",
    nome: "Lite Avulso",
    price: "R$ 39,88",
    per: "pagamento único",
    note: "30 dias de acesso. Sem assinatura, sem renovação automática.",
    badge: "Experimente por 1 mês",
    headline: "Teste o KLEAD por 30 dias. Sem assinatura, sem pegadinha.",
    sub: "Um pagamento único de R$ 39,88, um mês de acesso completo ao Lite — e nenhuma cobrança depois. Gostou? Aí você assina.",
    metaDescription: "Experimente o KLEAD por 30 dias com pagamento único de R$ 39,88. Sem assinatura e sem renovação automática.",
    cta: "Experimentar por 30 dias",
    features: LITE_FEATURES,
    faq: [
      { q: "Vou ser cobrado de novo depois dos 30 dias?", a: "Não. O Lite Avulso é pagamento único: acabaram os 30 dias, acabou a cobrança. Pra continuar, você escolhe assinar o mensal, semestral ou anual." },
      ...FAQ_LITE_BASE,
    ],
  },
  "lite-semestral": {
    plano: "lite",
    ciclo: "semestral",
    nome: "Lite Semestral",
    price: "R$ 149,88",
    per: "a cada 6 meses",
    note: "Sai por R$ 24,98/mês — 16% mais barato que o mensal.",
    badge: "-16%",
    headline: "6 meses de KLEAD Lite pagando menos por mês.",
    sub: "R$ 149,88 a cada 6 meses — o equivalente a R$ 24,98/mês. Mesmo Lite completo, com desconto por fidelidade.",
    metaDescription: "Plano Lite Semestral do KLEAD: R$ 149,88 a cada 6 meses (R$ 24,98/mês). Inbox com IA, CRM e dashboard de Meta Ads.",
    cta: "Assinar Lite Semestral",
    features: LITE_FEATURES,
    faq: [
      { q: "Como funciona a cobrança?", a: "Você paga R$ 149,88 agora e a cada 6 meses a assinatura renova automaticamente. Pode cancelar a renovação a qualquer momento." },
      ...FAQ_LITE_BASE,
    ],
  },
  "lite-anual": {
    plano: "lite",
    ciclo: "anual",
    nome: "Lite Anual",
    price: "R$ 249,88",
    per: "por ano",
    note: "Sai por R$ 20,82/mês — 30% mais barato que o mensal.",
    badge: "-30%",
    headline: "Um ano de KLEAD Lite pelo menor preço por mês.",
    sub: "R$ 249,88 por ano — o equivalente a R$ 20,82/mês. O melhor custo-benefício pra quem já decidiu organizar o atendimento.",
    metaDescription: "Plano Lite Anual do KLEAD: R$ 249,88/ano (R$ 20,82/mês). Inbox com IA, CRM Kanban e dashboard de Meta Ads.",
    cta: "Assinar Lite Anual",
    features: LITE_FEATURES,
    faq: [
      { q: "Como funciona a cobrança?", a: "Você paga R$ 249,88 agora e a assinatura renova uma vez por ano. Pode cancelar a renovação a qualquer momento." },
      ...FAQ_LITE_BASE,
    ],
  },
  "user": {
    plano: "user",
    ciclo: "",
    nome: "User",
    price: "R$ 69,88",
    per: "por mês",
    note: "Renova todo mês. Cancele quando quiser, sem multa.",
    badge: "IA autônoma",
    headline: "A IA que atende, qualifica e pontua seus leads — sozinha, 24h por dia.",
    sub: "Você dispara a campanha, a IA conversa com todo mundo na hora, dá nota de 0 a 100 e entrega só os leads quentes pro seu time fechar.",
    metaDescription: "Plano User do KLEAD por R$ 69,88/mês: SDR de IA autônomo 24/7, pontuação de leads 0-100, disparo de campanhas e CRM.",
    cta: "Assinar User",
    features: USER_FEATURES,
    faq: [
      { q: "A IA responde como robô?", a: "Não. Você escreve o prompt com o tom da sua empresa e as perguntas do seu negócio. A IA responde curto, natural, uma pergunta por vez — e você assume a conversa quando quiser." },
      { q: "Qual inteligência artificial é usada?", a: "Você escolhe: OpenAI, Anthropic ou Groq (com opção gratuita). A chave de API é sua — controle total de custo e modelo." },
      { q: "Como recebo o acesso?", a: "Na hora. Pagamento confirmado no Stripe, senha no seu email e WhatsApp conectado por QR Code em minutos." },
    ],
  },
  "pro": {
    plano: "pro",
    ciclo: "",
    nome: "Pro",
    price: "R$ 199,88",
    per: "por mês",
    note: "Renova todo mês. Cancele quando quiser, sem multa.",
    badge: "Mais popular",
    headline: "5 números de WhatsApp com IA atendendo em todos, ao mesmo tempo.",
    sub: "Pra times que rodam várias campanhas em paralelo: cada instância com SDR de IA 24/7, e a roleta distribuindo os leads quentes entre os vendedores.",
    metaDescription: "Plano Pro do KLEAD por R$ 199,88/mês: 5 instâncias de WhatsApp com IA autônoma, roleta de leads e supervisão de atendentes.",
    cta: "Assinar Pro",
    features: PRO_FEATURES,
    faq: [
      { q: "Posso usar números diferentes em cada instância?", a: "Sim. Cada instância é um WhatsApp independente, com prompt, campanhas e CRM próprios — perfeito pra separar operações ou marcas." },
      { q: "E se eu precisar de mais de 5 instâncias?", a: "Aí é o Enterprise, sob medida: 10, 20, 50 instâncias com condições negociadas. Chame a gente pelo WhatsApp na página de planos." },
      { q: "Como recebo o acesso?", a: "Na hora. Pagamento confirmado no Stripe, senha no seu email e WhatsApp conectado por QR Code em minutos." },
    ],
  },
};

export const PLAN_SLUGS = Object.keys(PLAN_PAGES);
