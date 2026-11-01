# Política de Privacidade — KLEAD

> **⚠️ RASCUNHO — REVISÃO JURÍDICA PENDENTE**
> Este documento é um ponto de partida técnico, escrito com base no funcionamento real do sistema. Não substitui a revisão de um advogado antes de publicar ou usar como documento vinculante, especialmente porque a plataforma processa dados pessoais de terceiros (leads) em nome de clientes pagantes.
> Campos entre `[COLCHETES]` precisam ser preenchidos antes da publicação.

**Última atualização:** [DATA]

## 1. Quem somos

Esta Política de Privacidade se aplica ao uso da plataforma **KLEAD** (`https://app.example.com` — endereço fictício a substituir pelo domínio desta instalação), operada por **[NOME/RAZÃO SOCIAL DO RESPONSÁVEL]**, doravante "KLEAD", "nós".

Contato para assuntos de privacidade e proteção de dados: **[E-MAIL DE CONTATO / ENCARREGADO DE DADOS - DPO]**

## 2. Papéis: quem é o quê, segundo a LGPD

- **Cliente da plataforma** (ex: corretor de imóveis, imobiliária) contrata o KLEAD para qualificar seus próprios leads. Nessa relação, o **cliente é o Controlador** dos dados dos leads dele — ele decide para que usa aquele dado.
- **KLEAD atua como Operador** — processamos os dados dos leads *em nome* do cliente, seguindo as instruções configuradas por ele na plataforma (prompt de qualificação, critérios de score, etc).
- Em relação aos **dados do próprio usuário da plataforma** (nome, e-mail, senha de quem faz login no KLEAD), o **KLEAD é Controlador**.

## 3. Quais dados coletamos

### 3.1. Dados de leads (processados em nome do cliente)
- Nome, telefone (WhatsApp), e-mail (quando informado)
- Cidade, estado, CEP, país (quando informado em formulário)
- Conteúdo das mensagens trocadas com o agente de IA ou atendente humano
- Dados vindos de formulários de anúncio da Meta (Lead Ads/Instant Forms): respostas do formulário, ID do anúncio/campanha, `leadgen_id`
- Score de qualificação e histórico de estágio no funil (CRM)

### 3.2. Dados do usuário da plataforma (cliente que usa o KLEAD)
- Nome, e-mail, senha (armazenada com hash bcrypt — nunca em texto puro)
- Registro de acesso (login, IP, data/hora)
- Credenciais de integração (chaves de API de IA, tokens do WhatsApp/Meta) — armazenadas **criptografadas** no banco de dados (Fernet, chave derivada de segredo do servidor)

### 3.3. Dados técnicos
- Logs de erro e diagnóstico (via Sentry) — configurados para **não incluir dado pessoal de lead** (`send_default_pii=False`)
- Logs de auditoria de acesso administrativo (ver seção 8)

## 4. Para que usamos esses dados

- Qualificar leads automaticamente via inteligência artificial (OpenAI, Anthropic ou Groq, conforme escolha do cliente)
- Permitir que o time do cliente acompanhe, responda e gerencie leads no CRM/Inbox
- Medir a qualidade dos anúncios/campanhas via integração com a Meta Conversions API
- Garantir segurança, prevenir fraude e cumprir obrigação legal
- Suporte técnico e manutenção da plataforma

## 5. Com quem compartilhamos dados

Para funcionar, o KLEAD envia dados a fornecedores que processam informação em nosso nome:

| Fornecedor | Finalidade | Dado envolvido |
|---|---|---|
| OpenAI / Anthropic / Groq | Geração de respostas de IA | Histórico da conversa, nome do lead |
| Meta (WhatsApp Business API / Conversions API) | Envio de mensagens, otimização de campanha | Telefone, nome, e-mail (hasheados no CAPI), estágio do funil |
| Evolution GO | Conexão com WhatsApp | Mensagens, telefone |
| Sentry | Monitoramento de erro técnico | Dados técnicos, sem PII de lead |
| [Gateway de pagamento — quando implementado] | Cobrança de assinatura | Dados do cliente pagante, não do lead |

Não vendemos nem alugamos dados de leads a terceiros para fins de marketing próprio.

## 6. Por quanto tempo guardamos os dados

- Dados de leads: enquanto durar a relação contratual entre o KLEAD e o cliente que gerou aquele lead, ou até o cliente solicitar exclusão
- Dados de conta do usuário: enquanto a conta estiver ativa
- Logs de auditoria/segurança: [DEFINIR — sugestão: 6 a 12 meses]
- Backups: retidos por período limitado para recuperação de desastre, depois descartados

## 7. Seus direitos (titular dos dados)

Conforme a LGPD (Lei 13.709/2018), o titular dos dados pode solicitar:
- Confirmação de que processamos seu dado
- Acesso aos dados
- Correção de dados incompletos, inexatos ou desatualizados
- Exclusão dos dados (exceto quando exigido por lei manter)
- Portabilidade
- Informação sobre com quem compartilhamos o dado
- Revogação de consentimento, quando aplicável

Se você é um **lead** (contato de um cliente do KLEAD) e quer exercer esses direitos, o pedido deve ser direcionado **à empresa que fez contato com você** (nosso cliente, que é o Controlador dos seus dados) — nós, KLEAD, atendemos essa solicitação através dele.

Se você é um **usuário cadastrado na plataforma**, pode solicitar diretamente pelo e-mail: [E-MAIL DE CONTATO]

## 8. Acesso administrativo interno

Nossa equipe pode acessar dados de conta de clientes exclusivamente para fins de **suporte técnico, correção de erro, investigação de abuso ou obrigação legal** — nunca por curiosidade ou finalidade comercial não relacionada.

Todo acesso de administrador da plataforma aos dados de uma conta de cliente é **registrado em log de auditoria** (quem acessou, quando, qual conta), disponível para consulta caso o cliente solicite transparência sobre acessos à sua conta.

## 9. Segurança

- Senhas armazenadas com hash bcrypt (nunca texto puro)
- Credenciais de API/tokens sensíveis criptografadas no banco (Fernet)
- Autenticação via JWT com expiração de sessão e possibilidade de revogação
- Conexões via HTTPS
- Rate limiting contra abuso
- Isolamento de dados por cliente — um cliente não acessa dados de outro

Apesar dessas medidas, nenhum sistema é 100% imune a incidentes. Em caso de incidente de segurança que afete dados pessoais, seguiremos os procedimentos de notificação exigidos pela LGPD.

## 10. Cookies

Usamos um cookie de sessão (`klead_token`), essencial para manter o usuário autenticado — não usamos cookies de rastreamento publicitário próprios.

## 11. Alterações nesta política

Podemos atualizar esta política periodicamente. Mudanças relevantes serão comunicadas aos clientes com antecedência razoável.

## 12. Contato

Dúvidas sobre esta política ou sobre o tratamento de dados: **[E-MAIL DE CONTATO / ENCARREGADO DE DADOS]**
