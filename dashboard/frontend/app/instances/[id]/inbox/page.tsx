"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import {
  listConversations,
  getConversation,
  humanSendMessage,
  humanSendAudio,
  humanSendMedia,
  takeoverConversation,
  releaseConversation,
  deleteConversation,
  deleteConversationMessage,
  getConversationSummary,
  inboxSendToCrm,
  createNewLead,
  updateConversationCampanha,
  updateConversationMetadata,
  getMetaAdsCampaigns,
  listInboxKnowledge,
  uploadInboxKnowledge,
  deleteInboxKnowledge,
  autoGeneratePatterns,
  synthesizeAgentKb,
  exportConversations,
  suggestReplies,
  getInstanceConfig,
  getInboxPollCapabilities,
  updateAutoSuggest,
  connectSSE,
  ConversationSummary,
  ConversationDetail,
  ConversationSummaryAI,
  KnowledgeFile,
  InboxPollTemplate,
} from "@/lib/api";
import { PollComposer, PollManager, PollResults } from "./Polls";

// ── Constantes de estilo ──────────────────────────────────────────────────────

const STAGE_STYLE: Record<string, { label: string; bg: string; text: string; dot: string }> = {
  qualifying:   { label: "Qualificando",    bg: "bg-[#2D2000]",  text: "text-[#F0B429]",  dot: "bg-[#F0B429] animate-pulse" },
  initiated:    { label: "Iniciou Conversa",bg: "bg-[#3D0A00]",  text: "text-[#CC3300]",  dot: "bg-[#CC3300] animate-pulse" },
  handoff:      { label: "Precisa Humano",  bg: "bg-[#2D1F00]",  text: "text-[#F0B429]",  dot: "bg-[#F0B429] animate-ping" },
  disqualified: { label: "Desqualificado",  bg: "bg-[#2D0A0A]",  text: "text-[#F87171]",  dot: "bg-[#F87171]" },
};

function errMessage(err: unknown): string {
  if (err instanceof Error) return err.message;
  if (err && typeof err === "object") {
    const obj = err as Record<string, unknown>;
    if (typeof obj.detail === "string") return obj.detail;
    if (typeof obj.message === "string") return obj.message;
    try { return JSON.stringify(err); } catch { /* ignore */ }
  }
  return String(err);
}

function stageMeta(stage: string) {
  return STAGE_STYLE[stage] ?? { label: stage, bg: "bg-[#D4CFC9]", text: "text-[#6B5E55]", dot: "bg-[#6B5E55]" };
}

function scoreColor(score: number | null) {
  if (score === null || score === undefined) return "text-[#6B5E55]";
  return score >= 70 ? "text-[#CC3300]" : score >= 40 ? "text-[#F0B429]" : "text-[#F87171]";
}

function timeAgo(iso: string): string {
  if (!iso) return "";
  const diff = Date.now() - new Date(iso).getTime();
  const m = Math.floor(diff / 60000);
  if (m < 1)  return "agora";
  if (m < 60) return `${m}min`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h`;
  return `${Math.floor(h / 24)}d`;
}

// ── Componente principal ──────────────────────────────────────────────────────

export default function InboxPage() {
  const params     = useParams();
  const instanceId = params.id as string;

  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [selected, setSelected]           = useState<string | null>(null);
  const [detail, setDetail]               = useState<ConversationDetail | null>(null);
  const [filter, setFilter]               = useState<string>("all");
  const [pollToUse, setPollToUse]           = useState<InboxPollTemplate | null>(null);
  const [message, setMessage]             = useState("");
  const [sending, setSending]             = useState(false);
  const [takingOver, setTakingOver]       = useState(false);
  const [releasing, setReleasing]         = useState(false);
  const [loading, setLoading]             = useState(true);
  const [loadError, setLoadError]         = useState("");
  const [summary, setSummary]             = useState<ConversationSummaryAI | null>(null);
  const [summaryLoading, setSummaryLoading] = useState(false);
  const [sendingToCrm, setSendingToCrm]   = useState(false);
  const [crmSent, setCrmSent]             = useState<string | null>(null);
  const [recording, setRecording]         = useState(false);
  const [sendingAudio, setSendingAudio]   = useState(false);
  const [recordedAudio, setRecordedAudio] = useState<{ blob: Blob; url: string } | null>(null);
  const [showEmoji, setShowEmoji]         = useState(false);
  const [selectedFile, setSelectedFile]   = useState<File | null>(null);
  const [sendingMedia, setSendingMedia]   = useState(false);

  // Modal novo lead
  const [showNewLead, setShowNewLead]         = useState(false);
  const [newLeadName, setNewLeadName]         = useState("");
  const [newLeadPhone, setNewLeadPhone]       = useState("");
  const [newLeadMsg, setNewLeadMsg]           = useState("");
  const [newLeadCampanha, setNewLeadCampanha] = useState("");
  const [newLeadEmail, setNewLeadEmail]       = useState("");
  const [newLeadgenId, setNewLeadgenId]       = useState("");
  const [newLeadCep, setNewLeadCep]           = useState("");
  const [creatingLead, setCreatingLead]       = useState(false);
  const [newLeadError, setNewLeadError]       = useState<string | null>(null);
  const [newLeadDuplicate, setNewLeadDuplicate] = useState<{ phone: string; name: string } | null>(null);
  const [campaignOptions, setCampaignOptions]     = useState<string[]>([]);
  const [loadingCampaigns, setLoadingCampaigns]   = useState(false);
  const [editCampanhaPhone, setEditCampanhaPhone] = useState<string | null>(null);
  const [editCampanhaVal, setEditCampanhaVal]     = useState("");
  const [savingCampanha, setSavingCampanha]       = useState(false);
  const [campaignSaveError, setCampaignSaveError] = useState<string | null>(null);
  const [editLeadNome, setEditLeadNome]           = useState("");
  const [editLeadEmail, setEditLeadEmail]         = useState("");
  const [editLeadId, setEditLeadId]               = useState("");
  const [editLeadCidade, setEditLeadCidade]       = useState("");
  const [editLeadEstado, setEditLeadEstado]       = useState("");
  const [editLeadPais, setEditLeadPais]           = useState("");
  const [editLeadCep, setEditLeadCep]             = useState("");
  const [loadingLeadMeta, setLoadingLeadMeta]     = useState(false);
  const [savingLeadMeta, setSavingLeadMeta]       = useState(false);
  // Inbox Knowledge Base
  const [inboxKbFiles, setInboxKbFiles]       = useState<KnowledgeFile[]>([]);
  const [showKbPanel, setShowKbPanel]         = useState(false);
  const [uploadingKb, setUploadingKb]         = useState(false);
  const [kbError, setKbError]                 = useState<string | null>(null);
  const [generatingPatterns, setGeneratingPatterns] = useState(false);
  const [patternsMsg, setPatternsMsg]         = useState<string | null>(null);
  const [synthesizing, setSynthesizing]       = useState(false);
  const inboxKbInputRef                       = useRef<HTMLInputElement>(null);

  // Agente de sugestões
  const [suggestions, setSuggestions]         = useState<string[]>([]);
  const [suggestionAnalise, setSuggestionAnalise] = useState<string>("");
  const [loadingSuggestions, setLoadingSuggestions] = useState(false);
  const [showSuggestions, setShowSuggestions] = useState(false);
  const [autoSuggestEnabled, setAutoSuggestEnabled] = useState(true);
  const [togglingAutoSuggest, setTogglingAutoSuggest] = useState(false);

  const mediaRecorderRef  = useRef<MediaRecorder | null>(null);
  const audioChunksRef    = useRef<Blob[]>([]);
  const messagesEndRef    = useRef<HTMLDivElement>(null);
  const textareaRef       = useRef<HTMLTextAreaElement>(null);
  const emojiPickerRef    = useRef<HTMLDivElement>(null);
  const fileInputRef      = useRef<HTMLInputElement>(null);

  // ── Carregamento ────────────────────────────────────────────────────────────

  const loadList = useCallback(async () => {
    try {
      const data = await listConversations(instanceId);
      setConversations(data.conversations ?? []);
      setLoadError("");
    } catch (e) {
      // Sem catch, um 403 (sem acesso à instância) deixava o spinner infinito.
      setLoadError(e instanceof Error && e.message.includes("403")
        ? "Você não tem acesso a esta instância. Peça ao administrador para liberá-la para o seu usuário."
        : "Não foi possível carregar as conversas. Tente novamente.");
    } finally {
      setLoading(false);
    }
  }, [instanceId]);

  const loadDetail = useCallback(async (phone: string) => {
    try {
      const data = await getConversation(instanceId, phone);
      // Descarta resposta atrasada de uma conversa que não é mais a selecionada
      // — evita sobrescrever a tela com dados da conversa anterior (race
      // condition ao trocar de conversa rapidamente).
      if (data?.phone && selectedRef.current === phone) setDetail(data);
    } catch { /* ignore */ }
  }, [instanceId]);

  useEffect(() => {
    document.body.style.overflow = "hidden";
    return () => { document.body.style.overflow = ""; };
  }, []);

  const selectedRef = useRef<string | null>(null);
  useEffect(() => {
    selectedRef.current = selected;
  }, [selected]);

  // Compartilhada entre os dois efeitos de poll abaixo — só existe pra evitar
  // pollar quando o SSE já está confirmadamente entregando eventos. Começa
  // sempre null (= "sem info", trata como desconectado), então qualquer falha
  // nessa checagem apenas volta ao comportamento de sempre pollar, sem regressão.
  const sseConnectedRef = useRef<(() => boolean) | null>(null);

  useEffect(() => {
    loadList();
    // SSE é o canal primário — atualiza a lista E a conversa aberta em tempo
    // real; o polling é só rede de segurança caso o stream caia.
    const disconnect = connectSSE(instanceId, (type, data) => {
      if (type === "new_message" || type === "ai_reply" || type === "handoff") {
        loadList();
        const phone = typeof data.phone === "string" ? data.phone : "";
        if (phone && phone === selectedRef.current) loadDetail(phone);
      }
    });
    sseConnectedRef.current = disconnect.isConnected;
    const fallback = setInterval(() => {
      if (!sseConnectedRef.current?.()) loadList();
    }, 30000);
    return () => { disconnect(); clearInterval(fallback); sseConnectedRef.current = null; };
  }, [loadList, loadDetail, instanceId]);

  const prevSelectedRef = useRef<string | null>(null);
  useEffect(() => {
    if (!selected) return;
    if (prevSelectedRef.current !== selected) {
      prevSelectedRef.current = selected;
      if (autoSuggestEnabled) {
        setShowSuggestions(true);
        setSuggestions([]);
        // pequeno delay para o backend processar o loadDetail paralelo
        setTimeout(handleSuggest, 300);
      }
    }
    loadDetail(selected);
    // SSE já atualiza a conversa aberta em tempo real — poll lento como fallback
    const interval = setInterval(() => {
      if (!sseConnectedRef.current?.()) loadDetail(selected);
    }, 15000);
    return () => clearInterval(interval);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected, loadDetail, autoSuggestEnabled]);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [detail?.messages?.length]);

  // ── Ações ───────────────────────────────────────────────────────────────────

  const handleSend = async () => {
    if (!selected || !message.trim() || sending) return;
    setSending(true);
    try {
      await humanSendMessage(instanceId, selected, message.trim());
      setMessage("");
      await loadDetail(selected);
    } catch (error) {
      const msg = error instanceof Error ? error.message : "Falha ao enviar a mensagem.";
      alert(msg);
    } finally {
      setSending(false);
    }
  };

  const handleDelete = async (phone: string) => {
    if (!confirm(`Apagar conversa com ${phone}?`)) return;
    try {
      await deleteConversation(instanceId, phone);
      if (selected === phone) { setSelected(null); setDetail(null); }
      loadList();
    } catch {
      alert("Erro ao apagar conversa. Tente novamente.");
    }
  };

  const handleDeleteMessage = async (phone: string, index: number) => {
    try {
      await deleteConversationMessage(instanceId, phone, index);
      const updated = await getConversation(instanceId, phone);
      setDetail(updated);
    } catch {
      alert("Erro ao apagar mensagem. Tente novamente.");
    }
  };

  const handleTakeover = async () => {
    if (!selected || takingOver) return;
    setTakingOver(true);
    try {
      await takeoverConversation(instanceId, selected);
      await loadDetail(selected);
    } catch {
      alert("Erro ao assumir conversa. Tente novamente.");
    } finally {
      setTakingOver(false);
    }
  };

  const handleRelease = async () => {
    if (!selected || releasing) return;
    setReleasing(true);
    try {
      await releaseConversation(instanceId, selected);
      await loadDetail(selected);
    } catch {
      alert("Erro ao devolver conversa para a IA. Tente novamente.");
    } finally {
      setReleasing(false);
    }
  };

  const handleSummary = async () => {
    if (!selected) return;
    setSummaryLoading(true);
    setSummary(null);
    try {
      const data = await getConversationSummary(instanceId, selected);
      setSummary(data);
    } catch {
      setSummary(null);
    }
    setSummaryLoading(false);
  };

  const handleSendToCrm = async () => {
    if (!selected || sendingToCrm) return;
    setSendingToCrm(true);
    try {
      const res = await inboxSendToCrm(instanceId, selected);
      if (res && res.id) {
        setCrmSent(selected);
        setTimeout(() => setCrmSent(null), 3000);
      } else {
        alert("Erro ao enviar para CRM. Verifique os logs do backend.");
      }
    } catch (err) {
      alert("Erro ao enviar para CRM: " + (err instanceof Error ? err.message : "desconhecido"));
    } finally {
      setSendingToCrm(false);
    }
  };

  const fetchCampaignOptions = async () => {
    setLoadingCampaigns(true);
    try {
      const data = await getMetaAdsCampaigns(instanceId);
      setCampaignOptions([...new Set((data.campaigns ?? []).map(c => c.name.trim()).filter(Boolean))].sort((a, b) => a.localeCompare(b)));
    } catch {
      setCampaignOptions([]);
    } finally {
      setLoadingCampaigns(false);
    }
  };

  const openNewLeadModal = async () => {
    setShowNewLead(true);
    setNewLeadError(null);
    await fetchCampaignOptions();
  };

  const openEditLead = async (phone: string, currentCampaign: string) => {
    setEditCampanhaPhone(phone);
    setEditCampanhaVal(currentCampaign);
    setCampaignSaveError(null);
    setEditLeadNome(""); setEditLeadEmail(""); setEditLeadId("");
    setEditLeadCidade(""); setEditLeadEstado(""); setEditLeadPais(""); setEditLeadCep("");
    await fetchCampaignOptions();
    setLoadingLeadMeta(true);
    try {
      const d = await getConversation(instanceId, phone);
      setEditLeadNome(d.name || "");
      setEditLeadEmail(d.lead_email || "");
      const sm = (d.source_meta || {}) as Record<string, unknown>;
      setEditLeadId((sm.leadgen_id as string) || "");
      const lf = (sm.lead_fields || {}) as Record<string, string>;
      setEditLeadCidade(lf.cidade || "");
      setEditLeadEstado(lf.estado || "");
      setEditLeadPais(lf.pais || "");
      setEditLeadCep(lf.cep || "");
    } catch { /* ignore */ } finally {
      setLoadingLeadMeta(false);
    }
  };

  const handleSaveCampanha = async (dismiss = false) => {
    if (!editCampanhaPhone) return;
    setCampaignSaveError(null);
    if (!dismiss && editCampanhaVal.length > 512) {
      setCampaignSaveError("O nome da campanha pode ter no máximo 512 caracteres.");
      return;
    }
    setSavingCampanha(true);
    setSavingLeadMeta(true);
    try {
      await Promise.all([
        updateConversationCampanha(instanceId, editCampanhaPhone, dismiss ? "" : editCampanhaVal, dismiss),
        updateConversationMetadata(instanceId, editCampanhaPhone, {
          nome:       editLeadNome       || undefined,
          email:      editLeadEmail      || undefined,
          leadgen_id: editLeadId         || undefined,
          cidade:     editLeadCidade     || undefined,
          estado:     editLeadEstado     || undefined,
          pais:       editLeadPais       || undefined,
          cep:        editLeadCep        || undefined,
        }),
      ]);
      setEditCampanhaPhone(null);
      await loadList();
      if (selected === editCampanhaPhone) await loadDetail(editCampanhaPhone);
    } catch (err) {
      setCampaignSaveError(err instanceof Error ? err.message : "Não foi possível salvar o lead.");
    } finally {
      setSavingCampanha(false);
      setSavingLeadMeta(false);
    }
  };

  const closeNewLeadModal = () => {
    setShowNewLead(false);
    setNewLeadName(""); setNewLeadPhone(""); setNewLeadMsg(""); setNewLeadCampanha(""); setNewLeadEmail(""); setNewLeadgenId(""); setNewLeadCep(""); setNewLeadError(null); setNewLeadDuplicate(null);
  };

  const handleNewLeadPhoneChange = (value: string) => {
    setNewLeadPhone(value);
    const digits = value.replace(/\D/g, "");
    if (digits.length >= 8) {
      const found = conversations.find(c => c.phone.replace(/\D/g, "").includes(digits) || digits.includes(c.phone.replace(/\D/g, "")));
      setNewLeadDuplicate(found ? { phone: found.phone, name: found.name || found.phone } : null);
    } else {
      setNewLeadDuplicate(null);
    }
  };

  const handleCreateNewLead = async () => {
    if (!newLeadName.trim() || !newLeadPhone.trim() || !newLeadCampanha.trim()) return;
    setCreatingLead(true);
    setNewLeadError(null);
    try {
      const res = await createNewLead(instanceId, newLeadName.trim(), newLeadPhone.trim(), newLeadMsg.trim(), newLeadCampanha.trim(), newLeadEmail.trim(), newLeadgenId.trim(), newLeadCep.trim());
      closeNewLeadModal();
      await loadList();
      setSelected(res.phone);
    } catch (e: unknown) {
      setNewLeadError(e instanceof Error ? e.message : "Erro ao criar lead.");
    } finally {
      setCreatingLead(false);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); handleSend(); }
  };

  const insertEmoji = (emoji: string) => {
    const el = textareaRef.current;
    if (!el) { setMessage(m => m + emoji); return; }
    const start = el.selectionStart ?? message.length;
    const end   = el.selectionEnd   ?? message.length;
    const next  = message.slice(0, start) + emoji + message.slice(end);
    setMessage(next);
    setTimeout(() => { el.focus(); el.setSelectionRange(start + emoji.length, start + emoji.length); }, 0);
  };

  useEffect(() => {
    if (!showEmoji) return;
    const handler = (e: MouseEvent) => {
      if (emojiPickerRef.current && !emojiPickerRef.current.contains(e.target as Node)) {
        setShowEmoji(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [showEmoji]);

  const handleMicStart = async () => {
    if (recording || !selected) return;
    if (!navigator.mediaDevices?.getUserMedia) {
      alert("Este navegador não dá acesso ao microfone (precisa de HTTPS e um navegador atualizado).");
      return;
    }
    if (typeof window.MediaRecorder === "undefined") {
      alert("Este navegador não suporta gravação de áudio (comum em versões antigas do Safari/iOS).");
      return;
    }
    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (err) {
      const name = err instanceof DOMException ? err.name : "";
      const msg = name === "NotFoundError" ? "Nenhum microfone encontrado neste dispositivo."
        : name === "NotAllowedError" ? "Permissão de microfone negada — libere o acesso nas configurações do navegador."
        : `Não foi possível acessar o microfone (${name || errMessage(err)}).`;
      alert(msg);
      return;
    }
    try {
      // Safari/iOS não suporta webm — usa mp4/aac quando disponível.
      const candidates = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4", "audio/aac"];
      const mimeType = candidates.find(c => MediaRecorder.isTypeSupported(c)) || "";
      const recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
      audioChunksRef.current = [];
      recorder.ondataavailable = e => { if (e.data.size > 0) audioChunksRef.current.push(e.data); };
      recorder.onstop = () => {
        stream.getTracks().forEach(t => t.stop());
        const blob = new Blob(audioChunksRef.current, { type: mimeType });
        if (blob.size < 100) return;
        setRecordedAudio(prev => {
          if (prev) URL.revokeObjectURL(prev.url);
          return { blob, url: URL.createObjectURL(blob) };
        });
      };
      recorder.start();
      mediaRecorderRef.current = recorder;
      setRecording(true);
    } catch (err) {
      stream.getTracks().forEach(t => t.stop());
      alert("Não foi possível iniciar a gravação: " + errMessage(err));
    }
  };

  const handleMicStop = () => {
    if (!recording || !mediaRecorderRef.current) return;
    mediaRecorderRef.current.stop();
    mediaRecorderRef.current = null;
    setRecording(false);
  };

  const handleDiscardAudio = () => {
    if (recordedAudio) URL.revokeObjectURL(recordedAudio.url);
    setRecordedAudio(null);
  };

  const handleSendRecordedAudio = async () => {
    if (!recordedAudio || !selected || sendingAudio) return;
    setSendingAudio(true);
    try {
      await humanSendAudio(instanceId, selected, recordedAudio.blob);
      await loadDetail(selected);
      URL.revokeObjectURL(recordedAudio.url);
      setRecordedAudio(null);
    } catch (err) {
      alert("Erro ao enviar áudio: " + (errMessage(err)));
    } finally {
      setSendingAudio(false);
    }
  };

  const handleSendMedia = async () => {
    if (!selectedFile || !selected || sendingMedia) return;
    setSendingMedia(true);
    try {
      await humanSendMedia(instanceId, selected, selectedFile);
      setSelectedFile(null);
      await loadDetail(selected);
    } catch (err) {
      alert("Erro ao enviar arquivo: " + (errMessage(err)));
    } finally {
      setSendingMedia(false);
    }
  };

  // ── Inbox KB ─────────────────────────────────────────────────────────────────

  const loadInboxKb = useCallback(async () => {
    try {
      const data = await listInboxKnowledge(instanceId);
      setInboxKbFiles(data.files ?? []);
    } catch { /* ignore */ }
  }, [instanceId]);

  useEffect(() => {
    if (showKbPanel) loadInboxKb();
  }, [showKbPanel, loadInboxKb]);

  const handleKbUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setUploadingKb(true);
    setKbError(null);
    try {
      await uploadInboxKnowledge(instanceId, file);
      await loadInboxKb();
    } catch (err) {
      setKbError(err instanceof Error ? err.message : "Erro ao fazer upload.");
    } finally {
      setUploadingKb(false);
      if (inboxKbInputRef.current) inboxKbInputRef.current.value = "";
    }
  };

  const handleKbDelete = async (filename: string) => {
    if (!confirm(`Remover "${filename}" da base de conhecimento?`)) return;
    try {
      await deleteInboxKnowledge(instanceId, filename);
      await loadInboxKb();
    } catch {
      alert("Erro ao remover documento. Tente novamente.");
    }
  };

  const handleAutoGenerate = async () => {
    setGeneratingPatterns(true);
    setPatternsMsg(null);
    setKbError(null);
    try {
      const data = await autoGeneratePatterns(instanceId);
      setPatternsMsg(`Padrões gerados com base em ${data.conversas_analisadas} conversas convertidas.`);
      await loadInboxKb();
    } catch (err) {
      setKbError(err instanceof Error ? err.message : "Erro ao gerar padrões.");
    } finally {
      setGeneratingPatterns(false);
    }
  };

  const handleSynthesize = async () => {
    setSynthesizing(true);
    setPatternsMsg(null);
    setKbError(null);
    try {
      const data = await synthesizeAgentKb(instanceId);
      setPatternsMsg(`Memória operacional do agente criada (${data.conversas_analisadas} conversas analisadas).`);
      await loadInboxKb();
    } catch (err) {
      setKbError(err instanceof Error ? err.message : "Erro ao sintetizar memória.");
    } finally {
      setSynthesizing(false);
    }
  };

  // ── Agente de sugestões ───────────────────────────────────────────────────

  useEffect(() => {
    getInstanceConfig(instanceId)
      .then(cfg => setAutoSuggestEnabled(cfg.auto_suggest_enabled ?? true))
      .catch(() => {});
  }, [instanceId]);

  const handleToggleAutoSuggest = async () => {
    if (togglingAutoSuggest) return;
    const next = !autoSuggestEnabled;
    setTogglingAutoSuggest(true);
    try {
      await updateAutoSuggest(instanceId, next);
      setAutoSuggestEnabled(next);
    } catch {
      // mantém o valor anterior se a persistência falhar
    } finally {
      setTogglingAutoSuggest(false);
    }
  };

  const handleSuggest = async () => {
    if (!selected || loadingSuggestions) return;
    setLoadingSuggestions(true);
    setShowSuggestions(true);
    setSuggestions([]);
    setSuggestionAnalise("");
    try {
      const data = await suggestReplies(instanceId, selected);
      setSuggestions(data.sugestoes ?? []);
      setSuggestionAnalise(data.analise ?? "");
    } catch (err) {
      setSuggestions([`Erro: ${errMessage(err)}`]);
    } finally {
      setLoadingSuggestions(false);
    }
  };

  // Dispara sugestões automaticamente quando o lead envia uma mensagem
  const lastMsgRoleRef = useRef<string | null>(null);
  useEffect(() => {
    if (!autoSuggestEnabled) return;
    if (!detail?.messages?.length) return;
    const last = detail.messages[detail.messages.length - 1];
    if (last.role === "user" && lastMsgRoleRef.current !== "user_auto_" + detail.messages.length) {
      lastMsgRoleRef.current = "user_auto_" + detail.messages.length;
      handleSuggest();
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [detail?.messages?.length, selected, autoSuggestEnabled]);

  // ── Filtragem ────────────────────────────────────────────────────────────────

  const afterFilter = filter === "all"
    ? conversations
    : conversations.filter(c => c.stage === filter);

  const filtered = afterFilter;

  const counts = conversations.reduce((acc, c) => {
    acc[c.stage] = (acc[c.stage] ?? 0) + 1;
    return acc;
  }, {} as Record<string, number>);

  // ── Render ──────────────────────────────────────────────────────────────────

  return (
    <div className="fixed inset-x-0 top-16 bottom-0 bg-[#F0EDE8] flex flex-col overflow-hidden">

      {/* Header — oculto quando uma conversa está aberta (chat header tem voltar + ações) */}
      <header className={`px-4 pt-2 pb-0 flex-shrink-0 ${selected ? "hidden" : ""}`}>
      <div className="rounded-2xl border border-[#D4CFC9] bg-[#E8E4DF]/85 px-5 py-4 shadow-[0_12px_30px_rgba(26,20,16,0.10)]">
        <div className="flex items-center justify-between w-full gap-4">
          <div className="hidden sm:flex items-center gap-1 text-sm text-[#6B5E55] shrink-0">
            <Link href="/" className="hover:text-[#1A1410] transition-colors font-medium">← Início</Link>
            <span>/</span>
            <span className="text-[#1A1410] font-semibold">{instanceId}</span>
          </div>
          <div className="flex items-center gap-2 flex-wrap">
            <Link
              href={`/crm?instancia_id=${encodeURIComponent(instanceId)}`}
              className="bg-[#0E0E18] border border-[#CC3300]/30 text-[#CC3300] hover:bg-[#252535] font-semibold px-3 py-1.5 rounded-xl text-xs transition-colors"
            >
              Abrir CRM
            </Link>
            <span className="text-[#6B5E55] text-xs">{conversations.length} conversa{conversations.length !== 1 ? "s" : ""}</span>
            <button
              onClick={openNewLeadModal}
              className="flex items-center gap-1.5 bg-[#CC3300] hover:bg-[#991F00] text-white text-xs font-semibold px-3 py-1.5 rounded-lg transition-colors"
            >
              <svg viewBox="0 0 24 24" className="w-3.5 h-3.5 fill-current"><path d="M19 13h-6v6h-2v-6H5v-2h6V5h2v6h6v2z"/></svg>
              Novo Lead
            </button>
            <button
              onClick={() => exportConversations(instanceId)}
              title="Baixar ZIP com todas as conversas"
              className="flex items-center gap-1.5 bg-[#1A2A1A] hover:bg-[#253525] text-[#25D366] text-xs font-semibold px-3 py-1.5 rounded-lg transition-colors border border-[#25D366]/20"
            >
              <svg viewBox="0 0 24 24" className="w-3.5 h-3.5 fill-current"><path d="M19 9h-4V3H9v6H5l7 7 7-7zm-8 2V5h2v6h1.17L12 13.17 9.83 11H11zm-6 7v2h14v-2H5z"/></svg>
              Exportar
            </button>
            <button
              onClick={() => setShowKbPanel(v => !v)}
              title="Base de conhecimento do agente de sugestões"
              className={`flex items-center gap-1.5 text-xs font-semibold px-3 py-1.5 rounded-lg transition-colors border ${showKbPanel ? "bg-[#CC3300]/20 border-[#CC3300]/60 text-[#CC3300]" : "bg-[#1A1A2A] hover:bg-[#252538] text-[#A0A0C0] border-[#A0A0C0]/20"}`}
            >
              <svg viewBox="0 0 24 24" className="w-3.5 h-3.5 fill-current"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-2 15l-5-5 1.41-1.41L10 14.17l7.59-7.59L19 8l-9 9z"/></svg>
              KB do Agente
            </button>
          </div>
        </div>
      </div>
      </header>


      {/* ── KB Panel ─────────────────────────────────────────────────────── */}
      {showKbPanel && (
        <div className="bg-[#1A1A2A] border-b border-[#CC3300]/30 px-4 py-3 flex-shrink-0">
          <div className="max-w-2xl">
            <div className="flex items-center justify-between mb-2">
              <span className="text-[#A0A0C0] text-xs font-semibold uppercase tracking-wider">Base de Conhecimento — Agente de Sugestões</span>
              <span className="text-[#6B5E55] text-[11px]">PDF, DOCX, TXT, MD</span>
            </div>
            {kbError && <p className="text-[#F87171] text-xs mb-2">{kbError}</p>}
            {patternsMsg && <p className="text-[#25D366] text-xs mb-2">✓ {patternsMsg}</p>}
            <div className="flex flex-wrap gap-2 mb-2">
              {inboxKbFiles.map(f => (
                <div key={f.filename} className="flex items-center gap-1.5 bg-[#252538] border border-[#A0A0C0]/20 rounded-lg px-2 py-1">
                  <span className="text-[#C0C0E0] text-[11px]">{f.filename}</span>
                  <span className="text-[#6B5E55] text-[10px]">({Math.round(f.size / 1024)}KB)</span>
                  <button onClick={() => handleKbDelete(f.filename)} className="text-[#F87171] hover:text-[#FF5555] ml-1 text-[11px] font-bold">×</button>
                </div>
              ))}
              {inboxKbFiles.length === 0 && <span className="text-[#6B5E55] text-xs">Nenhum arquivo. Adicione documentos para o agente usar como referência.</span>}
            </div>
            <input ref={inboxKbInputRef} type="file" accept=".pdf,.txt,.md,.docx" className="hidden" onChange={handleKbUpload} />
            <div className="flex flex-wrap gap-2">
              <button
                onClick={() => inboxKbInputRef.current?.click()}
                disabled={uploadingKb}
                className="bg-[#CC3300] hover:bg-[#991F00] disabled:opacity-50 text-white text-xs font-semibold px-3 py-1.5 rounded-lg transition-colors"
              >
                {uploadingKb ? "Enviando..." : "+ Adicionar arquivo"}
              </button>
              <button
                onClick={handleAutoGenerate}
                disabled={generatingPatterns}
                title="Analisa conversas com score ≥ 60 ou handoff e gera padrões de resposta automáticamente"
                className="flex items-center gap-1.5 bg-[#1A2A1A] hover:bg-[#253525] disabled:opacity-50 text-[#25D366] text-xs font-semibold px-3 py-1.5 rounded-lg transition-colors border border-[#25D366]/20"
              >
                <svg viewBox="0 0 24 24" className={`w-3.5 h-3.5 fill-current ${generatingPatterns ? "animate-spin" : ""}`}>
                  <path d="M12 4V1L8 5l4 4V6c3.31 0 6 2.69 6 6 0 1.01-.25 1.97-.7 2.8l1.46 1.46A7.93 7.93 0 0 0 20 12c0-4.42-3.58-8-8-8zm0 14c-3.31 0-6-2.69-6-6 0-1.01.25-1.97.7-2.8L5.24 7.74A7.93 7.93 0 0 0 4 12c0 4.42 3.58 8 8 8v3l4-4-4-4v3z"/>
                </svg>
                {generatingPatterns ? "Analisando conversas..." : "Aprender com conversas"}
              </button>
              <button
                onClick={handleSynthesize}
                disabled={synthesizing}
                title="O agente lê toda a KB e as conversas e cria sua própria memória operacional"
                className="flex items-center gap-1.5 bg-[#1A1A2A] hover:bg-[#252538] disabled:opacity-50 text-[#A0A0C0] text-xs font-semibold px-3 py-1.5 rounded-lg transition-colors border border-[#A0A0C0]/20"
              >
                <svg viewBox="0 0 24 24" className={`w-3.5 h-3.5 fill-current ${synthesizing ? "animate-spin" : ""}`}>
                  <path d="M12 2a10 10 0 1 0 10 10A10 10 0 0 0 12 2zm1 17.93V18a1 1 0 0 0-2 0v1.93A8 8 0 0 1 4.07 13H6a1 1 0 0 0 0-2H4.07A8 8 0 0 1 11 4.07V6a1 1 0 0 0 2 0V4.07A8 8 0 0 1 19.93 11H18a1 1 0 0 0 0 2h1.93A8 8 0 0 1 13 19.93z"/>
                </svg>
                {synthesizing ? "Sintetizando..." : "Criar memória do agente"}
              </button>
            </div>
          </div>
        </div>
      )}

      <div className="flex flex-1 overflow-hidden">

        {/* ── Sidebar: lista de conversas ─────────────────────────────────── */}
        <aside className={`w-full md:w-80 bg-[#E8E4DF] border-r border-[#D4CFC9] flex-col flex-shrink-0 ${filter === "polls" ? "hidden" : selected ? "hidden md:flex" : "flex"}`}>

          {/* Filtros */}
          <div className="flex gap-1 px-3 py-3 flex-wrap">
            {[
              { key: "all",          label: "Todos" },
              { key: "handoff",      label: "Humano" },
              { key: "qualifying",   label: "Qualificado" },
              { key: "disqualified", label: "Desqualificado" },
              { key: "polls",        label: "Enquete" },
            ].map(f => (
              <button
                key={f.key}
                onClick={() => setFilter(f.key)}
                className={`text-xs px-3 py-1.5 rounded-full font-semibold transition-colors ${
                  filter === f.key
                    ? "bg-[#CC3300] text-[#F0EDE8]"
                    : "bg-[#D4CFC9] text-[#6B5E55] hover:text-[#1A1410]"
                }`}
              >
                {f.label}
                {f.key === "all"
                  ? <span className="ml-1 opacity-70">({conversations.length})</span>
                  : counts[f.key]
                    ? <span className="ml-1 opacity-70">({counts[f.key]})</span>
                    : null}
              </button>
            ))}
          </div>

          <div className="border-t border-[#D4CFC9]" />

          {/* Lista */}
          <div className="flex-1 overflow-y-auto">
            {loading ? (
              <div className="flex flex-col items-center justify-center py-16 gap-2">
                <svg className="w-6 h-6 text-[#6B5E55] animate-spin" viewBox="0 0 24 24" fill="none">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"/>
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z"/>
                </svg>
                <p className="text-[#6B5E55] text-xs">Carregando...</p>
              </div>
            ) : loadError ? (
              <div className="flex flex-col items-center justify-center py-16 gap-2 px-6 text-center">
                <p className="text-[#1A1410] text-sm font-semibold">Sem acesso</p>
                <p className="text-[#6B5E55] text-xs">{loadError}</p>
              </div>
            ) : filtered.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-16 gap-3 px-6 text-center">
                <div className="w-12 h-12 rounded-full bg-[#D4CFC9] flex items-center justify-center">
                  <svg viewBox="0 0 24 24" className="w-6 h-6 fill-[#6B5E55]">
                    <path d="M20 2H4c-1.1 0-2 .9-2 2v18l4-4h14c1.1 0 2-.9 2-2V4c0-1.1-.9-2-2-2z"/>
                  </svg>
                </div>
                <p className="text-[#6B5E55] text-sm">
                  {filter === "all" ? "Nenhuma conversa ainda" : "Nenhuma nesta categoria"}
                </p>
              </div>
            ) : (
              filtered.map(conv => {
                const sm         = stageMeta(conv.stage);
                const isSelected = selected === conv.phone;
                const needsHuman = !!conv.needs_human;
                return (
                  <div
                    key={conv.phone}
                    onClick={() => setSelected(conv.phone)}
                    className={`px-4 py-3 border-b border-[#D4CFC9] cursor-pointer transition-colors group relative ${
                      isSelected ? "bg-[#D4CFC9]" : needsHuman ? "bg-[#2D1F00]/25 hover:bg-[#332600]" : "hover:bg-[#243240]"
                    } ${needsHuman ? "border-l-2 border-l-[#F0B429]" : conv.unread && (conv.stage === "initiated" || conv.stage === "handoff") ? "border-l-2 border-l-[#CC3300]" : ""}
                    ${conv.unread && conv.stage === "disqualified" ? "border-l-2 border-l-[#F87171]" : ""}`}
                  >
                    <div className="flex items-start justify-between gap-2">
                      {/* Avatar + info */}
                      <div className="flex items-center gap-2.5 min-w-0">
                        <div className="relative flex-shrink-0">
                          <div className={`w-10 h-10 rounded-full flex items-center justify-center font-bold text-sm ${
                            needsHuman || conv.ai_paused ? "bg-[#2D1F00] text-[#F0B429]" : "bg-[#0E0E18] text-[#CC3300]"
                          }`}>
                            {(conv.name || conv.phone).charAt(0).toUpperCase()}
                          </div>
                          {needsHuman && (
                            <span className="absolute -top-0.5 -right-0.5 w-3.5 h-3.5 rounded-full bg-[#F0B429] animate-ping" />
                          )}
                          {!needsHuman && conv.unread && (conv.stage === "initiated" || conv.stage === "handoff") && (
                            <span className="absolute -top-0.5 -right-0.5 w-3.5 h-3.5 rounded-full bg-[#CC3300] animate-ping" />
                          )}
                          {conv.unread && conv.stage === "disqualified" && (
                            <span className="absolute -top-0.5 -right-0.5 w-3.5 h-3.5 rounded-full bg-[#F87171] animate-ping" />
                          )}
                        </div>
                        <div className="min-w-0 flex-1">
                          <div className="flex items-center gap-1.5 mb-0.5">
                            <span className="text-[#1A1410] text-sm font-semibold truncate">{conv.name || conv.phone}</span>
                            {conv.ai_paused && (
                              <span className="text-[10px] bg-[#2D1F00] text-[#F0B429] px-1.5 py-0.5 rounded-full flex-shrink-0 font-semibold">humano</span>
                            )}
                            {needsHuman && (
                              <span className="text-[10px] bg-[#2D1F00] text-[#F0B429] px-1.5 py-0.5 rounded-full flex-shrink-0 font-semibold animate-pulse">atenção</span>
                            )}
                            {conv.roleta_active && (
                              <span className="text-[10px] bg-[#0D2040] text-[#3B82F6] px-1.5 py-0.5 rounded-full flex-shrink-0 font-semibold">ROLETA</span>
                            )}
                          </div>
                          {conv.name && conv.name !== conv.phone && (
                            <p className="text-[#3D4F5A] text-[10px] leading-none mb-0.5">{conv.phone}</p>
                          )}
                          <p className="text-[#6B5E55] text-xs truncate">{conv.last_message}</p>
                        </div>
                      </div>

                      {/* Tempo + score badge */}
                      <div className="flex-shrink-0 text-right flex flex-col items-end gap-1">
                        <p className="text-[#6B5E55] text-[10px]">{timeAgo(conv.last_activity)}</p>
                        {conv.score !== null && conv.score !== undefined && (() => {
                          const s = conv.score as number;
                          const hot  = s >= 70;
                          const warm = s >= 40;
                          return (
                            <span className={`text-[10px] font-bold tabular-nums px-1.5 py-0.5 rounded-full ${
                              hot  ? "bg-[#3D0A00] text-[#CC3300]" :
                              warm ? "bg-[#2D2000] text-[#F0B429]" :
                                     "bg-[#2D0A0A] text-[#F87171]"
                            }`}>
                              {hot ? "🔥" : warm ? "🌡" : "❄"} {s}
                            </span>
                          );
                        })()}
                      </div>
                    </div>

                    {/* Stage + delete */}
                    <div className="flex items-center justify-between mt-2">
                      <div className="flex items-center gap-1.5 min-w-0 flex-1">
                        <span className={`text-[10px] font-semibold px-2 py-0.5 rounded-full flex items-center gap-1 flex-shrink-0 ${sm.bg} ${sm.text}`}>
                          <span className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${sm.dot}`} />
                          {sm.label}
                        </span>
                      </div>
                      <div className="flex items-center gap-1">
                        <button
                          onClick={e => { e.stopPropagation(); openEditLead(conv.phone, conv.lead_campanha ?? ""); }}
                          className="text-[#3D4F5A] hover:text-[#CC3300] transition-colors p-1 rounded-lg hover:bg-[#D4CFC9]"
                          title="Editar lead"
                          aria-label="Editar lead"
                        >
                          <svg viewBox="0 0 24 24" className="w-3.5 h-3.5 fill-current">
                            <path d="M3 17.25V21h3.75L17.81 9.94l-3.75-3.75L3 17.25zM20.71 7.04a1 1 0 0 0 0-1.41l-2.34-2.34a1 1 0 0 0-1.41 0l-1.83 1.83 3.75 3.75 1.83-1.83z"/>
                          </svg>
                        </button>
                        <button
                        onClick={e => { e.stopPropagation(); handleDelete(conv.phone); }}
                        className="text-[#3D4F5A] hover:text-[#F87171] transition-colors p-1 rounded-lg hover:bg-[#2D0A0A]"
                        title="Apagar conversa"
                      >
                        <svg viewBox="0 0 24 24" className="w-3.5 h-3.5 fill-current">
                          <path d="M6 19c0 1.1.9 2 2 2h8c1.1 0 2-.9 2-2V7H6v12zM19 4h-3.5l-1-1h-5l-1 1H5v2h14V4z"/>
                        </svg>
                        </button>
                      </div>
                    </div>
                  </div>
                );
              })
            )}
          </div>
        </aside>

        {/* ── Painel direito: chat ────────────────────────────────────────── */}
        {filter === "polls" ? (
          <div className="flex-1 min-w-0">
            <PollManager onBack={() => setFilter("all")} onUse={async poll => {
              try {
                const capability = await getInboxPollCapabilities(instanceId);
                if (!capability.native_poll) {
                  alert("Enquete nativa indisponível para o canal desta instância.");
                  return;
                }
                setPollToUse(poll);
                setFilter("all");
              } catch { alert("Não foi possível verificar o canal desta instância."); }
            }} />
          </div>
        ) : !selected ? (
          <div className="flex-1 hidden md:flex items-center justify-center bg-[#F0EDE8]">
            <div className="text-center">
              <div className="w-16 h-16 rounded-full bg-[#E8E4DF] flex items-center justify-center mx-auto mb-4">
                <svg viewBox="0 0 24 24" className="w-8 h-8 fill-[#6B5E55]">
                  <path d="M20 2H4c-1.1 0-2 .9-2 2v18l4-4h14c1.1 0 2-.9 2-2V4c0-1.1-.9-2-2-2zm-2 12H6v-2h12v2zm0-3H6V9h12v2zm0-3H6V6h12v2z"/>
                </svg>
              </div>
              <p className="text-[#1A1410] text-sm font-semibold mb-1">Selecione uma conversa</p>
              <p className="text-[#6B5E55] text-xs">Escolha um contato na lista para visualizar o histórico</p>
            </div>
          </div>
        ) : !detail ? (
          <div className="flex-1 flex items-center justify-center bg-[#F0EDE8] w-full">
            <svg className="w-6 h-6 text-[#6B5E55] animate-spin" viewBox="0 0 24 24" fill="none">
              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"/>
              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z"/>
            </svg>
          </div>
        ) : (
          <div className="flex-1 flex flex-col min-w-0">

            {/* Chat header — linha única */}
            <div className="bg-[#E8E4DF] border-b border-[#D4CFC9] px-3 py-2 flex items-center gap-2">
              {/* Esquerda: voltar + avatar + nome */}
              <div className="flex items-center gap-2 min-w-0 flex-1">
                <button
                  onClick={() => setSelected(null)}
                  aria-label="Voltar"
                  className="flex items-center justify-center w-7 h-7 rounded-lg text-[#6B5E55] hover:text-[#1A1410] hover:bg-[#D4CFC9] transition-colors flex-shrink-0"
                >
                  <svg viewBox="0 0 24 24" className="w-4 h-4 fill-current"><path d="M20 11H7.83l5.59-5.59L12 4l-8 8 8 8 1.41-1.41L7.83 13H20v-2z"/></svg>
                </button>
                <div className={`w-8 h-8 rounded-full flex-shrink-0 flex items-center justify-center font-bold text-sm ${
                  detail.ai_paused ? "bg-[#2D1F00] text-[#F0B429]" : "bg-[#0E0E18] text-[#CC3300]"
                }`}>
                  {(detail.name || detail.phone).charAt(0).toUpperCase()}
                </div>
                <div className="min-w-0">
                  <div className="flex items-center gap-1.5">
                    <h2 className="text-[#1A1410] font-bold text-sm truncate">{detail.name || detail.phone}</h2>
                    {(() => { const sm = stageMeta(detail.stage); return (
                      <span className={`text-[10px] font-semibold px-1.5 py-0.5 rounded-full flex-shrink-0 ${sm.bg} ${sm.text}`}>{sm.label}</span>
                    ); })()}
                    {detail.ai_paused && <span className="text-[10px] bg-[#2D1F00] text-[#F0B429] px-1.5 py-0.5 rounded-full font-semibold flex-shrink-0">humano</span>}
                  </div>
                </div>
              </div>

              {/* Direita: score + ações + apagar */}
              <div className="flex items-center gap-1.5 flex-shrink-0">
                {detail.score !== null && detail.score !== undefined && (
                  <span className={`text-sm font-bold tabular-nums ${scoreColor(detail.score)}`}>{detail.score}</span>
                )}
                <Link
                  href={`/crm?instancia_id=${encodeURIComponent(instanceId)}`}
                  className="flex bg-[#0E0E18] border border-[#CC3300]/30 text-[#CC3300] hover:bg-[#252535] font-semibold px-2.5 py-1.5 rounded-lg text-xs transition-colors"
                >CRM</Link>
                {detail.ai_paused ? (
                  <button onClick={handleRelease} disabled={releasing}
                    className="hidden sm:flex bg-[#3D0A00] hover:bg-[#0F3824] disabled:opacity-50 text-[#CC3300] text-xs font-semibold px-2.5 py-1.5 rounded-lg transition-colors border border-[#CC3300]/30"
                  >{releasing ? "..." : "→ IA"}</button>
                ) : (
                  <button onClick={handleTakeover} disabled={takingOver}
                    className="hidden sm:flex bg-[#2D1F00] hover:bg-[#3D2900] disabled:opacity-50 text-[#F0B429] text-xs font-semibold px-2.5 py-1.5 rounded-lg transition-colors border border-[#F0B429]/30"
                  >{takingOver ? "..." : "Assumir"}</button>
                )}
                <button onClick={handleSummary} disabled={summaryLoading}
                  className="hidden sm:flex bg-[#D4CFC9] hover:bg-[#3D4F5A] disabled:opacity-50 text-[#1A1410] text-xs font-semibold px-2.5 py-1.5 rounded-lg transition-colors"
                >{summaryLoading ? "..." : "Resumo"}</button>
                <button
                  onClick={handleToggleAutoSuggest}
                  disabled={togglingAutoSuggest}
                  title={autoSuggestEnabled ? "Sugestões automáticas ligadas — clique para desligar" : "Sugestões automáticas desligadas — clique para ligar"}
                  className={`hidden sm:flex items-center gap-1 disabled:opacity-50 text-xs font-semibold px-2.5 py-1.5 rounded-lg transition-colors border ${
                    autoSuggestEnabled
                      ? "bg-[#0E0E18] text-[#CC3300] border-[#CC3300]/30 hover:bg-[#252535]"
                      : "bg-[#D4CFC9] text-[#6B5E55] border-transparent hover:bg-[#C4BFB9]"
                  }`}
                >💡 {autoSuggestEnabled ? "Auto" : "Off"}</button>
                <button onClick={handleSendToCrm} disabled={sendingToCrm}
                  className={`text-xs font-semibold px-2.5 py-1.5 rounded-lg transition-colors disabled:opacity-50 ${
                    crmSent === detail.phone ? "bg-[#3D0A00] text-[#CC3300] border border-[#CC3300]/30" : "bg-[#0E0E18] hover:bg-[#252535] text-[#CC3300] border border-[#CC3300]/30"
                  }`}
                >{sendingToCrm ? "..." : crmSent === detail.phone ? "✓ CRM" : "→ CRM"}</button>
                <button onClick={() => handleDelete(detail.phone)}
                  className="hidden sm:flex items-center justify-center bg-[#D4CFC9] hover:bg-[#2D0A0A] text-[#6B5E55] hover:text-[#F87171] p-1.5 rounded-lg transition-colors"
                  title="Apagar conversa"
                >
                  <svg viewBox="0 0 24 24" className="w-4 h-4 fill-current"><path d="M6 19c0 1.1.9 2 2 2h8c1.1 0 2-.9 2-2V7H6v12zM19 4h-3.5l-1-1h-5l-1 1H5v2h14V4z"/></svg>
                </button>
              </div>
            </div>

            {/* Score reason */}
            {detail.score_reason && (
              <div className="bg-[#F0EDE8] border-b border-[#D4CFC9] px-5 py-2 text-xs text-[#6B5E55] flex items-center gap-2">
                <svg viewBox="0 0 24 24" className="w-3.5 h-3.5 fill-[#6B5E55] flex-shrink-0"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm1 15h-2v-2h2v2zm0-4h-2V7h2v6z"/></svg>
                {detail.score_reason}
              </div>
            )}

            {(() => {
              const sm = (detail.source_meta || {}) as Record<string, string>;
              const lgId = detail.leadgen_id || sm.leadgen_id || "";
              const ctwa = detail.ctwa_clid || sm.ctwa_clid || "";
              return (lgId || ctwa) ? (
                <div className="bg-[#F0EDE8] border-b border-[#D4CFC9] px-5 py-2 flex flex-wrap gap-x-4 gap-y-1">
                  {lgId && (
                    <span className="text-[10px] text-[#6B5E55]">
                      <span className="font-semibold">Lead ID:</span>{" "}
                      <span className="font-mono select-all">{lgId}</span>
                    </span>
                  )}
                  {ctwa && (
                    <span className="text-[10px] text-[#6B5E55]">
                      <span className="font-semibold">CTWA:</span>{" "}
                      <span className="font-mono break-all select-all">{ctwa}</span>
                    </span>
                  )}
                </div>
              ) : null;
            })()}

            {detail.needs_human && (
              <div className="bg-[#2D1F00] border-b border-[#F0B429]/40 px-5 py-3 flex items-center gap-3">
                <span className="w-3 h-3 rounded-full bg-[#F0B429] animate-ping flex-shrink-0" />
                <div>
                  <p className="text-[#F0B429] text-sm font-bold">Atendimento humano solicitado</p>
                  <p className="text-[#F0EDE8] text-xs mt-0.5">{detail.handoff_reason || "A IA sinalizou que um humano deve assumir esta conversa."}</p>
                </div>
              </div>
            )}

            {/* Mensagens */}
            <div className="flex-1 overflow-y-auto px-5 py-4 space-y-2 bg-[#F0EDE8]">
              {(detail.messages ?? []).length === 0 ? (
                <div className="flex flex-col items-center justify-center h-full gap-2 opacity-50">
                  <svg viewBox="0 0 24 24" className="w-8 h-8 fill-[#6B5E55]">
                    <path d="M20 2H4c-1.1 0-2 .9-2 2v18l4-4h14c1.1 0 2-.9 2-2V4c0-1.1-.9-2-2-2z"/>
                  </svg>
                  <p className="text-[#6B5E55] text-sm">Nenhuma mensagem</p>
                </div>
              ) : (
                detail.messages.map((msg, i) => {
                  const isLead    = msg.role === "user";
                  const isHuman   = msg.role === "human";
                  const isAi      = msg.role === "assistant";
                  const isAudio   = msg.is_audio || msg.media_type === "audio" || (msg.content ?? "").startsWith("🎤") || (msg.content ?? "").startsWith("[audio");
                  const audioTranscript = isAudio && msg.content.startsWith("🎤 ")
                    ? msg.content.slice(2).trim()
                    : null;
                  const isReceivedMedia = msg.is_media && msg.media_url && (msg.media_type === "image" || msg.media_type === "video" || msg.media_type === "document");
                  const mediaSrc = msg.media_url ? `/api/proxy${msg.media_url}` : "";

                  return (
                    <div key={i} className={`group flex items-end gap-1 ${isLead ? "justify-start" : "justify-end"}`}>
                      {!isLead && (
                        <button
                          onClick={() => handleDeleteMessage(detail.phone, i)}
                          className="opacity-0 group-hover:opacity-100 transition-opacity p-1 rounded hover:bg-red-500/20 text-[#6B5E55] hover:text-red-400 flex-shrink-0 mb-1"
                          title="Apagar mensagem"
                        >
                          <svg viewBox="0 0 24 24" className="w-3.5 h-3.5 fill-current">
                            <path d="M6 19c0 1.1.9 2 2 2h8c1.1 0 2-.9 2-2V7H6v12zM19 4h-3.5l-1-1h-5l-1 1H5v2h14V4z"/>
                          </svg>
                        </button>
                      )}
                      <div className={`max-w-[85%] sm:max-w-xs lg:max-w-md xl:max-w-lg rounded-2xl px-4 py-2.5 shadow-sm ${
                        isLead  ? "bg-[#E8E4DF] rounded-tl-sm" :
                        isHuman ? "bg-[#CC3300] rounded-tr-sm" :
                                  "bg-[#FDE8E4] border border-[#CC3300]/20 rounded-tr-sm"
                      }`}>
                        {(isAi || isHuman) && (
                          <p className={`text-[10px] font-bold mb-1 uppercase tracking-wide ${isHuman ? "text-white/70" : "text-[#CC3300]/70"}`}>
                            {isHuman ? "Você" : "IA"}
                          </p>
                        )}
                        {msg.is_poll ? (
                          <div className="space-y-2">
                            <p className="text-[11px] font-semibold text-white/75">Você enviou uma enquete</p>
                            {(msg.poll_question_count ?? 1) > 1 && <p className="text-[10px] text-white/60">Pergunta {msg.poll_question_index} de {msg.poll_question_count}</p>}
                            <p className="text-sm font-semibold text-white whitespace-pre-wrap break-words">{msg.content}</p>
                            {(msg.poll_options ?? []).map((option, optionIndex) => (
                              <p key={optionIndex} className="text-sm text-white/90">{msg.poll_allow_multiple_answers ? "☐" : "◯"} {option}</p>
                            ))}
                            {msg.poll_send_id && <PollResults instanceId={instanceId} phone={selected!} sendId={msg.poll_send_id} />}
                          </div>
                        ) : isReceivedMedia && msg.media_type === "image" ? (
                          <div className="flex flex-col gap-1.5">
                            <a href={mediaSrc} target="_blank" rel="noopener noreferrer">
                              {/* eslint-disable-next-line @next/next/no-img-element -- mídia recebida do WhatsApp via proxy do backend (URL dinâmica/temporária); next/image não se aplica */}
                              <img
                                src={mediaSrc}
                                alt={msg.filename || "Imagem"}
                                className="max-w-full max-h-64 rounded-xl object-cover cursor-zoom-in"
                                loading="lazy"
                              />
                            </a>
                            {msg.content && msg.content !== "[image]" && (
                              <p className={`text-sm whitespace-pre-wrap break-words leading-relaxed ${isLead ? "text-[#1A1410]" : "text-white"}`}>{msg.content}</p>
                            )}
                          </div>
                        ) : isReceivedMedia && msg.media_type === "video" ? (
                          <div className="flex flex-col gap-1.5">
                            <video src={mediaSrc} controls className="max-w-full max-h-64 rounded-xl" preload="metadata" />
                            {msg.content && msg.content !== "[video]" && (
                              <p className={`text-sm whitespace-pre-wrap break-words leading-relaxed ${isLead ? "text-[#1A1410]" : "text-white"}`}>{msg.content}</p>
                            )}
                          </div>
                        ) : isReceivedMedia && msg.media_type === "document" ? (
                          <a
                            href={mediaSrc}
                            target="_blank"
                            rel="noopener noreferrer"
                            className={`flex items-center gap-2 rounded-xl px-3 py-2 ${isLead ? "bg-[#F0EDE8]" : "bg-white/10"}`}
                          >
                            <span className="text-xl">📄</span>
                            <span className={`text-sm underline break-all ${isLead ? "text-[#1A1410]" : "text-white"}`}>
                              {msg.filename || "Documento"}
                            </span>
                          </a>
                        ) : isAudio ? (
                          <div className="flex flex-col gap-2">
                            <div className="flex items-center gap-2">
                              <div className="w-7 h-7 rounded-full bg-white/20 flex items-center justify-center flex-shrink-0">
                                <svg viewBox="0 0 24 24" className={`w-3.5 h-3.5 ${isLead ? "fill-[#CC3300]" : "fill-white"}`}>
                                  <path d="M12 14c1.66 0 3-1.34 3-3V5c0-1.66-1.34-3-3-3S9 3.34 9 5v6c0 1.66 1.34 3 3 3zm-1-9c0-.55.45-1 1-1s1 .45 1 1v6c0 .55-.45 1-1 1s-1-.45-1-1V5zm6 6c0 2.76-2.24 5-5 5s-5-2.24-5-5H5c0 3.53 2.61 6.43 6 6.92V21h2v-3.08c3.39-.49 6-3.39 6-6.92h-2z"/>
                                </svg>
                              </div>
                              <span className={`text-xs font-medium ${isLead ? "text-[#6B5E55]" : "text-white/80"}`}>
                                {msg.content === "🎤 [áudio enviado]" ? "Áudio enviado" : "Áudio recebido"}
                              </span>
                            </div>
                            {mediaSrc && (
                              <audio src={mediaSrc} controls preload="metadata" className="w-64 max-w-full" />
                            )}
                            {audioTranscript && audioTranscript !== "[áudio enviado]" && (
                              <p className={`text-sm italic leading-relaxed pl-9 ${isLead ? "text-[#1A1410]" : "text-white/90"}`}>
                                &ldquo;{audioTranscript}&rdquo;
                              </p>
                            )}
                          </div>
                        ) : (
                          <p className={`text-sm whitespace-pre-wrap break-words leading-relaxed ${isLead ? "text-[#1A1410]" : isHuman ? "text-white" : "text-[#1A1410]"}`}>{msg.content}</p>
                        )}
                        <p className={`text-[10px] mt-1.5 text-right ${isLead ? "text-[#6B5E55]" : isHuman ? "text-white/60" : "text-[#6B5E55]"}`}>
                          {msg.timestamp ? new Date(msg.timestamp).toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" }) : ""}
                        </p>
                      </div>
                      {isLead && (
                        <button
                          onClick={() => handleDeleteMessage(detail.phone, i)}
                          className="opacity-0 group-hover:opacity-100 transition-opacity p-1 rounded hover:bg-red-500/20 text-[#6B5E55] hover:text-red-400 flex-shrink-0 mb-1"
                          title="Apagar mensagem"
                        >
                          <svg viewBox="0 0 24 24" className="w-3.5 h-3.5 fill-current">
                            <path d="M6 19c0 1.1.9 2 2 2h8c1.1 0 2-.9 2-2V7H6v12zM19 4h-3.5l-1-1h-5l-1 1H5v2h14V4z"/>
                          </svg>
                        </button>
                      )}
                    </div>
                  );
                })
              )}
              <div ref={messagesEndRef} />
            </div>

            {/* Input de mensagem — sempre disponível */}
            <div className="bg-[#E8E4DF] border-t border-[#D4CFC9] px-4 py-3 flex flex-col gap-2">

              {/* Box de sugestões do agente */}
              {showSuggestions && (
                <div className="bg-[#1A1A2A] border border-[#CC3300]/30 rounded-xl p-3 flex flex-col gap-2">
                  <div className="flex items-center justify-between">
                    <span className="text-[#A0A0C0] text-[11px] font-semibold uppercase tracking-wider">
                      {loadingSuggestions ? "Agente analisando..." : "Sugestões do Agente"}
                    </span>
                    <div className="flex items-center gap-2">
                      <button onClick={handleSuggest} disabled={loadingSuggestions} title="Gerar novas sugestões" className="text-[#CC3300] hover:text-[#FF4422] text-[11px] disabled:opacity-50">↻ Refazer</button>
                      <button onClick={() => setShowSuggestions(false)} className="text-[#6B5E55] hover:text-[#F87171] text-[13px] font-bold">×</button>
                    </div>
                  </div>
                  {loadingSuggestions ? (
                    <div className="flex gap-1.5">
                      {[1,2,3].map(i => <div key={i} className="h-7 bg-[#252538] rounded-lg flex-1 animate-pulse" />)}
                    </div>
                  ) : (
                    <div className="flex flex-col gap-1.5">
                      {suggestionAnalise && (
                        <p className="text-[#7070A0] text-[10px] italic px-1 pb-0.5 border-b border-[#A0A0C0]/10">{suggestionAnalise}</p>
                      )}
                      {suggestions.map((s, i) => (
                        <button
                          key={i}
                          onClick={() => { setMessage(s); setShowSuggestions(false); textareaRef.current?.focus(); }}
                          className="text-left text-[#C0C0E0] text-xs bg-[#252538] hover:bg-[#303050] border border-[#A0A0C0]/10 rounded-lg px-3 py-2 transition-colors"
                        >
                          {s}
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              )}

              {!detail.ai_paused && (
                <div className="flex items-center gap-2 text-[10px] text-[#6B5E55]">
                  <div className="w-1.5 h-1.5 rounded-full bg-[#CC3300] animate-pulse flex-shrink-0" />
                  IA ativa — ao enviar uma mensagem você assume a conversa automaticamente
                </div>
              )}
              {/* Preview de arquivo selecionado */}
              {selectedFile && (
                <div className="flex items-center gap-2 bg-[#D4CFC9] rounded-xl px-3 py-2 text-sm">
                  <span className="text-lg">{selectedFile.type.startsWith("image/") ? "🖼" : "📄"}</span>
                  <span className="text-[#1A1410] flex-1 truncate text-xs">{selectedFile.name}</span>
                  <button onClick={handleSendMedia} disabled={sendingMedia} className="bg-[#CC3300] hover:bg-[#AA2200] disabled:opacity-50 text-white text-xs font-semibold px-3 py-1 rounded-lg transition-colors">
                    {sendingMedia ? "Enviando..." : "Enviar"}
                  </button>
                  <button onClick={() => setSelectedFile(null)} aria-label="Cancelar envio de arquivo" className="text-[#6B5E55] hover:text-[#CC3300] transition-colors" title="Cancelar">
                    <svg viewBox="0 0 24 24" className="w-4 h-4 fill-current"><path d="M19 6.41L17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z"/></svg>
                  </button>
                </div>
              )}
              {/* Preview de áudio gravado — só envia de fato ao confirmar */}
              {recordedAudio && (
                <div className="flex items-center gap-2 bg-[#D4CFC9] rounded-xl px-3 py-2 text-sm">
                  <audio src={recordedAudio.url} controls preload="metadata" className="flex-1 h-8" />
                  <button onClick={handleSendRecordedAudio} disabled={sendingAudio} className="bg-[#CC3300] hover:bg-[#AA2200] disabled:opacity-50 text-white text-xs font-semibold px-3 py-1 rounded-lg transition-colors flex-shrink-0">
                    {sendingAudio ? "Enviando..." : "Enviar"}
                  </button>
                  <button onClick={handleDiscardAudio} disabled={sendingAudio} aria-label="Descartar áudio" title="Descartar" className="text-[#6B5E55] hover:text-red-500 transition-colors disabled:opacity-50 flex-shrink-0">
                    <svg viewBox="0 0 24 24" className="w-4 h-4 fill-current"><path d="M6 19c0 1.1.9 2 2 2h8c1.1 0 2-.9 2-2V7H6v12zM19 4h-3.5l-1-1h-5l-1 1H5v2h14V4z"/></svg>
                  </button>
                </div>
              )}
              <div className="flex items-end gap-2">
                {/* Input de arquivo oculto */}
                <input
                  ref={fileInputRef}
                  type="file"
                  accept="image/jpeg,image/png,image/webp,image/gif,video/mp4,video/quicktime,video/3gpp,video/webm,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                  className="hidden"
                  onChange={e => { const f = e.target.files?.[0]; if (f) { setSelectedFile(f); setMessage(""); } e.target.value = ""; }}
                />
                {/* Botão clipe */}
                <PollComposer instanceId={instanceId} phone={selected!}
                  initialPreview={pollToUse} onConsumePreview={() => setPollToUse(null)}
                  onSent={async () => { await loadDetail(selected!); await loadList(); }}
                  onManage={() => setFilter("polls")} />
                <button
                  type="button"
                  onClick={() => fileInputRef.current?.click()}
                  disabled={recording || sendingAudio}
                  title="Enviar imagem ou documento"
                  className="w-10 h-10 rounded-full flex items-center justify-center flex-shrink-0 bg-[#D4CFC9] hover:bg-[#CC3300]/20 text-[#6B5E55] hover:text-[#CC3300] transition-all disabled:opacity-40"
                >
                  <svg viewBox="0 0 24 24" className="w-4 h-4 fill-current">
                    <path d="M16.5 6v11.5c0 2.21-1.79 4-4 4s-4-1.79-4-4V5c0-1.38 1.12-2.5 2.5-2.5s2.5 1.12 2.5 2.5v10.5c0 .55-.45 1-1 1s-1-.45-1-1V6H10v9.5c0 1.38 1.12 2.5 2.5 2.5s2.5-1.12 2.5-2.5V5c0-2.21-1.79-4-4-4S7 2.79 7 5v12.5c0 3.04 2.46 5.5 5.5 5.5s5.5-2.46 5.5-5.5V6h-1.5z"/>
                  </svg>
                </button>
                <div className="relative flex-1">
                  <textarea
                    ref={textareaRef}
                    value={message}
                    onChange={e => setMessage(e.target.value)}
                    onKeyDown={handleKeyDown}
                    placeholder="Digite uma mensagem... (Enter para enviar)"
                    rows={2}
                    className="w-full bg-[#D4CFC9] text-[#1A1410] placeholder-[#6B5E55] rounded-xl px-4 py-2.5 pr-10 text-sm resize-none outline-none focus:ring-1 focus:ring-[#CC3300]"
                  />
                  <button
                    type="button"
                    onClick={() => setShowEmoji(v => !v)}
                    className="absolute right-2 bottom-2 text-lg leading-none text-[#6B5E55] hover:text-[#1A1410] transition-colors"
                    title="Emojis"
                  >😊</button>
                  {showEmoji && (
                    <div ref={emojiPickerRef} className="absolute bottom-12 right-0 z-50 bg-[#E8E4DF] border border-[#D4CFC9] rounded-2xl shadow-2xl p-3 w-72 max-w-[calc(100vw-2rem)]">
                      {[
                        { cat: "Positivo",    emojis: ["😀","😃","😄","😁","😆","🤩","😍","🥳","👏","🙌","👍","❤️","🔥","⭐","✅","💯"] },
                        { cat: "Neutro",      emojis: ["😊","🙂","😐","🤔","👀","💬","📱","💡","📌","🗓️","⏰","📞","🤝","📊","🎯","💼"] },
                        { cat: "Negativo",    emojis: ["😕","😟","😮","😬","🤦","❌","⚠️","🚫","😴","👎","😤","💸","🙅","😣","😥","⛔"] },
                        { cat: "Diversos",    emojis: ["🚀","💎","🌟","🏆","🎁","🛒","💰","📈","🤖","🌎","🏠","🏋️","🎉","🎊","🍀","✨"] },
                      ].map(({ cat, emojis }) => (
                        <div key={cat} className="mb-2">
                          <p className="text-[10px] text-[#6B5E55] uppercase tracking-wide mb-1">{cat}</p>
                          <div className="grid grid-cols-8 gap-0.5">
                            {emojis.map(e => (
                              <button
                                key={e}
                                type="button"
                                onClick={() => { insertEmoji(e); setShowEmoji(false); }}
                                className="text-lg w-8 h-8 flex items-center justify-center rounded-lg hover:bg-[#D4CFC9] transition-colors"
                              >{e}</button>
                            ))}
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
                {/* Botão microfone */}
                <button
                  onMouseDown={handleMicStart}
                  onMouseUp={handleMicStop}
                  onTouchStart={e => { e.preventDefault(); handleMicStart(); }}
                  onTouchEnd={e => { e.preventDefault(); handleMicStop(); }}
                  onContextMenu={e => e.preventDefault()}
                  disabled={sendingAudio || !!message.trim() || !!recordedAudio}
                  title={recording ? "Solte para enviar" : "Segurar para gravar áudio"}
                  className={`w-10 h-10 rounded-full flex items-center justify-center transition-all flex-shrink-0 ${
                    recording
                      ? "bg-[#F87171] scale-110 animate-pulse"
                      : sendingAudio
                        ? "bg-[#D4CFC9] text-[#6B5E55]"
                        : message.trim()
                          ? "bg-[#D4CFC9] text-[#3D4F5A] cursor-not-allowed"
                          : "bg-[#D4CFC9] hover:bg-[#CC3300]/20 text-[#6B5E55] hover:text-[#CC3300]"
                  }`}
                >
                  {sendingAudio ? (
                    <svg className="w-4 h-4 animate-spin text-[#6B5E55]" viewBox="0 0 24 24" fill="none">
                      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"/>
                      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z"/>
                    </svg>
                  ) : (
                    <svg viewBox="0 0 24 24" className="w-4 h-4 fill-current">
                      <path d="M12 14c1.66 0 3-1.34 3-3V5c0-1.66-1.34-3-3-3S9 3.34 9 5v6c0 1.66 1.34 3 3 3zm-1-9c0-.55.45-1 1-1s1 .45 1 1v6c0 .55-.45 1-1 1s-1-.45-1-1V5zm6 6c0 2.76-2.24 5-5 5s-5-2.24-5-5H5c0 3.53 2.61 6.43 6 6.92V21h2v-3.08c3.39-.49 6-3.39 6-6.92h-2z"/>
                    </svg>
                  )}
                </button>
                {/* Botão enviar texto */}
                <button
                  onClick={handleSend}
                  disabled={!message.trim() || sending}
                  className="bg-[#CC3300] hover:bg-[#991F00] disabled:bg-[#D4CFC9] disabled:text-[#6B5E55] text-[#F0EDE8] font-bold w-10 h-10 rounded-full flex items-center justify-center transition-colors flex-shrink-0"
                >
                  {sending ? (
                    <svg className="w-4 h-4 animate-spin" viewBox="0 0 24 24" fill="none">
                      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"/>
                      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z"/>
                    </svg>
                  ) : (
                    <svg viewBox="0 0 24 24" className="w-4 h-4 fill-current">
                      <path d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z"/>
                    </svg>
                  )}
                </button>
              </div>
              {recording && (
                <p className="text-xs text-[#F87171] flex items-center gap-1.5 animate-pulse">
                  <span className="w-2 h-2 rounded-full bg-[#F87171] inline-block" />
                  Gravando... solte para enviar
                </p>
              )}
            </div>
          </div>
        )}
      </div>

      {/* Modal de Resumo IA */}
      {summary && (
        <div className="fixed inset-0 bg-black/70 backdrop-blur-sm flex items-center justify-center z-[200] p-4" onClick={() => setSummary(null)}>
          <div className="bg-[#E8E4DF] rounded-2xl border border-[#D4CFC9] p-6 max-w-md w-full shadow-2xl" onClick={e => e.stopPropagation()}>
            <div className="flex items-center justify-between mb-5">
              <div>
                <h2 className="text-[#1A1410] font-bold text-base">{summary.nome}</h2>
                <p className="text-[#6B5E55] text-xs">Análise gerada por IA</p>
              </div>
              <button onClick={() => setSummary(null)} className="text-[#6B5E55] hover:text-[#1A1410] w-8 h-8 flex items-center justify-center rounded-lg hover:bg-[#D4CFC9] transition-colors">
                <svg viewBox="0 0 24 24" className="w-4 h-4 fill-current"><path d="M19 6.41L17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z"/></svg>
              </button>
            </div>

            <div className="space-y-3">
              {/* Interesse */}
              <div className="bg-[#F0EDE8] rounded-xl p-3">
                <p className="text-[#6B5E55] text-xs mb-1 font-semibold uppercase tracking-wide">Interesse</p>
                <p className="text-[#1A1410] text-sm">{summary.interesse}</p>
              </div>

              {/* Score + Sentimento */}
              <div className="grid grid-cols-2 gap-3">
                <div className="bg-[#F0EDE8] rounded-xl p-3 text-center">
                  <p className="text-[#6B5E55] text-xs mb-1 font-semibold uppercase tracking-wide">Score</p>
                  <p className={`text-2xl font-bold ${scoreColor(summary.score_atual)}`}>
                    {summary.score_atual ?? "—"}
                  </p>
                  {summary.score_estimado !== summary.score_atual && (
                    <p className="text-[#6B5E55] text-[10px]">estimado: {summary.score_estimado}</p>
                  )}
                </div>
                <div className="bg-[#F0EDE8] rounded-xl p-3 text-center">
                  <p className="text-[#6B5E55] text-xs mb-1 font-semibold uppercase tracking-wide">Sentimento</p>
                  <p className={`text-sm font-bold ${
                    summary.sentimento === "positivo" ? "text-[#CC3300]" :
                    summary.sentimento === "negativo" ? "text-[#F87171]" : "text-[#F0B429]"
                  }`}>
                    {summary.sentimento === "positivo" ? "Positivo" : summary.sentimento === "negativo" ? "Negativo" : "Neutro"}
                  </p>
                </div>
              </div>

              {/* Temas */}
              {summary.principais_temas.length > 0 && (
                <div className="bg-[#F0EDE8] rounded-xl p-3">
                  <p className="text-[#6B5E55] text-xs mb-2 font-semibold uppercase tracking-wide">Temas discutidos</p>
                  <div className="flex flex-wrap gap-1.5">
                    {summary.principais_temas.map((t, i) => (
                      <span key={i} className="text-xs bg-[#D4CFC9] text-[#1A1410] px-2.5 py-1 rounded-full">{t}</span>
                    ))}
                  </div>
                </div>
              )}

              {/* Perguntas */}
              {summary.perguntas_feitas.length > 0 && (
                <div className="bg-[#F0EDE8] rounded-xl p-3">
                  <p className="text-[#6B5E55] text-xs mb-2 font-semibold uppercase tracking-wide">Perguntas do lead</p>
                  <ul className="space-y-1.5">
                    {summary.perguntas_feitas.map((q, i) => (
                      <li key={i} className="text-[#1A1410] text-xs flex gap-2">
                        <span className="text-[#CC3300] flex-shrink-0">›</span>
                        {q}
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              {/* Próxima ação */}
              <div className="bg-[#3D0A00] border border-[#CC3300]/20 rounded-xl p-3">
                <p className="text-[#6B5E55] text-xs mb-1 font-semibold uppercase tracking-wide">Próxima ação recomendada</p>
                <p className="text-[#CC3300] text-sm font-semibold">{summary.proxima_acao}</p>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Modal — Editar Lead */}
      {editCampanhaPhone && (
        <div className="fixed inset-0 z-[200] flex items-end sm:items-center justify-center bg-black/50 px-4 pt-16 pb-4">
          <div className="bg-[#F0EDE8] rounded-2xl border border-[#D4CFC9] w-full max-w-sm shadow-xl flex flex-col max-h-full overflow-hidden">
            <div className="px-5 py-4 border-b border-[#D4CFC9] flex items-center justify-between flex-shrink-0">
              <h2 className="text-[#1A1410] font-bold text-sm">Editar lead</h2>
              <button onClick={() => setEditCampanhaPhone(null)} className="text-[#6B5E55] hover:text-[#1A1410] text-lg leading-none">✕</button>
            </div>
            <div className="px-5 py-4 space-y-3 overflow-y-auto flex-1">
              {loadingLeadMeta ? (
                <div className="flex items-center justify-center py-4">
                  <svg className="w-4 h-4 text-[#6B5E55] animate-spin" viewBox="0 0 24 24" fill="none">
                    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"/>
                    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z"/>
                  </svg>
                </div>
              ) : (
                <>
                  {/* Dados do lead */}
                  <div>
                    <p className="text-[#6B5E55] text-[10px] font-semibold uppercase tracking-wide mb-2">Dados do lead</p>
                    <div className="space-y-2">
                      <input type="text" value={editLeadNome} onChange={e => setEditLeadNome(e.target.value)}
                        placeholder="Nome"
                        className="w-full bg-[#E8E4DF] border border-[#D4CFC9] rounded-lg px-3 py-2 text-sm text-[#1A1410] placeholder-[#6B5E55] focus:outline-none focus:border-[#CC3300]"
                      />
                      <input type="email" value={editLeadEmail} onChange={e => setEditLeadEmail(e.target.value)}
                        placeholder="Email"
                        className="w-full bg-[#E8E4DF] border border-[#D4CFC9] rounded-lg px-3 py-2 text-sm text-[#1A1410] placeholder-[#6B5E55] focus:outline-none focus:border-[#CC3300]"
                      />
                      <input type="text" value={editLeadId} onChange={e => setEditLeadId(e.target.value)}
                        placeholder="Lead ID (WhatsApp / Meta)"
                        className="w-full bg-[#E8E4DF] border border-[#D4CFC9] rounded-lg px-3 py-2 text-sm text-[#1A1410] placeholder-[#6B5E55] focus:outline-none focus:border-[#CC3300] font-mono"
                      />
                    </div>
                  </div>
                  {/* Endereço */}
                  <div>
                    <p className="text-[#6B5E55] text-[10px] font-semibold uppercase tracking-wide mb-2">Localização</p>
                    <div className="grid grid-cols-2 gap-2">
                      <input type="text" value={editLeadCidade} onChange={e => setEditLeadCidade(e.target.value)}
                        placeholder="Cidade"
                        className="bg-[#E8E4DF] border border-[#D4CFC9] rounded-lg px-3 py-2 text-sm text-[#1A1410] placeholder-[#6B5E55] focus:outline-none focus:border-[#CC3300]"
                      />
                      <input type="text" value={editLeadEstado} onChange={e => setEditLeadEstado(e.target.value)}
                        placeholder="Estado"
                        className="bg-[#E8E4DF] border border-[#D4CFC9] rounded-lg px-3 py-2 text-sm text-[#1A1410] placeholder-[#6B5E55] focus:outline-none focus:border-[#CC3300]"
                      />
                      <input type="text" value={editLeadPais} onChange={e => setEditLeadPais(e.target.value)}
                        placeholder="País"
                        className="bg-[#E8E4DF] border border-[#D4CFC9] rounded-lg px-3 py-2 text-sm text-[#1A1410] placeholder-[#6B5E55] focus:outline-none focus:border-[#CC3300]"
                      />
                      <input type="text" value={editLeadCep} onChange={e => setEditLeadCep(e.target.value)}
                        placeholder="CEP"
                        className="bg-[#E8E4DF] border border-[#D4CFC9] rounded-lg px-3 py-2 text-sm text-[#1A1410] placeholder-[#6B5E55] focus:outline-none focus:border-[#CC3300]"
                      />
                    </div>
                  </div>
                  {/* Campanha */}
                  <div>
                    <p className="text-[#6B5E55] text-[10px] font-semibold uppercase tracking-wide mb-2">Campanha</p>
                    <p className="text-[#6B5E55] text-xs mb-2">Se não pertencer a nenhuma, o lead será removido do Inbox SDR.</p>
                    {loadingCampaigns ? (
                      <div className="text-xs text-[#6B5E55]">Carregando campanhas...</div>
                    ) : (
                      <>
                        {campaignOptions.length > 0 && (
                          <select
                            value=""
                            onChange={e => { if (e.target.value) setEditCampanhaVal(e.target.value); }}
                            className="w-full bg-[#E8E4DF] border border-[#D4CFC9] rounded-lg px-3 py-2 text-sm text-[#1A1410] focus:outline-none focus:border-[#CC3300] mb-2"
                          >
                            <option value="">— Selecionar campanha Meta —</option>
                            {campaignOptions.map(c => <option key={c} value={c}>{c}</option>)}
                          </select>
                        )}
                        <input
                          type="text"
                          value={editCampanhaVal}
                          maxLength={512}
                          onChange={e => setEditCampanhaVal(e.target.value)}
                          placeholder="Ex: OPERA LANÇAMENTO, Indicação, Evento..."
                          className="w-full bg-[#E8E4DF] border border-[#D4CFC9] rounded-lg px-3 py-2 text-sm text-[#1A1410] placeholder-[#6B5E55] focus:outline-none focus:border-[#CC3300]"
                        />
                        {campaignSaveError && <p className="text-[#B42318] text-xs mt-2">{campaignSaveError}</p>}
                      </>
                    )}
                  </div>
                </>
              )}
            </div>
            <div className="px-5 py-4 border-t border-[#D4CFC9] flex gap-2 justify-between flex-shrink-0">
              <button
                onClick={() => handleSaveCampanha(true)}
                disabled={savingCampanha || savingLeadMeta}
                className="px-4 py-2 text-xs text-[#F87171] hover:text-white hover:bg-[#2D0A0A] rounded-lg transition-colors disabled:opacity-50"
              >
                Remover do Inbox
              </button>
              <div className="flex gap-2">
                <button onClick={() => setEditCampanhaPhone(null)} className="px-4 py-2 text-xs text-[#6B5E55] hover:text-[#1A1410] transition-colors">
                  Cancelar
                </button>
                <button
                  onClick={() => handleSaveCampanha(false)}
                  disabled={savingCampanha || savingLeadMeta || loadingLeadMeta}
                  className="px-4 py-2 bg-[#CC3300] hover:bg-[#991F00] disabled:opacity-50 text-white text-xs font-semibold rounded-lg transition-colors"
                >
                  {(savingCampanha || savingLeadMeta) ? "Salvando..." : "Salvar"}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Modal — Novo Lead */}
      {showNewLead && (
        <div className="fixed inset-0 z-[150] flex items-end sm:items-center justify-center bg-black/50 px-4 pt-16 pb-4">
          <div className="bg-[#F0EDE8] rounded-2xl border border-[#D4CFC9] w-full max-w-sm shadow-xl flex flex-col max-h-full overflow-hidden">
            <div className="px-5 py-3 border-b border-[#D4CFC9] flex items-center justify-between flex-shrink-0">
              <h2 className="text-[#1A1410] font-bold text-sm">+ Novo Lead</h2>
              <button onClick={closeNewLeadModal} className="text-[#6B5E55] hover:text-[#1A1410] text-lg leading-none">✕</button>
            </div>
            <div className="px-5 py-3 space-y-2 overflow-y-auto flex-1">
              {/* Campanha — obrigatória */}
              <div>
                <label className="text-[#6B5E55] text-xs font-semibold uppercase tracking-wider">Campanha *</label>
                {loadingCampaigns ? (
                  <div className="mt-1 w-full bg-[#E8E4DF] border border-[#D4CFC9] rounded-lg px-3 py-2 text-xs text-[#6B5E55]">Carregando campanhas...</div>
                ) : null}
                {!loadingCampaigns && campaignOptions.length > 0 && (
                  <select
                    value=""
                    onChange={e => { if (e.target.value) setNewLeadCampanha(e.target.value); }}
                    className="mt-1 w-full bg-[#E8E4DF] border border-[#D4CFC9] rounded-lg px-3 py-2 text-sm text-[#1A1410] focus:outline-none focus:border-[#CC3300]"
                  >
                    <option value="">— Selecionar campanha Meta —</option>
                    {campaignOptions.map(c => <option key={c} value={c}>{c}</option>)}
                  </select>
                )}
                {!loadingCampaigns && (
                  <input
                    type="text"
                    value={newLeadCampanha}
                    onChange={e => setNewLeadCampanha(e.target.value)}
                    placeholder="Ex: OPERA LANÇAMENTO, Indicação, Evento..."
                    className="mt-1 w-full bg-[#E8E4DF] border border-[#D4CFC9] rounded-lg px-3 py-2 text-sm text-[#1A1410] placeholder-[#6B5E55] focus:outline-none focus:border-[#CC3300]"
                  />
                )}
              </div>
              <div>
                <label className="text-[#6B5E55] text-xs font-semibold uppercase tracking-wider">Nome *</label>
                <input
                  type="text"
                  value={newLeadName}
                  onChange={e => setNewLeadName(e.target.value)}
                  placeholder="Ex: João Silva"
                  className="mt-1 w-full bg-[#E8E4DF] border border-[#D4CFC9] rounded-lg px-3 py-2 text-sm text-[#1A1410] placeholder-[#6B5E55] focus:outline-none focus:border-[#CC3300]"
                />
              </div>
              <div>
                <label className="text-[#6B5E55] text-xs font-semibold uppercase tracking-wider">Telefone * <span className="font-normal normal-case">(com DDD — com ou sem +55)</span></label>
                <input
                  type="tel"
                  value={newLeadPhone}
                  onChange={e => handleNewLeadPhoneChange(e.target.value)}
                  placeholder="Ex: 11999998888 ou 5511999998888"
                  className={`mt-1 w-full bg-[#E8E4DF] border rounded-lg px-3 py-2 text-sm text-[#1A1410] placeholder-[#6B5E55] focus:outline-none ${newLeadDuplicate ? "border-[#F0B429] focus:border-[#F0B429]" : "border-[#D4CFC9] focus:border-[#CC3300]"}`}
                />
                {newLeadDuplicate && (
                  <div className="mt-2 flex items-start gap-2 bg-[#2D1F00] border border-[#F0B429]/50 rounded-lg px-3 py-2">
                    <svg viewBox="0 0 24 24" className="w-4 h-4 fill-[#F0B429] flex-shrink-0 mt-0.5"><path d="M1 21h22L12 2 1 21zm12-3h-2v-2h2v2zm0-4h-2v-4h2v4z"/></svg>
                    <div className="min-w-0 flex-1">
                      <p className="text-[#F0B429] text-xs font-semibold">Número já cadastrado</p>
                      <p className="text-[#C09030] text-[11px] mt-0.5 truncate">
                        Lead: <span className="font-semibold">{newLeadDuplicate.name}</span>
                      </p>
                      <button
                        type="button"
                        onClick={() => { closeNewLeadModal(); setSelected(newLeadDuplicate.phone); }}
                        className="text-[#F0B429] text-[11px] underline hover:text-[#FFD080] mt-1"
                      >
                        Ver conversa existente →
                      </button>
                    </div>
                  </div>
                )}
              </div>
              <div>
                <label className="text-[#6B5E55] text-xs font-semibold uppercase tracking-wider">Email <span className="font-normal normal-case">(opcional)</span></label>
                <input
                  type="email"
                  value={newLeadEmail}
                  onChange={e => setNewLeadEmail(e.target.value)}
                  placeholder="Ex: joao@email.com"
                  className="mt-1 w-full bg-[#E8E4DF] border border-[#D4CFC9] rounded-lg px-3 py-2 text-sm text-[#1A1410] placeholder-[#6B5E55] focus:outline-none focus:border-[#CC3300]"
                />
              </div>
              <div className="flex gap-2">
                <div className="flex-1">
                  <label className="text-[#6B5E55] text-xs font-semibold uppercase tracking-wider">ID do Lead Meta <span className="font-normal normal-case">(opcional)</span></label>
                  <input
                    type="text"
                    value={newLeadgenId}
                    onChange={e => setNewLeadgenId(e.target.value)}
                    placeholder="Ex: 1384911087034253"
                    className="mt-1 w-full bg-[#E8E4DF] border border-[#D4CFC9] rounded-lg px-3 py-2 text-sm text-[#1A1410] placeholder-[#6B5E55] focus:outline-none focus:border-[#CC3300]"
                  />
                </div>
                <div className="w-28">
                  <label className="text-[#6B5E55] text-xs font-semibold uppercase tracking-wider">CEP <span className="font-normal normal-case">(opcional)</span></label>
                  <input
                    type="text"
                    value={newLeadCep}
                    onChange={e => setNewLeadCep(e.target.value)}
                    placeholder="00000-000"
                    className="mt-1 w-full bg-[#E8E4DF] border border-[#D4CFC9] rounded-lg px-3 py-2 text-sm text-[#1A1410] placeholder-[#6B5E55] focus:outline-none focus:border-[#CC3300]"
                  />
                </div>
              </div>
              <div>
                <label className="text-[#6B5E55] text-xs font-semibold uppercase tracking-wider">Primeira mensagem <span className="font-normal normal-case">(opcional)</span></label>
                <textarea
                  value={newLeadMsg}
                  onChange={e => setNewLeadMsg(e.target.value)}
                  placeholder="Olá! Vi seu interesse..."
                  rows={3}
                  className="mt-1 w-full bg-[#E8E4DF] border border-[#D4CFC9] rounded-lg px-3 py-2 text-sm text-[#1A1410] placeholder-[#6B5E55] focus:outline-none focus:border-[#CC3300] resize-none"
                />
              </div>
              {newLeadError && <p className="text-[#F85149] text-xs">{newLeadError}</p>}
            </div>
            <div className="px-5 py-3 border-t border-[#D4CFC9] flex gap-2 justify-end flex-shrink-0">
              <button
                onClick={closeNewLeadModal}
                className="px-4 py-2 text-xs text-[#6B5E55] hover:text-[#1A1410] transition-colors"
              >
                Cancelar
              </button>
              <button
                onClick={handleCreateNewLead}
                disabled={creatingLead || !newLeadName.trim() || !newLeadPhone.trim() || !newLeadCampanha.trim()}
                className="px-4 py-2 bg-[#CC3300] hover:bg-[#991F00] disabled:opacity-50 text-white text-xs font-semibold rounded-lg transition-colors"
              >
                {creatingLead ? "Criando..." : "Criar Lead"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
