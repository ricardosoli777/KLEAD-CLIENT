export const metadata = {
  title: "Termos de Uso — KLEAD",
  description: "Termos de Uso da plataforma KLEAD.",
};

export default function TermosPage() {
  return (
    <div className="min-h-screen bg-[#F0EDE8] text-[#1A1410]">
      <main className="max-w-3xl mx-auto px-5 py-14">
        <h1 className="text-2xl font-bold mb-1">Termos de Uso</h1>
        <p className="text-sm text-[#6B5E55] mb-10">Última atualização: 18 de julho de 2026</p>

        <div className="space-y-8 text-sm leading-relaxed">
          <section>
            <p>
              Estes Termos de Uso regem o acesso e uso da plataforma KLEAD, destinada a empresas e
              profissionais que desejam qualificar automaticamente contatos comerciais recebidos via WhatsApp.
              Ao criar uma conta ou utilizar o KLEAD, você concorda com estes termos.
            </p>
          </section>

          <section>
            <h2 className="text-base font-bold mb-2">1. O que é o KLEAD</h2>
            <p>
              O KLEAD conecta o WhatsApp Business do cliente a um sistema de qualificação automática de leads
              (SDR virtual) e a um CRM próprio. Opcionalmente, o cliente pode conectar sua conta da Meta para
              receber cadastros de formulários de anúncios (Lead Ads) diretamente na conversa do WhatsApp, e
              enviar de volta eventos de qualificação (Conversions API) para otimizar suas campanhas.
            </p>
          </section>

          <section>
            <h2 className="text-base font-bold mb-2">2. Conta e responsabilidades do cliente</h2>
            <ul className="list-disc pl-5 space-y-1.5">
              <li>O cliente é responsável por manter a confidencialidade das credenciais de acesso ao painel.</li>
              <li>O cliente é responsável por garantir que possui base legal (LGPD) para tratar os dados dos próprios contatos processados via KLEAD.</li>
              <li>O cliente é responsável pelo conteúdo das mensagens enviadas através da plataforma, respeitando as políticas de uso do WhatsApp e da Meta.</li>
              <li>Ao conectar uma conta Meta, o cliente autoriza o KLEAD a acessar, em seu nome, os ativos (páginas, contas de anúncio, WhatsApp Business Account) que ele mesmo selecionar.</li>
            </ul>
          </section>

          <section>
            <h2 className="text-base font-bold mb-2">3. Uso aceitável</h2>
            <p>
              É proibido usar o KLEAD para envio de mensagens não solicitadas em massa (spam), conteúdo ilegal,
              enganoso ou que viole direitos de terceiros. O uso do módulo de integração com a Meta deve
              respeitar os Termos da Plataforma Meta e as Políticas de Negócios do WhatsApp.
            </p>
          </section>

          <section>
            <h2 className="text-base font-bold mb-2">4. Disponibilidade do serviço</h2>
            <p>
              O KLEAD é fornecido &quot;como está&quot;. Envidamos esforços para manter o serviço disponível e
              seguro, mas não garantimos operação ininterrupta, especialmente diante de instabilidades de
              serviços de terceiros integrados (Meta, provedores de WhatsApp).
            </p>
          </section>

          <section>
            <h2 className="text-base font-bold mb-2">5. Planos, pagamento e reembolso</h2>
            <ul className="list-disc pl-5 space-y-1.5">
              <li>O KLEAD é oferecido em planos de assinatura recorrente (mensal ou conforme ciclo contratado), cobrados de forma antecipada através de processador de pagamento terceirizado (Stripe). Não armazenamos dados de cartão em nossos servidores.</li>
              <li>Os valores, limites e recursos de cada plano são os exibidos na página de planos no momento da contratação. Alterações de preço serão comunicadas com antecedência mínima de 30 dias e valem a partir do ciclo seguinte.</li>
              <li><strong>Direito de arrependimento:</strong> em conformidade com o art. 49 do Código de Defesa do Consumidor, o cliente pode desistir da contratação em até 7 (sete) dias corridos após a primeira compra, com reembolso integral do valor pago.</li>
              <li>O cancelamento da assinatura pode ser feito a qualquer momento pelo painel ou por e-mail. Após o cancelamento, o serviço permanece ativo até o fim do período já pago; não há reembolso proporcional de períodos já iniciados, salvo o caso de arrependimento acima ou falha grave de prestação do serviço imputável ao KLEAD.</li>
              <li>A inadimplência pode acarretar suspensão do acesso após aviso, e exclusão dos dados conforme o item 7.</li>
            </ul>
          </section>

          <section>
            <h2 className="text-base font-bold mb-2">6. Propriedade intelectual</h2>
            <p>
              O software, marca, layout e demais elementos da plataforma KLEAD são de propriedade de
              Ricardo Soares — © 2026, todos os direitos reservados — e protegidos pela legislação aplicável. A assinatura concede licença de uso, pessoal e
              intransferível, limitada à vigência do contrato. Os dados inseridos ou gerados pelo cliente
              (leads, conversas, configurações) permanecem de propriedade do cliente.
            </p>
          </section>

          <section>
            <h2 className="text-base font-bold mb-2">7. Proteção de dados, backups e portabilidade</h2>
            <ul className="list-disc pl-5 space-y-1.5">
              <li>Realizamos backups automáticos diários do banco de dados, com cópia adicional armazenada fora do servidor principal (off-site), retida por até 30 dias.</li>
              <li>Os backups destinam-se à recuperação de desastres da plataforma e não substituem exportações próprias do cliente.</li>
              <li>Ao encerrar a conta, o cliente pode solicitar a exportação dos seus leads e conversas em formato legível por máquina (portabilidade) em até 30 dias após o encerramento. Após esse prazo, os dados são excluídos definitivamente, ressalvadas obrigações legais de retenção.</li>
              <li>O tratamento de dados pessoais segue a nossa <a href="/privacidade" className="text-[#CC3300] underline">Política de Privacidade</a> e a Lei Geral de Proteção de Dados (Lei nº 13.709/2018).</li>
            </ul>
          </section>

          <section>
            <h2 className="text-base font-bold mb-2">8. Limitação de responsabilidade</h2>
            <p>
              Na máxima extensão permitida em lei, a responsabilidade total do KLEAD por danos decorrentes do
              uso da plataforma fica limitada ao valor efetivamente pago pelo cliente nos 12 (doze) meses
              anteriores ao evento. O KLEAD não responde por lucros cessantes, por decisões comerciais tomadas
              com base nas qualificações automáticas geradas por IA, nem por indisponibilidades causadas por
              serviços de terceiros (Meta, WhatsApp, provedores de infraestrutura), sem prejuízo dos direitos
              do consumidor previstos em lei.
            </p>
          </section>

          <section>
            <h2 className="text-base font-bold mb-2">9. Cancelamento e encerramento</h2>
            <p>
              O cliente pode encerrar sua conta a qualquer momento. Dados de conversas e leads podem ser
              removidos mediante solicitação, conforme descrito na nossa{" "}
              <a href="/privacidade" className="text-[#CC3300] underline">Política de Privacidade</a>.
              O KLEAD pode suspender ou encerrar contas que violem estes termos, mediante aviso prévio,
              exceto em casos de violação grave (spam, fraude, ilegalidade), em que a suspensão pode ser imediata.
            </p>
          </section>

          <section>
            <h2 className="text-base font-bold mb-2">10. Contato</h2>
            <p>
              Dúvidas sobre estes termos podem ser enviadas para{" "}
              <a href="mailto:contato@example.com" className="text-[#CC3300] underline">contato@example.com</a>.
            </p>
          </section>
        </div>
      </main>
    </div>
  );
}
