"use client";
import { useCallback, useEffect, useState } from "react";
import Image from "next/image";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { getAuthToken } from "@/lib/auth";
import { getWaStates } from "@/lib/api";

interface User {
  id: string; email: string; nome: string; avatar: string;
  funcao: string; status: string; created_at: string;
  allowed_instances: string[];
  owned_instances?: string[];
  plano?: string; max_instances?: number;
}

const FUNCOES_SUPERADMIN = ["sdr", "gerente", "admin"];
const FUNCOES_ADMIN = ["sdr", "gerente"];
const PLANOS = ["lite", "user", "pro", "enterprise", "personalizado"];
const planoLabel: Record<string, string> = { lite: "Lite (1 instância, sem disparador/SDR autônomo)", user: "User (1 instância)", pro: "Pro (5 instâncias)", enterprise: "Enterprise (20 instâncias)", personalizado: "Personalizado" };
const planoNome: Record<string, string> = { lite: "Lite", user: "User", pro: "Pro", enterprise: "Enterprise", personalizado: "Personalizado" };

interface Me { funcao: string; max_instances?: number; allowed_instances?: string[]; }

export default function UsersPage() {
  const router = useRouter();
  const [me, setMe]             = useState<Me | null>(null);
  const [users, setUsers]       = useState<User[]>([]);
  const [instances, setInstances] = useState<string[]>([]);
  const [loading, setLoading]   = useState(true);
  const [loadError, setLoadError] = useState("");
  const [showInvite, setShowInvite] = useState(false);
  const [inviteEmail, setInviteEmail] = useState("");
  const [inviteNome, setInviteNome]   = useState("");
  const [inviteFuncao, setInviteFuncao] = useState("sdr");
  const [invitePlano, setInvitePlano]   = useState("user");
  const [inviteCustomMax, setInviteCustomMax] = useState("1");
  const [inviteError, setInviteError]   = useState("");
  const [inviteOk, setInviteOk]         = useState(false);
  const [sending, setSending]           = useState(false);

  // Modal de instâncias
  const [instancesUser, setInstancesUser] = useState<User | null>(null);
  const [selectedInstances, setSelectedInstances] = useState<string[]>([]);
  const [savingInstances, setSavingInstances] = useState(false);

  // Status de conexão WhatsApp das instâncias de equipe (SDR/gerente) + reconexão silenciosa
  const [waStates, setWaStates] = useState<Record<string, string>>({});
  const [reconnecting, setReconnecting] = useState<string | null>(null);

  function token() { return getAuthToken(); }

  const loadUsers = useCallback(async function loadUsers() {
    await Promise.resolve();
    setLoading(true);
    setLoadError("");
    try {
      const res = await fetch("/api/proxy/auth/users", { headers: { "X-Auth-Token": token() } });
      if (res.status === 403) { router.push("/"); return; }
      if (!res.ok) { setLoadError("Não foi possível carregar os usuários. Tente novamente."); return; }
      const data = await res.json();
      setUsers(data);
    } catch {
      setLoadError("Erro ao conectar. Tente novamente.");
    } finally {
      setLoading(false);
    }
  }, [router]);

  const loadInstances = useCallback(async function loadInstances() {
    const res = await fetch("/api/proxy/instances", { headers: { "X-Auth-Token": token() } });
    if (res.ok) {
      const data = await res.json();
      setInstances(Object.keys(data));
    }
  }, []);

  const loadMe = useCallback(async function loadMe() {
    const res = await fetch("/api/proxy/auth/me", { headers: { "X-Auth-Token": token() } });
    if (res.ok) setMe(await res.json());
  }, []);

  useEffect(() => {
    queueMicrotask(() => {
      loadUsers();
      loadInstances();
      loadMe();
    });
  }, [loadUsers, loadInstances, loadMe]);

  useEffect(() => {
    let cancelled = false;
    async function poll() {
      const states = await getWaStates().catch(() => ({} as Record<string, string>));
      if (!cancelled) setWaStates(states);
    }
    poll();
    const id = setInterval(poll, 30000);
    return () => { cancelled = true; clearInterval(id); };
  }, []);

  async function handleReconnect(instanceId: string) {
    setReconnecting(instanceId);
    try {
      const r = await fetch(`/api/proxy/instance/${instanceId}/reconnect`, {
        method: "POST", headers: { "X-Auth-Token": token() },
      });
      if (r.ok) {
        await new Promise(res => setTimeout(res, 3000));
        const states = await getWaStates().catch(() => ({} as Record<string, string>));
        setWaStates(states);
        if (states[instanceId] === "open") {
          alert("WhatsApp reconectado com sucesso!");
        } else {
          alert("Não reconectou automaticamente. Se o WhatsApp foi desconectado pelo celular, gere um novo QR Code para essa instância.");
        }
      } else {
        const d = await r.json().catch(() => ({} as { detail?: string }));
        alert(d.detail || "Não foi possível reconectar automaticamente.");
      }
    } catch {
      alert("Erro de conexão ao tentar reconectar. Tente novamente.");
    } finally {
      setReconnecting(null);
    }
  }

  async function handleInvite(e: React.FormEvent) {
    e.preventDefault();
    setInviteError(""); setSending(true);
    try {
      const res = await fetch("/api/proxy/auth/users/invite", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Auth-Token": token() },
        body: JSON.stringify({
          email: inviteEmail, nome: inviteNome, funcao: inviteFuncao,
          ...(inviteFuncao === "admin" ? {
            plano: invitePlano,
            ...(invitePlano === "personalizado" ? { custom_max_instances: parseInt(inviteCustomMax, 10) || 1 } : {}),
          } : {}),
        }),
      });
      if (res.status === 409) { setInviteError("Este email já está cadastrado."); return; }
      if (!res.ok) { setInviteError("Erro ao convidar."); return; }
      setInviteOk(true);
      setInviteEmail(""); setInviteNome(""); setInviteFuncao("sdr"); setInvitePlano("user"); setInviteCustomMax("1");
      setTimeout(() => { setInviteOk(false); setShowInvite(false); }, 2000);
      loadUsers();
    } catch {
      setInviteError("Erro ao conectar. Tente novamente.");
    } finally {
      setSending(false);
    }
  }

  async function handleDelete(email: string) {
    if (!confirm(`Excluir permanentemente o usuário "${email}"? Esta ação não pode ser desfeita.`)) return;
    try {
      const res = await fetch(`/api/proxy/auth/users/${encodeURIComponent(email)}`, {
        method: "DELETE", headers: { "X-Auth-Token": token() },
      });
      if (res.status === 404) {
        await loadUsers();
        alert("Este usuário já não existe no servidor. A lista foi atualizada.");
        return;
      }
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        alert(data.detail || "Erro ao excluir usuário. Tente novamente.");
        return;
      }
      loadUsers();
    } catch {
      alert("Erro ao conectar. Tente novamente.");
    }
  }

  async function handleRevoke(email: string) {
    try {
      const res = await fetch(`/api/proxy/auth/users/${encodeURIComponent(email)}/revoke`, {
        method: "PATCH", headers: { "X-Auth-Token": token() },
      });
      if (!res.ok) { alert("Erro ao revogar usuário. Tente novamente."); return; }
      loadUsers();
    } catch {
      alert("Erro ao conectar. Tente novamente.");
    }
  }

  async function handleReactivate(email: string) {
    try {
      const res = await fetch(`/api/proxy/auth/users/${encodeURIComponent(email)}/reactivate`, {
        method: "PATCH", headers: { "X-Auth-Token": token() },
      });
      if (!res.ok) { alert("Erro ao reativar usuário. Tente novamente."); return; }
      loadUsers();
    } catch {
      alert("Erro ao conectar. Tente novamente.");
    }
  }

  async function handleActivateSignup(email: string) {
    if (!confirm(`Ativar "${email}"? Uma senha temporária será gerada e enviada por email.`)) return;
    try {
      const res = await fetch(`/api/proxy/auth/users/${encodeURIComponent(email)}/activate`, {
        method: "PATCH", headers: { "X-Auth-Token": token() },
      });
      if (!res.ok) { alert("Erro ao ativar usuário. Tente novamente."); return; }
      loadUsers();
    } catch {
      alert("Erro ao conectar. Tente novamente.");
    }
  }

  async function handleResetPassword(email: string) {
    try {
      const res = await fetch(`/api/proxy/auth/users/${encodeURIComponent(email)}/reset-password`, {
        method: "POST", headers: { "X-Auth-Token": token() },
      });
      if (!res.ok) { alert("Erro ao redefinir senha. Tente novamente."); return; }
      alert("Nova senha temporária enviada por email.");
    } catch {
      alert("Erro ao conectar. Tente novamente.");
    }
  }

  function openInstancesModal(u: User) {
    setInstancesUser(u);
    setSelectedInstances(u.allowed_instances ?? []);
  }

  function toggleInstance(id: string) {
    setSelectedInstances(prev =>
      prev.includes(id) ? prev.filter(i => i !== id) : [...prev, id]
    );
  }

  async function saveInstances() {
    if (!instancesUser) return;
    setSavingInstances(true);
    try {
      const res = await fetch(`/api/proxy/auth/users/${encodeURIComponent(instancesUser.email)}/instances`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json", "X-Auth-Token": token() },
        body: JSON.stringify({ allowed_instances: selectedInstances }),
      });
      if (!res.ok) { alert("Erro ao salvar instâncias. Tente novamente."); return; }
      setInstancesUser(null);
      loadUsers();
    } catch {
      alert("Erro ao conectar. Tente novamente.");
    } finally {
      setSavingInstances(false);
    }
  }

  const funcaoLabel: Record<string, string> = {
    superadmin: "Superadmin", admin: "Admin", gerente: "Gerente", sdr: "SDR",
  };
  const funcaoColor: Record<string, string> = {
    superadmin: "text-[#A78BFA]", admin: "text-[#F97316]", gerente: "text-[#60A5FA]", sdr: "text-[#CC3300]",
  };

  return (
    <div className="min-h-screen bg-[#F0EDE8]">
      <div className="max-w-3xl mx-auto p-6">
        <div className="flex items-center justify-between mb-6">
          <div className="flex items-center gap-3">
            <button onClick={() => router.back()} className="text-[#6B5E55] hover:text-[#1A1410] transition-colors">←</button>
            <h1 className="text-[#1A1410] text-xl font-bold">Usuários</h1>
            <span className="bg-[#D4CFC9] text-[#6B5E55] text-xs px-2 py-1 rounded-full">{users.length}</span>
          </div>
          <button
            onClick={() => setShowInvite(true)}
            disabled={me?.funcao === "admin" && (me?.max_instances ?? 1) <= 2}
            className="bg-[#CC3300] hover:bg-[#991F00] disabled:opacity-40 disabled:cursor-not-allowed text-white text-sm font-semibold px-4 py-2 rounded-xl transition-colors"
          >
            + Convidar
          </button>
        </div>
        {me?.funcao === "admin" && (me?.max_instances ?? 1) <= 2 && (
          <p className="text-[#6B5E55] text-xs -mt-4 mb-6">Seu plano atual não permite convidar outros usuários. Fale com o suporte para aumentar seu plano.</p>
        )}

        {/* Modal convite */}
        {showInvite && (
          <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4">
            <div className="bg-[#E8E4DF] rounded-2xl border border-[#D4CFC9] w-full max-w-sm p-6">
              <h2 className="text-[#1A1410] font-bold mb-4">Convidar usuário</h2>
              <form onSubmit={handleInvite} className="space-y-4">
                <div>
                  <label className="block text-xs font-semibold text-[#6B5E55] uppercase tracking-wide mb-1.5">Nome</label>
                  <input value={inviteNome} onChange={e => setInviteNome(e.target.value)} required placeholder="Nome completo"
                    className="w-full bg-[#F0EDE8] border border-[#D4CFC9] rounded-xl px-4 py-3 text-[#1A1410] text-sm focus:outline-none focus:border-[#CC3300] transition-colors" />
                </div>
                <div>
                  <label className="block text-xs font-semibold text-[#6B5E55] uppercase tracking-wide mb-1.5">Email</label>
                  <input type="email" value={inviteEmail} onChange={e => setInviteEmail(e.target.value)} required placeholder="email@empresa.com"
                    className="w-full bg-[#F0EDE8] border border-[#D4CFC9] rounded-xl px-4 py-3 text-[#1A1410] text-sm focus:outline-none focus:border-[#CC3300] transition-colors" />
                </div>
                <div>
                  <label className="block text-xs font-semibold text-[#6B5E55] uppercase tracking-wide mb-1.5">Função</label>
                  <select value={inviteFuncao} onChange={e => setInviteFuncao(e.target.value)}
                    className="w-full bg-[#F0EDE8] border border-[#D4CFC9] rounded-xl px-4 py-3 text-[#1A1410] text-sm focus:outline-none focus:border-[#CC3300] transition-colors">
                    {(me?.funcao === "superadmin" ? FUNCOES_SUPERADMIN : FUNCOES_ADMIN).map(f => <option key={f} value={f}>{funcaoLabel[f]}</option>)}
                  </select>
                </div>
                {inviteFuncao === "admin" && (
                  <div>
                    <label className="block text-xs font-semibold text-[#6B5E55] uppercase tracking-wide mb-1.5">Plano</label>
                    <select value={invitePlano} onChange={e => setInvitePlano(e.target.value)}
                      className="w-full bg-[#F0EDE8] border border-[#D4CFC9] rounded-xl px-4 py-3 text-[#1A1410] text-sm focus:outline-none focus:border-[#CC3300] transition-colors">
                      {PLANOS.map(p => <option key={p} value={p}>{planoLabel[p]}</option>)}
                    </select>
                    <p className="text-[#6B5E55] text-xs mt-1">Quantas instâncias esse admin pode ter.</p>
                  </div>
                )}
                {inviteFuncao === "admin" && invitePlano === "personalizado" && (
                  <div>
                    <label className="block text-xs font-semibold text-[#6B5E55] uppercase tracking-wide mb-1.5">Limite de instâncias</label>
                    <input type="number" min={1} value={inviteCustomMax} onChange={e => setInviteCustomMax(e.target.value)}
                      className="w-full bg-[#F0EDE8] border border-[#D4CFC9] rounded-xl px-4 py-3 text-[#1A1410] text-sm focus:outline-none focus:border-[#CC3300] transition-colors" />
                  </div>
                )}
                {inviteError && <p className="text-red-700 text-sm">{inviteError}</p>}
                {inviteOk && <p className="text-[#CC3300] text-sm">✓ Convite enviado por email!</p>}
                <div className="flex gap-3">
                  <button type="button" onClick={() => setShowInvite(false)}
                    className="flex-1 border border-[#D4CFC9] text-[#6B5E55] hover:text-[#1A1410] py-3 rounded-xl text-sm transition-colors">
                    Cancelar
                  </button>
                  <button type="submit" disabled={sending}
                    className="flex-1 bg-[#CC3300] hover:bg-[#991F00] disabled:opacity-50 text-white font-semibold py-3 rounded-xl text-sm transition-colors">
                    {sending ? "Enviando..." : "Convidar"}
                  </button>
                </div>
              </form>
            </div>
          </div>
        )}

        {/* Modal instâncias */}
        {instancesUser && (
          <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4">
            <div className="bg-[#E8E4DF] rounded-2xl border border-[#D4CFC9] w-full max-w-sm p-6">
              <h2 className="text-[#1A1410] font-bold mb-1">Instâncias</h2>
              <p className="text-[#6B5E55] text-xs mb-4">{instancesUser.nome}</p>

              {(() => {
                // Instâncias da conta mestre (superadmin) nunca podem ser
                // atribuídas a um admin independente nem à equipe dele —
                // só à própria equipe do superadmin (gerente/sdr sem
                // nenhuma instância de outro dono ainda atribuída).
                const belongsToOtherTenant =
                  me?.funcao === "superadmin" &&
                  (instancesUser.funcao === "admin" ||
                    (instancesUser.allowed_instances.length > 0 &&
                      !instancesUser.allowed_instances.every(id => instances.includes(id))));
                const pickerInstances = belongsToOtherTenant ? [] : instances;
                if (belongsToOtherTenant) {
                  return <p className="text-[#6B5E55] text-sm mb-5">Este usuário pertence a outra conta — instâncias geridas pelo próprio dono, não por aqui.</p>;
                }
                if (pickerInstances.length === 0) {
                  return <p className="text-[#6B5E55] text-sm mb-5">Nenhuma instância encontrada.</p>;
                }
                return (
                <div className="space-y-2 mb-5">
                  {pickerInstances.map(id => {
                    const active = selectedInstances.includes(id);
                    return (
                      <button
                        key={id}
                        onClick={() => toggleInstance(id)}
                        className={`w-full flex items-center justify-between px-4 py-3 rounded-xl border transition-colors text-sm ${
                          active
                            ? "border-[#CC3300] bg-[#CC3300]/10 text-[#CC3300]"
                            : "border-[#D4CFC9] bg-[#F0EDE8] text-[#6B5E55] hover:border-[#6B5E55]"
                        }`}
                      >
                        <span className="font-medium truncate">{id}</span>
                        <span className={`ml-3 w-5 h-5 rounded-full border-2 flex items-center justify-center shrink-0 ${
                          active ? "border-[#CC3300] bg-[#CC3300]" : "border-[#D4CFC9]"
                        }`}>
                          {active && <span className="text-white text-xs">✓</span>}
                        </span>
                      </button>
                    );
                  })}
                </div>
                );
              })()}

              <div className="flex gap-3">
                <button onClick={() => setInstancesUser(null)}
                  className="flex-1 border border-[#D4CFC9] text-[#6B5E55] hover:text-[#1A1410] py-3 rounded-xl text-sm transition-colors">
                  Cancelar
                </button>
                <button onClick={saveInstances} disabled={savingInstances}
                  className="flex-1 bg-[#CC3300] hover:bg-[#991F00] disabled:opacity-50 text-white font-semibold py-3 rounded-xl text-sm transition-colors">
                  {savingInstances ? "Salvando..." : "Salvar"}
                </button>
              </div>
            </div>
          </div>
        )}

        {/* Lista */}
        {loading ? (
          <div className="text-[#6B5E55] text-sm">Carregando...</div>
        ) : loadError ? (
          <div className="bg-[#3D0A00] border border-[#CC3300]/30 rounded-xl px-4 py-3 text-[#CC3300] text-sm">
            {loadError}
          </div>
        ) : (
          <div className="space-y-3">
            {users.map(u => {
              const initials = u.nome.split(" ").map((w: string) => w[0]).slice(0, 2).join("").toUpperCase();
              const instCount = u.allowed_instances?.length ?? 0;
              const ownedTeamInstances = (u.funcao === "sdr" || u.funcao === "gerente") ? (u.owned_instances ?? []) : [];
              return (
                <div key={u.id} className="flex flex-col gap-2">
                <div className="bg-[#E8E4DF] rounded-2xl border border-[#D4CFC9] px-5 py-4 flex flex-col gap-3 sm:flex-row sm:items-center sm:gap-4">
                  <div className="flex items-center gap-3 min-w-0 flex-1">
                    {/* Avatar */}
                    {u.avatar ? (
                      <Image src={u.avatar} alt={u.nome} width={40} height={40} className="w-10 h-10 rounded-full object-cover shrink-0" unoptimized />
                    ) : (
                      <div className="w-10 h-10 rounded-full bg-[#CC3300] flex items-center justify-center text-white text-sm font-bold shrink-0">
                        {initials}
                      </div>
                    )}

                    {/* Info */}
                    <div className="flex-1 min-w-0">
                      <p className="text-[#1A1410] font-semibold text-sm truncate">{u.nome}</p>
                      <p className="text-[#6B5E55] text-xs truncate">{u.email}</p>
                      {u.funcao !== "superadmin" && u.funcao !== "admin" && (
                        <p className="text-[#6B5E55] text-xs mt-0.5">
                          {instCount === 0
                            ? <span className="text-red-700">Sem instâncias</span>
                            : <span className="text-[#CC3300]">{instCount} instância{instCount > 1 ? "s" : ""}</span>
                          }
                        </p>
                      )}
                      {u.funcao === "admin" && (
                        <p className="text-[#6B5E55] text-xs mt-0.5">
                          <span className="text-[#CC3300] font-semibold">{instCount}/{u.max_instances ?? 1}</span> instâncias · plano {planoNome[u.plano ?? "user"] || u.plano}
                        </p>
                      )}
                    </div>

                    {/* Função + status */}
                    <div className="text-right shrink-0">
                      <p className={`text-xs font-semibold ${funcaoColor[u.funcao] || "text-[#6B5E55]"}`}>
                        {funcaoLabel[u.funcao] || u.funcao}
                      </p>
                      <p className={`text-xs mt-0.5 ${
                        u.status === "active" ? "text-[#CC3300]" : u.status === "pending" ? "text-[#F0B429]" : "text-red-700"
                      }`}>
                        {u.status === "active" ? "Ativo" : u.status === "pending" ? "Pendente (aguardando ativação)" : "Revogado"}
                      </p>
                    </div>
                  </div>

                  {/* Ações */}
                  {u.funcao !== "superadmin" && (
                    <div className="flex flex-wrap gap-2 sm:shrink-0">
                      <button
                        onClick={() => openInstancesModal(u)}
                        title="Gerenciar instâncias"
                        className="text-[#6B5E55] hover:text-[#CC3300] text-xs border border-[#D4CFC9] px-2 py-1 rounded-lg transition-colors"
                      >
                        Instâncias
                      </button>
                      <button
                        onClick={() => handleResetPassword(u.email)}
                        title="Resetar senha"
                        className="text-[#6B5E55] hover:text-[#F0B429] text-xs border border-[#D4CFC9] px-2 py-1 rounded-lg transition-colors"
                      >
                        Reset
                      </button>
                      {u.status === "active" ? (
                        <button onClick={() => handleRevoke(u.email)}
                          className="text-[#6B5E55] hover:text-red-700 text-xs border border-[#D4CFC9] px-2 py-1 rounded-lg transition-colors">
                          Revogar
                        </button>
                      ) : (
                        <button onClick={() => u.status === "pending" ? handleActivateSignup(u.email) : handleReactivate(u.email)}
                          className="text-[#6B5E55] hover:text-[#CC3300] text-xs border border-[#D4CFC9] px-2 py-1 rounded-lg transition-colors">
                          {u.status === "pending" ? "Ativar" : "Reativar"}
                        </button>
                      )}
                      <button onClick={() => handleDelete(u.email)}
                        className="text-[#6B5E55] hover:text-red-500 text-xs border border-[#D4CFC9] hover:border-red-500/40 px-2 py-1 rounded-lg transition-colors"
                        title="Excluir permanentemente">
                        Excluir
                      </button>
                    </div>
                  )}
                </div>

                {ownedTeamInstances.map(iid => {
                  const state = waStates[iid];
                  const connected = state === "open";
                  return (
                    <div key={iid} className="ml-6 bg-[#E8E4DF]/60 rounded-xl border border-[#D4CFC9] px-4 py-3 flex items-center justify-between gap-3">
                      <div className="min-w-0">
                        <p className="text-[#1A1410] text-xs font-semibold truncate">{iid}</p>
                        <p className={`text-xs mt-0.5 ${connected ? "text-[#CC3300]" : "text-red-700"}`}>
                          {state ? (connected ? "WhatsApp conectado" : "WhatsApp desconectado") : "Verificando..."}
                        </p>
                      </div>
                      <div className="flex items-center gap-2 shrink-0">
                        <Link href={`/instances/${encodeURIComponent(iid)}/inbox`}
                          className="text-[#6B5E55] hover:text-[#CC3300] text-xs border border-[#D4CFC9] px-2 py-1 rounded-lg transition-colors">
                          Inbox
                        </Link>
                        {!connected && (
                          <button
                            onClick={() => handleReconnect(iid)}
                            disabled={reconnecting === iid}
                            className="bg-[#CC3300] hover:bg-[#991F00] disabled:opacity-50 text-white text-xs font-semibold px-2 py-1 rounded-lg transition-colors"
                          >
                            {reconnecting === iid ? "Reconectando..." : "Reconectar"}
                          </button>
                        )}
                      </div>
                    </div>
                  );
                })}
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
