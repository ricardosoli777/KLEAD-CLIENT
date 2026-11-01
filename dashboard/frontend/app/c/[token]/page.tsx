"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { getPublicConfirmation, respondPublicConfirmation, schedulePublicConfirmation, type ConfirmationOption } from "@/lib/api";

export default function PublicConfirmationPage() {
  const { token } = useParams<{ token: string }>();
  const [options, setOptions] = useState<ConfirmationOption[]>([]);
  const [selected, setSelected] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [scheduledFor, setScheduledFor] = useState("");
  const [scheduleValue, setScheduleValue] = useState("");
  const [savingSchedule, setSavingSchedule] = useState(false);

  useEffect(() => {
    getPublicConfirmation(token).then(data => {
      setOptions(data.options);
      setSelected(data.response_option_id || "");
      setScheduledFor(data.scheduled_for || "");
    }).catch(err => setError(err instanceof Error ? err.message : "Confirmação inválida."))
      .finally(() => setLoading(false));
  }, [token]);

  const choose = async (optionId: string) => {
    if (selected) return;
    setError("");
    try {
      const result = await respondPublicConfirmation(token, optionId);
      setSelected(result.option_id);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Não foi possível registrar.");
    }
  };

  const schedule = async () => {
    if (!scheduleValue || savingSchedule) return;
    setSavingSchedule(true);
    setError("");
    try {
      const result = await schedulePublicConfirmation(token, new Date(scheduleValue).toISOString());
      setScheduledFor(result.scheduled_for);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Não foi possível reservar o horário.");
    } finally {
      setSavingSchedule(false);
    }
  };

  const minSchedule = new Date(Date.now() + 60 * 60 * 1000);
  minSchedule.setMinutes(0, 0, 0);
  const minLocalValue = new Date(minSchedule.getTime() - minSchedule.getTimezoneOffset() * 60000).toISOString().slice(0, 16);

  return <main className="min-h-screen bg-[#F0EDE8] flex items-center justify-center p-5">
    <section className="w-full max-w-md bg-white border border-[#D4CFC9] rounded-3xl shadow-xl p-6">
      <h1 className="text-xl font-bold text-[#1A1410]">Confirmação</h1>
      <p className="text-sm text-[#6B5E55] mt-1 mb-5">Escolha somente uma opção abaixo.</p>
      {loading && <p className="text-sm text-[#6B5E55]">Carregando...</p>}
      <div className="space-y-3">
        {options.map(option => <button key={option.id} onClick={() => choose(option.id)} disabled={Boolean(selected)} className={`w-full rounded-2xl border px-4 py-4 text-left font-bold transition ${selected === option.id ? "border-[#CC3300] bg-[#FFF0EC] text-[#CC3300]" : "border-[#D4CFC9] text-[#1A1410] hover:border-[#CC3300]"}`}>
          <span className="mr-2">{option.emoji}</span>{option.label}
        </button>)}
      </div>
      {selected === "reschedule" && !scheduledFor && <div className="mt-5 rounded-2xl border border-[#D4CFC9] p-4">
        <h2 className="font-bold text-[#1A1410]">Escolha a nova data e horário</h2>
        <p className="mt-1 text-xs text-[#6B5E55]">O horário será reservado quando você confirmar.</p>
        <input type="datetime-local" min={minLocalValue} value={scheduleValue} onChange={(event) => setScheduleValue(event.target.value)} className="mt-3 w-full rounded-xl border border-[#D4CFC9] px-3 py-3 text-sm text-[#1A1410]" />
        <button type="button" onClick={schedule} disabled={!scheduleValue || savingSchedule} className="mt-3 w-full rounded-xl bg-[#CC3300] px-4 py-3 font-bold text-white disabled:opacity-40">{savingSchedule ? "Reservando..." : "Confirmar novo horário"}</button>
      </div>}
      {scheduledFor && <p className="mt-5 rounded-xl bg-[#FFF0EC] p-3 text-sm font-semibold text-[#CC3300]">Visita reagendada com sucesso para {new Date(scheduledFor).toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" })}.</p>}
      {selected && selected !== "reschedule" && <p className="mt-5 rounded-xl bg-[#FFF0EC] p-3 text-sm font-semibold text-[#CC3300]">Resposta registrada com sucesso.</p>}
      {error && <p className="mt-5 rounded-xl bg-[#FFF0F0] p-3 text-sm text-[#D9534F]">{error}</p>}
    </section>
  </main>;
}
