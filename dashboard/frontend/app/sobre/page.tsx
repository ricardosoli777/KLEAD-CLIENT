import Link from "next/link";

export const metadata = {
  title: "KLEAD-CLIENT — Qualificação automática de leads via WhatsApp",
  description: "O KLEAD-CLIENT conecta seu WhatsApp Business a um SDR virtual que qualifica leads automaticamente e organiza tudo em um CRM próprio.",
};

const FEATURES = [
  {
    title: "SDR virtual no WhatsApp",
    text: "Cada contato que chega no seu WhatsApp Business é atendido e qualificado automaticamente, 24 horas por dia.",
  },
  {
    title: "CRM integrado",
    text: "Leads qualificados caem direto num quadro Kanban simples, sem precisar exportar ou copiar dados manualmente.",
  },
  {
    title: "Integração com Meta Ads",
    text: "Conecte sua conta da Meta para receber cadastros de Lead Ads direto na conversa, e enviar de volta eventos de KPI para otimizar suas campanhas.",
  },
];

export default function SobrePage() {
  return (
    <div className="min-h-screen bg-[#F0EDE8] text-[#1A1410]">
      <main className="max-w-4xl mx-auto px-5 py-16">
        <div className="text-center mb-16">
          <div className="w-14 h-14 rounded-2xl bg-[#CC3300] text-white flex items-center justify-center font-bold text-xl mx-auto mb-5">
            K
          </div>
          <h1 className="text-3xl sm:text-4xl font-bold mb-4 text-wrap-balance">
            Qualificação automática de leads, direto no WhatsApp
          </h1>
          <p className="text-[#6B5E55] text-base max-w-xl mx-auto">
            O KLEAD-CLIENT transforma cada conversa recebida no seu WhatsApp Business em um lead qualificado
            e organizado — sem depender de um time de SDR manual.
          </p>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 mb-16">
          {FEATURES.map((f) => (
            <div key={f.title} className="bg-white rounded-2xl border border-[#D4CFC9] p-5">
              <h2 className="font-bold text-[#1A1410] mb-2 text-sm">{f.title}</h2>
              <p className="text-xs text-[#6B5E55] leading-relaxed">{f.text}</p>
            </div>
          ))}
        </div>

        <div className="text-center border-t border-[#D4CFC9] pt-10">
          <p className="text-sm text-[#6B5E55] mb-4">
            Já é cliente?{" "}
            <Link href="/login" className="text-[#CC3300] font-semibold underline">
              Entrar no painel
            </Link>
          </p>
          <p className="text-xs text-[#6B5E55]">
            <Link href="/privacidade" className="underline">Política de Privacidade</Link>
            {" · "}
            <Link href="/termos" className="underline">Termos de Uso</Link>
          </p>
        </div>
      </main>
    </div>
  );
}
