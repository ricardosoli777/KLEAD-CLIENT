"use client";
import { useState } from "react";
import { useRouter } from "next/navigation";
import { clearAuthToken, getAuthToken } from "@/lib/auth";

export default function ChangePasswordPage() {
  const router = useRouter();
  const [current, setCurrent]     = useState("");
  const [newPass, setNewPass]     = useState("");
  const [confirm, setConfirm]     = useState("");
  const [error, setError]         = useState("");
  const [loading, setLoading]     = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    if (newPass !== confirm) { setError("As senhas não coincidem."); return; }
    if (newPass.length < 8)  { setError("A senha deve ter pelo menos 8 caracteres."); return; }
    setLoading(true);
    try {
      const token = getAuthToken();
      const res = await fetch("/api/proxy/auth/change-password", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Auth-Token": token },
        body: JSON.stringify({ current, new_password: newPass }),
      });
      if (!res.ok) { setError("Senha atual incorreta."); return; }
      clearAuthToken();
      router.replace("/login");
    } catch {
      setError("Erro ao conectar. Tente novamente.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="min-h-screen bg-[#F0EDE8] flex items-center justify-center p-4">
      <div className="w-full max-w-sm">
        <div className="text-center mb-8">
          <div className="inline-flex items-center gap-2 mb-2">
            <span className="text-3xl font-bold text-[#CC3300]">⚡</span>
            <span className="text-2xl font-bold text-[#1A1410]">KLEAD</span>
          </div>
          <p className="text-[#6B5E55] text-sm">Primeiro acesso — defina sua senha</p>
        </div>
        <div className="bg-[#E8E4DF] rounded-2xl border border-[#D4CFC9] p-6">
          <form onSubmit={handleSubmit} className="space-y-4">
            <div>
              <label className="block text-xs font-semibold text-[#6B5E55] uppercase tracking-wide mb-1.5">Senha temporária</label>
              <input type="password" value={current} onChange={e => setCurrent(e.target.value)} required placeholder="••••••••"
                className="w-full bg-[#F0EDE8] border border-[#D4CFC9] rounded-xl px-4 py-3 text-[#1A1410] text-sm placeholder-[#6B5E55] focus:outline-none focus:border-[#CC3300] transition-colors" />
            </div>
            <div>
              <label className="block text-xs font-semibold text-[#6B5E55] uppercase tracking-wide mb-1.5">Nova senha</label>
              <input type="password" value={newPass} onChange={e => setNewPass(e.target.value)} required placeholder="Mínimo 8 caracteres"
                className="w-full bg-[#F0EDE8] border border-[#D4CFC9] rounded-xl px-4 py-3 text-[#1A1410] text-sm placeholder-[#6B5E55] focus:outline-none focus:border-[#CC3300] transition-colors" />
            </div>
            <div>
              <label className="block text-xs font-semibold text-[#6B5E55] uppercase tracking-wide mb-1.5">Confirmar nova senha</label>
              <input type="password" value={confirm} onChange={e => setConfirm(e.target.value)} required placeholder="••••••••"
                className="w-full bg-[#F0EDE8] border border-[#D4CFC9] rounded-xl px-4 py-3 text-[#1A1410] text-sm placeholder-[#6B5E55] focus:outline-none focus:border-[#CC3300] transition-colors" />
            </div>
            {error && <div className="bg-red-900/30 border border-red-700/50 rounded-xl px-4 py-3 text-red-400 text-sm">{error}</div>}
            <button type="submit" disabled={loading}
              className="w-full bg-[#CC3300] hover:bg-[#991F00] disabled:opacity-50 text-white font-semibold py-3 rounded-xl transition-colors text-sm">
              {loading ? "Salvando..." : "Definir senha e entrar"}
            </button>
          </form>
        </div>
      </div>
    </div>
  );
}
