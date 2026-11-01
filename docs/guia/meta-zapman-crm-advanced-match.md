# Guia Meta Ads + KLEAD CRM

Use dados e contas da sua instalação; todos os valores de exemplo são fictícios. Antes de configurar, leia [integrações e limitações atuais](../integracoes/configuracao.md), incluindo a pendência de OAuth em domínio próprio. Este guia descreve campos e fluxos, sem declarar homologação para todos os ambientes.

Este guia mostra, de forma simples e passo a passo, como conectar a Meta ao KLEAD CRM para:

- receber leads da Meta Ads no CRM;
- enviar dados de volta para a Meta via Conversions API;
- melhorar o `Advanced Matching` com dados hashados.

## Visão geral do fluxo

O fluxo tem 2 sentidos diferentes:

1. **Meta -> KLEAD**: a Meta envia o lead para o webhook do KLEAD.
2. **KLEAD -> Meta**: o KLEAD devolve eventos para a Meta via CAPI.

Importante:

- O webhook de entrada não é o mesmo fluxo do retorno.
- Receber o lead no CRM e enviar sinais de volta para a Meta são etapas separadas.

## O que você precisa antes de começar

Separe estas credenciais e dados:

- `WEBHOOK_SECRET`
- `meta_token`
- `meta_app_secret`
- `meta_page_id`
- `phone_number_id` se for usar WhatsApp Cloud API
- `meta_capi_access_token`
- `meta_capi_event_source_id`

## O que cada item significa

### 1. `WEBHOOK_SECRET`

É a configuração global de token de webhook. Também há segredos por instância; use o `Verify Token` informado pelo KLEAD para a rota/instância que você está configurando.

O token participa da verificação inicial por `GET`. Nos endpoints Lead Ads/Forms, o `POST` aceita assinatura válida do app ou token configurado válido. Mensagens WhatsApp Cloud API exigem assinatura `X-Hub-Signature-256` validada com o segredo do app. O Verify Token não substitui universalmente o App Secret.

### 2. `meta_token`

É o access token da Meta Graph API.

Ele pode ser usado para:

- buscar dados completos do lead;
- consultar detalhes da conta e da página;
- apoiar integrações com a Meta.

### 3. `meta_app_secret`

É o segredo do app Meta.

Ele serve para validar a assinatura dos webhooks no header:

```text
X-Hub-Signature-256
```

Isso ajuda a confirmar que a requisição veio mesmo da Meta.

### 4. `meta_page_id`

É o ID da Página vinculada ao formulário de Lead Ads.

O KLEAD usa esse ID para identificar qual instância deve receber o lead.

### 5. `phone_number_id`

É o ID do número do WhatsApp Cloud API.

Não é o número do cliente.

Use esse dado apenas se também for enviar mensagens pela API oficial do WhatsApp.

### 6. `meta_capi_enabled`

Liga ou desliga o envio de eventos de volta para a Meta.

### 7. `meta_capi_access_token`

Token usado para chamar a Conversions API.

### 8. `meta_capi_event_source_id`

Identificador da fonte de eventos usada na CAPI.

### 9. `meta_capi_test_event_code`

Código opcional de teste da CAPI.

Use quando quiser validar a integração sem depender de produção.

## Chaves, tokens e APIs

Esta seção mostra de onde cada chave vem, para onde ela vai e para que ela serve.

| Chave / API | Vem de | Vai para | Para que serve | Exemplo fictício |
|---|---|---|---|---|
| `WEBHOOK_SECRET` / segredo da instância | KLEAD | Use o `Verify Token` informado para a rota/instância | Verificação inicial; nos endpoints Lead Ads/Forms, token válido também é uma das formas de autorizar o POST | `whsec_demo_9f2a7c4b` |
| `meta_token` | Meta for Developers | Salvo na instância do KLEAD | Buscar detalhes do lead, página, conta e métricas da Meta | `EAA_demo_token_123` |
| `meta_app_secret` | Meta for Developers | Salvo na instância do KLEAD | Validar a assinatura `X-Hub-Signature-256` dos webhooks | `appsec_demo_8d51` |
| `meta_page_id` | Página da Meta / Lead Ads | Salvo na instância do KLEAD | Rotejar o lead para a instância correta | `PAGE_123456789` |
| `phone_number_id` | WhatsApp Cloud API | Salvo na instância do KLEAD | Enviar e receber mensagens pelo WhatsApp oficial | `123456789012345` |
| `meta_capi_access_token` | Meta / Conversions API | Salvo na instância do KLEAD | Enviar eventos server-side para a Meta | `EAA_demo_capi_456` |
| `meta_capi_event_source_id` | Meta Events Manager | Salvo na instância do KLEAD | Identificar a fonte de eventos usada na CAPI | `987654321012345` |
| `meta_capi_test_event_code` | Meta Events Manager | Salvo na instância do KLEAD | Testar envios sem depender de produção | `TEST1234` |
| `meta_ad_account_id` | Meta Ads Account | Salvo na instância do KLEAD | Consultar métricas de campanha no dashboard | `act_123456789012345` |

Regras rápidas:

- **Meta -> KLEAD** usa o webhook e sua validação, o segredo do app para assinatura e os identificadores necessários para rotear os eventos.
- **KLEAD -> Meta** usa o access token apropriado para buscar dados/enviar mensagens; para CAPI, usa `meta_capi_access_token` e `meta_capi_event_source_id`.
- O Verify Token pertence à configuração do webhook de entrada, não à autenticação das chamadas de CAPI.
- `meta_capi_test_event_code` é opcional e serve só para teste.

## Passo a passo para configurar

### Passo 1. Criar o app na Meta

1. Entre no Meta for Developers.
2. Crie um app novo.
3. Escolha o tipo de app compatível com o seu cenário.
4. Habilite os produtos necessários, como:
   - Webhooks;
   - Lead Ads;
   - WhatsApp Cloud API, se for usar.

### Passo 2. Pegar as credenciais

1. Copie o `meta_token`.
2. Copie o `meta_app_secret`.
3. Copie o `meta_page_id`.
4. Copie o `phone_number_id` se o projeto usar WhatsApp Cloud API.
5. Gere e salve o `meta_capi_access_token`.
6. Separe o `meta_capi_event_source_id`.

### Passo 3. Publicar o KLEAD

O KLEAD precisa estar acessível por uma URL pública.

Essa URL será usada pela Meta para chamar o webhook.

### Passo 4. Configurar o webhook no KLEAD

No backend do KLEAD, a Meta expõe a informação de configuração por instância em:

```text
GET /webhook/meta-info/{instance_id}
```

Esse retorno ajuda a montar:

- a URL pública do webhook;
- o `verify token`;
- a referência da página.

### Passo 5. Configurar o webhook na Meta

No painel da Meta:

1. Cadastre a URL pública do webhook.
2. Informe o `Verify Token`.
3. Assine os eventos corretos.

O fluxo de verificação funciona assim:

1. A Meta faz um `GET` com `hub.mode`, `hub.verify_token` e `hub.challenge`.
2. O KLEAD valida o token.
3. Se estiver correto, devolve o `hub.challenge`.
4. Depois disso a Meta começa a enviar os `POST` com os payloads.

### Passo 6. Configurar a instância no KLEAD

Na tela da instância, preencha os campos da Meta:

- `meta_page_id`
- `meta_token`
- `meta_app_secret`
- `phone_number_id`, se necessário
- `meta_capi_enabled`
- `meta_capi_access_token`
- `meta_capi_event_source_id`
- `meta_capi_test_event_code`, se quiser testar

### Passo 7. Escolher quais dados serão recebidos

Em um lead da Meta, normalmente você trabalha com:

- nome;
- telefone;
- email;
- id do lead;
- id da página;
- id do formulário;
- respostas extras do formulário.

O KLEAD tenta montar o lead com o que vier no payload.

### Passo 8. Salvar o lead no CRM

Quando o `POST` chega, o KLEAD:

1. lê o JSON;
2. identifica a instância;
3. normaliza nome, telefone e email;
4. salva o lead no CRM;
5. registra a origem da Meta;
6. guarda os campos extras.

### Passo 9. Enviar sinais de volta para a Meta

Quando `meta_capi_enabled` estiver ativo, o KLEAD pode mandar eventos para a Meta via Conversions API.

Os eventos mais comuns são:

- `Lead`
- `QualifiedLead`
- `Contact`

Esses eventos ajudam a Meta a entender melhor o ciclo do lead.

### Passo 10. Melhorar o Advanced Matching

O KLEAD pode enviar dados hashados para a Meta, como:

- email;
- telefone;
- nome;
- sobrenome;
- `external_id`.

Se o lead tiver mais contexto disponível, o envio também pode carregar:

- cidade;
- estado;
- CEP;
- país;
- `fbc`;
- `fbp`;
- `client_ip_address`;
- `client_user_agent`.

Quanto mais dados válidos você coletar, melhor tende a ser o matching.

## Como validar se está funcionando

### Validação de entrada

1. Gere um lead de teste na Meta.
2. Verifique se o webhook do KLEAD recebeu o `POST`.
3. Confira se o lead apareceu no CRM.

### Validação de retorno

1. Atualize o lead ou mova a etapa no CRM.
2. Verifique se o KLEAD enviou o evento para a Meta.
3. Confirme no Events Manager se o evento chegou.

### O que você deve ver

- lead entrando no CRM;
- origem identificada como Meta;
- evento de retorno sendo enviado quando a CAPI estiver ativa.

## Erros mais comuns

### 1. Webhook não valida

Possíveis causas:

- `WEBHOOK_SECRET` incorreto;
- URL pública errada;
- assinatura do webhook não configurada.

### 2. Lead chega sem dados suficientes

Possíveis causas:

- formulário com poucos campos;
- payload incompleto;
- mapeamento da instância errado.

### 3. CAPI não envia eventos

Possíveis causas:

- `meta_capi_enabled` desligado;
- `meta_capi_access_token` ausente;
- `meta_capi_event_source_id` vazio.

### 4. Advanced Matching fraco

Possíveis causas:

- poucos dados no formulário;
- email e telefone ausentes;
- dados não normalizados antes do hash.

## Ordem recomendada de implantação

1. Criar o app da Meta.
2. Pegar todas as credenciais.
3. Publicar o KLEAD com URL pública.
4. Configurar o webhook na Meta.
5. Salvar os dados da instância no KLEAD.
6. Testar o recebimento de lead.
7. Ativar a CAPI.
8. Validar o retorno e o Advanced Matching.

## Regra simples para lembrar

- **Meta -> KLEAD**: recebe o lead.
- **KLEAD -> CRM**: salva e organiza.
- **KLEAD -> Meta**: devolve sinais para CAPI e Advanced Matching.

## Arquivos relacionados

- [dashboard/backend/main.py](../../dashboard/backend/main.py)
- [dashboard/backend/services/meta_capi_service.py](../../dashboard/backend/services/meta_capi_service.py)
- [dashboard/frontend/app/guide/page.tsx](../../dashboard/frontend/app/guide/page.tsx)
