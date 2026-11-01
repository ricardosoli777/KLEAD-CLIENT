# Meta + KLEAD: guia simples de integração

Os endereços sob `example.com` neste guia são fictícios. Substitua `https://api.example.com` pela URL pública HTTPS da API da sua instalação e use os identificadores e segredos configurados por você.

> Este guia foi consolidado e expandido em [docs/guia/meta-zapman-crm-advanced-match.md](guia/meta-zapman-crm-advanced-match.md).

Antes de configurar, confira [integrações e limitações atuais](integracoes/configuracao.md), inclusive a pendência de OAuth para domínios próprios. Este guia explica os campos; ele não comprova homologação da integração em qualquer instalação.

Este documento explica, em linguagem direta, como a Meta envia leads para o KLEAD e como o KLEAD devolve eventos para a Meta para melhorar o matching/Advanced Match.

## Resumo em 1 minuto

1. A pessoa preenche um formulário na Meta ou envia dados por um fluxo conectado.
2. A Meta chama a URL pública do KLEAD via webhook.
3. O backend do KLEAD lê o payload, encontra a instância certa e salva o lead no CRM.
4. Se a instância estiver configurada para isso, o KLEAD também envia eventos de volta para a Meta via Conversions API.

O ponto importante:

- **Entrada de leads**: Meta -> KLEAD
- **Retorno para Advanced Match / CAPI**: KLEAD -> Meta

O webhook de entrada não é o mesmo fluxo do retorno.

## O que cada coisa significa

### 1) `instance_id`

É o ID interno do KLEAD.

Exemplo:

```text
minha-conta-meta
```

Esse ID aparece na URL do webhook:

```text
https://api.example.com/webhook/minha-conta-meta?token=SEU_WEBHOOK_SECRET
```

### 2) `WEBHOOK_SECRET`

É a configuração global de token de webhook. O KLEAD também possui segredos por instância: use o `Verify Token` exibido para o fluxo/instância que você está configurando, em vez de presumir que todos os canais usam o mesmo valor.

O token participa da verificação inicial por `GET`. Nos endpoints Lead Ads/Forms, o `POST` aceita assinatura válida do app ou token configurado válido. Para mensagens WhatsApp Cloud API, a assinatura `X-Hub-Signature-256` com o segredo do app é obrigatória. O Verify Token não substitui universalmente o App Secret.

### 3) `meta_token`

É o access token da Meta Graph API.

Ele é usado quando o KLEAD precisa:

- buscar detalhes completos do lead
- consultar métricas de anúncios
- enviar eventos de CAPI, se aplicável ao seu setup

### 4) `meta_app_secret`

É o segredo do app Meta.

Ele é usado para validar a assinatura do webhook da Meta no header:

```text
X-Hub-Signature-256
```

Isso confirma que o payload veio mesmo da Meta.

### 5) `meta_page_id`

É o ID da Página do Facebook/Instagram conectada ao formulário de Lead Ads.

O KLEAD usa isso para descobrir qual instância deve receber aquele lead.

### 6) `phone_number_id`

É o ID do número do WhatsApp Cloud API da Meta.

Isso **não é** o número de telefone do cliente.

Esse ID serve para enviar mensagens no WhatsApp pela API da Meta.

Se você só quer receber leads no CRM, ele não é o mais importante.

Se você quer responder o lead via WhatsApp Cloud API, ele é obrigatório.

### 7) `meta_capi_enabled`

Liga ou desliga o envio de eventos de volta para a Meta.

### 8) `meta_capi_access_token`

Token usado para chamar a Conversions API.

### 9) `meta_capi_event_source_id`

É a fonte de eventos usada na CAPI.

Na prática, é o identificador que a Meta usa para receber os eventos de volta.

### 10) `meta_capi_test_event_code`

Código opcional de teste da CAPI.

Serve para validar a integração sem depender de produção.

## Quais dados a Meta manda para o KLEAD

A Meta manda um JSON de webhook com campos como:

- nome
- telefone
- email
- id do lead
- id da página
- formulário
- respostas extras

O KLEAD não depende só de um campo fixo. Ele tenta montar o lead com o que vier no payload.

Se o formulário tiver mais campos, o KLEAD pode guardar isso como dados extras no CRM.

## Onde o KLEAD recebe isso no código

Os endpoints principais são:

- `GET /webhook/meta-leads`
- `POST /webhook/meta-leads`
- `GET /webhook/meta-forms`
- `POST /webhook/meta-forms`

Esses endpoints fazem:

- a verificação do webhook no `GET`
- a leitura do payload no `POST`
- o salvamento do lead no CRM

## Como a Meta conversa com o KLEAD

### Passo 1: você configura a URL no painel da Meta

No painel da Meta, você coloca:

- a URL pública do webhook
- o `Verify Token`

No KLEAD, essa informação sai em:

- `GET /webhook/meta-info/{instance_id}`
- e também na tela de configuração da Meta no dashboard

### Passo 2: a Meta faz a verificação

A Meta chama a URL com parâmetros parecidos com:

```text
hub.mode=subscribe
hub.verify_token=...
hub.challenge=...
```

Se o `verify_token` corresponder ao segredo aceito para aquela rota/instância, o KLEAD devolve o `hub.challenge`. Use o valor informado pelo KLEAD para esse fluxo.

Isso confirma para a Meta que a URL é válida.

### Passo 3: a Meta envia o lead

Depois da verificação, a Meta faz `POST` para o mesmo webhook com o payload do lead.

O KLEAD:

1. lê o JSON
2. descobre a instância pela `page_id`
3. normaliza nome, telefone e email
4. chama o CRM interno
5. salva o lead

## Como o lead entra no CRM KLEAD

No backend, o fluxo principal é:

1. receber o webhook
2. achar a instância certa
3. montar os dados do lead
4. chamar `crm_svc.upsert_lead(...)`
5. registrar origem, página, formulário e campos extras

Ou seja: a Meta não fala direto com o banco do CRM.
Ela fala com o backend do KLEAD, e o backend é quem grava no CRM.

## Como o KLEAD devolve dados para a Meta

Isso é o ponto que mais confunde.

O retorno para a Meta **não é o mesmo webhook de entrada**.

Quando um lead entra ou muda de etapa no CRM, o KLEAD pode enviar um evento para a Meta usando CAPI.

Esse envio acontece quando a instância Meta está configurada com:

- `meta_capi_enabled = true`
- `meta_capi_access_token`
- `meta_capi_event_source_id`

### O que o KLEAD envia de volta

Ele envia eventos como:

- `Lead`
- `QualifiedLead`
- `Contact`

Esses eventos ajudam a Meta a correlacionar o lead com o que aconteceu no CRM.

### Como o Advanced Match melhora

O KLEAD pega os dados do lead e envia hashes de:

- email
- telefone
- nome
- sobrenome
- external_id

Quanto mais dados você captar no formulário, melhor o matching.

Se você só tiver nome, o matching funciona pior.
Se você tiver nome + telefone + email, ele melhora muito.

## O que você precisa preencher na prática

### Se o seu objetivo é só receber leads no CRM

Você precisa, no mínimo:

- `WEBHOOK_SECRET`
- `PUBLIC_WEBHOOK_BASE` ou uma URL pública válida
- `meta_page_id`
- `meta_token`
- configurar a assinatura do webhook na Meta

### Se o seu objetivo é também mandar eventos de volta para a Meta

Além do que já foi dito, você precisa:

- `meta_capi_enabled = true`
- `meta_capi_access_token`
- `meta_capi_event_source_id`

### Se o seu objetivo é responder por WhatsApp Cloud API

Você precisa também:

- `phone_number_id`
- `meta_token`

## Ordem correta de configuração

### 1. Criar o app da Meta

Você cria o app no painel da Meta e habilita o que for necessário para:

- Lead Ads / formulários
- WhatsApp Cloud API, se for usar
- Webhooks

### 2. Pegar as credenciais

Separe estes dados:

- `meta_token`
- `meta_app_secret`
- `meta_page_id`
- `phone_number_id`
- `meta_capi_access_token`
- `meta_capi_event_source_id`

### 3. Publicar o KLEAD

O KLEAD precisa ter uma URL pública.

Essa URL vai receber os webhooks da Meta.

### 4. Configurar o webhook na Meta

No painel da Meta:

- coloque a URL pública do KLEAD
- coloque o `Verify Token` informado para essa rota/instância
- assine os eventos corretos

### 5. Testar com um lead real

Preencha um formulário de teste.

Se tudo estiver certo:

- o lead aparece no CRM
- o payload fica salvo com origem Meta
- os eventos de CAPI podem ser enviados de volta

## Onde cada coisa fica no KLEAD

### Backend

- recebe o webhook
- valida assinatura/token
- salva lead no CRM
- envia eventos para a Meta

### Frontend

- mostra as URLs
- mostra o `Verify Token`
- ajuda a copiar a configuração certa

### CRM

- guarda o lead
- guarda origem e campanha
- permite mover o lead entre colunas

## Exemplo mental do fluxo

Imagine este cenário:

1. A pessoa preenche o formulário da Meta.
2. A Meta chama:

```text
https://api.example.com/webhook/minha-instancia?token=SEU_WEBHOOK_SECRET
```

3. O KLEAD identifica a página e a instância.
4. O KLEAD salva o lead no CRM.
5. Se `meta_capi_enabled` estiver ligado, o KLEAD envia um evento de volta para a Meta.

Resultado:

- o lead aparece no seu CRM KLEAD
- a Meta recebe sinais melhores para otimização/matching

## O que não acontece

- A Meta não grava direto no seu CRM.
- O KLEAD não precisa de Worker para entender o lead.
- O `phone_number_id` não é o telefone do lead.
- O retorno para Advanced Match não acontece automaticamente só porque o lead entrou. Ele depende de CAPI.

## Regra simples para lembrar

- **Webhook de entrada**: Meta manda dados para o KLEAD
- **CRM**: KLEAD salva e organiza os leads
- **CAPI / Advanced Match**: KLEAD manda sinais de volta para a Meta

## Arquivos do projeto que implementam isso

- [dashboard/backend/main.py](../dashboard/backend/main.py)
- [dashboard/backend/services/meta_capi_service.py](../dashboard/backend/services/meta_capi_service.py)
- [dashboard/frontend/app/settings/meta/page.tsx](../dashboard/frontend/app/settings/meta/page.tsx)
