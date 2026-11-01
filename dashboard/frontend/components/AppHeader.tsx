"use client";

import Link from "next/link";
import Image from "next/image";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";
import { clearAuthToken, getAuthToken } from "@/lib/auth";

interface User {
  email: string;
  nome: string;
  avatar: string;
  funcao: string;
  first_access?: boolean;
  last_instance_id?: string;
  allowed_instances?: string[];
  owned_instances?: string[];
  max_instances?: number;
  plano?: string;
  effective_plan?: string;
}

const planoNomeHeader: Record<string, string> = {
  user: "Básico", pro: "Pro", enterprise: "Enterprise", personalizado: "Personalizado",
};

type HeaderNavItem = {
  key: string;
  label: string;
  href: string;
};

const funcaoLabel: Record<string, string> = {
  superadmin: "Superadmin",
  admin: "Admin",
  gerente: "Gerente",
  sdr: "Agente",
};

function getActiveKey(pathname: string): string {
  if (pathname === "/" || pathname.startsWith("/instances/new")) return "instances";
  if (pathname.startsWith("/instances/") && pathname.endsWith("/inbox")) return "inbox";
  if (pathname.startsWith("/instances/")) return "disparador";
  if (pathname.startsWith("/dashboard/")) return "dashboard";
  if (pathname.startsWith("/crm")) return "crm";
  if (pathname.startsWith("/roleta")) return "roleta";
  if (pathname.startsWith("/supervisao")) return "supervisao";
  if (pathname.startsWith("/admin")) return "admin";
  return "";
}

function getInstanceIdFromPath(pathname: string): string {
  const dashboardMatch = pathname.match(/^\/dashboard\/([^/]+)/);
  if (dashboardMatch) return dashboardMatch[1];
  const instanceMatch = pathname.match(/^\/instances\/([^/]+)/);
  if (instanceMatch && instanceMatch[1] !== "new") return instanceMatch[1];
  return "";
}

export default function AppHeader() {
  const router = useRouter();
  const pathname = usePathname();
  const [user, setUser] = useState<User | null>(null);
  const [open, setOpen] = useState(false);
  const [fallbackInstanceId, setFallbackInstanceId] = useState("");
  const dropRef = useRef<HTMLDivElement>(null);
  const lastSavedInstanceRef = useRef("");

  useEffect(() => {
    fetch("/api/proxy/auth/me")
      .then(r => {
        if (r.status === 401) {
          clearAuthToken();
          router.replace("/login?reason=session_expired");
          return null;
        }
        return r.ok ? r.json() : null;
      })
      .then(data => {
        if (!data) return;
        setUser(data);
        // Auto-cura de sessões antigas: os cookies klead_funcao/klead_plano só
        // eram gravados no login, então quem já estava logado antes da mudança
        // ficava com cookie vazio — o proxy redirecionava tudo pra "/" e as
        // telas escondiam seções de admin. Sincroniza aqui a partir do /auth/me.
        try {
          const maxAge = 60 * 60 * 24 * 7;
          document.cookie = `klead_funcao=${encodeURIComponent(data.funcao || "")}; path=/; max-age=${maxAge}; samesite=lax`;
          document.cookie = `klead_plano=${encodeURIComponent(data.effective_plan || "")}; path=/; max-age=${maxAge}; samesite=lax`;
        } catch {}
        const saved = data.last_instance_id || window.localStorage.getItem("zp_last_inbox") || "";
        setFallbackInstanceId(saved);
        lastSavedInstanceRef.current = data.last_instance_id || "";
      })
      .catch(() => {});
  }, [router]);

  useEffect(() => {
    function handleClick(e: MouseEvent) {
      if (dropRef.current && !dropRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, []);

  // Plano Lite: não tem disparador, SDR autônomo, roleta nem supervisão.
  // Mantém dashboard, inbox (com co-piloto de sugestões), CRM e credenciais.
  const isLite = user?.effective_plan === "lite";
  const canManageAi = user?.funcao === "superadmin" || user?.funcao === "admin";
  // Disparador/Dashboard: só quem administra a instância — gerente e sdr não veem
  const showAdminInstanceNav = user?.funcao === "superadmin" || user?.funcao === "admin";
  // Roleta: quem administra a instância — nunca gerente/sdr, e não no plano Lite
  const showRoleta = (user?.funcao === "superadmin" || user?.funcao === "admin") && !isLite;
  // Supervisão: superadmin, admin e gerente — nunca sdr, e não no plano Lite
  const showSupervisao = (user?.funcao === "superadmin" || user?.funcao === "admin" || user?.funcao === "gerente") && !isLite;
  // Painel Admin (multi-cliente): exclusivo do superadmin
  const showSuperadminNav = user?.funcao === "superadmin";
  const activeKey = getActiveKey(pathname);
  const instanceId = getInstanceIdFromPath(pathname) || fallbackInstanceId;
  const showGuideAction = pathname !== "/guide";
  // Nova instância: SÓ quem administra (superadmin/admin). Gerente e SDR
  // nunca criam instâncias — eles apenas veem a própria instância (e as que
  // o admin liberar, no caso do gerente) com Inbox SDR e CRM.
  const showNewInstanceAction =
    user?.funcao === "superadmin" || user?.funcao === "admin";

  useEffect(() => {
    const currentInstanceId = getInstanceIdFromPath(pathname);
    if (!currentInstanceId || !user) return;
    queueMicrotask(() => {
      setFallbackInstanceId(prev => (prev === currentInstanceId ? prev : currentInstanceId));
    });
    window.localStorage.setItem("zp_last_inbox", currentInstanceId);
    if (lastSavedInstanceRef.current === currentInstanceId) return;
    lastSavedInstanceRef.current = currentInstanceId;
    const token = getAuthToken();
    fetch("/api/proxy/auth/last-instance", {
      method: "PATCH",
      headers: {
        "Content-Type": "application/json",
        ...(token ? { "X-Auth-Token": token } : {}),
      },
      body: JSON.stringify({ last_instance_id: currentInstanceId }),
    })
      .then(async (response) => {
        if (!response.ok) {
          throw new Error("failed");
        }
        return response.json();
      })
      .then((updatedUser: User) => {
        setUser(prev => (prev ? { ...prev, last_instance_id: updatedUser.last_instance_id || currentInstanceId } : prev));
      })
      .catch(() => {
        lastSavedInstanceRef.current = user.last_instance_id || "";
      });
  }, [pathname, user]);

  const navItems = useMemo<HeaderNavItem[]>(() => {
    const items: HeaderNavItem[] = [{ key: "instances", label: "Instancias", href: "/" }];

    if (instanceId) {
      if (showAdminInstanceNav) {
        // No Lite a raiz da instância não é disparador — vira só configuração
        // (credenciais e base de conhecimento). Fora do Lite, é o Disparador.
        items.push({
          key: "disparador",
          label: isLite ? "Configuracao" : "Disparador",
          href: `/instances/${instanceId}`,
        });
      }
      items.push({ key: "inbox", label: "Inbox SDR", href: `/instances/${instanceId}/inbox` });
      if (showAdminInstanceNav) {
        items.push({ key: "dashboard", label: "Dashboard", href: `/dashboard/${instanceId}` });
      }
    }

    items.push({ key: "crm", label: "CRM", href: instanceId ? `/crm?instancia_id=${encodeURIComponent(instanceId)}` : "/crm" });

    if (showRoleta) {
      items.push({ key: "roleta", label: "Roleta", href: "/roleta" });
    }
    if (showSupervisao) {
      items.push({ key: "supervisao", label: "Supervisao", href: "/supervisao" });
    }

    if (showSuperadminNav) {
      items.push({ key: "admin", label: "Admin", href: "/admin" });
    }

    return items;
  }, [instanceId, isLite, showAdminInstanceNav, showRoleta, showSupervisao, showSuperadminNav]);

  async function handleLogout() {
    const token = getAuthToken();
    try {
      await fetch("/api/proxy/auth/logout", {
        method: "POST",
        headers: token ? { "X-Auth-Token": token } : undefined,
      });
    } catch {
      // Sessão local é encerrada de qualquer forma abaixo — o usuário pediu
      // para sair, uma falha ao invalidar no servidor não deve prendê-lo logado.
    } finally {
      clearAuthToken();
      router.push("/login");
    }
  }

  if (!user) return null;

  const initials = user.nome
    ? user.nome
        .split(" ")
        .map(word => word[0])
        .slice(0, 2)
        .join("")
        .toUpperCase()
    : user.email[0].toUpperCase();

  return (
    <header className="fixed inset-x-0 top-0 z-[100] border-b border-[#252535] bg-[#0E0E18]/95 backdrop-blur-xl">
      <div className="mx-auto flex h-[64px] max-w-[1680px] items-center gap-3 px-4 sm:px-6">
        <div className="flex items-center lg:min-w-[132px]">
          <Link href="/" className="group inline-flex items-baseline gap-2 transition-colors">
            <span className="text-sm font-semibold tracking-[0.32em] text-[#CC3300] uppercase group-hover:text-[#FF6633] transition-colors">
              KLEAD-CLIENT
            </span>
          </Link>
        </div>

        <nav className="hidden min-w-0 flex-1 items-center justify-center lg:flex">
          <div className="flex items-center justify-center gap-1.5 rounded-full border border-[#252535] bg-[#141420]/85 px-2.5 py-1.5 shadow-[0_12px_32px_rgba(0,0,0,0.32)]">
            {navItems.map((item) => {
              const isActive = activeKey === item.key;
              return (
                <Link
                  key={item.key}
                  href={item.href}
                  className={`rounded-full px-3.5 py-1.5 text-[12px] font-semibold leading-none transition-colors ${
                    isActive
                      ? "bg-[#CC3300] text-white shadow-[0_4px_14px_rgba(204,51,0,0.4)]"
                      : "text-[#9999AD] hover:bg-[#252535] hover:text-[#CC3300]"
                  }`}
                >
                  {item.label}
                </Link>
              );
            })}
          </div>
        </nav>

        <div className="ml-auto flex items-center gap-2 sm:gap-3" ref={dropRef}>
          <div className="hidden items-center gap-2 lg:flex">
            {showGuideAction && (
              <Link
                href="/guide"
                className="rounded-full border border-[#252535] bg-[#141420] px-3 py-1.5 text-[12px] font-semibold text-[#9999AD] transition-colors hover:border-[#303050] hover:text-[#CC3300]"
              >
                Como Usar
              </Link>
            )}
            {showNewInstanceAction && (
              <Link
                href="/instances/new"
                className="rounded-full bg-[#CC3300] px-3.5 py-1.5 text-[12px] font-semibold text-white transition-colors hover:bg-[#991F00]"
              >
                Nova Instância
              </Link>
            )}
          </div>

          <div className="hidden text-right lg:block">
            <p className="text-[13px] font-semibold text-[#CC3300] leading-none">{user.nome}</p>
            <p className="mt-0.5 text-[11px] text-[#9999AD]">{funcaoLabel[user.funcao] || user.funcao}</p>
          </div>

          <button
            onClick={() => setOpen(v => !v)}
            className="flex items-center gap-2 rounded-full border border-[#252535] bg-[#141420]/95 px-2 py-1.5 shadow-lg transition-colors hover:bg-[#252535]"
            title="Menu do usuário"
          >
            {user.avatar ? (
              <Image src={user.avatar} alt={user.nome} width={32} height={32} className="h-8 w-8 rounded-full object-cover" unoptimized />
            ) : (
              <div className="flex h-8 w-8 items-center justify-center rounded-full bg-[#CC3300] text-[11px] font-bold text-white">
                {initials}
              </div>
            )}
          </button>

          {open && (
            <div className="absolute right-0 top-full mt-3 w-64 overflow-hidden rounded-2xl border border-[#252535] bg-[#141420] shadow-xl">
              <div className="border-b border-[#252535] px-4 py-3">
                <p className="truncate text-sm font-semibold text-[#CC3300]">{user.nome}</p>
                <p className="truncate text-xs text-[#9999AD]">{user.email}</p>
                {user.funcao === "admin" && (
                  <p className="mt-1 text-xs text-[#9999AD]">
                    <span className="font-semibold text-[#CC3300]">{user.allowed_instances?.length ?? 0}/{user.max_instances ?? 1}</span>{" "}
                    instâncias · plano {planoNomeHeader[user.plano ?? "user"] || user.plano}
                  </p>
                )}
                {user.funcao === "superadmin" && (
                  <p className="mt-1 text-xs text-[#9999AD]">
                    <span className="font-semibold text-[#CC3300]">{user.allowed_instances?.length ?? 0}</span> instância{(user.allowed_instances?.length ?? 0) !== 1 ? "s" : ""} · conta mestre
                  </p>
                )}
                {(user.funcao === "gerente" || user.funcao === "sdr") && (
                  <p className="mt-1 text-xs text-[#9999AD]">
                    <span className="font-semibold text-[#CC3300]">{user.allowed_instances?.length ?? 0}</span> instância{(user.allowed_instances?.length ?? 0) !== 1 ? "s" : ""}
                  </p>
                )}
              </div>
              <div className="border-b border-[#252535] py-1 lg:hidden">
                {navItems.map((item) => (
                  <button
                    key={item.key}
                    onClick={() => {
                      setOpen(false);
                      router.push(item.href);
                    }}
                    className="w-full px-4 py-2.5 text-left text-sm text-[#CC3300] transition-colors hover:bg-[#252535]"
                  >
                    {item.label}
                  </button>
                ))}
                {showNewInstanceAction && (
                  <button
                    onClick={() => { setOpen(false); router.push("/instances/new"); }}
                    className="w-full px-4 py-2.5 text-left text-sm font-semibold text-[#CC3300] transition-colors hover:bg-[#252535]"
                  >
                    + Nova Instância
                  </button>
                )}
              </div>
              <div className="py-1">
                {canManageAi && (
                  <button onClick={() => { setOpen(false); router.push("/settings/meta"); }} className="w-full px-4 py-2.5 text-left text-sm text-[#CC3300] transition-colors hover:bg-[#252535]">
                    Integracao Meta
                  </button>
                )}
                <button onClick={() => { setOpen(false); router.push("/profile"); }} className="w-full px-4 py-2.5 text-left text-sm text-[#CC3300] transition-colors hover:bg-[#252535]">
                  Meu perfil
                </button>
                {(user.funcao === "superadmin" || user.funcao === "admin") && (
                  <button onClick={() => { setOpen(false); router.push("/users"); }} className="w-full px-4 py-2.5 text-left text-sm text-[#CC3300] transition-colors hover:bg-[#252535]">
                    Gerenciar usuarios
                  </button>
                )}
                <button onClick={() => { setOpen(false); router.push("/change-password"); }} className="w-full px-4 py-2.5 text-left text-sm text-[#CC3300] transition-colors hover:bg-[#252535]">
                  Trocar senha
                </button>
              </div>
              <div className="border-t border-[#252535] py-1">
                <button onClick={handleLogout} className="w-full px-4 py-2.5 text-left text-sm text-red-400 transition-colors hover:bg-[#252535]">
                  Sair
                </button>
              </div>
            </div>
          )}
        </div>
      </div>
    </header>
  );
}
