"use client";

import { useEffect, useState } from "react";
import PageHeaderShell from "@/components/PageHeaderShell";
import { getAiGlobalConfig, updateAiGlobalConfig, type AiGlobalConfig } from "@/lib/api";

const MODELS: Record<string, string[]> = {
  openai: ["gpt-4.1-nano", "gpt-5.5"],
  groq: ["openai/gpt-oss-120b"],
};

const PROVIDER_LABEL: Record<string, string> = {
  openai: "OpenAI",
  groq: "Groq",
};

export default function AiSettingsPage() {
  const [form, setForm] = useState<AiGlobalConfig>({
    llm_provider: "groq",
    llm_model: "openai/gpt-oss-120b",
    llm_api_key: "",
    key_openai: "",
    key_anthropic: "",
    key_groq: "",
  });
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    getAiGlobalConfig()
      .then((data) => setForm((prev) => ({ ...prev, ...data })))
      .catch(() => setError("Nao foi possivel carregar a configuracao de IA."))
      .finally(() => setLoading(false));
  }, []);

  const activeKey =
    form.llm_provider === "anthropic" ? form.key_anthropic :
    form.llm_provider === "groq" ? form.key_groq :
    form.key_openai;

  const setActiveKey = (value: string) => {
    if (form.llm_provider === "anthropic") setForm({ ...form, key_anthropic: value, llm_api_key: value });
    else if (form.llm_provider === "groq") setForm({ ...form, key_groq: value, llm_api_key: value });
    else setForm({ ...form, key_openai: value, llm_api_key: value });
  };

  const handleProvider = (provider: string) => {
    setForm({
      ...form,
      llm_provider: provider,
      llm_model: MODELS[provider][0],
      llm_api_key:
        provider === "anthropic" ? form.key_anthropic || "" :
        provider === "groq" ? form.key_groq || "" :
        form.key_openai || "",
    });
  };

  const handleSave = async () => {
    setSaving(true);
    setSaved(false);
    setError("");
    try {
      const res = await updateAiGlobalConfig(form);
      if (res.ok) {
        const { ok, ...config } = res;
        void ok;
        setForm((prev) => ({ ...prev, ...config }));
        setSaved(true);
        setTimeout(() => setSaved(false), 2200);
      } else {
        setError(res.detail || "Erro ao salvar.");
      }
    } catch {
      setError("Erro de conexao ao salvar.");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="min-h-screen bg-[#F0EDE8]">
      <PageHeaderShell maxWidthClassName="max-w-4xl">
        <div className="text-center">
          <h1 className="text-[#1A1410] text-lg font-bold leading-tight">IA Global</h1>
          <p className="text-[#6B5E55] text-[12px] mt-0.5">Configuracao central usada por Disparador, Inbox SDR, resumo e qualificacao.</p>
        </div>
      </PageHeaderShell>

      <main className="max-w-4xl mx-auto px-6 py-6">
        <div className="bg-[#E8E4DF] rounded-2xl border border-[#D4CFC9] p-6">
          {loading ? (
            <p className="text-[#6B5E55] text-sm">Carregando...</p>
          ) : (
            <div className="space-y-6">
              <div>
                <label className="block text-xs font-semibold text-[#6B5E55] uppercase tracking-wide mb-2">Provedor principal</label>
                <div className="grid grid-cols-2 gap-2">
                  {Object.keys(PROVIDER_LABEL).map((provider) => (
                    <button
                      key={provider}
                      type="button"
                      onClick={() => handleProvider(provider)}
                      className={`py-2.5 rounded-xl text-sm font-semibold border transition-colors ${
                        form.llm_provider === provider
                          ? "bg-[#CC3300] border-[#CC3300] text-[#F0EDE8]"
                          : "bg-[#F0EDE8] border-[#D4CFC9] text-[#6B5E55] hover:text-[#1A1410]"
                      }`}
                    >
                      {PROVIDER_LABEL[provider]}
                    </button>
                  ))}
                </div>
              </div>

              <div>
                <label className="block text-xs font-semibold text-[#6B5E55] uppercase tracking-wide mb-2">Modelo padrao</label>
                <select
                  value={form.llm_model}
                  onChange={(event) => setForm({ ...form, llm_model: event.target.value })}
                  className="w-full bg-[#F0EDE8] border border-[#D4CFC9] rounded-xl px-4 py-3 text-sm text-[#1A1410] outline-none focus:border-[#CC3300]"
                >
                  {MODELS[form.llm_provider].map((model) => <option key={model} value={model}>{model}</option>)}
                </select>
              </div>

              <div>
                <label className="block text-xs font-semibold text-[#6B5E55] uppercase tracking-wide mb-2">
                  Chave {PROVIDER_LABEL[form.llm_provider]}
                </label>
                <input
                  type="text"
                  autoComplete="off"
                  spellCheck={false}
                  data-lpignore="true"
                  data-form-type="other"
                  value={activeKey || ""}
                  onChange={(event) => setActiveKey(event.target.value)}
                  placeholder={form.llm_provider === "groq" ? "gsk_..." : form.llm_provider === "anthropic" ? "sk-ant-..." : "sk-proj-..."}
                  className="secret-input api-key-input w-full bg-[#F0EDE8] border border-[#D4CFC9] rounded-xl px-4 py-3 text-sm text-[#1A1410] placeholder-[#3D4F5A] outline-none focus:border-[#CC3300]"
                />
                <p className="text-[#6B5E55] text-xs mt-2">
                  Instancias que ja possuem chave propria continuam usando a chave delas. Se a instancia nao tiver chave, ela usa esta configuracao global.
                </p>
              </div>

              {error && <p className="text-[#F87171] text-sm">{error}</p>}
              {saved && <p className="text-[#CC3300] text-sm font-semibold">Configuracao global salva.</p>}

              <button
                onClick={handleSave}
                disabled={saving}
                className="w-full bg-[#CC3300] hover:bg-[#991F00] disabled:bg-[#CC3300]/40 text-[#F0EDE8] font-bold py-3 rounded-xl text-sm transition-colors"
              >
                {saving ? "Salvando..." : "Salvar IA Global"}
              </button>
            </div>
          )}
        </div>
      </main>
    </div>
  );
}
