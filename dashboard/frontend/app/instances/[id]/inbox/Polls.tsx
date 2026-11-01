"use client";

import { useCallback, useEffect, useState } from "react";
import {
  createInboxPoll, deleteInboxPoll, duplicateInboxPoll, getInboxPollCapabilities,
  getInboxPollResults, InboxPollInput, InboxPollQuestion, InboxPollTemplate, InboxPollVote,
  listInboxPolls, sendInboxPoll, updateInboxPoll,
} from "@/lib/api";

const emptyInput = (): InboxPollInput => ({
  name: "", question: "", options: ["", ""], questions: [{ question: "", options: ["", ""] }],
  allow_multiple_answers: false, is_default: false,
});

const pollOptions = (questions: InboxPollQuestion[]) => questions.flatMap(item =>
  item.options.map(option => option.trim()));

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : "Não foi possível concluir a operação.";
}

function PollEditor({ initial, onClose, onSaved }: {
  initial: InboxPollTemplate | null;
  onClose: () => void;
  onSaved: () => Promise<void>;
}) {
  const [form, setForm] = useState<InboxPollInput>(() => initial ? {
    name: initial.name, question: initial.question, options: [...initial.options_json],
    questions: (initial.questions?.length ? initial.questions : [{ question: initial.question, options: initial.options_json }])
      .map(item => ({ question: item.question, options: [...item.options] })),
    allow_multiple_answers: initial.allow_multiple_answers, is_default: initial.is_default,
  } : emptyInput());
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const combinedOptions = pollOptions(form.questions);
  const valid = !!form.name.trim() && form.questions.length > 0 && combinedOptions.length <= 12
    && combinedOptions.every(option => option.length <= 100)
    && new Set(combinedOptions.map(option => option.toLocaleLowerCase())).size === combinedOptions.length
    && form.questions.every(item =>
    !!item.question.trim() && item.question.trim().length <= 255
      && item.options.length >= 2 && item.options.length <= 12
      && item.options.every(option => !!option.trim() && option.trim().length <= 100)
      && new Set(item.options.map(option => option.trim().toLocaleLowerCase())).size === item.options.length);

  const updateQuestion = (index: number, patch: Partial<InboxPollQuestion>) => setForm(current => ({
    ...current, questions: current.questions.map((item, i) => i === index ? { ...item, ...patch } : item),
  }));
  const updateOption = (questionIndex: number, optionIndex: number, text: string) => setForm(current => ({
    ...current, questions: current.questions.map((item, i) => i === questionIndex ? {
      ...item, options: item.options.map((option, j) => j === optionIndex ? text : option),
    } : item),
  }));
  const moveItem = (questionIndex: number, optionIndex: number | null, direction: number) => setForm(current => {
    if (optionIndex === null) {
      const questions = [...current.questions];
      const other = questionIndex + direction;
      if (other < 0 || other >= questions.length) return current;
      [questions[questionIndex], questions[other]] = [questions[other], questions[questionIndex]];
      return { ...current, questions };
    }
    return { ...current, questions: current.questions.map((item, i) => {
      if (i !== questionIndex) return item;
      const options = [...item.options];
      const other = optionIndex + direction;
      if (other < 0 || other >= options.length) return item;
      [options[optionIndex], options[other]] = [options[other], options[optionIndex]];
      return { ...item, options };
    }) };
  });
  const save = async () => {
    if (!valid || saving) return;
    setSaving(true); setError("");
    try {
      const questions = form.questions.map(item => ({ question: item.question.trim(), options: item.options.map(o => o.trim()) }));
      const clean = { ...form, name: form.name.trim(), questions,
        question: questions[0].question, options: questions[0].options };
      if (initial) await updateInboxPoll(initial.id, clean);
      else await createInboxPoll(clean);
      await onSaved();
      onClose();
    } catch (err) { setError(errorText(err)); }
    finally { setSaving(false); }
  };

  return <div className="fixed inset-0 z-[210] bg-black/70 p-4 flex items-center justify-center" onClick={onClose}>
    <div role="dialog" aria-modal="true" aria-label={initial ? "Editar enquete" : "Nova enquete"}
      className="w-full max-w-2xl max-h-[90vh] overflow-y-auto rounded-2xl bg-[#E8E4DF] border border-[#D4CFC9] p-5 sm:p-6 shadow-2xl"
      onClick={event => event.stopPropagation()}>
      <div className="flex items-center justify-between mb-5">
        <h2 className="text-lg font-bold text-[#1A1410]">{initial ? "Editar enquete" : "Nova enquete"}</h2>
        <button type="button" onClick={onClose} aria-label="Fechar" className="text-[#6B5E55] hover:text-[#1A1410]">✕</button>
      </div>
      <div className="space-y-4">
        <label className="block text-xs font-semibold text-[#1A1410]">Nome interno
          <input value={form.name} maxLength={255} onChange={e => setForm({ ...form, name: e.target.value })}
            placeholder="Qualificação inicial" className="mt-1 w-full rounded-xl bg-[#F0EDE8] border border-[#D4CFC9] p-3 text-sm outline-none focus:border-[#CC3300]" />
        </label>
        {form.questions.map((item, questionIndex) => <section key={questionIndex} className="rounded-xl border border-[#D4CFC9] bg-[#F0EDE8] p-4">
          <div className="flex items-center justify-between gap-2 mb-3">
            <h3 className="text-sm font-bold text-[#1A1410]">Grupo {questionIndex + 1}</h3>
            <div className="flex items-center gap-2 text-xs">
              <button type="button" onClick={() => moveItem(questionIndex, null, -1)} disabled={questionIndex === 0} aria-label={`Subir pergunta ${questionIndex + 1}`} className="disabled:opacity-30 text-[#6B5E55]">↑</button>
              <button type="button" onClick={() => moveItem(questionIndex, null, 1)} disabled={questionIndex === form.questions.length - 1} aria-label={`Descer pergunta ${questionIndex + 1}`} className="disabled:opacity-30 text-[#6B5E55]">↓</button>
              <button type="button" onClick={() => setForm(current => ({ ...current, questions: current.questions.filter((_, i) => i !== questionIndex) }))}
                disabled={form.questions.length <= 1} aria-label={`Remover pergunta ${questionIndex + 1}`} className="disabled:opacity-30 text-[#F87171]">Remover</button>
            </div>
          </div>
          <label className="block text-xs font-semibold text-[#1A1410]">Rótulo / descrição do grupo
            <textarea value={item.question} maxLength={255} onChange={e => updateQuestion(questionIndex, { question: e.target.value })}
              rows={2} placeholder="Ex.: Tipo de imóvel"
              className="mt-1 w-full rounded-xl bg-white border border-[#D4CFC9] p-3 text-sm outline-none focus:border-[#CC3300]" />
          </label>
          <div className="mt-3">
            <p className="text-xs font-semibold text-[#1A1410] mb-2">Opções ({item.options.length})</p>
            <div className="space-y-2">
              {item.options.map((option, optionIndex) => <div key={optionIndex} className="flex items-center gap-2">
                <span className="w-5 text-xs text-[#6B5E55]">{optionIndex + 1}.</span>
                <input value={option} maxLength={100} onChange={e => updateOption(questionIndex, optionIndex, e.target.value)}
                  aria-label={`Pergunta ${questionIndex + 1}, opção ${optionIndex + 1}`} className="min-w-0 flex-1 rounded-xl bg-white border border-[#D4CFC9] p-2.5 text-sm outline-none focus:border-[#CC3300]" />
                <button type="button" onClick={() => moveItem(questionIndex, optionIndex, -1)} disabled={optionIndex === 0} aria-label={`Subir opção ${optionIndex + 1}`} className="disabled:opacity-30 text-[#6B5E55]">↑</button>
                <button type="button" onClick={() => moveItem(questionIndex, optionIndex, 1)} disabled={optionIndex === item.options.length - 1} aria-label={`Descer opção ${optionIndex + 1}`} className="disabled:opacity-30 text-[#6B5E55]">↓</button>
                <button type="button" onClick={() => updateQuestion(questionIndex, { options: item.options.filter((_, i) => i !== optionIndex) })}
                  disabled={item.options.length <= 2} aria-label={`Remover opção ${optionIndex + 1}`} className="disabled:opacity-30 text-[#F87171]">✕</button>
              </div>)}
            </div>
            <button type="button" onClick={() => updateQuestion(questionIndex, { options: [...item.options, ""] })} disabled={item.options.length >= 12}
              className="mt-3 text-xs font-semibold text-[#CC3300] disabled:opacity-40">+ Adicionar opção</button>
          </div>
        </section>)}
        <button type="button" onClick={() => setForm(current => ({ ...current, questions: [...current.questions, { question: "", options: ["", ""] }] }))}
          className="rounded-xl border border-dashed border-[#CC3300] px-4 py-2 text-sm font-semibold text-[#CC3300]">+ Adicionar grupo</button>
        <p className="text-xs text-[#6B5E55]">Uma enquete nativa com até 12 opções no total. Cada opção será enviada exatamente como escrita (até 100 caracteres).</p>
        <label className="flex items-center gap-2 text-sm text-[#1A1410]">
          <input type="checkbox" checked={form.allow_multiple_answers} onChange={e => setForm({ ...form, allow_multiple_answers: e.target.checked })} />
          Permitir múltiplas respostas
        </label>
        <label className="flex items-center gap-2 text-sm text-[#1A1410]">
          <input type="checkbox" checked={form.is_default} onChange={e => setForm({ ...form, is_default: e.target.checked })} />
          Definir como padrão
        </label>
      </div>
      {error && <p role="alert" className="mt-4 text-sm text-[#B42318]">{error}</p>}
      <div className="flex justify-end gap-3 mt-6">
        <button type="button" onClick={onClose} className="px-4 py-2 text-sm text-[#6B5E55]">Cancelar</button>
        <button type="button" onClick={save} disabled={!valid || saving}
          className="px-4 py-2 rounded-xl bg-[#CC3300] text-white text-sm font-semibold disabled:opacity-40">
          {saving ? "Salvando..." : "Salvar enquete"}
        </button>
      </div>
    </div>
  </div>;
}

export function PollManager({ onBack, onUse }: { onBack: () => void; onUse: (poll: InboxPollTemplate) => void }) {
  const [polls, setPolls] = useState<InboxPollTemplate[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [editing, setEditing] = useState<InboxPollTemplate | null | "new">(null);
  const [busyId, setBusyId] = useState("");
  const refresh = useCallback(async () => {
    try { setPolls(await listInboxPolls()); setError(""); }
    catch (err) { setError(errorText(err)); }
    finally { setLoading(false); }
  }, []);
  useEffect(() => { void refresh(); }, [refresh]);

  const duplicate = async (poll: InboxPollTemplate) => {
    setBusyId(poll.id); setError("");
    try { await duplicateInboxPoll(poll.id); await refresh(); setNotice("Enquete duplicada."); }
    catch (err) { setError(errorText(err)); }
    finally { setBusyId(""); }
  };
  const remove = async (poll: InboxPollTemplate) => {
    if (!window.confirm("Tem certeza de que deseja excluir esta enquete?")) return;
    setBusyId(poll.id); setError("");
    try { await deleteInboxPoll(poll.id); await refresh(); setNotice("Enquete excluída."); }
    catch (err) { setError(errorText(err)); }
    finally { setBusyId(""); }
  };
  const setDefault = async (poll: InboxPollTemplate) => {
    setBusyId(poll.id); setError("");
    try { await updateInboxPoll(poll.id, { is_default: !poll.is_default }); await refresh(); setNotice(poll.is_default ? "Padrão removido." : "Enquete padrão atualizada."); }
    catch (err) { setError(errorText(err)); }
    finally { setBusyId(""); }
  };

  return <div className="h-full overflow-y-auto bg-[#F0EDE8] px-4 py-5 sm:px-8">
    <div className="max-w-5xl mx-auto">
      <div className="flex flex-wrap items-center justify-between gap-3 mb-6">
        <div>
          <button type="button" onClick={onBack} className="text-xs font-semibold text-[#6B5E55] hover:text-[#CC3300] mb-2">← Voltar às conversas</button>
          <h1 className="text-xl font-bold text-[#1A1410]">Enquetes</h1>
          <p className="text-sm text-[#6B5E55]">Crie uma vez e reutilize em qualquer conversa disponível.</p>
        </div>
        <button type="button" onClick={() => setEditing("new")} className="rounded-xl bg-[#CC3300] px-4 py-2.5 text-sm font-semibold text-white">+ Nova enquete</button>
      </div>
      {error && <p role="alert" className="mb-4 rounded-xl bg-[#FDE8E4] p-3 text-sm text-[#B42318]">{error}</p>}
      {notice && <p role="status" className="mb-4 rounded-xl bg-white p-3 text-sm text-[#1A1410]">{notice}</p>}
      {loading ? <p className="text-sm text-[#6B5E55]">Carregando enquetes...</p> : polls.length === 0 ?
        <div className="rounded-2xl border border-[#D4CFC9] bg-[#E8E4DF] p-8 text-center">
          <p className="text-lg font-semibold text-[#1A1410]">Nenhuma enquete criada</p>
          <p className="mt-1 text-sm text-[#6B5E55]">Crie uma enquete uma vez e reutilize em qualquer conversa.</p>
          <button type="button" onClick={() => setEditing("new")} className="mt-5 rounded-xl bg-[#CC3300] px-4 py-2 text-sm font-semibold text-white">Criar primeira enquete</button>
        </div> : <div className="grid gap-4 md:grid-cols-2">
          {polls.map(poll => <article key={poll.id} className="rounded-2xl border border-[#D4CFC9] bg-[#E8E4DF] p-5 shadow-sm">
            <div className="flex items-start justify-between gap-2">
              <h2 className="font-bold text-[#1A1410]">{poll.name}</h2>
              {poll.is_default && <span className="rounded-full bg-[#CC3300]/10 px-2 py-1 text-[10px] font-bold text-[#CC3300]">PADRÃO</span>}
            </div>
            <p className="mt-2 text-sm text-[#1A1410] break-words">{poll.question_count ?? poll.questions?.length ?? 1} grupos · {poll.questions?.[0]?.question ?? poll.question}</p>
            <p className="mt-2 text-xs text-[#6B5E55]">{poll.allow_multiple_answers ? "Múltiplas respostas" : "Resposta única"} na enquete</p>
            <div className="mt-4 flex flex-wrap gap-3 text-xs font-semibold">
              <button type="button" onClick={() => onUse(poll)} className="text-[#CC3300]">Usar</button>
              <button type="button" onClick={() => setEditing(poll)} className="text-[#1A1410]">Editar</button>
              <button type="button" disabled={busyId === poll.id} onClick={() => void duplicate(poll)} className="text-[#1A1410] disabled:opacity-40">Duplicar</button>
              <button type="button" disabled={busyId === poll.id} onClick={() => void setDefault(poll)} className="text-[#1A1410] disabled:opacity-40">{poll.is_default ? "Remover padrão" : "Definir padrão"}</button>
              <button type="button" disabled={busyId === poll.id} onClick={() => void remove(poll)} className="text-[#B42318] disabled:opacity-40">Excluir</button>
            </div>
          </article>)}
        </div>}
    </div>
    {editing && <PollEditor initial={editing === "new" ? null : editing} onClose={() => setEditing(null)} onSaved={refresh} />}
  </div>;
}

export function PollComposer({ instanceId, phone, onSent, onManage, initialPreview, onConsumePreview }: {
  instanceId: string; phone: string; onSent: () => Promise<void>; onManage: () => void;
  initialPreview?: InboxPollTemplate | null; onConsumePreview?: () => void;
}) {
  const [available, setAvailable] = useState(false);
  const [open, setOpen] = useState(false);
  const [polls, setPolls] = useState<InboxPollTemplate[]>([]);
  const [preview, setPreview] = useState<InboxPollTemplate | null>(null);
  const [requestId, setRequestId] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    getInboxPollCapabilities(instanceId).then(data => { if (active) setAvailable(data.native_poll); })
      .catch(() => { if (active) setAvailable(false); });
    return () => { active = false; };
  }, [instanceId]);
  useEffect(() => {
    if (initialPreview) {
      setPreview(initialPreview);
      setRequestId(crypto.randomUUID());
      setOpen(true);
      onConsumePreview?.();
    }
  }, [initialPreview, onConsumePreview]);
  if (!available) return null;
  const showPicker = async () => {
    setOpen(true); setError("");
    try { setPolls(await listInboxPolls()); }
    catch (err) { setError(errorText(err)); }
  };
  const send = async () => {
    if (!preview || sending) return;
    setSending(true); setError("");
    try {
      await sendInboxPoll(instanceId, phone, preview.id, requestId);
      setPreview(null); setOpen(false);
      await onSent();
    } catch (err) { setError(errorText(err)); }
    finally { setSending(false); }
  };
  return <>
    <button type="button" title="Enquete" aria-label="Enquete" onClick={() => void showPicker()}
      className="w-10 h-10 rounded-full flex items-center justify-center flex-shrink-0 bg-[#D4CFC9] hover:bg-[#CC3300]/20 text-[#6B5E55] hover:text-[#CC3300] transition-all">
      <svg viewBox="0 0 24 24" className="w-5 h-5 fill-current" aria-hidden="true"><path d="M3 3h2v18H3V3zm4 9h3v9H7v-9zm5-5h3v14h-3V7zm5-4h3v18h-3V3z"/></svg>
    </button>
    {open && <div className="fixed inset-0 z-[205] bg-black/70 p-4 flex items-center justify-center" onClick={() => { if (!sending) { setOpen(false); setPreview(null); } }}>
      <div role="dialog" aria-modal="true" aria-label={preview ? "Pré-visualização da enquete" : "Enviar enquete"}
        className="w-full max-w-md max-h-[85vh] overflow-y-auto rounded-2xl bg-[#E8E4DF] border border-[#D4CFC9] p-5 shadow-2xl"
        onClick={e => e.stopPropagation()}>
        <h2 className="text-lg font-bold text-[#1A1410] mb-4">{preview ? "Pré-visualização" : "Enviar enquete"}</h2>
        {error && <p role="alert" className="mb-3 text-sm text-[#B42318]">{error}</p>}
        {preview ? <>
          <p className="text-xs text-[#6B5E55]">Será enviada uma enquete nativa: {preview.name}.</p>
          <div className="mt-3 space-y-4">{(preview.questions?.length ? preview.questions : [{ question: preview.question, options: preview.options_json }]).map((item, questionIndex) => <section key={questionIndex} className="rounded-xl bg-[#F0EDE8] p-3">
            <p className="text-xs font-semibold text-[#6B5E55]">Grupo {questionIndex + 1}</p>
            <p className="mt-1 text-sm font-semibold text-[#1A1410]">{item.question}</p>
            <div className="mt-2 space-y-1">{item.options.map((option, index) =>
              <p key={index} className="text-sm text-[#1A1410]">{preview.allow_multiple_answers ? "☐" : "◯"} {option}</p>)}</div>
          </section>)}</div>
          <p className="mt-3 text-xs text-[#6B5E55]">{preview.allow_multiple_answers ? "Permite múltiplas respostas" : "Uma resposta na enquete"}</p>
          <div className="mt-6 flex justify-end gap-3">
            <button type="button" onClick={() => setPreview(null)} disabled={sending} className="text-sm text-[#6B5E55]">Voltar</button>
            <button type="button" onClick={() => void send()} disabled={sending} className="rounded-xl bg-[#CC3300] px-4 py-2 text-sm font-semibold text-white disabled:opacity-40">{sending ? "Enviando enquete..." : "Enviar enquete"}</button>
          </div>
        </> : <>
          <div className="space-y-2">{polls.map(poll => <button key={poll.id} type="button" onClick={() => { setPreview(poll); setRequestId(crypto.randomUUID()); }}
            className="w-full text-left rounded-xl border border-[#D4CFC9] bg-[#F0EDE8] p-3 hover:border-[#CC3300]">
            <span className="block text-sm font-semibold text-[#1A1410]">{poll.is_default ? "⭐ " : ""}{poll.name}</span>
            {poll.is_default && <span className="text-xs text-[#CC3300]">Enquete padrão</span>}
          </button>)}</div>
          {polls.length === 0 && !error && <p className="text-sm text-[#6B5E55]">Nenhuma enquete criada.</p>}
          <div className="mt-5 flex justify-between gap-3">
            <button type="button" onClick={() => { setOpen(false); onManage(); }} className="text-sm font-semibold text-[#CC3300]">+ Criar nova enquete</button>
            <button type="button" onClick={() => setOpen(false)} className="text-sm text-[#6B5E55]">Cancelar</button>
          </div>
        </>}
      </div>
    </div>}
  </>;
}

export function PollResults({ instanceId, phone, sendId }: { instanceId: string; phone: string; sendId: string }) {
  const [votes, setVotes] = useState<InboxPollVote[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const refresh = useCallback(async () => {
    setLoading(true); setError("");
    try {
      const result = await getInboxPollResults(instanceId, phone, sendId);
      setVotes(result.votes);
    } catch (err) { setError(errorText(err)); }
    finally { setLoading(false); }
  }, [instanceId, phone, sendId]);
  useEffect(() => { void refresh(); }, [refresh]);
  return <div className="mt-3 border-t border-white/25 pt-2 text-xs text-white/90">
    {votes.map(vote => <div key={vote.voter_key} className="mt-2">
      <p className="font-semibold">{vote.voter_name || vote.voter_key}</p>
      {vote.selected_options_json.map(option => <p key={option}>✓ {option}</p>)}
    </div>)}
    {votes.length === 0 && !loading && !error && <p>Ainda não há votos disponíveis.</p>}
    {error && <p role="alert">{error}</p>}
    <button type="button" onClick={() => void refresh()} disabled={loading} className="mt-2 underline disabled:opacity-50">
      {loading ? "Consultando votos..." : "Atualizar votos"}
    </button>
  </div>;
}
