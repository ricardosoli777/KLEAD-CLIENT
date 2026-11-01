export const metadata = {
  title: "Política de Privacidade — KLEAD",
  description: "Política de Privacidade da plataforma KLEAD.",
};

export default function PrivacidadePage() {
  return (
    <div className="min-h-screen bg-[#F0EDE8] text-[#1A1410]">
      <main className="max-w-3xl mx-auto px-5 py-14">
        <h1 className="text-2xl font-bold mb-1">Política de Privacidade</h1>
        <p className="text-sm text-[#6B5E55] mb-10">Última atualização: 6 de julho de 2026</p>

        <div className="space-y-8 text-sm leading-relaxed">
          <section>
            <p>
              O KLEAD (&quot;nós&quot;) é uma plataforma de qualificação automática de leads via WhatsApp,
              operada para atender empresas e profissionais (&quot;clientes&quot;) que utilizam o serviço para
              conversar com seus próprios contatos comerciais (&quot;usuários finais&quot;). Esta política explica
              quais dados coletamos, por que coletamos e como você pode solicitar sua remoção.
            </p>
          </section>

          <section>
            <h2 className="text-base font-bold mb-2">1. Quais dados coletamos</h2>
            <ul className="list-disc pl-5 space-y-1.5">
              <li><strong>Dados de conversa do WhatsApp:</strong> mensagens trocadas entre o cliente e seus contatos, nome, número de telefone e histórico da conversa, usados para qualificação automática do lead.</li>
              <li><strong>Dados de formulários de anúncios (Meta Lead Ads):</strong> quando um cliente vincula suas campanhas da Meta ao KLEAD, recebemos os dados que a pessoa preencheu no formulário do anúncio (nome, telefone, e-mail e demais campos configurados pelo cliente na campanha).</li>
              <li><strong>Dados de conta Meta (quando o cliente conecta via &quot;Integração Meta&quot;):</strong> token de acesso OAuth, identificadores de Business Manager, conta de anúncio, página, Instagram e WhatsApp Business Account que o cliente escolhe vincular à própria instância.</li>
              <li><strong>Dados de conta do cliente:</strong> nome, e-mail e credenciais de acesso ao painel KLEAD.</li>
            </ul>
          </section>

          <section>
            <h2 className="text-base font-bold mb-2">2. Como usamos os dados</h2>
            <ul className="list-disc pl-5 space-y-1.5">
              <li>Processar e qualificar automaticamente os contatos recebidos via WhatsApp, atribuindo-os a um CRM interno do próprio cliente.</li>
              <li>Receber, dentro da conversa do WhatsApp, os cadastros preenchidos em formulários de anúncios da Meta vinculados pelo cliente.</li>
              <li>Enviar de volta à Meta, a pedido do cliente, eventos de qualificação (ex: lead qualificado, agendamento, compra) através da Conversions API — usados pela Meta para otimizar as campanhas de anúncio do próprio cliente.</li>
              <li>Exibir métricas de campanhas e desempenho de anúncios no painel do cliente.</li>
            </ul>
          </section>

          <section>
            <h2 className="text-base font-bold mb-2">3. Armazenamento e segurança</h2>
            <p>
              Tokens de acesso e credenciais sensíveis são armazenados criptografados em repouso, com chaves
              derivadas isoladamente por usuário/instância — o comprometimento de uma credencial não expõe as
              demais. O acesso ao painel exige autenticação, e cada cliente só visualiza os dados das próprias
              instâncias.
            </p>
          </section>

          <section>
            <h2 className="text-base font-bold mb-2">4. Compartilhamento de dados</h2>
            <p>
              Não vendemos nem compartilhamos dados pessoais com terceiros para fins de marketing. Os únicos
              compartilhamentos que ocorrem são: (a) com a Meta, exclusivamente para receber cadastros de
              formulários de anúncio e enviar eventos de conversão (Conversions API) das campanhas do próprio
              cliente; e (b) com operadores (subprocessadores) estritamente para operação técnica do serviço:
            </p>
            <ul className="list-disc pl-5 space-y-1.5 mt-2">
              <li><strong>Hospedagem e banco de dados</strong> — infraestrutura onde a plataforma é executada.</li>
              <li><strong>Cloudflare</strong> — proteção de tráfego e armazenamento de cópias de segurança (backups) criptografadas.</li>
              <li><strong>Stripe</strong> — processamento de pagamentos; os dados de cartão são tratados diretamente pela Stripe e não passam pelos nossos servidores.</li>
              <li><strong>Provedores de IA</strong> (ex.: OpenAI, Anthropic) — recebem o conteúdo das conversas estritamente para gerar a qualificação automática dos leads, sem uso para treinamento de modelos conforme os termos empresariais desses provedores.</li>
            </ul>
            <p className="mt-2">
              Alguns desses operadores podem processar dados fora do Brasil. Nesses casos, a transferência
              internacional ocorre com base nas salvaguardas do art. 33 da LGPD (cláusulas contratuais e
              padrões de segurança equivalentes).
            </p>
          </section>

          <section>
            <h2 className="text-base font-bold mb-2">5. Retenção</h2>
            <p>
              Os dados de conversas e leads são mantidos enquanto a conta do cliente estiver ativa, para que o
              histórico de qualificação permaneça disponível. Tokens de integração Meta são mantidos até que o
              cliente desconecte a integração pelo próprio painel (&quot;Desconectar Meta&quot;), momento em que são
              invalidados. Cópias de segurança (backups) do banco de dados são retidas por até 14 dias no
              servidor e até 30 dias em armazenamento externo criptografado, sendo então excluídas
              automaticamente. Dados removidos a pedido do titular desaparecem dos backups ao fim desse ciclo.
            </p>
          </section>

          <section>
            <h2 className="text-base font-bold mb-2">6. Seus direitos e exclusão de dados</h2>
            <p className="mb-2">
              Em conformidade com a Lei Geral de Proteção de Dados (LGPD), qualquer titular de dados pode
              solicitar acesso, correção ou exclusão dos próprios dados armazenados pelo KLEAD.
            </p>
            <p className="mb-2">
              <strong>Se você conectou uma conta Meta ao KLEAD</strong> e deseja revogar o acesso, você pode:
            </p>
            <ul className="list-disc pl-5 space-y-1.5 mb-2">
              <li>Clicar em &quot;Desconectar Meta&quot; dentro do painel KLEAD, na tela de credenciais da instância; ou</li>
              <li>Revogar o acesso do app diretamente em <span className="whitespace-nowrap">facebook.com/settings?tab=business_tools</span>.</li>
            </ul>
            <p>
              Para solicitar a exclusão completa de qualquer dado pessoal armazenado pelo KLEAD, siga as{" "}
              <a href="/excluirdados" className="text-[#CC3300] underline">instruções de exclusão de dados</a>.
            </p>
          </section>

          <section>
            <h2 className="text-base font-bold mb-2">7. Contato</h2>
            <p>
              Dúvidas sobre esta política podem ser enviadas para{" "}
              <a href="mailto:privacidade@example.com" className="text-[#CC3300] underline">privacidade@example.com</a>.
            </p>
          </section>
        </div>
      </main>
    </div>
  );
}
