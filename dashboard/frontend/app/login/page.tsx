"use client";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  // Login sem senha (magic link)
  const [magicMode, setMagicMode] = useState(false);
  const [magicEmail, setMagicEmail] = useState("");
  const [magicSent, setMagicSent] = useState(false);
  const [magicLoading, setMagicLoading] = useState(false);
  const [magicError, setMagicError] = useState("");
  const [magicConsuming, setMagicConsuming] = useState(false);

  // Segundo passo do 2FA (quando a conta tem autenticador ativo)
  const [pending2fa, setPending2fa] = useState("");
  const [totpCode, setTotpCode] = useState("");
  const [totpError, setTotpError] = useState("");
  const [totpLoading, setTotpLoading] = useState(false);

  async function handleVerify2fa(e: React.FormEvent) {
    e.preventDefault();
    setTotpError("");
    setTotpLoading(true);
    try {
      const res = await fetch("/api/proxy/auth/2fa/verify", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ pending_token: pending2fa, code: totpCode.trim() }),
      });
      if (!res.ok) {
        setTotpError(res.status === 429
          ? "Muitas tentativas. Aguarde um minuto."
          : "Código inválido ou expirado. Tente novamente.");
        return;
      }
      const data = await res.json();
      router.push(data.first_access ? "/change-password" : "/");
    } catch {
      setTotpError("Erro ao conectar. Tente novamente.");
    } finally {
      setTotpLoading(false);
    }
  }

  // Resultado do link de verificação de e-mail (?verified=1|0, redirect do backend)
  const [verifiedBanner, setVerifiedBanner] = useState<"ok" | "fail" | null>(null);
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const v = params.get("verified");
    if (v === "1") setVerifiedBanner("ok");
    else if (v === "0") setVerifiedBanner("fail");

    // Chegou pelo e-mail do magic link — troca o token pela sessão via proxy
    const magicToken = params.get("magic_token");
    if (magicToken) {
      setMagicConsuming(true);
      // Tira o token da URL antes de consumir (não fica no histórico do navegador)
      window.history.replaceState({}, "", "/login");
      fetch("/api/proxy/auth/magic-login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token: magicToken }),
      })
        .then(async res => {
          if (!res.ok) throw new Error();
          const data = await res.json();
          if (data.needs_2fa) {
            setPending2fa(data.pending_token);
            setMagicConsuming(false);
            return;
          }
          router.push(data.first_access ? "/change-password" : "/");
        })
        .catch(() => {
          setMagicError("Link de acesso inválido ou expirado. Solicite um novo abaixo.");
          setMagicConsuming(false);
        });
    }
  }, [router]);

  async function handleMagicRequest(e: React.FormEvent) {
    e.preventDefault();
    setMagicLoading(true);
    setMagicError("");
    try {
      const res = await fetch("/api/proxy/auth/magic-link", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: magicEmail }),
      });
      if (!res.ok) {
        setMagicError(res.status === 429
          ? "Muitas tentativas. Aguarde um minuto antes de tentar novamente."
          : "Não foi possível processar sua solicitação. Tente novamente.");
        return;
      }
      setMagicSent(true);
    } catch {
      setMagicError("Erro ao conectar. Tente novamente.");
    } finally {
      setMagicLoading(false);
    }
  }

  const [forgotMode, setForgotMode] = useState(false);
  const [forgotEmail, setForgotEmail] = useState("");
  const [forgotSent, setForgotSent] = useState(false);
  const [forgotLoading, setForgotLoading] = useState(false);
  const [forgotError, setForgotError] = useState("");

  async function handleLogin(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      const res = await fetch("/api/proxy/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password }),
      });
      if (!res.ok) {
        setError("Email ou senha incorretos.");
        return;
      }
      const data = await res.json();
      if (data.needs_2fa) {
        // Senha certa, mas a conta tem 2FA: guarda o token curto e mostra o
        // passo do código do autenticador em vez de entrar direto.
        setPending2fa(data.pending_token);
        setTotpCode("");
        setTotpError("");
        return;
      }
      if (data.first_access) {
        router.push("/change-password");
      } else {
        router.push("/");
      }
    } catch {
      setError("Erro ao conectar. Tente novamente.");
    } finally {
      setLoading(false);
    }
  }

  async function handleForgot(e: React.FormEvent) {
    e.preventDefault();
    setForgotLoading(true);
    setForgotError("");
    try {
      const res = await fetch("/api/proxy/auth/forgot-password", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: forgotEmail, password: "" }),
      });
      // O backend sempre retorna ok:true (não revela se o email existe) —
      // um erro aqui é falha da própria requisição (rede, rate limit),
      // não deve ser mascarado como "email enviado".
      if (!res.ok) {
        setForgotError(res.status === 429
          ? "Muitas tentativas. Aguarde um minuto antes de tentar novamente."
          : "Não foi possível processar sua solicitação. Tente novamente.");
        return;
      }
      setForgotSent(true);
    } catch {
      setForgotError("Erro ao conectar. Tente novamente.");
    } finally {
      setForgotLoading(false);
    }
  }

  return (
    <div className="min-h-screen bg-[#F0EDE8] flex items-center justify-center p-4">
      <div className="w-full max-w-sm">
        <div className="text-center mb-8">
          <div className="inline-flex items-center gap-2 mb-2">
            <span className="text-3xl font-bold text-[#CC3300]">KLEAD-CLIENT</span>
          </div>
          <p className="text-[#6B5E55] text-sm">Acesso restrito - faca login para continuar</p>
          <p className="text-[#6B5E55] text-xs mt-2 max-w-xs mx-auto">
            Se voce recebeu um convite por email, use a senha temporaria aqui.
            No primeiro acesso, voce sera direcionado para criar sua nova senha.
          </p>
        </div>

        {verifiedBanner === "ok" && (
          <div className="mb-4 bg-green-100 border border-green-300 rounded-xl px-4 py-3 text-green-800 text-sm">
            ✓ E-mail verificado com sucesso! Faça login para continuar.
          </div>
        )}
        {verifiedBanner === "fail" && (
          <div className="mb-4 bg-amber-100 border border-amber-300 rounded-xl px-4 py-3 text-amber-800 text-sm">
            Link de verificação inválido ou expirado. Faça login e solicite um novo no seu perfil.
          </div>
        )}
        {magicError && !magicMode && (
          <div className="mb-4 bg-amber-100 border border-amber-300 rounded-xl px-4 py-3 text-amber-800 text-sm">
            {magicError}
          </div>
        )}

        <div className="bg-[#E8E4DF] rounded-2xl border border-[#D4CFC9] p-6">
          {pending2fa ? (
            <form onSubmit={handleVerify2fa} className="space-y-4">
              <div className="text-center mb-2">
                <p className="text-[#1A1410] font-semibold text-sm">Verificação em duas etapas</p>
                <p className="text-[#6B5E55] text-xs mt-1">Digite o código de 6 dígitos do seu app autenticador (ou um código de backup).</p>
              </div>
              {totpError && (
                <div className="bg-[#3D0A00] border border-[#CC3300]/30 rounded-xl px-4 py-3 text-[#CC3300] text-sm">
                  {totpError}
                </div>
              )}
              <input
                autoFocus
                inputMode="text"
                value={totpCode}
                onChange={e => setTotpCode(e.target.value)}
                required
                placeholder="000000"
                className="w-full bg-[#F0EDE8] border border-[#D4CFC9] rounded-xl px-4 py-3 text-[#1A1410] text-center text-lg tracking-[0.3em] font-mono placeholder-[#6B5E55] focus:outline-none focus:border-[#CC3300] transition-colors"
              />
              <button
                type="submit"
                disabled={totpLoading}
                className="w-full bg-[#CC3300] hover:bg-[#991F00] disabled:opacity-50 text-white font-semibold py-3 rounded-xl transition-colors text-sm"
              >
                {totpLoading ? "Verificando..." : "Entrar"}
              </button>
              <button
                type="button"
                onClick={() => { setPending2fa(""); setTotpCode(""); setTotpError(""); }}
                className="w-full border border-[#D4CFC9] text-[#6B5E55] hover:text-[#1A1410] py-3 rounded-xl text-sm transition-colors"
              >
                Voltar
              </button>
            </form>
          ) : magicConsuming ? (
            <div className="py-10 text-center">
              <div className="w-6 h-6 border-2 border-[#CC3300]/30 border-t-[#CC3300] rounded-full animate-spin mx-auto mb-3" />
              <p className="text-[#6B5E55] text-sm">Validando seu link de acesso...</p>
            </div>
          ) : !forgotMode && !magicMode ? (
            <form onSubmit={handleLogin} className="space-y-4">
              <div>
                <label className="block text-xs font-semibold text-[#6B5E55] uppercase tracking-wide mb-1.5">
                  Email
                </label>
                <input
                  type="email"
                  value={email}
                  onChange={e => setEmail(e.target.value)}
                  required
                  placeholder="seu@email.com"
                  className="w-full bg-[#F0EDE8] border border-[#D4CFC9] rounded-xl px-4 py-3 text-[#1A1410] text-sm placeholder-[#6B5E55] focus:outline-none focus:border-[#CC3300] transition-colors"
                />
              </div>
              <div>
                <label className="block text-xs font-semibold text-[#6B5E55] uppercase tracking-wide mb-1.5">
                  Senha ou senha temporaria
                </label>
                <input
                  type="password"
                  value={password}
                  onChange={e => setPassword(e.target.value)}
                  required
                  placeholder="Digite sua senha temporaria ou atual"
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
                disabled={loading}
                className="w-full bg-[#CC3300] hover:bg-[#991F00] disabled:opacity-50 text-white font-semibold py-3 rounded-xl transition-colors text-sm"
              >
                {loading ? "Entrando..." : "Entrar"}
              </button>

              <button
                type="button"
                onClick={() => {
                  setMagicMode(true);
                  setMagicEmail(email);
                  setMagicSent(false);
                  setMagicError("");
                }}
                className="w-full border border-[#D4CFC9] hover:border-[#CC3300] text-[#6B5E55] hover:text-[#CC3300] font-semibold py-3 rounded-xl transition-colors text-sm"
              >
                ✉ Entrar sem senha (link por e-mail)
              </button>

              <button
                type="button"
                onClick={() => {
                  setForgotMode(true);
                  setForgotEmail(email);
                  setForgotSent(false);
                  setForgotError("");
                }}
                className="w-full text-[#6B5E55] hover:text-[#CC3300] text-xs transition-colors pt-1"
              >
                Esqueci minha senha
              </button>

              <p className="text-center text-[#6B5E55] text-xs pt-2">
                Ainda não tem conta?{" "}
                <button type="button" onClick={() => router.push("/plans")} className="text-[#CC3300] hover:underline">
                  Ver planos e assinar
                </button>
              </p>
            </form>
          ) : magicMode ? (
            <div>
              <h2 className="text-[#1A1410] font-bold mb-1">Entrar sem senha</h2>
              <p className="text-[#6B5E55] text-xs mb-4">
                Informe seu email e enviaremos um link de acesso. Ele vale por 15 minutos e só pode ser usado uma vez.
              </p>

              {magicSent ? (
                <div className="space-y-4">
                  <div className="bg-[#3D0A00] border border-[#CC3300]/30 rounded-xl px-4 py-3 text-[#CC3300] text-sm">
                    Se este email estiver cadastrado, você receberá o link de acesso em instantes.
                    Verifique sua caixa de entrada (e o spam).
                  </div>
                  <button
                    onClick={() => {
                      setMagicMode(false);
                      setMagicSent(false);
                    }}
                    className="w-full border border-[#D4CFC9] text-[#6B5E55] hover:text-[#1A1410] py-3 rounded-xl text-sm transition-colors"
                  >
                    Voltar ao login
                  </button>
                </div>
              ) : (
                <form onSubmit={handleMagicRequest} className="space-y-4">
                  {magicError && (
                    <div className="bg-[#3D0A00] border border-[#CC3300]/30 rounded-xl px-4 py-3 text-[#CC3300] text-sm">
                      {magicError}
                    </div>
                  )}
                  <div>
                    <label className="block text-xs font-semibold text-[#6B5E55] uppercase tracking-wide mb-1.5">
                      Email
                    </label>
                    <input
                      type="email"
                      value={magicEmail}
                      onChange={e => setMagicEmail(e.target.value)}
                      required
                      placeholder="seu@email.com"
                      className="w-full bg-[#F0EDE8] border border-[#D4CFC9] rounded-xl px-4 py-3 text-[#1A1410] text-sm placeholder-[#6B5E55] focus:outline-none focus:border-[#CC3300] transition-colors"
                    />
                  </div>
                  <button
                    type="submit"
                    disabled={magicLoading}
                    className="w-full bg-[#CC3300] hover:bg-[#991F00] disabled:opacity-50 text-white font-semibold py-3 rounded-xl transition-colors text-sm"
                  >
                    {magicLoading ? "Enviando..." : "Enviar link de acesso"}
                  </button>
                  <button
                    type="button"
                    onClick={() => setMagicMode(false)}
                    className="w-full border border-[#D4CFC9] text-[#6B5E55] hover:text-[#1A1410] py-3 rounded-xl text-sm transition-colors"
                  >
                    Cancelar
                  </button>
                </form>
              )}
            </div>
          ) : (
            <div>
              <h2 className="text-[#1A1410] font-bold mb-1">Recuperar senha</h2>
              <p className="text-[#6B5E55] text-xs mb-4">
                Informe seu email e enviaremos uma nova senha temporaria.
              </p>

              {forgotSent ? (
                <div className="space-y-4">
                  <div className="bg-[#3D0A00] border border-[#CC3300]/30 rounded-xl px-4 py-3 text-[#CC3300] text-sm">
                    Se este email estiver cadastrado, voce recebera a nova senha em instantes.
                    Verifique sua caixa de entrada.
                  </div>
                  <button
                    onClick={() => {
                      setForgotMode(false);
                      setForgotSent(false);
                    }}
                    className="w-full border border-[#D4CFC9] text-[#6B5E55] hover:text-[#1A1410] py-3 rounded-xl text-sm transition-colors"
                  >
                    Voltar ao login
                  </button>
                </div>
              ) : (
                <form onSubmit={handleForgot} className="space-y-4">
                  {forgotError && (
                    <div className="bg-[#3D0A00] border border-[#CC3300]/30 rounded-xl px-4 py-3 text-[#CC3300] text-sm">
                      {forgotError}
                    </div>
                  )}
                  <div>
                    <label className="block text-xs font-semibold text-[#6B5E55] uppercase tracking-wide mb-1.5">
                      Email
                    </label>
                    <input
                      type="email"
                      value={forgotEmail}
                      onChange={e => setForgotEmail(e.target.value)}
                      required
                      placeholder="seu@email.com"
                      className="w-full bg-[#F0EDE8] border border-[#D4CFC9] rounded-xl px-4 py-3 text-[#1A1410] text-sm placeholder-[#6B5E55] focus:outline-none focus:border-[#CC3300] transition-colors"
                    />
                  </div>
                  <button
                    type="submit"
                    disabled={forgotLoading}
                    className="w-full bg-[#CC3300] hover:bg-[#991F00] disabled:opacity-50 text-white font-semibold py-3 rounded-xl transition-colors text-sm"
                  >
                    {forgotLoading ? "Enviando..." : "Enviar nova senha"}
                  </button>
                  <button
                    type="button"
                    onClick={() => setForgotMode(false)}
                    className="w-full border border-[#D4CFC9] text-[#6B5E55] hover:text-[#1A1410] py-3 rounded-xl text-sm transition-colors"
                  >
                    Cancelar
                  </button>
                </form>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
