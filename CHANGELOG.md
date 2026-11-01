# CHANGELOG — ZapMan SDR

---

## [2026-05-16] — Redesign Visual + Meta Ads + SDR Inteligente

### Design System — Nova Paleta
- **globals.css**: paleta atualizada em todo o app (fundo `#0D1117`, cards `#161B22`, bordas `#21262D`)
- Header com gradiente verde escuro sutil em vez do verde saturado `#075E54`
- **SuiteNav**: substituído pills verdes por abas com sublinhado azul (`#3B82F6`) no item ativo
- Aplicado em todos os 16 arquivos TSX: CRM, Supervisão, Inbox, SDR, Login, Settings, Home

### Dashboard — Redesign
- KPIs com números `text-4xl font-extrabold` e borda superior colorida por tipo de métrica
- Badge "Ao vivo" pulsante no header quando há dados
- Botão "Abrir Inbox" com hover azul
- Funil com barras mais finas e mais espaçamento
- Score com valores `text-2xl` por temperatura (quente/morno/frio)

### Meta Ads Manager
- Novo card no Dashboard mostrando métricas dos últimos 30 dias via Graph API
- KPIs: gasto total, leads gerados, cliques, CPL médio
- Tabela por campanha: impressões, cliques, leads, gasto, CPL
- Novo endpoint `GET /metrics/meta-ads/{instance_id}` no backend
- Campos **Page ID** e **Ad Account ID** adicionados ao formulário de credenciais Meta
- `lib/api.ts`: tipos `MetaAdsMetrics`, `MetaAdsCampaign` e função `getMetaAdsMetrics`

### SDR — Nome do Lead e Período do Dia
- Backend injeta o **primeiro nome do lead** diretamente no system prompt da IA (OpenAI, Anthropic, Groq)
- IA usa `bom dia` / `boa tarde` / `boa noite` conforme fuso horário de Brasília (já existia, agora conectado corretamente)
- Card visual na aba SDR mostrando período atual e confirmando que o nome é injetado automaticamente

### Meta Lead Ads — Payload Formatado
- Campos do formulário Meta (com underscores) são convertidos para texto legível antes de entrar no SDR
- Exemplo: `você_está_buscando_apartamento?` → `• Você está buscando apartamento: SIM`
- Funciona para qualquer quantidade de perguntas do formulário

---

## [2026-05-01] — Meta Lead Ads + Dashboard de Métricas + IA por Voz

### Meta Lead Ads → SDR
- Webhook `POST /webhook/meta-leads` recebe leads de formulários do Facebook
- Roteamento automático pelo `meta_page_id` para a instância correta
- Lead é salvo no CRM e o SDR é disparado automaticamente
- Novo campo `meta_page_id` nas credenciais da instância

### Dashboard de Métricas em Tempo Real
- Novo endpoint `GET /metrics/{instance_id}` com funil de qualificação, score, turnos médios
- SSE (`GET /events/{instance_id}`) para atualização em tempo real sem polling
- Página `/dashboard/{id}` com gráfico de leads por horário e distribuição de score

### IA Responde em Voz (TTS)
- Endpoint `POST /tts/{instance_id}` gera áudio via OpenAI TTS e envia via WhatsApp
- Toggle `tts_enabled` por instância na aba SDR

### Transcrição de Áudio (Whisper)
- Áudios recebidos via WhatsApp são transcritos automaticamente (Groq Whisper / OpenAI)
- Transcrição entra no histórico e é qualificada pela IA normalmente

---

## [2026-04-15] — Segurança Fase 4 + Isolamento por Usuário

### Segurança
- **Escritas atômicas**: `_atomic_write()` — escreve em `.tmp` e renomeia com `os.replace()`
- **Criptografia Fernet**: credenciais sensíveis (`api_key`, `openai_key`, `meta_token`, `llm_api_key`) criptografadas em disco
- **Retry de IA**: 3 tentativas com backoff exponencial (2s, 4s) em `responder_service.py`
- **Fila persistente**: `progress/{id}_queue.json` salva e restaura fila no startup

### Isolamento por Usuário
- Navegação, CRM e instâncias isolados por usuário logado
- Roles: `admin` vê tudo, `user` vê apenas suas instâncias

---

## [2026-04-01] — Redesign Inicial + Funcionalidades Base

### Autostart no Windows
- `start-zapman.ps1` — inicia backend e frontend em janelas separadas
- `install-autostart.ps1` — registra tarefa no Windows Task Scheduler

### Design System WhatsApp (v1)
- Variáveis CSS com cores do WhatsApp (dark mode: `#111B21`, `#25D366`)
- Header verde, cards escuros, status dots animados

### Importação de Contatos
- `POST /preview-contacts-import` — validação antes de importar
- `POST /import-contacts-text/{instance_id}` — importa texto puro
- Preview lado a lado: original vs processado, inválidos riscados em vermelho

### Preview de Campanha
- `CampaignPreviewModal` — 5 mensagens no estilo balão WhatsApp antes do disparo
- Disparo só ocorre após aprovação explícita

## [2026-04-01] — Redesign + Novas Funcionalidades

### Tarefa 1 — Autostart no Windows
- **Criado** `start-zapman.ps1` — inicia backend (FastAPI porta 8000) e frontend (Next.js porta 3000) em janelas separadas e abre o navegador automaticamente
- **Criado** `install-autostart.ps1` — registra a tarefa `ZapMan-Autostart` no Windows Task Scheduler para rodar no login do usuário

### Tarefa 2 — Redesign (design system WhatsApp)
- **Alterado** `dashboard/frontend/app/globals.css` — variáveis CSS com cores do WhatsApp (dark mode: #111B21, #1F2C34, #25D366, etc.) + scrollbar personalizada
- **Alterado** `dashboard/frontend/app/layout.tsx` — dark background no body
- **Alterado** `dashboard/frontend/app/page.tsx` — redesign completo: header verde #075E54, cards escuros, stats globais, status dots animados, ícone WhatsApp
- **Alterado** `dashboard/frontend/app/instances/new/page.tsx` — redesign: formulário dark com campos agrupados por seção

### Tarefa 3 — Aba de Importação de Contatos
- **Alterado** `dashboard/backend/main.py` — dois novos endpoints:
  - `POST /preview-contacts-import` — processa texto bruto e retorna validação com entradas originais + formatadas + motivo de rejeição
  - `POST /import-contacts-text/{instance_id}` — importa contatos de texto puro para uma instância
- **Alterado** `dashboard/frontend/lib/api.ts` — novas funções `previewContactsImport()` e `importContactsText()`
- **Alterado** `dashboard/frontend/app/instances/[id]/page.tsx` — aba "Contatos" redesenhada com:
  - Dois modos: "Colar lista" (textarea) e "Upload de arquivo"
  - Preview lado a lado: coluna original vs coluna processada
  - Números inválidos com riscado em vermelho e motivo
  - Contador "X válidos | Y removidos"
  - Botão "Confirmar e salvar" ativo somente após processamento

### Tarefa 4 — Preview de Campanha antes do Disparo
- **Alterado** `dashboard/backend/main.py` — `/preview-prompt` agora usa contatos reais da instância (random.sample) em vez de nomes genéricos
- **Adicionado** componente `CampaignPreviewModal` no `[id]/page.tsx`:
  - Exibe 5 mensagens geradas no estilo balão WhatsApp (fundo #DCF8C6, bordas arredondadas)
  - Mostra nome + número do destinatário real
  - Botões: "Regenerar" | "Cancelar" | "Aprovar e iniciar campanha"
  - Intercepta "Envio Direto" e "Enviar Fila" — disparo só ocorre após aprovação explícita
