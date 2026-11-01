"use client";

import React, { useCallback, useEffect, useRef, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import PageHeaderShell from "@/components/PageHeaderShell";
import { getMetrics, MetricsData, getMetaAdsMetrics, MetaAdsMetrics, MetaAdsCampaign, getMetaAdsByAd, MetaAdsAd, MetaAdsAdMetrics, getMetaAdsComparison, WeeklyComparison, analyzeMetaAds, chatMetaAds, getMetaAdsAccountsTree, MetaAdsBusinessTree, getMetaAdsCampaigns, MetaAdsCampaignInfo, getMetaAdsExplorerMetrics, type AdsAnalysisResult, type ChatMessage } from "@/lib/api";

// Períodos aceitos pelo explorador (espelha _ADS_DATE_PRESETS do backend)
const DATE_PRESETS: { value: string; label: string }[] = [
  { value: "today",      label: "Hoje" },
  { value: "yesterday",  label: "Ontem" },
  { value: "last_7d",    label: "Últimos 7 dias" },
  { value: "last_14d",   label: "Últimos 14 dias" },
  { value: "last_30d",   label: "Últimos 30 dias" },
  { value: "last_90d",   label: "Últimos 90 dias" },
  { value: "this_month", label: "Este mês" },
  { value: "last_month", label: "Mês passado" },
  { value: "maximum",    label: "Máximo" },
];

function StatCard({ label, value, sub, color, accent }: { label: string; value: string | number; sub?: string; color?: string; accent?: string }) {
  return (
    <div className="relative bg-[#E8E4DF] rounded-xl border border-[#D4CFC9] p-4 flex flex-col gap-1 overflow-hidden transition-all hover:-translate-y-0.5 hover:border-[#3D4F5A]">
      <div
        className="absolute top-0 left-0 right-0 h-[2px]"
        style={{ background: `linear-gradient(90deg, ${accent ?? "#CC3300"} 0%, transparent 70%)` }}
      />
      <p className="text-[#6B5E55] text-[10px] font-semibold uppercase tracking-wider">{label}</p>
      <p className={`text-2xl font-extrabold tabular-nums leading-none ${color ?? "text-[#1A1410]"}`}>{value}</p>
      {sub && <p className="text-[#6B5E55] text-[10px]">{sub}</p>}
    </div>
  );
}

function SectionHeader({ title, subtitle, tone }: { title: string; subtitle: string; tone: string }) {
  return (
    <div className="flex items-center justify-between gap-4 mb-5">
      <div>
        <h2 className="text-[#1A1410] font-bold text-lg tracking-tight">{title}</h2>
        <p className="text-[#6B5E55] text-xs mt-0.5">{subtitle}</p>
      </div>
      <div className={`hidden sm:inline-flex items-center gap-2 rounded-full border px-3 py-1 text-[11px] font-semibold ${tone}`}>
        <span className="w-1.5 h-1.5 rounded-full bg-current" />
        Dados separados
      </div>
    </div>
  );
}

export default function DashboardPage() {
  const params = useParams();
  const instanceId = params.id as string;

  const [metrics, setMetrics] = useState<MetricsData | null>(null);
  const [adsMetrics, setAdsMetrics] = useState<MetaAdsMetrics | null>(null);
  const [adsError, setAdsError] = useState<string | null>(null);
  const [loading, setLoading]   = useState(true);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);

  // Análise IA
  const [analysis, setAnalysis]         = useState<AdsAnalysisResult | null>(null);
  const [analyzing, setAnalyzing]       = useState(false);
  const [analysisError, setAnalysisError] = useState<string | null>(null);
  const [selectedCampaign, setSelectedCampaign] = useState("");
  const [showAnalysis, setShowAnalysis] = useState(false);

  // Chat IA Meta Ads
  const [chatHistory, setChatHistory]   = useState<ChatMessage[]>([]);
  const [chatInput, setChatInput]       = useState("");
  const [chatLoading, setChatLoading]   = useState(false);
  const [showChat, setShowChat]         = useState(false);
  const chatEndRef                      = useRef<HTMLDivElement>(null);

  // Anúncios individuais
  const [adMetrics, setAdMetrics]       = useState<MetaAdsAdMetrics | null>(null);
  const [adsLoading, setAdsLoading]     = useState(false);
  const [showAds, setShowAds]           = useState(false);
  const [adsAdError, setAdsAdError]     = useState<string | null>(null);

  // Comparativo semanal
  const [comparison, setComparison]     = useState<WeeklyComparison | null>(null);

  const [adsRefreshing, setAdsRefreshing] = useState(false);

  // Explorador Meta Ads — navegar por qualquer BM / conta de anúncio / campanha / período.
  // selectedAccount vazio = conta padrão configurada na instância (comportamento original).
  const [accountsTree, setAccountsTree]         = useState<MetaAdsBusinessTree[] | null>(null);
  const [selectedBusiness, setSelectedBusiness] = useState("");
  const [selectedAccount, setSelectedAccount]   = useState("");
  const [datePreset, setDatePreset]             = useState("last_30d");
  const [campaignOptions, setCampaignOptions]   = useState<MetaAdsCampaignInfo[] | null>(null);

  // Métricas internas (do próprio banco do Klead) — leves, sem custo de API externa,
  // seguras para atualizar automaticamente em intervalo curto.
  const loadInstanceMetrics = useCallback(async () => {
    try {
      const data = await getMetrics(instanceId);
      setMetrics(data);
    } catch {
      setMetrics(null);
    }
  }, [instanceId]);

  // Métricas de anúncios (Graph API do Meta) — cada chamada bate na API externa
  // de verdade. Não roda em intervalo automático para não estourar o rate limit
  // do Meta; só busca ao entrar na página, ao clicar em "Atualizar" e quando o
  // explorador muda de conta/período.
  const loadAdsMetrics = useCallback(async () => {
    setAdsRefreshing(true);
    try {
      // Vista padrão (conta da instância + 30 dias) mantém o endpoint original,
      // que também grava o snapshot diário do histórico.
      const ads = (!selectedAccount && datePreset === "last_30d")
        ? await getMetaAdsMetrics(instanceId)
        : await getMetaAdsExplorerMetrics(instanceId, { ad_account: selectedAccount, date_preset: datePreset });
      setAdsMetrics(ads);
      setAdsError(null);
      setLastUpdated(new Date());
      // Comparativo semanal vem dos snapshots da conta padrão — não se aplica a outra conta
      if (!selectedAccount) {
        getMetaAdsComparison(instanceId).then(setComparison).catch(() => null);
      } else {
        setComparison(null);
      }
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e);
      if (!msg.includes("obrigatórios")) setAdsError(msg);
    } finally {
      setLoading(false);
      setAdsRefreshing(false);
    }
  }, [instanceId, selectedAccount, datePreset]);

  useEffect(() => {
    loadInstanceMetrics();
    loadAdsMetrics();
    const interval = setInterval(loadInstanceMetrics, 15000);
    return () => clearInterval(interval);
  }, [loadInstanceMetrics, loadAdsMetrics]);

  // Árvore BM → contas do usuário conectado via OAuth. Sem conexão, o explorador
  // fica oculto e a tela segue com a conta fixa da instância (sem regressão).
  useEffect(() => {
    getMetaAdsAccountsTree(instanceId)
      .then(r => setAccountsTree(r.businesses ?? []))
      .catch(() => setAccountsTree(null));
  }, [instanceId]);

  // Todas as campanhas da conta selecionada (mesmo sem entrega no período)
  useEffect(() => {
    setSelectedCampaign("");
    getMetaAdsCampaigns(instanceId, selectedAccount)
      .then(r => setCampaignOptions(r.campaigns ?? []))
      .catch(() => setCampaignOptions(null));
  }, [instanceId, selectedAccount]);

  // Criativos carregados sob demanda pertencem à conta/período anteriores — invalida
  useEffect(() => {
    setAdMetrics(null);
    setShowAds(false);
  }, [selectedAccount, datePreset]);

  const m = metrics;
  const displayedCampaigns = adsMetrics
    ? (selectedCampaign
        ? adsMetrics.campaigns.filter((c: MetaAdsCampaign) => c.campaign === selectedCampaign)
        : adsMetrics.campaigns)
    : [];
  const adsSummary = displayedCampaigns.length > 0
    ? displayedCampaigns.reduce(
        (acc, c) => {
          acc.impressions += c.impressions;
          acc.reach += c.reach ?? 0;
          acc.clicks += c.clicks;
          acc.spend += c.spend;
          acc.leads += c.leads;
          return acc;
        },
        { impressions: 0, reach: 0, clicks: 0, spend: 0, leads: 0 }
      )
    : null;
  const displayCpl = adsSummary && adsSummary.leads > 0 ? adsSummary.spend / adsSummary.leads : null;
  const adsCtr  = adsSummary && adsSummary.impressions > 0 ? (adsSummary.clicks / adsSummary.impressions) * 100 : null;
  const adsCpc  = adsSummary && adsSummary.clicks > 0 ? adsSummary.spend / adsSummary.clicks : null;
  const adsCpm  = adsSummary && adsSummary.impressions > 0 ? (adsSummary.spend / adsSummary.impressions) * 1000 : null;
  const adsFreq = adsSummary && adsSummary.reach > 0 ? adsSummary.impressions / adsSummary.reach : null;

  // Grupos de contas do seletor (todas as BMs ou só a escolhida)
  const accountGroups = (accountsTree ?? []).filter(b => !selectedBusiness || b.id === selectedBusiness);
  // Campanhas do seletor: lista completa da conta (inclui sem entrega no período);
  // fallback para as campanhas presentes nas métricas quando a listagem falhar
  const campaignSelectOptions = campaignOptions && campaignOptions.length > 0
    ? campaignOptions.map(c => ({
        value: c.name,
        label: c.effective_status === "PAUSED" ? `${c.name} · pausada`
          : c.effective_status && c.effective_status !== "ACTIVE" ? `${c.name} · ${c.effective_status.toLowerCase()}`
          : c.name,
      }))
    : (adsMetrics?.campaigns ?? []).map(c => ({ value: c.campaign, label: c.campaign }));

  const handleBusinessChange = (bizId: string) => {
    setSelectedBusiness(bizId);
    if (bizId) {
      const biz = (accountsTree ?? []).find(b => b.id === bizId);
      if (selectedAccount && !biz?.ad_accounts.some(a => a.id === selectedAccount)) setSelectedAccount("");
    }
  };

  const explorerOpts = { ad_account: selectedAccount, date_preset: datePreset };
  const datePresetLabel = DATE_PRESETS.find(p => p.value === datePreset)?.label ?? "Últimos 30 dias";

  const handleAnalyze = async (campaign = "") => {
    setAnalyzing(true);
    setAnalysisError(null);
    setShowAnalysis(true);
    setSelectedCampaign(campaign);
    try {
      const result = await analyzeMetaAds(instanceId, campaign, explorerOpts);
      setAnalysis(result);
    } catch (e: unknown) {
      setAnalysisError(e instanceof Error ? e.message : "Erro ao analisar");
    } finally {
      setAnalyzing(false);
    }
  };

  const handleChat = async () => {
    const text = chatInput.trim();
    if (!text || chatLoading || !adsMetrics) return;
    const userMsg: ChatMessage = { role: "user", content: text };
    const newHistory = [...chatHistory, userMsg];
    setChatHistory(newHistory);
    setChatInput("");
    setChatLoading(true);
    setTimeout(() => chatEndRef.current?.scrollIntoView({ behavior: "smooth" }), 50);
    try {
      const { reply } = await chatMetaAds(instanceId, newHistory.slice(-20), selectedCampaign, explorerOpts);
      setChatHistory((prev: ChatMessage[]) => [...prev, { role: "assistant", content: reply }]);
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : "Erro ao consultar IA";
      setChatHistory((prev: ChatMessage[]) => [...prev, { role: "assistant", content: `⚠️ ${msg}` }]);
    } finally {
      setChatLoading(false);
      setTimeout(() => chatEndRef.current?.scrollIntoView({ behavior: "smooth" }), 50);
    }
  };

  return (
    <div className="min-h-screen bg-[#F0EDE8] flex flex-col">
      <PageHeaderShell maxWidthClassName="max-w-full" className="pt-4">
        <div className="flex items-center justify-between gap-2 flex-wrap">
          <div className="flex items-center gap-3 min-w-0">
            <div className="w-9 h-9 rounded-xl flex items-center justify-center font-bold text-[#F0EDE8] text-sm flex-shrink-0"
              style={{ background: "linear-gradient(135deg, #CC3300, #991F00)" }}>
              {instanceId.charAt(0).toUpperCase()}
            </div>
            <div className="min-w-0">
              <h1 className="text-[#1A1410] font-bold text-[15px] leading-tight truncate">Dashboard SDR</h1>
              <p className="text-[#6B5E55] text-[11px] truncate">{instanceId}</p>
            </div>
          </div>
          <div className="flex items-center gap-2 flex-shrink-0">
            {lastUpdated && (
              <span className="text-[#6B5E55] text-xs hidden sm:block">
                Atualizado {lastUpdated.toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit", second: "2-digit" })}
              </span>
            )}
            <button
              onClick={loadAdsMetrics}
              disabled={adsRefreshing}
              title="Buscar campanhas atualizadas agora na Meta"
              className="flex items-center gap-1.5 bg-[#E8E4DF] border border-[#D4CFC9] hover:border-[#CC3300] disabled:opacity-50 text-[#6B5E55] hover:text-[#CC3300] text-xs font-semibold px-3 py-1.5 rounded-xl transition-colors whitespace-nowrap"
            >
              <svg className={`w-3.5 h-3.5 ${adsRefreshing ? "animate-spin" : ""}`} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M21 12a9 9 0 1 1-2.64-6.36" strokeLinecap="round" />
                <path d="M21 3v6h-6" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
              {adsRefreshing ? "Atualizando..." : "Atualizar dados"}
            </button>
            {m && m.total > 0 && (
              <div className="flex items-center gap-1.5 bg-[#0A2B1A] border border-[#CC3300]/30 rounded-full px-2.5 py-1">
                <span className="w-1.5 h-1.5 rounded-full bg-[#CC3300] animate-pulse" />
                <span className="text-[#CC3300] text-xs font-semibold">Ao vivo</span>
              </div>
            )}
            <Link
              href={`/instances/${instanceId}/inbox`}
              className="bg-[#E8E4DF] border border-[#D4CFC9] hover:border-[#3B82F6] text-[#6B5E55] hover:text-[#3B82F6] text-xs font-semibold px-3 py-1.5 rounded-xl transition-colors whitespace-nowrap"
            >
              Abrir Inbox
            </Link>
          </div>
        </div>
      </PageHeaderShell>

      <div className="flex-1 px-4 sm:px-6 py-7 max-w-6xl mx-auto w-full">
        {loading ? (
          <div className="flex items-center justify-center py-24">
            <svg className="w-8 h-8 text-[#CC3300] animate-spin" viewBox="0 0 24 24" fill="none">
              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"/>
              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z"/>
            </svg>
          </div>
        ) : (
          <div className="space-y-5">
            <div className="flex flex-col gap-2 md:flex-row md:items-end md:justify-between mb-1">
              <div>
                <h2 className="text-[#1A1410] font-bold text-lg tracking-tight">Dashboard separado por origem</h2>
                <p className="text-[#6B5E55] text-xs mt-0.5">Meta Ads e CRM aparecem em blocos independentes</p>
              </div>
              <div className="flex flex-wrap gap-2">
                {adsMetrics && (
                  <span className="bg-[#4267B2] border border-[#4267B2] text-white text-[11px] font-semibold px-3 py-1 rounded-full">
                    Meta Ads
                  </span>
                )}
                {m && (
                  <span className="bg-[#0A2B1A] border border-[#CC3300]/25 text-[#CC3300] text-[11px] font-semibold px-3 py-1 rounded-full">
                    CRM
                  </span>
                )}
              </div>
            </div>

            <div className="bg-[#E8E4DF] rounded-2xl border border-[#D4CFC9] p-6">
              <div className="flex flex-col gap-3 mb-4 sm:flex-row sm:items-start sm:justify-between">
                <SectionHeader
                  title="Meta Ads"
                  subtitle="Resultados de mídia pagos vindos da conta de anúncios"
                  tone="bg-[#4267B2] border-[#4267B2] text-white"
                />
                {(adsMetrics || (accountsTree && accountsTree.length > 0)) && (
                  <div className="flex items-center gap-2 flex-shrink-0 flex-wrap">
                    {/* Explorador: BM → conta de anúncio (visível só com conexão Meta OAuth) */}
                    {accountsTree && accountsTree.length > 0 && (
                      <>
                        <select
                          value={selectedBusiness}
                          onChange={(e: React.ChangeEvent<HTMLSelectElement>) => handleBusinessChange(e.target.value)}
                          title="Business Manager"
                          className="min-w-0 max-w-[180px] bg-[#F0EDE8] border border-[#D4CFC9] rounded-xl px-2 py-1.5 text-[#6B5E55] text-xs outline-none focus:border-[#4267B2]"
                        >
                          <option value="">Todas as BMs</option>
                          {accountsTree.map(b => (
                            <option key={b.id} value={b.id}>{b.name}</option>
                          ))}
                        </select>
                        <select
                          value={selectedAccount}
                          onChange={(e: React.ChangeEvent<HTMLSelectElement>) => setSelectedAccount(e.target.value)}
                          title="Conta de anúncios"
                          className="min-w-0 max-w-[200px] bg-[#F0EDE8] border border-[#D4CFC9] rounded-xl px-2 py-1.5 text-[#6B5E55] text-xs outline-none focus:border-[#4267B2]"
                        >
                          <option value="">Conta padrão da instância</option>
                          {accountGroups.map(b => (
                            <optgroup key={b.id} label={b.name}>
                              {b.ad_accounts.map(a => (
                                <option key={a.id} value={a.id}>{a.name || a.account_id || a.id}</option>
                              ))}
                            </optgroup>
                          ))}
                        </select>
                      </>
                    )}
                    <select
                      value={datePreset}
                      onChange={(e: React.ChangeEvent<HTMLSelectElement>) => setDatePreset(e.target.value)}
                      title="Período"
                      className="min-w-0 bg-[#F0EDE8] border border-[#D4CFC9] rounded-xl px-2 py-1.5 text-[#6B5E55] text-xs outline-none focus:border-[#4267B2]"
                    >
                      {DATE_PRESETS.map(p => (
                        <option key={p.value} value={p.value}>{p.label}</option>
                      ))}
                    </select>
                    {campaignSelectOptions.length > 0 && (
                      <select
                        value={selectedCampaign}
                        onChange={(e: React.ChangeEvent<HTMLSelectElement>) => setSelectedCampaign(e.target.value)}
                        title="Campanha"
                        className="min-w-0 flex-1 sm:flex-none max-w-[220px] bg-[#F0EDE8] border border-[#D4CFC9] rounded-xl px-2 py-1.5 text-[#6B5E55] text-xs outline-none focus:border-[#4267B2]"
                      >
                        <option value="">Todas as campanhas</option>
                        {campaignSelectOptions.map((c, i) => (
                          <option key={i} value={c.value}>{c.label}</option>
                        ))}
                      </select>
                    )}
                    <button
                      onClick={() => handleAnalyze(selectedCampaign)}
                      disabled={analyzing}
                      className="flex items-center gap-1.5 bg-[#4267B2] hover:bg-[#3b5ea0] border border-[#4267B2] text-white font-semibold px-3 py-1.5 rounded-xl text-xs transition-colors disabled:opacity-50 whitespace-nowrap"
                    >
                      {analyzing ? (
                        <><span className="w-3 h-3 border-2 border-[#9BB5E9]/30 border-t-[#9BB5E9] rounded-full animate-spin" />Analisando...</>
                      ) : (
                        <>✦ Analisar com IA</>
                      )}
                    </button>
                  </div>
                )}
              </div>

              {adsError ? (
                <p className="text-[#F85149] text-sm">{adsError}</p>
              ) : adsMetrics ? (
                <>
                  {/* Resumo geral — mesma ordem do Gerenciador de Anúncios */}
                  <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-2 mb-4">
                    {/* Resultado (leads) — métrica principal, destaque */}
                    <div className="relative bg-[#0D1F16] rounded-xl border border-[#CC3300]/30 px-4 py-3 flex flex-col gap-1 overflow-hidden lg:col-span-1">
                      <div className="absolute top-0 left-0 right-0 h-[2px] bg-gradient-to-r from-[#CC3300] to-transparent" />
                      <p className="text-[#6B5E55] text-[10px] font-semibold uppercase tracking-wider">Resultado</p>
                      <p className="text-2xl font-extrabold tabular-nums text-[#CC3300]">{adsSummary?.leads ?? 0}</p>
                      <p className="text-[#6B5E55] text-[10px]">leads · {datePresetLabel.toLowerCase()}</p>
                    </div>
                    {/* Custo por resultado */}
                    <div className="relative bg-[#E8E4DF] rounded-xl border border-[#D4CFC9] px-4 py-3 flex flex-col gap-1 overflow-hidden">
                      <div className="absolute top-0 left-0 right-0 h-[2px] bg-gradient-to-r from-[#E3B341] to-transparent" />
                      <p className="text-[#6B5E55] text-[10px] font-semibold uppercase tracking-wider">Custo/resultado</p>
                      <p className={`text-2xl font-extrabold tabular-nums ${displayCpl !== null && displayCpl < 30 ? "text-[#CC3300]" : "text-[#E3B341]"}`}>
                        {displayCpl !== null ? `R$ ${displayCpl.toFixed(2)}` : "—"}
                      </p>
                      <p className="text-[#6B5E55] text-[10px]">CPL médio</p>
                    </div>
                    {/* Valor usado */}
                    <StatCard label="Valor usado" value={`R$ ${(adsSummary?.spend ?? 0).toLocaleString("pt-BR", { minimumFractionDigits: 2 })}`} accent="#6B5E55" />
                    {/* Alcance */}
                    <StatCard label="Alcance" value={(adsSummary?.reach ?? 0).toLocaleString("pt-BR")} accent="#A78BFA" />
                    {/* Impressões */}
                    <StatCard label="Impressões" value={(adsSummary?.impressions ?? 0).toLocaleString("pt-BR")} accent="#6B5E55" />
                    {/* Frequência com alerta visual */}
                    <div className="relative bg-[#E8E4DF] rounded-xl border border-[#D4CFC9] px-4 py-3 flex flex-col gap-1 overflow-hidden">
                      <div className={`absolute top-0 left-0 right-0 h-[2px] ${adsFreq !== null && adsFreq > 3 ? "bg-gradient-to-r from-[#F85149] to-transparent" : "bg-gradient-to-r from-[#F0B429] to-transparent"}`} />
                      <p className="text-[#6B5E55] text-[10px] font-semibold uppercase tracking-wider">Frequência</p>
                      <p className={`text-2xl font-extrabold tabular-nums ${adsFreq !== null && adsFreq > 3 ? "text-[#F85149]" : "text-[#F0B429]"}`}>
                        {adsFreq !== null ? adsFreq.toFixed(2) : "—"}
                      </p>
                      <p className={`text-[10px] ${adsFreq !== null && adsFreq > 3 ? "text-[#F85149]" : "text-[#6B5E55]"}`}>
                        {adsFreq !== null && adsFreq > 3 ? "⚠ audiência saturada" : "imp. / alcance"}
                      </p>
                    </div>
                  </div>

                  {/* Métricas de eficiência */}
                  <div className="grid grid-cols-2 md:grid-cols-4 gap-2 mb-4">
                    <StatCard
                      label="CTR (taxa de clique)"
                      value={adsCtr !== null ? `${adsCtr.toFixed(2)}%` : "—"}
                      sub={adsCtr !== null ? (adsCtr >= 1 ? "✓ acima da média" : "abaixo da média (< 1%)") : undefined}
                      color={adsCtr !== null && adsCtr >= 1 ? "text-[#CC3300]" : "text-[#E3B341]"}
                      accent="#3B82F6"
                    />
                    <StatCard
                      label="CPC (custo/clique)"
                      value={adsCpc !== null ? `R$ ${adsCpc.toFixed(2)}` : "—"}
                      accent="#CC3300"
                    />
                    <StatCard
                      label="CPM (custo/mil imp.)"
                      value={adsCpm !== null ? `R$ ${adsCpm.toFixed(2)}` : "—"}
                      accent="#E3B341"
                    />
                    <StatCard
                      label="Cliques no link"
                      value={(adsSummary?.clicks ?? 0).toLocaleString("pt-BR")}
                      accent="#3B82F6"
                    />
                  </div>

                  {/* Comparativo semanal */}
                  {comparison && comparison.this_week && Object.keys(comparison.this_week).length > 0 && (() => {
                    const d = comparison.delta_pct;
                    const tw = comparison.this_week;
                    const lw = comparison.last_week;
                    const delta = (val: number | null, inverse = false) => {
                      if (val === null || val === undefined) return <span className="text-[#6B5E55] text-[10px]">—</span>;
                      const better = inverse ? val < 0 : val > 0;
                      const color  = better ? "text-green-600" : "text-red-500";
                      const arrow  = val > 0 ? "↑" : "↓";
                      return <span className={`text-[10px] font-semibold ${color}`}>{arrow} {Math.abs(val).toFixed(1)}%</span>;
                    };
                    return (
                      <div className="mb-4 rounded-xl border border-[#4267B2]/20 bg-[#F0EDE8] overflow-hidden">
                        <div className="px-4 py-2.5 border-b border-[#4267B2]/10 flex items-center justify-between">
                          <span className="text-[#4267B2] text-xs font-bold">📊 Últimos 7 dias vs. 7 dias anteriores</span>
                          <span className="text-[#6B5E55] text-[10px]">incremento no período (delta entre snapshots)</span>
                        </div>
                        <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-6 divide-x divide-[#D4CFC9]">
                          {[
                            { label: "Leads",    tw: tw.leads,   lw: lw?.leads,   fmt: (v: number) => String(v),                    dv: d.leads,   inv: false },
                            { label: "CPL",      tw: tw.cpl,     lw: lw?.cpl,     fmt: (v: number) => `R$ ${v.toFixed(2)}`,         dv: d.cpl,     inv: true  },
                            { label: "Spend",    tw: tw.spend,   lw: lw?.spend,   fmt: (v: number) => `R$ ${v.toFixed(0)}`,         dv: d.spend,   inv: true  },
                            { label: "CTR",      tw: tw.ctr,     lw: lw?.ctr,     fmt: (v: number) => `${v.toFixed(2)}%`,           dv: d.ctr,     inv: false },
                            { label: "CPM",      tw: tw.cpm,     lw: lw?.cpm,     fmt: (v: number) => `R$ ${v.toFixed(2)}`,         dv: d.cpm,     inv: true  },
                            { label: "Cliques",  tw: tw.clicks,  lw: lw?.clicks,  fmt: (v: number) => v.toLocaleString("pt-BR"),    dv: d.clicks,  inv: false },
                          ].map(({ label, tw: twv, lw: lwv, fmt, dv, inv }) => (
                            <div key={label} className="px-3 py-2.5 flex flex-col gap-0.5">
                              <p className="text-[#6B5E55] text-[10px] font-semibold uppercase tracking-wider">{label}</p>
                              <p className="text-[#1A1410] text-sm font-bold tabular-nums">{twv != null ? fmt(twv as number) : "—"}</p>
                              <div className="flex items-center gap-1.5">
                                {delta(dv, inv)}
                                {lwv != null && <span className="text-[#6B5E55] text-[10px]">({fmt(lwv as number)})</span>}
                              </div>
                            </div>
                          ))}
                        </div>
                      </div>
                    );
                  })()}

                  {/* Tabela de campanhas — padrão Gerenciador de Anúncios */}
                  {adsMetrics.campaigns.length > 0 && (
                    <div className="overflow-x-auto rounded-xl border border-[#D4CFC9]">
                      <table className="w-full text-[11px] text-left">
                        <thead>
                          <tr className="bg-[#E8E4DF] border-b border-[#D4CFC9] text-[#6B5E55]">
                            <th className="py-2.5 pl-3 pr-3 font-semibold">Nome da campanha</th>
                            <th className="py-2.5 pr-3 font-semibold text-center">Status</th>
                            <th className="py-2.5 pr-3 font-semibold text-right">Resultado<br/><span className="font-normal text-[10px]">leads</span></th>
                            <th className="py-2.5 pr-3 font-semibold text-right">Custo/resultado<br/><span className="font-normal text-[10px]">CPL</span></th>
                            <th className="py-2.5 pr-3 font-semibold text-right">Valor usado<br/><span className="font-normal text-[10px]">R$</span></th>
                            <th className="py-2.5 pr-3 font-semibold text-right">Orç. diário<br/><span className="font-normal text-[10px]">R$</span></th>
                            <th className="py-2.5 pr-3 font-semibold text-right">Alcance</th>
                            <th className="py-2.5 pr-3 font-semibold text-right">Impressões</th>
                            <th className="py-2.5 pr-3 font-semibold text-right">Freq.</th>
                            <th className="py-2.5 pr-3 font-semibold text-right">CTR</th>
                            <th className="py-2.5 pr-3 font-semibold text-right">CPC</th>
                            <th className="py-2.5 pr-3 font-semibold text-right">CPM</th>
                            <th className="py-2.5 pr-3 font-semibold text-right">Qualidade</th>
                          </tr>
                        </thead>
                        <tbody>
                          {displayedCampaigns.map((c, i) => {
                            const ctr = c.impressions > 0 ? (c.clicks / c.impressions) * 100 : null;
                            const cpc = c.clicks > 0 ? c.spend / c.clicks : null;
                            const freqAlert = c.frequency > 3;

                            const qualityColor = c.quality_ranking === "ABOVE_AVERAGE" ? "text-[#CC3300]"
                              : c.quality_ranking === "AVERAGE" ? "text-[#6B5E55]"
                              : c.quality_ranking && c.quality_ranking.startsWith("BELOW") ? "text-[#F85149]"
                              : "text-[#6B5E55]";
                            const qualityBg = c.quality_ranking === "ABOVE_AVERAGE" ? "bg-[#3D0A00] border-[#CC3300]/30"
                              : c.quality_ranking && c.quality_ranking.startsWith("BELOW") ? "bg-[#2D0A0A] border-[#F85149]/30"
                              : "bg-[#E8E4DF] border-transparent";
                            const qualityLabel = c.quality_ranking === "ABOVE_AVERAGE" ? "Acima da média"
                              : c.quality_ranking === "AVERAGE" ? "Média"
                              : c.quality_ranking === "BELOW_AVERAGE_10_PERCENT" ? "Abaixo 10%"
                              : c.quality_ranking === "BELOW_AVERAGE_20_PERCENT" ? "Abaixo 20%"
                              : c.quality_ranking === "BELOW_AVERAGE_35_PERCENT" ? "Abaixo 35%"
                              : "—";

                            return (
                              <tr key={i} className="border-b border-[#D4CFC9]/50 hover:bg-[#D4CFC9]/40 transition-colors group">
                                {/* Nome */}
                                <td className="py-2.5 pl-3 pr-3">
                                  <div className="flex items-center gap-2 max-w-[200px]">
                                    <span className="text-[#1A1410] font-medium truncate" title={c.campaign}>{c.campaign}</span>
                                  </div>
                                </td>
                                {/* Status */}
                                <td className="py-2.5 pr-3 text-center">
                                  {c.status === "PAUSED" ? (
                                    <span className="inline-flex items-center gap-1 text-[#6B5E55] text-[10px] font-semibold">
                                      <span className="w-1.5 h-1.5 rounded-full bg-[#6B5E55] inline-block" />
                                      Pausada
                                    </span>
                                  ) : (
                                    <span className="inline-flex items-center gap-1 text-[#CC3300] text-[10px] font-semibold">
                                      <span className="w-1.5 h-1.5 rounded-full bg-[#CC3300] animate-pulse inline-block" />
                                      Ativa
                                    </span>
                                  )}
                                </td>
                                {/* Resultado — destaque verde */}
                                <td className="py-2.5 pr-3 text-right">
                                  <span className="text-[#CC3300] font-bold text-[13px]">{c.leads}</span>
                                </td>
                                {/* CPL — alerta se alto */}
                                <td className="py-2.5 pr-3 text-right tabular-nums">
                                  <span className={c.cpl !== null && c.cpl > 50 ? "text-[#F85149] font-semibold" : "text-[#E3B341]"}>
                                    {c.cpl !== null ? `R$ ${c.cpl.toFixed(2)}` : "—"}
                                  </span>
                                </td>
                                {/* Valor usado */}
                                <td className="py-2.5 pr-3 text-right tabular-nums text-[#1A1410] font-semibold">
                                  R$ {c.spend.toLocaleString("pt-BR", { minimumFractionDigits: 2 })}
                                </td>
                                {/* Orçamento diário */}
                                <td className="py-2.5 pr-3 text-right tabular-nums text-[#6B5E55]">
                                  {c.daily_budget !== null ? `R$ ${c.daily_budget.toLocaleString("pt-BR", { minimumFractionDigits: 2 })}` : "—"}
                                </td>
                                {/* Alcance */}
                                <td className="py-2.5 pr-3 text-right tabular-nums text-[#A78BFA]">
                                  {(c.reach ?? 0).toLocaleString("pt-BR")}
                                </td>
                                {/* Impressões */}
                                <td className="py-2.5 pr-3 text-right tabular-nums text-[#6B5E55]">
                                  {c.impressions.toLocaleString("pt-BR")}
                                </td>
                                {/* Frequência — vermelho se > 3 */}
                                <td className={`py-2.5 pr-3 text-right tabular-nums font-semibold ${freqAlert ? "text-[#F85149]" : "text-[#F0B429]"}`}>
                                  {c.frequency > 0 ? c.frequency.toFixed(2) : "—"}
                                  {freqAlert && <span className="ml-0.5 text-[10px]">⚠</span>}
                                </td>
                                {/* CTR */}
                                <td className={`py-2.5 pr-3 text-right tabular-nums ${ctr !== null && ctr >= 1 ? "text-[#CC3300]" : "text-[#6B5E55]"}`}>
                                  {ctr !== null ? `${ctr.toFixed(2)}%` : "—"}
                                </td>
                                {/* CPC */}
                                <td className="py-2.5 pr-3 text-right tabular-nums text-[#6B5E55]">
                                  {cpc !== null ? `R$ ${cpc.toFixed(2)}` : "—"}
                                </td>
                                {/* CPM */}
                                <td className="py-2.5 pr-3 text-right tabular-nums text-[#6B5E55]">
                                  {c.cpm > 0 ? `R$ ${c.cpm.toFixed(2)}` : "—"}
                                </td>
                                {/* Qualidade */}
                                <td className="py-2.5 pr-3 text-right">
                                  <span className={`inline-block px-2 py-0.5 rounded-full text-[10px] font-semibold border ${qualityBg} ${qualityColor}`}>
                                    {qualityLabel}
                                  </span>
                                </td>
                              </tr>
                            );
                          })}
                        </tbody>
                        {/* Totais — linha de rodapé como no Facebook */}
                        <tfoot>
                          <tr className="bg-[#E8E4DF] border-t border-[#D4CFC9] text-[#6B5E55] font-semibold">
                            <td className="py-2.5 pl-3 pr-3 text-[#1A1410]">Total</td>
                            <td className="py-2.5 pr-3" />
                            <td className="py-2.5 pr-3 text-right tabular-nums text-[#CC3300] font-bold">{adsSummary?.leads ?? 0}</td>
                            <td className="py-2.5 pr-3 text-right tabular-nums text-[#E3B341]">
                              {displayCpl !== null ? `R$ ${displayCpl.toFixed(2)}` : "—"}
                            </td>
                            <td className="py-2.5 pr-3 text-right tabular-nums text-[#1A1410] font-bold">
                              R$ {(adsSummary?.spend ?? 0).toLocaleString("pt-BR", { minimumFractionDigits: 2 })}
                            </td>
                            <td className="py-2.5 pr-3" />
                            <td className="py-2.5 pr-3 text-right tabular-nums text-[#A78BFA]">
                              {(adsSummary?.reach ?? 0).toLocaleString("pt-BR")}
                            </td>
                            <td className="py-2.5 pr-3 text-right tabular-nums">
                              {(adsSummary?.impressions ?? 0).toLocaleString("pt-BR")}
                            </td>
                            <td className={`py-2.5 pr-3 text-right tabular-nums ${adsFreq !== null && adsFreq > 3 ? "text-[#F85149]" : "text-[#F0B429]"}`}>
                              {adsFreq !== null ? adsFreq.toFixed(2) : "—"}
                            </td>
                            <td className="py-2.5 pr-3 text-right tabular-nums">
                              {adsCtr !== null ? `${adsCtr.toFixed(2)}%` : "—"}
                            </td>
                            <td className="py-2.5 pr-3 text-right tabular-nums">
                              {adsCpc !== null ? `R$ ${adsCpc.toFixed(2)}` : "—"}
                            </td>
                            <td className="py-2.5 pr-3 text-right tabular-nums">
                              {adsCpm !== null ? `R$ ${adsCpm.toFixed(2)}` : "—"}
                            </td>
                            <td className="py-2.5 pr-3" />
                          </tr>
                        </tfoot>
                      </table>
                    </div>
                  )}
                </>
              ) : (
                <p className="text-[#6B5E55] text-sm">Nenhum dado de Meta Ads disponível para este período.</p>
              )}

              {/* Painel de análise IA */}
              {showAnalysis && (
                <div className="mt-4 rounded-2xl border border-[#4267B2]/30 bg-[#F0EDE8] overflow-hidden">
                  <div className="flex items-center justify-between px-4 py-3 border-b border-[#4267B2]/20">
                    <div className="flex items-center gap-2">
                      <span className="text-[#9BB5E9] font-bold text-sm">✦ Análise de IA</span>
                      {analysis?._meta && (
                        <span className="text-[#6B5E55] text-[10px]">
                          {analysis._meta.model} · {analysis._meta.campaigns_analyzed} campanha{analysis._meta.campaigns_analyzed !== 1 ? "s" : ""}
                          {analysis._meta.campaign_filter ? ` · filtro: "${analysis._meta.campaign_filter}"` : ""}
                        </span>
                      )}
                    </div>
                    <button onClick={() => { setShowAnalysis(false); setAnalysis(null); }} className="text-[#6B5E55] hover:text-[#F85149] text-sm transition-colors">✕</button>
                  </div>

                  {analyzing && (
                    <div className="px-4 py-8 text-center">
                      <div className="w-6 h-6 border-2 border-[#9BB5E9]/30 border-t-[#9BB5E9] rounded-full animate-spin mx-auto mb-3" />
                      <p className="text-[#6B5E55] text-sm">Analisando métricas com IA...</p>
                    </div>
                  )}

                  {analysisError && (
                    <div className="px-4 py-4">
                      <p className="text-[#F85149] text-sm">{analysisError}</p>
                    </div>
                  )}

                  {analysis && !analyzing && (() => {
                    const STATUS_COLOR = { bom: "text-[#CC3300]", atencao: "text-[#F0B429]", critico: "text-[#F85149]" };
                    const STATUS_BG = { bom: "bg-[#3D0A00] border-[#CC3300]/20", atencao: "bg-[#211900] border-[#F0B429]/20", critico: "bg-[#2D0A0A] border-[#F85149]/20" };
                    const STATUS_ICON = { bom: "🟢", atencao: "🟡", critico: "🔴" };
                    const NIVEL_COLOR = { ok: "text-[#CC3300]", atencao: "text-[#F0B429]", critico: "text-[#F85149]" };

                    return (
                      <div className="p-4 space-y-4">
                        {/* Resumo geral */}
                        <div className={`rounded-xl border px-4 py-3 ${STATUS_BG[analysis.status_geral] ?? STATUS_BG.atencao}`}>
                          <div className="flex items-center gap-2 mb-1">
                            <span>{STATUS_ICON[analysis.status_geral]}</span>
                            <span className={`font-bold text-sm ${STATUS_COLOR[analysis.status_geral] ?? "text-[#1A1410]"}`}>
                              {analysis.status_geral === "bom" ? "Conta saudável" : analysis.status_geral === "atencao" ? "Atenção necessária" : "Ação urgente"}
                            </span>
                          </div>
                          <p className="text-white text-xs">{analysis.resumo}</p>
                        </div>

                        {/* Alertas */}
                        {analysis.alertas?.length > 0 && (
                          <div className="space-y-2">
                            <p className="text-[#6B5E55] text-[10px] font-semibold uppercase tracking-wider">Alertas</p>
                            {analysis.alertas.map((a, i) => (
                              <div key={i} className="flex gap-2 text-xs">
                                <span className={`font-bold flex-shrink-0 ${NIVEL_COLOR[a.nivel] ?? "text-[#6B5E55]"}`}>
                                  {a.nivel === "critico" ? "🔴" : a.nivel === "atencao" ? "🟡" : "🟢"}
                                </span>
                                <div>
                                  <span className="text-[#1A1410] font-semibold">{a.titulo}</span>
                                  <span className="text-[#6B5E55] ml-1">— {a.detalhe}</span>
                                </div>
                              </div>
                            ))}
                          </div>
                        )}

                        {/* Análise por área */}
                        {analysis.analise && (
                          <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                            {(["criativo", "audiencia", "orcamento"] as const).map(key => {
                              const sec = analysis.analise[key];
                              if (!sec) return null;
                              const labels = { criativo: "Criativo", audiencia: "Audiência", orcamento: "Orçamento" };
                              return (
                                <div key={key} className={`rounded-xl border px-3 py-2.5 ${STATUS_BG[sec.status] ?? STATUS_BG.atencao}`}>
                                  <div className="flex items-center gap-1.5 mb-1.5">
                                    <span>{STATUS_ICON[sec.status]}</span>
                                    <span className="text-white font-semibold text-xs">{labels[key]}</span>
                                  </div>
                                  <p className="text-white/80 text-[11px] mb-2">{sec.diagnostico}</p>
                                  {sec.acoes?.length > 0 && (
                                    <ul className="space-y-1">
                                      {sec.acoes.map((a, i) => (
                                        <li key={i} className="text-[11px] text-white/90 flex gap-1">
                                          <span className="text-[#9BB5E9] flex-shrink-0">→</span>{a}
                                        </li>
                                      ))}
                                    </ul>
                                  )}
                                </div>
                              );
                            })}
                          </div>
                        )}

                        {/* Próximas ações */}
                        {analysis.proximas_acoes?.length > 0 && (
                          <div>
                            <p className="text-[#6B5E55] text-[10px] font-semibold uppercase tracking-wider mb-2">Próximas ações</p>
                            <ol className="space-y-1">
                              {analysis.proximas_acoes.map((a, i) => (
                                <li key={i} className="text-xs text-[#1A1410] flex gap-2">
                                  <span className="text-[#4267B2] font-bold flex-shrink-0">{i + 1}.</span>{a}
                                </li>
                              ))}
                            </ol>
                          </div>
                        )}

                        {/* Por campanha */}
                        {analysis.por_campanha?.length > 0 && (
                          <div>
                            <p className="text-[#6B5E55] text-[10px] font-semibold uppercase tracking-wider mb-2">Por campanha</p>
                            <div className="space-y-1.5">
                              {analysis.por_campanha.map((c, i) => (
                                <div key={i} className="flex items-start gap-2 text-xs">
                                  <span className={`font-bold flex-shrink-0 mt-0.5 ${STATUS_COLOR[c.status] ?? "text-[#6B5E55]"}`}>{STATUS_ICON[c.status]}</span>
                                  <div>
                                    <span className="text-[#1A1410] font-semibold">{c.nome}</span>
                                    <span className="text-[#6B5E55] ml-1">— {c.acao_prioritaria}</span>
                                  </div>
                                </div>
                              ))}
                            </div>
                          </div>
                        )}
                      </div>
                    );
                  })()}
                </div>
              )}

              {/* Anúncios individuais */}
              {adsMetrics && (
                <div className="mt-4 rounded-2xl border border-[#4267B2]/30 bg-[#F0EDE8] overflow-hidden">
                  <button
                    onClick={async () => {
                      if (showAds) { setShowAds(false); return; }
                      setShowAds(true);
                      if (adMetrics) return;
                      setAdsLoading(true);
                      setAdsAdError(null);
                      try {
                        const data = await getMetaAdsByAd(instanceId, explorerOpts);
                        setAdMetrics(data);
                      } catch (e: unknown) {
                        setAdsAdError(e instanceof Error ? e.message : String(e));
                      } finally {
                        setAdsLoading(false);
                      }
                    }}
                    className="w-full flex items-center justify-between px-4 py-3 border-b border-[#4267B2]/20 hover:bg-[#E8E4DF] transition-colors"
                  >
                    <span className="flex items-center gap-2 text-[#9BB5E9] font-bold text-sm">
                      🎯 Anúncios por Criativo
                      {adMetrics && <span className="text-[10px] font-normal text-[#6B5E55]">{adMetrics.ads.length} anúncios · {adMetrics.total_leads} leads</span>}
                    </span>
                    <span className="text-[#6B5E55] text-xs">{showAds ? "▲ fechar" : "▼ ver criativos"}</span>
                  </button>

                  {showAds && (
                    <div className="p-4">
                      {adsLoading && <p className="text-[#6B5E55] text-xs text-center py-4">Buscando anúncios...</p>}
                      {adsAdError && <p className="text-[#F85149] text-xs text-center py-4">{adsAdError}</p>}
                      {adMetrics && adMetrics.ads.length === 0 && (
                        <p className="text-[#6B5E55] text-xs text-center py-4">Nenhum anúncio encontrado no período selecionado.</p>
                      )}
                      {adMetrics && adMetrics.ads.length > 0 && (
                        <div className="space-y-3">
                          {adMetrics.ads.map((ad: MetaAdsAd, i: number) => {
                            const isBest  = i === 0 && ad.leads > 0;
                            const isWorst = adMetrics.ads.length > 1 && i === adMetrics.ads.length - 1 && ad.leads === 0;
                            const qColor  = ad.quality_ranking?.includes("ABOVE") ? "text-green-600"
                                          : ad.quality_ranking?.includes("BELOW") ? "text-red-500"
                                          : "text-[#6B5E55]";
                            return (
                              <div key={ad.ad_id} className={`rounded-xl border p-3 flex gap-3 ${
                                isBest  ? "border-green-500/40 bg-green-50"
                                : isWorst ? "border-red-400/30 bg-red-50"
                                : "border-[#D4CFC9] bg-[#E8E4DF]"
                              }`}>
                                {/* Thumbnail */}
                                <div className="w-16 h-16 flex-shrink-0 rounded-lg overflow-hidden bg-[#D4CFC9] flex items-center justify-center">
                                  {ad.thumbnail_url
                                    // eslint-disable-next-line @next/next/no-img-element -- thumbnail vem do CDN do Meta (domínio dinâmico); next/image exigiria remotePatterns abertos
                                    ? <img src={ad.thumbnail_url} alt="" className="w-full h-full object-cover" />
                                    : <span className="text-2xl">🖼</span>
                                  }
                                </div>
                                {/* Info */}
                                <div className="flex-1 min-w-0">
                                  <div className="flex items-start justify-between gap-2">
                                    <div className="flex items-center gap-1.5 min-w-0">
                                      <span className="flex-shrink-0 text-sm" title={ad.creative_type === "video" ? "Vídeo" : ad.creative_type === "image" ? "Imagem" : "Criativo"}>
                                        {ad.creative_type === "video" ? "📹" : ad.creative_type === "image" ? "🖼️" : "🎨"}
                                      </span>
                                      <p className="text-[#1A1410] text-xs font-semibold truncate">{ad.ad_name}</p>
                                    </div>
                                    <div className="flex gap-1 flex-shrink-0">
                                      {isBest  && <span className="text-[10px] font-bold text-green-600 bg-green-100 px-1.5 py-0.5 rounded">MELHOR</span>}
                                      {isWorst && <span className="text-[10px] font-bold text-red-500 bg-red-100 px-1.5 py-0.5 rounded">REVISAR</span>}
                                    </div>
                                  </div>
                                  <p className="text-[#6B5E55] text-[10px] truncate">{ad.campaign_name}</p>
                                  {ad.creative_body && <p className="text-[#6B5E55] text-[10px] mt-1 line-clamp-1 italic">{ad.creative_body}</p>}
                                  <div className="flex flex-wrap gap-x-3 gap-y-0.5 mt-1.5 text-[10px] font-medium">
                                    <span className="text-[#1A1410]">Leads: <strong>{ad.leads}</strong></span>
                                    <span className="text-[#1A1410]">CTR: <strong>{ad.ctr.toFixed(2)}%</strong></span>
                                    <span className="text-[#1A1410]">CPL: <strong>{ad.cpl ? `R$ ${ad.cpl.toFixed(2)}` : "—"}</strong></span>
                                    <span className="text-[#1A1410]">Gasto: <strong>R$ {ad.spend.toFixed(2)}</strong></span>
                                    <span className={`${qColor}`}>Quality: <strong>{ad.quality_ranking || "—"}</strong></span>
                                  </div>
                                </div>
                              </div>
                            );
                          })}
                        </div>
                      )}
                    </div>
                  )}
                </div>
              )}

              {/* Chat IA Meta Ads */}
              {adsMetrics && (
                <div className="mt-4 rounded-2xl border border-[#4267B2]/30 bg-[#F0EDE8] overflow-hidden">
                  <button
                    onClick={() => setShowChat(v => !v)}
                    className="w-full flex items-center justify-between px-4 py-3 border-b border-[#4267B2]/20 hover:bg-[#E8E4DF] transition-colors"
                  >
                    <span className="flex items-center gap-2 text-[#9BB5E9] font-bold text-sm">
                      💬 Pergunte ao Agente Meta Ads
                    </span>
                    <span className="text-[#6B5E55] text-xs">{showChat ? "▲ fechar" : "▼ abrir"}</span>
                  </button>

                  {showChat && (
                    <div className="flex flex-col" style={{ height: "360px" }}>
                      {/* histórico */}
                      <div className="flex-1 overflow-y-auto px-4 py-3 space-y-3">
                        {chatHistory.length === 0 && (
                          <p className="text-[#6B5E55] text-xs text-center pt-4">
                            Faça uma pergunta sobre as suas métricas Meta Ads.<br />
                            Ex: <em>&quot;Por que meu CPM está alto?&quot;</em> · <em>&quot;Qual campanha devo pausar?&quot;</em>
                          </p>
                        )}
                        {chatHistory.map((m, i) => (
                          <div key={i} className={`flex ${m.role === "user" ? "justify-end" : "justify-start"}`}>
                            <div className={`max-w-[80%] rounded-xl px-3 py-2 text-xs leading-relaxed whitespace-pre-wrap ${
                              m.role === "user"
                                ? "bg-[#4267B2] text-white"
                                : "bg-[#E8E4DF] border border-[#4267B2]/20 text-[#1A1410]"
                            }`}>
                              {m.content}
                            </div>
                          </div>
                        ))}
                        {chatLoading && (
                          <div className="flex justify-start">
                            <div className="bg-[#DDD9D4] border border-[#4267B2]/20 rounded-xl px-3 py-2">
                              <span className="flex gap-1">
                                <span className="w-1.5 h-1.5 bg-[#9BB5E9] rounded-full animate-bounce" style={{ animationDelay: "0ms" }} />
                                <span className="w-1.5 h-1.5 bg-[#9BB5E9] rounded-full animate-bounce" style={{ animationDelay: "150ms" }} />
                                <span className="w-1.5 h-1.5 bg-[#9BB5E9] rounded-full animate-bounce" style={{ animationDelay: "300ms" }} />
                              </span>
                            </div>
                          </div>
                        )}
                        <div ref={chatEndRef} />
                      </div>

                      {/* input */}
                      <div className="border-t border-[#4267B2]/20 px-3 py-2 flex gap-2">
                        <input
                          type="text"
                          value={chatInput}
                          onChange={e => setChatInput(e.target.value)}
                          onKeyDown={e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); handleChat(); } }}
                          placeholder="Faça uma pergunta sobre suas métricas..."
                          disabled={chatLoading}
                          className="flex-1 bg-[#E8E4DF] border border-[#30363D] rounded-lg px-3 py-1.5 text-xs text-[#1A1410] placeholder-[#6B5E55] focus:outline-none focus:border-[#4267B2] disabled:opacity-50"
                        />
                        <button
                          onClick={handleChat}
                          disabled={chatLoading || !chatInput.trim()}
                          className="px-3 py-1.5 bg-[#4267B2] hover:bg-[#3b5ea0] disabled:opacity-40 text-white text-xs font-semibold rounded-lg transition-colors"
                        >
                          Enviar
                        </button>
                        {chatHistory.length > 0 && (
                          <button
                            onClick={() => setChatHistory([])}
                            className="px-2 py-1.5 text-[#6B5E55] hover:text-[#F85149] text-xs transition-colors"
                            title="Limpar conversa"
                          >✕</button>
                        )}
                      </div>
                    </div>
                  )}
                </div>
              )}
            </div>


          </div>
        )}
      </div>
    </div>
  );
}
