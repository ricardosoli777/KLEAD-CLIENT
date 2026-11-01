import Link from "next/link";

export const metadata = {
  title: "Exclusão de Dados — KLEAD",
  description: "Instruções para solicitar a exclusão de dados pessoais no KLEAD.",
};

export default function ExclusaoDadosPage() {
  return (
    <div className="min-h-screen bg-[#F0EDE8] text-[#1A1410]">
      <main className="max-w-3xl mx-auto px-5 py-14">
        <h1 className="text-2xl font-bold mb-1">Instruções para Exclusão de Dados</h1>
        <p className="text-sm text-[#6B5E55] mb-10">Última atualização: 6 de julho de 2026</p>

        <div className="space-y-8 text-sm leading-relaxed">
          <section>
            <p>
              Se você interagiu com um cliente do KLEAD via WhatsApp, preencheu um formulário de anúncio
              (Lead Ads) vinculado ao KLEAD, ou conectou sua própria conta da Meta à plataforma, você tem o
              direito de solicitar a exclusão dos seus dados pessoais armazenados por nós.
            </p>
          </section>

          <section>
            <h2 className="text-base font-bold mb-2">Como solicitar a exclusão</h2>
            <p className="mb-3">
              Envie um e-mail para{" "}
              <a href="mailto:privacidade@example.com?subject=Solicita%C3%A7%C3%A3o%20de%20exclus%C3%A3o%20de%20dados" className="text-[#CC3300] underline font-semibold">
                privacidade@example.com
              </a>{" "}
              com o assunto <strong>&quot;Solicitação de exclusão de dados&quot;</strong>, informando:
            </p>
            <ul className="list-disc pl-5 space-y-1.5">
              <li>O número de telefone (WhatsApp) usado na conversa; <strong>ou</strong></li>
              <li>O e-mail associado ao cadastro/lead; <strong>ou</strong></li>
              <li>O nome da instância/empresa do KLEAD com a qual você interagiu, se souber.</li>
            </ul>
          </section>

          <section>
            <h2 className="text-base font-bold mb-2">O que é excluído</h2>
            <ul className="list-disc pl-5 space-y-1.5">
              <li>Histórico de conversas do WhatsApp associado ao seu contato.</li>
              <li>Dados de lead (nome, telefone, e-mail, respostas de formulário) armazenados no CRM do cliente.</li>
              <li>Caso você seja o titular de uma conexão Meta (OAuth) no KLEAD: o token de acesso e os ativos vinculados (Business Manager, conta de anúncio, página, WhatsApp Business Account).</li>
            </ul>
          </section>

          <section>
            <h2 className="text-base font-bold mb-2">Prazo</h2>
            <p>
              As solicitações são atendidas em até <strong>15 dias corridos</strong> a partir da confirmação de
              identidade. Você recebe uma resposta por e-mail com o status do seu pedido — confirmando a
              exclusão ou, no caso raro de não podermos excluir algum dado (ex: obrigação legal de retenção),
              explicando o motivo específico da recusa.
            </p>
          </section>

          <section>
            <h2 className="text-base font-bold mb-2">Revogar acesso da conta Meta imediatamente</h2>
            <p>
              Se você conectou sua própria conta Meta ao KLEAD e quer revogar o acesso agora, sem esperar pelo
              e-mail, você pode remover a permissão diretamente em{" "}
              <span className="whitespace-nowrap">facebook.com/settings?tab=business_tools</span>, procurando
              pelo app &quot;KLEAD&quot; e removendo o acesso. Isso invalida o token imediatamente do lado da Meta.
            </p>
          </section>

          <p className="text-xs text-[#6B5E55] pt-4 border-t border-[#D4CFC9]">
            Veja também nossa <Link href="/privacidade" className="underline">Política de Privacidade</Link> completa.
          </p>
        </div>
      </main>
    </div>
  );
}
