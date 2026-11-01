"use client";
import { useState } from "react";

// Formulário de checkout embutido nas páginas individuais de plano
// (/planos/[slug]) — mesmo fluxo do modal da /plans: nome + email →
// POST /billing/checkout → redirect pro checkout seguro do Stripe.
export default function CheckoutForm({ plano, ciclo, cta }: { plano: string; ciclo: string; cta: string }) {
  const [nome, setNome] = useState("");
  const [email, setEmail] = useState("");
  const [error, setError] = useState("");
  const [sending, setSending] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setSending(true);
    try {
      const res = await fetch("/api/proxy/billing/checkout", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          email, nome, plano,
          ...(plano === "lite" && ciclo ? { ciclo } : {}),
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || !data.url) {
        setError(data.detail || "Erro ao iniciar pagamento.");
        setSending(false);
        return;
      }
      window.location.href = data.url;
    } catch {
      setError("Erro ao conectar. Tente novamente.");
      setSending(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-3">
      <div>
        <label className="block text-xs font-semibold text-[#6B5E55] uppercase tracking-wide mb-1.5">Nome</label>
        <input
          type="text" value={nome} onChange={e => setNome(e.target.value)} required
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
        className="w-full bg-[#CC3300] hover:bg-[#991F00] disabled:opacity-50 text-white font-semibold py-3.5 rounded-xl transition-colors text-sm"
      >
        {sending ? "Redirecionando..." : cta}
      </button>

      <p className="text-[#6B5E55] text-[11px] text-center">
        Checkout seguro do Stripe. Sua senha de acesso chega por email assim que o pagamento for confirmado.
      </p>
    </form>
  );
}
