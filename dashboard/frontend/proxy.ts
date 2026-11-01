import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

const PUBLIC_PATHS = [
  "/login", "/change-password", "/plans", "/planos",
  "/sobre", "/privacidade", "/termos", "/excluirdados",
  "/c/", "/api/proxy/public/confirmations/",
  "/api/proxy/auth/login", "/api/proxy/auth/forgot-password",
  "/api/proxy/auth/register", "/api/proxy/auth/plans-public",
  "/api/proxy/auth/magic-link", "/api/proxy/auth/magic-login",
  "/api/proxy/auth/2fa/verify",
  "/api/proxy/billing/checkout",
];

// Roleta, Dashboard e Usuários: quem administra a instância (superadmin + admin)
// — gerente e sdr não veem. Admin so consegue convidar de fato se o plano
// permitir (checado no backend); a tela ainda abre pra mostrar o aviso.
const ADMIN_PATHS = ["/roleta", "/dashboard", "/users"];
// Supervisão: superadmin + admin + gerente — nunca sdr
const GERENTE_OU_ACIMA_PATHS = ["/supervisao"];
// Painel multi-cliente e trilha de auditoria: só superadmin. O backend já nega
// os dados (require_superadmin), mas a tela nem deve abrir — /admin/auditoria
// expõe quem fez o quê em todos os tenants.
const SUPERADMIN_PATHS = ["/admin"];

export function proxy(request: NextRequest) {
  const { pathname } = request.nextUrl;

  if (
    PUBLIC_PATHS.some(p => pathname.startsWith(p)) ||
    pathname.startsWith("/_next") ||
    pathname.startsWith("/favicon") ||
    pathname === "/manifest.webmanifest" ||
    pathname.startsWith("/icon-") ||
    pathname.startsWith("/apple-icon")
  ) {
    return NextResponse.next();
  }

  const token =
    request.cookies.get("klead_token")?.value ||
    request.headers.get("x-auth-token") ||
    "";

  if (!token) {
    return NextResponse.redirect(new URL("/login", request.url));
  }

  const funcao = request.cookies.get("klead_funcao")?.value || "";
  const plano = request.cookies.get("klead_plano")?.value || "";

  // Sessões criadas ANTES dos cookies klead_funcao/klead_plano existirem têm
  // token válido mas cookie de função vazio. Nesse caso o proxy não pode
  // adivinhar o cargo — deixa passar (fail-open) e o backend continua negando
  // os DADOS via require_superadmin/require_feature. O AppHeader sincroniza os
  // cookies a partir do /auth/me, então a partir da próxima navegação o gating
  // volta a valer. Sem isso, todo clique em /roleta, /dashboard, /users,
  // /supervisao e /admin redirecionava pra "/" com reload completo do app.
  const funcaoConhecida = funcao !== "";

  // Gating por PLANO (independe do cargo): o plano Lite não tem roleta nem
  // supervisão. Só afeta quem está no lite — qualquer outro plano passa igual
  // a antes. O disparador (raiz da instância) e o dashboard continuam
  // acessíveis; suas partes restritas são escondidas dentro da própria página.
  const LITE_BLOCKED_PATHS = ["/roleta", "/supervisao"];
  if (plano === "lite" && LITE_BLOCKED_PATHS.some(p => pathname.startsWith(p))) {
    return NextResponse.redirect(new URL("/", request.url));
  }

  // Bloqueia rotas de quem administra a instância (superadmin + admin)
  if (
    funcaoConhecida &&
    ADMIN_PATHS.some(p => pathname.startsWith(p)) &&
    funcao !== "superadmin" && funcao !== "admin"
  ) {
    return NextResponse.redirect(new URL("/", request.url));
  }

  // Bloqueia Supervisão pra sdr (superadmin + admin + gerente passam)
  if (
    funcaoConhecida &&
    GERENTE_OU_ACIMA_PATHS.some(p => pathname.startsWith(p)) &&
    funcao !== "superadmin" && funcao !== "admin" && funcao !== "gerente"
  ) {
    return NextResponse.redirect(new URL("/", request.url));
  }

  // Bloqueia o painel admin/auditoria pra qualquer um que não seja superadmin
  if (funcaoConhecida && SUPERADMIN_PATHS.some(p => pathname.startsWith(p)) && funcao !== "superadmin") {
    return NextResponse.redirect(new URL("/", request.url));
  }

  // "Disparador" (raiz da instância, ex: /instances/abc) — só quem administra a
  // instância. Sub-rotas como /instances/abc/inbox e /instances/abc/crm continuam
  // liberadas pra gerente/sdr. /instances/new não é uma instância existente —
  // é a tela de criação, que gerente/sdr agora também podem usar.
  if (
    funcaoConhecida &&
    pathname !== "/instances/new" &&
    /^\/instances\/[^/]+\/?$/.test(pathname) &&
    funcao !== "superadmin" && funcao !== "admin"
  ) {
    return NextResponse.redirect(new URL("/", request.url));
  }

  return NextResponse.next();
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
