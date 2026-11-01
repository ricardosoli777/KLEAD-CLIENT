# Configurar WhatsApp, Meta, IA e email

Use contas e endereços do responsável pela instalação. `app.example.com`, `api.example.com`, `go.example.com` e emails em `example.com` são exemplos fictícios.

## Escolher os recursos

| Recurso | O que precisa | Como verificar |
|---|---|---|
| WhatsApp por QR Code | Evolution GO compatível, URL/chave próprias e vinculação do número | Receber e responder mensagem de outro número |
| WhatsApp Cloud API | App, ativos/número, token, segredo e webhooks da conta Meta | Receber e enviar mensagem pela instância configurada |
| Formulários Lead Ads | Página/formulário, permissões, token e webhook de entrada | Gerar lead de teste e conferir a origem no CRM |
| Conversions API | Fonte de eventos, token e configuração CAPI na instância | Conferir evento de teste no ambiente da conta Meta |
| Atendimento por IA | Provedor, chave válida, instruções e IA de entrada habilitada | Confirmar resposta e transferência para humano |
| Email | SMTP com host, porta, usuário e senha | Testar convite e recuperação de acesso |

Receber formulários não configura automaticamente WhatsApp Cloud API ou CAPI. A escolha de Evolution GO para mensagens pode coexistir com Lead Ads.

## Endereços internos e públicos

`WEBHOOK_BASE_URL` e `EVO_MEDIA_BASE` podem usar um serviço interno acessível pela rede dos containers. `PUBLIC_WEBHOOK_BASE` deve indicar a API pública quando o provedor externo precisar alcançá-la.

`KLEAD_APP_URL` identifica o painel usado em links da aplicação. `ALLOWED_ORIGINS` deve corresponder às origens autorizadas do painel. `localhost` não substitui uma URL pública para callbacks de servidores externos.

Copie as URLs geradas na instalação e confirme que usam seu domínio antes de cadastrar no provedor. O pacote ainda tem referências fixas e um problema de cookie OAuth que precisam de correção para distribuição geral; veja as limitações abaixo.

## WhatsApp por Evolution GO

Use o serviço e a chave da sua instalação. O nome da instância no KLEAD pode diferir do nome no Evolution GO; use a URL de webhook produzida pelo KLEAD.

A vinculação por QR não comprova o fluxo completo. Valide entrada, saída e reconexão, e mantenha o backend alcançável pelo Evolution GO na rede configurada.

## Meta

Configure o app e os ativos da sua conta. O wizard oferece App ID, App Secret e URL de callback OAuth; ele não cria o app nem concede permissões.

Para OAuth, a URL de retorno precisa corresponder ao cadastro do app e ser alcançável pelo navegador. Para webhooks, o serviço da Meta precisa alcançar a URL pública. Em ambiente local, planeje publicação HTTPS para esses recursos.

O Verify Token confirma a verificação inicial do webhook. A autorização dos POSTs depende da rota: Lead Ads/Forms aceita assinatura válida do app ou token configurado válido; mensagens WhatsApp Cloud API exigem a assinatura validada com o segredo do app. Não use o Verify Token como substituto universal do App Secret.

Consulte o [guia detalhado de campos e fluxo](../guia/meta-zapman-crm-advanced-match.md). Use seus próprios IDs e segredos; exemplos não são credenciais utilizáveis.

### Limitação atual de OAuth e domínios

O frontend ainda trata compartilhamento do cookie CSRF de forma específica para o domínio do fornecedor. Em domínios de compradores, painel e callback em subdomínios diferentes podem resultar em `csrf_invalido`. Essa é uma pendência de código, sem correção nesta revisão documental.

Também existem endereços fixos em telas e um fallback de webhook. Alterar somente o texto dos guias não elimina essas dependências. Até corrigir e homologar, o fluxo Meta não deve ser anunciado como pronto para qualquer domínio.

## IA e SMTP

As chaves de IA informadas no wizard têm prioridade como configuração de ambiente. Para administrar uma chave exclusivamente pelo dashboard, o responsável precisa retirar a variável correspondente da configuração privada e reaplicá-la pelo procedimento de atualização de configuração.

SMTP não é necessário para digitar uma senha de administrador já definida, mas é necessário para envio de convites e recuperação por email. Teste a entrega; salvar host e senha não comprova que a mensagem chegou.

Configuração privada e segredos devem permanecer fora de exemplos, imagens e material enviado ao suporte. Veja [diagnóstico](../operacao/diagnostico.md) para preparar uma solicitação sem esses valores.
