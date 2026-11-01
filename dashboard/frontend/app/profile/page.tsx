"use client";
import { useEffect, useRef, useState } from "react";
import Image from "next/image";
import { useRouter } from "next/navigation";
import { getAuthToken } from "@/lib/auth";

type ProfileUser = {
  email: string;
  nome: string;
  avatar?: string;
  funcao: string;
  plano?: string;
  max_instances?: number;
  allowed_instances?: string[];
  telefone?: string;
  email_verified?: boolean;
  created_at?: string;
  totp_enabled?: boolean;
};

const planoNome: Record<string, string> = {
  user: "Básico", pro: "Pro", enterprise: "Enterprise", personalizado: "Personalizado",
};

const funcaoNome: Record<string, string> = {
  superadmin: "Conta mestre", admin: "Administrador", gerente: "Gerente", sdr: "SDR",
};

// Exibição amigável do telefone salvo normalizado (5511912345678 → (11) 91234-5678)
function formatTelefone(raw: string): string {
  const digits = raw.replace(/\D/g, "");
  if (digits.length === 13 && digits.startsWith("55")) {
    return `(${digits.slice(2, 4)}) ${digits.slice(4, 9)}-${digits.slice(9)}`;
  }
  return raw;
}

export default function ProfilePage() {
  const router = useRouter();
  const fileRef = useRef<HTMLInputElement>(null);
  const [user, setUser]         = useState<ProfileUser | null>(null);
  const [nome, setNome]         = useState("");
  const [avatar, setAvatar]     = useState("");
  const [telefone, setTelefone] = useState("");
  const [saved, setSaved]       = useState(false);
  const [error, setError]       = useState("");
  const [saving, setSaving]     = useState(false);

  // Reenvio do link de verificação de e-mail
  const [verifSending, setVerifSending] = useState(false);
  const [verifSent, setVerifSent]       = useState(false);
  const [verifError, setVerifError]     = useState("");

  // 2FA (autenticador)
  const [twoFaQr, setTwoFaQr]           = useState("");     // data URI do QR durante o setup
  const [twoFaSecret, setTwoFaSecret]   = useState("");     // segredo em texto (entrada manual)
  const [twoFaCode, setTwoFaCode]       = useState("");
  const [twoFaBusy, setTwoFaBusy]       = useState(false);
  const [twoFaError, setTwoFaError]     = useState("");
  const [backupCodes, setBackupCodes]   = useState<string[]>([]);
  const [disablePwd, setDisablePwd]     = useState("");
  const [disableCode, setDisableCode]   = useState("");
  const [showDisable, setShowDisable]   = useState(false);

  function token() { return getAuthToken(); }

  async function start2fa() {
    setTwoFaError(""); setTwoFaBusy(true); setBackupCodes([]);
    try {
      const res = await fetch("/api/proxy/auth/2fa/setup", { method: "POST", headers: { "X-Auth-Token": token() } });
      if (!res.ok) { setTwoFaError("Não foi possível iniciar. Tente novamente."); return; }
      const d = await res.json();
      setTwoFaQr(d.qr_svg); setTwoFaSecret(d.secret); setTwoFaCode("");
    } catch { setTwoFaError("Erro ao conectar."); } finally { setTwoFaBusy(false); }
  }

  async function confirm2fa(e: React.FormEvent) {
    e.preventDefault();
    setTwoFaError(""); setTwoFaBusy(true);
    try {
      const res = await fetch("/api/proxy/auth/2fa/confirm", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Auth-Token": token() },
        body: JSON.stringify({ code: twoFaCode.trim() }),
      });
      if (!res.ok) { setTwoFaError("Código inválido. Verifique o app e tente de novo."); return; }
      const d = await res.json();
      setBackupCodes(d.backup_codes || []);
      setTwoFaQr(""); setTwoFaSecret(""); setTwoFaCode("");
      setUser(u => u ? { ...u, totp_enabled: true } : u);
    } catch { setTwoFaError("Erro ao conectar."); } finally { setTwoFaBusy(false); }
  }

  async function disable2fa(e: React.FormEvent) {
    e.preventDefault();
    setTwoFaError(""); setTwoFaBusy(true);
    try {
      const res = await fetch("/api/proxy/auth/2fa/disable", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Auth-Token": token() },
        body: JSON.stringify({ password: disablePwd, code: disableCode.trim() }),
      });
      if (!res.ok) { setTwoFaError("Senha ou código incorretos."); return; }
      setUser(u => u ? { ...u, totp_enabled: false } : u);
      setShowDisable(false); setDisablePwd(""); setDisableCode("");
    } catch { setTwoFaError("Erro ao conectar."); } finally { setTwoFaBusy(false); }
  }

  useEffect(() => {
    fetch("/api/proxy/auth/me", { headers: { "X-Auth-Token": token() } })
      .then(r => r.ok ? r.json() : null)
      .then(d => {
        if (d) {
          setUser(d);
          setNome(d.nome);
          setAvatar(d.avatar || "");
          setTelefone(d.telefone ? formatTelefone(d.telefone) : "");
        }
      })
      .catch(() => {});
  }, []);

  function handleAvatarChange(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = ev => setAvatar(ev.target?.result as string);
    reader.readAsDataURL(file);
  }

  async function handleSave(e: React.FormEvent) {
    e.preventDefault();
    setError(""); setSaved(false); setSaving(true);
    try {
      const res = await fetch("/api/proxy/auth/profile", {
        method: "PUT",
        headers: { "Content-Type": "application/json", "X-Auth-Token": token() },
        body: JSON.stringify({ nome, avatar, telefone }),
      });
      if (res.ok) {
        const updated = await res.json().catch(() => null);
        if (updated) {
          setUser(updated);
          setTelefone(updated.telefone ? formatTelefone(updated.telefone) : "");
        }
        setSaved(true);
        setTimeout(() => setSaved(false), 2000);
      } else {
        const err = await res.json().catch(() => ({}));
        setError(err.detail ?? "Erro ao salvar.");
      }
    } catch {
      setError("Erro ao conectar. Tente novamente.");
    } finally {
      setSaving(false);
    }
  }

  async function handleResendVerification() {
    setVerifSending(true); setVerifError("");
    try {
      const res = await fetch("/api/proxy/auth/send-verification", {
        method: "POST",
        headers: { "X-Auth-Token": token() },
      });
      if (res.ok) {
        setVerifSent(true);
      } else {
        setVerifError(res.status === 429
          ? "Aguarde um minuto antes de reenviar."
          : "Não foi possível enviar. Tente novamente.");
      }
    } catch {
      setVerifError("Erro ao conectar. Tente novamente.");
    } finally {
      setVerifSending(false);
    }
  }

  if (!user) return <div className="min-h-screen bg-[#F0EDE8]" />;

  const initials = nome.split(" ").map((w: string) => w[0]).slice(0, 2).join("").toUpperCase() || user.email[0].toUpperCase();
  const instCount = user.allowed_instances?.length ?? 0;
  const emailVerified = user.email_verified !== false;
  const memberSince = user.created_at
    ? new Date(user.created_at).toLocaleDateString("pt-BR", { month: "long", year: "numeric" })
    : null;

  return (
    <div className="min-h-screen bg-[#F0EDE8]">
      <div className="max-w-lg mx-auto p-6">
        <div className="flex items-center gap-3 mb-6">
          <button onClick={() => router.back()} className="text-[#6B5E55] hover:text-[#1A1410] transition-colors">←</button>
          <h1 className="text-[#1A1410] text-xl font-bold">Meu Perfil</h1>
        </div>

        <div className="bg-[#E8E4DF] rounded-2xl border border-[#D4CFC9] overflow-hidden">
          {/* Cabeçalho do card — avatar + identidade + badges */}
          <div className="relative px-6 pt-6 pb-5 border-b border-[#D4CFC9]">
            <div className="absolute top-0 left-0 right-0 h-[3px]" style={{ background: "linear-gradient(90deg, #CC3300 0%, transparent 70%)" }} />
            <div className="flex items-center gap-4">
              <div
                onClick={() => fileRef.current?.click()}
                className="w-20 h-20 flex-shrink-0 rounded-full bg-[#CC3300] flex items-center justify-center cursor-pointer overflow-hidden hover:opacity-80 transition-opacity relative group"
              >
                {avatar ? (
                  <Image src={avatar} alt={nome} width={80} height={80} className="w-full h-full object-cover" unoptimized />
                ) : (
                  <span className="text-white text-2xl font-bold">{initials}</span>
                )}
                <div className="absolute inset-0 bg-black/40 flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity rounded-full">
                  <span className="text-white text-xs">Trocar</span>
                </div>
              </div>
              <input ref={fileRef} type="file" accept="image/*" className="hidden" onChange={handleAvatarChange} />
              <div className="min-w-0">
                <p className="text-[#1A1410] font-bold text-lg truncate">{nome || user.email}</p>
                <p className="text-[#6B5E55] text-xs truncate">{user.email}</p>
                <div className="flex flex-wrap items-center gap-1.5 mt-2">
                  <span className="inline-flex items-center gap-1 bg-[#F0EDE8] border border-[#D4CFC9] text-[#6B5E55] text-[10px] font-semibold px-2 py-0.5 rounded-full">
                    {funcaoNome[user.funcao] ?? user.funcao}
                  </span>
                  {(user.funcao === "admin" || user.funcao === "superadmin") && (
                    <span className="inline-flex items-center gap-1 bg-[#CC3300]/10 border border-[#CC3300]/30 text-[#CC3300] text-[10px] font-semibold px-2 py-0.5 rounded-full">
                      Plano {user.funcao === "superadmin" ? "Master" : (planoNome[user.plano ?? "user"] || user.plano)}
                    </span>
                  )}
                  {emailVerified ? (
                    <span className="inline-flex items-center gap-1 bg-green-100 border border-green-300 text-green-700 text-[10px] font-semibold px-2 py-0.5 rounded-full">
                      ✓ E-mail verificado
                    </span>
                  ) : (
                    <span className="inline-flex items-center gap-1 bg-amber-100 border border-amber-300 text-amber-700 text-[10px] font-semibold px-2 py-0.5 rounded-full">
                      E-mail não verificado
                    </span>
                  )}
                </div>
                {memberSince && (
                  <p className="text-[#6B5E55] text-[10px] mt-1.5">Membro desde {memberSince}</p>
                )}
              </div>
            </div>

            {/* Aviso + reenvio quando não verificado */}
            {!emailVerified && (
              <div className="mt-4 bg-amber-50 border border-amber-200 rounded-xl px-4 py-3">
                {verifSent ? (
                  <p className="text-amber-800 text-xs">
                    ✓ Link enviado para <strong>{user.email}</strong>. Confira sua caixa de entrada (e o spam).
                  </p>
                ) : (
                  <div className="flex items-center justify-between gap-3 flex-wrap">
                    <p className="text-amber-800 text-xs">Confirme seu e-mail para validar sua conta.</p>
                    <button
                      type="button"
                      onClick={handleResendVerification}
                      disabled={verifSending}
                      className="bg-amber-600 hover:bg-amber-700 disabled:opacity-50 text-white text-xs font-semibold px-3 py-1.5 rounded-lg transition-colors whitespace-nowrap"
                    >
                      {verifSending ? "Enviando..." : "Enviar link de verificação"}
                    </button>
                  </div>
                )}
                {verifError && <p className="text-red-700 text-xs mt-2">{verifError}</p>}
              </div>
            )}
          </div>

          {/* Formulário */}
          <form onSubmit={handleSave} className="p-6 space-y-4">
            <div>
              <label className="block text-xs font-semibold text-[#6B5E55] uppercase tracking-wide mb-1.5">Nome</label>
              <input
                value={nome} onChange={e => setNome(e.target.value)} required
                className="w-full bg-[#F0EDE8] border border-[#D4CFC9] rounded-xl px-4 py-3 text-[#1A1410] text-sm focus:outline-none focus:border-[#CC3300] transition-colors"
              />
            </div>
            <div>
              <label className="block text-xs font-semibold text-[#6B5E55] uppercase tracking-wide mb-1.5">Telefone (WhatsApp)</label>
              <input
                value={telefone}
                onChange={e => setTelefone(e.target.value)}
                type="tel"
                placeholder="(11) 91234-5678"
                className="w-full bg-[#F0EDE8] border border-[#D4CFC9] rounded-xl px-4 py-3 text-[#1A1410] text-sm placeholder-[#6B5E55]/60 focus:outline-none focus:border-[#CC3300] transition-colors"
              />
              <p className="text-[#6B5E55] text-[10px] mt-1">Celular brasileiro com DDD. Deixe vazio para remover.</p>
            </div>
            <div>
              <label className="block text-xs font-semibold text-[#6B5E55] uppercase tracking-wide mb-1.5">Email</label>
              <input value={user.email} disabled
                className="w-full bg-[#F0EDE8] border border-[#D4CFC9] rounded-xl px-4 py-3 text-[#6B5E55] text-sm cursor-not-allowed" />
            </div>
            <div>
              <label className="block text-xs font-semibold text-[#6B5E55] uppercase tracking-wide mb-1.5">Instâncias</label>
              <div className="w-full bg-[#F0EDE8] border border-[#D4CFC9] rounded-xl px-4 py-3 text-sm">
                {user.funcao === "admin" ? (
                  <span className="text-[#1A1410]">
                    <span className="text-[#CC3300] font-semibold">{instCount}/{user.max_instances ?? 1}</span> instâncias usadas · plano {planoNome[user.plano ?? "user"] || user.plano}
                  </span>
                ) : user.funcao === "superadmin" ? (
                  <span className="text-[#1A1410]">
                    <span className="text-[#CC3300] font-semibold">{instCount}</span> instância{instCount !== 1 ? "s" : ""} · conta mestre (sem limite)
                  </span>
                ) : instCount === 0 ? (
                  <span className="text-red-700">Sem instâncias atribuídas</span>
                ) : (
                  <span className="text-[#CC3300] font-semibold">{instCount}</span>
                )}
              </div>
            </div>
            {error && <p className="text-red-700 text-sm">{error}</p>}
            <button type="submit" disabled={saving}
              className="w-full bg-[#CC3300] hover:bg-[#991F00] disabled:opacity-50 text-white font-semibold py-3 rounded-xl transition-colors text-sm">
              {saving ? "Salvando..." : saved ? "✓ Salvo!" : "Salvar perfil"}
            </button>
          </form>
        </div>

        {/* Card: Verificação em duas etapas (2FA) */}
        <div className="bg-[#E8E4DF] rounded-2xl border border-[#D4CFC9] p-6 mt-4">
          <div className="flex items-center justify-between gap-3 mb-1">
            <h2 className="text-[#1A1410] font-bold text-sm">Verificação em duas etapas</h2>
            {user.totp_enabled ? (
              <span className="inline-flex items-center gap-1 bg-green-100 border border-green-300 text-green-700 text-[10px] font-semibold px-2 py-0.5 rounded-full">✓ Ativo</span>
            ) : (
              <span className="inline-flex items-center gap-1 bg-[#F0EDE8] border border-[#D4CFC9] text-[#6B5E55] text-[10px] font-semibold px-2 py-0.5 rounded-full">Desativado</span>
            )}
          </div>
          <p className="text-[#6B5E55] text-xs mb-4">Adiciona uma camada extra: além da senha, o login pede um código do seu app autenticador (Google Authenticator, Authy, etc.).</p>

          {twoFaError && <div className="mb-3 bg-[#3D0A00] border border-[#CC3300]/30 rounded-xl px-4 py-3 text-[#CC3300] text-sm">{twoFaError}</div>}

          {/* Backup codes recém-gerados — mostrados uma única vez */}
          {backupCodes.length > 0 && (
            <div className="mb-4 bg-[#F0EDE8] border border-[#D4CFC9] rounded-xl p-4">
              <p className="text-[#1A1410] text-xs font-bold mb-1">Códigos de backup — guarde agora</p>
              <p className="text-[#6B5E55] text-xs mb-3">Cada código funciona uma vez, caso você perca o acesso ao autenticador. Não serão mostrados de novo.</p>
              <div className="grid grid-cols-2 gap-2 font-mono text-sm text-[#1A1410]">
                {backupCodes.map(c => <span key={c} className="bg-white border border-[#D4CFC9] rounded px-2 py-1 text-center tracking-wider">{c}</span>)}
              </div>
            </div>
          )}

          {/* Fluxo de ativação */}
          {!user.totp_enabled && !twoFaQr && backupCodes.length === 0 && (
            <button onClick={start2fa} disabled={twoFaBusy}
              className="bg-[#CC3300] hover:bg-[#991F00] disabled:opacity-50 text-white font-semibold px-5 py-2.5 rounded-xl text-sm transition-colors">
              {twoFaBusy ? "..." : "Ativar autenticador"}
            </button>
          )}

          {twoFaQr && (
            <form onSubmit={confirm2fa} className="space-y-3">
              <p className="text-[#6B5E55] text-xs">1. Escaneie o QR no seu app autenticador:</p>
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src={twoFaQr} alt="QR code 2FA" className="w-44 h-44 bg-white rounded-xl p-2 border border-[#D4CFC9]" />
              <p className="text-[#6B5E55] text-xs">Ou digite manualmente: <span className="font-mono text-[#1A1410] break-all">{twoFaSecret}</span></p>
              <p className="text-[#6B5E55] text-xs">2. Digite o código gerado para confirmar:</p>
              <input value={twoFaCode} onChange={e => setTwoFaCode(e.target.value)} required placeholder="000000"
                className="w-full bg-[#F0EDE8] border border-[#D4CFC9] rounded-xl px-4 py-2.5 text-[#1A1410] text-center text-lg tracking-[0.3em] font-mono placeholder-[#6B5E55] focus:outline-none focus:border-[#CC3300]" />
              <div className="flex gap-2">
                <button type="submit" disabled={twoFaBusy}
                  className="flex-1 bg-[#CC3300] hover:bg-[#991F00] disabled:opacity-50 text-white font-semibold py-2.5 rounded-xl text-sm transition-colors">
                  {twoFaBusy ? "Confirmando..." : "Confirmar e ativar"}
                </button>
                <button type="button" onClick={() => { setTwoFaQr(""); setTwoFaSecret(""); setTwoFaCode(""); setTwoFaError(""); }}
                  className="px-4 border border-[#D4CFC9] text-[#6B5E55] hover:text-[#1A1410] rounded-xl text-sm transition-colors">
                  Cancelar
                </button>
              </div>
            </form>
          )}

          {/* Desativação */}
          {user.totp_enabled && !showDisable && (
            <button onClick={() => { setShowDisable(true); setTwoFaError(""); }}
              className="border border-[#F87171]/40 text-[#CC3300] hover:bg-[#3D0A00] font-semibold px-5 py-2.5 rounded-xl text-sm transition-colors">
              Desativar
            </button>
          )}
          {user.totp_enabled && showDisable && (
            <form onSubmit={disable2fa} className="space-y-3">
              <p className="text-[#6B5E55] text-xs">Confirme com sua senha e um código do autenticador para desativar:</p>
              <input type="password" value={disablePwd} onChange={e => setDisablePwd(e.target.value)} required placeholder="Senha atual"
                className="w-full bg-[#F0EDE8] border border-[#D4CFC9] rounded-xl px-4 py-2.5 text-[#1A1410] text-sm placeholder-[#6B5E55] focus:outline-none focus:border-[#CC3300]" />
              <input value={disableCode} onChange={e => setDisableCode(e.target.value)} required placeholder="Código do autenticador"
                className="w-full bg-[#F0EDE8] border border-[#D4CFC9] rounded-xl px-4 py-2.5 text-[#1A1410] text-sm placeholder-[#6B5E55] focus:outline-none focus:border-[#CC3300]" />
              <div className="flex gap-2">
                <button type="submit" disabled={twoFaBusy}
                  className="flex-1 bg-[#3D0A0A] border border-[#F87171]/40 text-[#F87171] hover:bg-[#F87171] hover:text-white font-semibold py-2.5 rounded-xl text-sm transition-colors">
                  {twoFaBusy ? "..." : "Confirmar desativação"}
                </button>
                <button type="button" onClick={() => { setShowDisable(false); setDisablePwd(""); setDisableCode(""); setTwoFaError(""); }}
                  className="px-4 border border-[#D4CFC9] text-[#6B5E55] hover:text-[#1A1410] rounded-xl text-sm transition-colors">
                  Cancelar
                </button>
              </div>
            </form>
          )}
        </div>
      </div>
    </div>
  );
}
