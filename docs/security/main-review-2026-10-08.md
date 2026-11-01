# Revisão da main — 2026-10-08

**Diagnóstico: a main compila e passa nos testes existentes, mas ainda possui falhas que impedem considerá-la pronta para distribuição e instalação homologada.**

Base analisada: `3629cbbb59bbba41105cd94acc07919c539e0989`. Alterações posteriores retiram Stripe do README, do guia público e das perguntas do wizard. A integração particular no app permanece disponível.

## Escopo e evidências

- Inventário dos 208 arquivos versionados; análise sintática de todos os 100 arquivos Python, dos 5 scripts shell e dos 7 YAMLs.
- Mapeamento de 219 handlers nos routers, com revisão dos controles diretos e dos helpers de autenticação nos caminhos principais. O inventário automatizado não substitui análise de cada caminho de execução.
- Revisão dos fluxos de autenticação, isolamento entre clientes, credenciais, Evolution GO, webhook, persistência, cobrança, proxy, instalação, atualização e backup.
- Backend: `python -m pytest tests -q`: **480 passaram**, um aviso de depreciação Starlette/TestClient.
- Frontend: `npm run lint` e `npm run build`: **passaram**; compilação, TypeScript e geração das páginas concluídos.
- Auditor de segredos: nenhum candidato pelos padrões reconhecidos no conteúdo atual; **12 ocorrências candidatas no histórico**. Isso não certifica ausência de toda forma de credencial ou dado pessoal.
- `npm audit --json`: 15 pacotes sinalizados — 1 crítico, 12 altos, 2 moderados. Com `--omit=dev`: 6 — 1 crítico, 4 altos, 1 moderado. São contagens de pacotes classificados pelo npm, não de explorações comprovadas no KLEAD.
- Reproduções adicionais feitas com mocks, contas de exemplo e segredos sintéticos: envio da chave global, alteração de configuração global por cliente, IPv6 interno, reentrega Stripe, saúde do banco, perda de escrita de auth e porta fixa do CRM.
- Ambiente local: Python 3.13.15 / Node 24.13.0. As imagens usam Python 3.12 / Node 20; esses ambientes não foram recompilados nesta revisão.

## Achados prioritários

### R01 — Crítico: chave global Evolution enviada para URL escolhida por cliente

`dashboard/backend/routers/instances.py:388` e `services/evolution_service.py:104`.

`create_channel` aceita `api_url` do payload e usa a chave global do servidor quando `global_api_key` está vazio. Um administrador de cliente pode indicar outro servidor; o backend envia a chave global no header `apikey`. A chamada também não passa pelo validador de URL usado na criação manual de instância.

**Evidência:** requisição mockada capturou a chave global sintética enviada a `https://collector.example.com/instance/create` por um ator `admin`.

**Correção:** vincular a credencial global exclusivamente à origem Evolution definida pelo operador; para servidores de clientes, exigir credenciais próprias. Aplicar a regra a todos os caminhos de fallback global, incluindo QR, reconexão, status e configurações. Validar o destino antes de qualquer chamada.

### R02 — Alto: administrador de cliente altera a chave global

`dashboard/backend/routers/instances.py:394`.

Ao criar um canal com uma chave explícita, `create_channel` substitui `ai_config['evo_global_api_key']` e persiste a alteração. A rota exige administrador, enquanto a rota de edição de IA global exige superadmin. Isso permite afetar outros clientes que dependem do fallback global.

**Evidência:** criação simulada por `admin` substituiu a configuração global e chamou sua persistência.

**Correção:** guardar a chave fornecida somente na instância/tenant. Alterações globais devem permanecer restritas ao superadmin.

### R03 — Alto: validador de URL aceita IPv6 interno

`dashboard/backend/main.py:230`.

O retorno antecipado para hostnames sem ponto ocorre antes da classificação de IP. Endereços como IPv6 loopback e link-local não possuem ponto e passam por essa exceção destinada a serviços Docker.

**Evidência:** `http://[::1]:8000` e `http://[fe80::1]:8000` foram aceitos em verificações locais sem conexão de rede.

**Correção:** classificar literais IPv4/IPv6 primeiro; permitir somente nomes internos expressamente configurados. Considerar resolução DNS e redirecionamentos HTTP na política de destino.

### R04 — Alto: candidatos a credenciais continuam no histórico Git

README histórico, commits `603e84a`, `43965b3` e `cc8178f`.

Foram encontradas 12 ocorrências de URLs de banco com senha. As ocorrências podem repetir valores; não representam necessariamente 12 segredos distintos. Remover do arquivo atual não remove versões recuperáveis pelo Git.

**Correção:** verificar se os valores são reais, revogar/rotacionar os que forem credenciais e coordenar limpeza do histórico. Reescrita do histórico e alteração de credenciais da VPS não foram executadas nesta revisão.

### R05 — Alto: dependências do frontend com alertas, incluindo classificação crítica

`dashboard/frontend/package.json:13` e `package-lock.json`.

Next está fixado em 16.2.10. O npm sinalizou o pacote como crítico e apontou atualização para 16.4.0. Há também alertas transitivos de produção. A aplicação passar no lint e build não elimina esses alertas.

A aplicabilidade depende da configuração: por exemplo, o advisory de bypass de proxy exige uma configuração específica de locale, e o de execução remota via imagens exige AVIF. Esta revisão não demonstrou exploração desses advisories no KLEAD.

**Correção:** atualizar versões e lockfile de forma controlada, conferir advisories aplicáveis e repetir testes/build/auditoria. Não usar `npm audit fix --force` indiscriminadamente.

Fontes oficiais: [advisory de proxy](https://github.com/vercel/next.js/security/advisories/GHSA-6gpp-xcg3-4w24), [advisory de AVIF](https://github.com/vercel/next.js/security/advisories/GHSA-2xp9-vwfh-vxw4), [advisory de cache SSG/ISR](https://github.com/vercel/next.js/security/advisories/GHSA-mcj8-r9mp-w47p).

### R06 — Alto: erro de persistência de autenticação descarta o snapshot

`dashboard/backend/services/auth_service.py:68`.

O worker remove o item da fila antes da escrita no Postgres. Quando a escrita falha, registra um aviso e segue sem retentativa. Mudanças em usuários ou tokens revogados podem existir apenas em memória e desaparecer no restart.

**Evidência:** uma falha temporária simulada deixou o snapshot fora da fila sem nova tentativa.

**Correção:** retentativas com controle de versão dos snapshots, para não sobrescrever uma mudança mais recente, e persistência confirmada para operações de segurança. Tratar também encerramento do processo e falha prolongada do banco.

### R07 — Alto: falha ao processar Stripe impede reentrega

`dashboard/backend/routers/billing.py:94` e `:176`.

O evento é marcado como processado em memória e no banco antes da ativação do assinante. Se a ativação falhar, a reentrega é descartada como duplicada.

**Evidência:** primeira chamada simulada retornou 500; segunda retornou `duplicate_event`, sem repetir a ativação.

**Correção:** distinguir evento em processamento de evento concluído; permitir recuperação de falhas e confirmar os efeitos duráveis antes de considerar concluído. Este achado se refere à operação particular do proprietário, pois Stripe não integra a apresentação do README para terceiros.

Referência: [documentação de webhooks Stripe](https://docs.stripe.com/webhooks).

## Achados operacionais

| ID | Gravidade | Evidência e impacto | Correção recomendada |
|---|---|---|---|
| R08 | Média | `main.py:562`: `/health` responde 200 e `status=ok` mesmo com `postgres=false`. Os checks do instalador verificam sucesso HTTP, portanto podem aceitar um banco que ficou indisponível após o boot. Reproduzido com mock. | Separar liveness/readiness e retornar 503 na readiness quando a persistência necessária falhar; validar conteúdo e código no instalador. |
| R09 | Média | `routers/crm.py:260`: `_pg_connect` fixa porta 5432; o wizard permite outra porta. Reproduzido com `EVO_PG_PORT=6543`; endpoints `/config` tentam 5432. | Usar as configurações compartilhadas de conexão, incluindo a porta configurada. |
| R10 | Média | `scripts/pg-backup.sh:34` cobre dumps PG; arquivos de conhecimento, filas/histórico de campanhas e `data/guide_videos.json` permanecem fora desses dumps. Restore apenas dos bancos não recupera a instalação inteira. | Backup separado do volume e configuração privada, com proteção apropriada e procedimento de restauração conjunto; testar recuperação completa. |
| R11 | Média | `scripts/deploy.py:65`, `:98`, `:152`: script legado aceita host SSH desconhecido, filtros de `.env` só atuam em diretórios, usa layout remoto `/backend` em vez de `/dashboard/backend` e pipelines com `tail` mascaram falhas. | Consolidar no fluxo atual ou retirar o script obsoleto; validar host, filtrar arquivos privados e preservar exit codes. Não foi executado contra a VPS. |
| R12 | Média | `install.sh:595`: rerun recompila tags `latest` e reaplica stack sem versão de imagem distinta ou força de atualização. Com especificação inalterada e sem digest resolvido, tarefas existentes podem continuar na imagem antiga; `/health` não identifica a versão executada. Inferência de código, sem reprodução em Swarm. | Usar tags por versão no wizard/reexecução e verificar a imagem efetiva; evitar tratar rerun como atualização. |
| R13 | Média | `stacks/pgadmin.yml:33` ainda define email de login pessoal e rede/domínio específicos. `.claude/settings.local.json` permanece versionado apesar do ignore. Dois arquivos em `contatos/processados/` contêm sequências compatíveis com telefones; autenticidade não confirmada. | Parametrizar a identidade do pgAdmin e excluir configurações pessoais da distribuição. Revisar contatos e outros metadados sem publicar seus valores. |

Referência operacional: [Docker stack deploy](https://docs.docker.com/reference/cli/docker/stack/deploy/) documenta resolução de imagem e comportamento de deploy assíncrono.

## Documentação e instalação para terceiros

- README utiliza o instalador SSH, configuração privada fora do checkout e build oficial Evolution GO na VPS. Banner presente.
- A referência a Stripe foi retirada do README conforme orientação do proprietário.
- `INSTALL.md` e o wizard também deixam de oferecer configuração Stripe para terceiros. A gravação da configuração preserva variáveis privadas já existentes; a integração particular não foi removida do backend.
- `routers/webhook.py:45` ainda contém domínio público fixo de fallback. A instalação guiada fornece a variável, mas execuções fora desse fluxo podem gerar URLs do operador original.
- O launcher envia um pacote sem `.git` e não sobrescreve instalação existente. A documentação explica isso; atualização de código por arquivo continua manual.
- Docker Compose e pgAdmin conservam referências operacionais antigas. Não fazem parte do provisionamento padrão do novo wizard e precisam ser identificados como legados ou atualizados.

## Plano de correções futuras

**Status em 2026-10-08: todos os achados R01–R13 estão pendentes.** Este documento registra o diagnóstico; não representa correções implementadas. O fluxo público sem Stripe foi publicado separadamente no commit `5366f7616a13d08bad20a6fdcb703c6ce4863ea0`.

### Etapa 1 — Segurança e distribuição

- [ ] **R01:** impedir envio de credenciais globais a destinos escolhidos por clientes. Validar criação, QR, reconexão, status e configurações usando duas origens Evolution distintas e chamadas simuladas; a chave global só pode alcançar a origem autorizada.
- [ ] **R02:** impedir alteração da configuração global por administradores de clientes. Confirmar que criação de canal mantém a chave global intacta e que alteração global exige superadmin.
- [ ] **R03:** corrigir validação de destinos IPv4/IPv6, DNS e redirecionamentos. Confirmar bloqueio de loopback/link-local e aceitação somente dos destinos internos explicitamente permitidos.
- [ ] **R04:** revisar os candidatos no histórico, identificar valores reais e coordenar rotação/limpeza. Registrar evidências sem valores de segredos. Reescrita do histórico e acesso à VPS exigem autorização específica antes da execução.
- [ ] **R13:** retirar identidades/configurações pessoais da distribuição e revisar os arquivos de contatos. Confirmar que os arquivos atuais não contêm dados privados; tratar o histórico junto de R04 quando aplicável.
- [ ] **R05:** atualizar dependências e lockfile após consultar os advisories vigentes. Repetir lint, build e auditoria; documentar alertas restantes e sua aplicabilidade.

### Etapa 2 — Persistência e operação particular

- [ ] **R06:** garantir recuperação de escritas de auth que falharam. Simular indisponibilidade temporária do PG, recuperação e restart; verificar usuários e revogações duráveis sem sobrescrever snapshots mais recentes.
- [ ] **R07:** corrigir conclusão/idempotência dos eventos Stripe na operação particular. Simular falha na primeira ativação e reentrega; deve recuperar a ativação e evitar duplicação após sucesso confirmado.

### Etapa 3 — Instalação, manutenção e recuperação

- [ ] **R08:** criar/verificar readiness que falha quando o banco necessário está indisponível. Confirmar que o instalador não declara sucesso nesse cenário.
- [ ] **R09:** respeitar a porta configurada do PG em todos os caminhos. Validar os endpoints de configuração usando porta diferente de 5432.
- [ ] **R10:** cobrir volume, configuração privada e bancos no procedimento de recuperação. Restaurar em ambiente descartável e verificar conhecimento, filas, vídeos, credenciais descriptografadas e dados do CRM.
- [ ] **R11:** consolidar ou retirar o deploy legado. Validar filtros de arquivos privados, identidade SSH, layout remoto e propagação de falhas de build/update.
- [ ] **R12:** usar e verificar versões distintas de imagem em reexecuções. Em Swarm descartável, confirmar que a tarefa executa a imagem recém-compilada e que rollback recupera a anterior.

### Critérios para encerrar o plano

- [ ] Cada achado corrigido possui evidência de validação e referência ao commit correspondente neste documento.
- [ ] Executar novamente os testes de backend, lint/build de frontend e auditorias de segredos/dependências. Os números desta revisão são uma referência histórica, não resultados de versões futuras.
- [ ] Validar nos ambientes das imagens: Python 3.12 e Node 20, ou versões que forem adotadas e documentadas.
- [ ] Homologar instalação em VPS limpa e reutilização de infraestrutura compatível, com credenciais próprias do ambiente de validação.
- [ ] Homologar atualização, rollback e restauração completa em ambiente descartável.
- [ ] Conferir README/INSTALL e remover fallbacks/domínios operacionais antigos dos caminhos destinados à distribuição.

Ao retomar, conferir a main atual e atualizar as referências de linha; elas correspondem à revisão original. Começar pela Etapa 1.

## Limites e próximo passo

Não foram executados deploy, acesso à VPS, build de imagens Docker, instalação em VPS limpa, migração real, restauração real ou testes com WhatsApp/provedores externos. Docker CLI não está disponível no ambiente de revisão. Também não foi executada auditoria de vulnerabilidades das dependências Python.

O escopo cobre o inventário completo e os fluxos principais, sem afirmar que cada linha, configuração de produção ou cenário externo foi validado. Os testes atuais não cobrem os problemas reproduzidos acima.

Prioridade: corrigir R01/R02/R03 e tratar histórico/identidades privadas; atualizar dependências; corrigir persistência e operação; homologar instalação e recuperação em VPS descartável. Até isso, **não considerar a main totalmente correta nem a instalação pronta para distribuição sem ressalvas**.
