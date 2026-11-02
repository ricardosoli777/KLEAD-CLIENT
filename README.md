![KLEAD — Qualificação de leads com IA, WhatsApp e CRM](docs/assets/klead-banner.png)

# KLEAD-CLIENT

Cópia independente do KLEAD para evolução da edição destinada a usuários finais. Base: commit `16a9332` de 2026-11-01. Veja [origem e escopo da cópia](COPY_ORIGIN.md).

CRM para organizar contatos, acompanhar oportunidades, centralizar conversas do WhatsApp e receber cadastros dos formulários da Meta. A IA pode atender e qualificar contatos, enquanto a equipe acompanha o histórico e assume as conversas quando necessário.

Esta é uma cópia-base com os recursos atuais. A simplificação de funcionalidades e um instalador local automático ainda não foram implementados.

**Endereços nos exemplos:** `example.com` e seus subdomínios são fictícios. Use os domínios, IPs e emails da nova instalação. Links oficiais de ferramentas e dependências são referências externas.

## Comece aqui

1. **Entenda o app:** [arquitetura](docs/arquitetura.md) mostra componentes, ferramentas, conexões, pastas e variáveis.
2. **Verifique seu ambiente:** [verificação do PC e da VPS](docs/instalacao/verificacao.md) mostra o que está instalado e o que falta.
3. **Escolha o caminho:**

| Caminho | Para quê | Como roda | Guia | Situação |
|---|---|---|---|---|
| **Local** | Conhecer, testar, desenvolver | PostgreSQL em Docker + backend Python + frontend Node no seu computador, em `http://localhost:3000` | [Instalação local](docs/instalacao/local.md) | Procedimento manual; sem instalador automático |
| **VPS** | Atender clientes 24h com domínio e HTTPS | Docker Swarm + Traefik + PostgreSQL + Evolution GO no servidor | [Instalação VPS](docs/instalacao/vps.md) e [INSTALL.md](INSTALL.md) | Wizard `install.py` implementado; homologação completa pendente |

Instalar localmente não significa usar offline: IA, WhatsApp e Meta dependem de internet, e webhooks externos precisam de um endereço HTTPS público.

## Verificar o que está instalado

Na pasta do projeto. Os comandos só leem o sistema; com `-Instalar`/`--instalar` oferecem instalar cada item ausente e **só instalam depois de você responder `s`**.

```powershell
# PC Windows (modo: local, vps ou ambos)
powershell -ExecutionPolicy Bypass -File scripts\verificar-pc.ps1 -Modo ambos
powershell -ExecutionPolicy Bypass -File scripts\verificar-pc.ps1 -Instalar
```

```bash
# PC Linux/macOS
bash scripts/verificar-pc.sh --modo ambos
bash scripts/verificar-pc.sh --instalar

# VPS (informe seus domínios para conferir o DNS)
bash scripts/verificar-vps.sh app.example.com api.example.com go.example.com
bash scripts/verificar-vps.sh --instalar app.example.com
```

| Precisa de | Local | VPS (no PC) | VPS (no servidor) |
|---|---|---|---|
| Git | Sim | Recomendado | Sim (wizard instala) |
| Python | 3.12+ | 3 qualquer | 3 (wizard instala) |
| Node.js | 20.9+ | Não | Não (vai na imagem) |
| Docker | Sim (PostgreSQL) | Não | Sim (wizard instala) |
| OpenSSH `ssh`/`scp` | Não | Sim | — |
| Sistema | Windows 10/11 com WSL 2, Linux ou macOS | Qualquer | Ubuntu 22.04/24.04 ou Debian 12 |

## O que você vai usar

| Componente | Para que serve | Onde |
|---|---|---|
| Frontend Next.js 16 / React 19 | Painel do CRM, Inbox, configurações | `dashboard/frontend` |
| Backend FastAPI / Python 3.12 | API, regras, IA, webhooks | `dashboard/backend` |
| PostgreSQL 14 | Dados da aplicação (banco `klead`) e do Evolution GO | Container |
| Evolution GO | WhatsApp por QR Code (repositório oficial, revisão fixada) | VPS |
| Meta Cloud API / Lead Ads / CAPI | WhatsApp oficial, formulários e eventos | Conta Meta do comprador |
| Provedor de IA | OpenAI, Anthropic ou Groq | Conta do comprador |
| Traefik v3 | HTTPS e roteamento por domínio | VPS |
| Docker / Swarm | Executar e orquestrar os serviços | VPS (local: só o banco) |
| Portainer | Administração Docker, auxiliar | VPS |

O navegador fala só com o frontend; o frontend chama o backend por um proxy no servidor, que guarda a `API_KEY`. Diagrama e detalhes em [arquitetura](docs/arquitetura.md).

## Início rápido — local

Resumo do [guia local](docs/instalacao/local.md):

```bash
docker run -d --name klead-postgres -e POSTGRES_PASSWORD=SENHA_DO_BANCO \
  -p 127.0.0.1:5432:5432 -v klead_pg_data:/var/lib/postgresql/data postgres:14
docker exec klead-postgres createdb -U postgres klead
# criar dashboard/backend/.env e dashboard/frontend/.env.local (ver guia)
cd dashboard/backend && python -m venv .venv && <ativar .venv> && pip install -r requirements.txt
uvicorn main:app --host 127.0.0.1 --port 8000
cd dashboard/frontend && npm ci && npm run dev    # http://localhost:3000
```

## Início rápido — VPS

Resumo do [guia VPS](docs/instalacao/vps.md):

1. Verificar o PC, criar a VPS com sua chave SSH pública e testar `ssh`.
2. Apontar `app`, `api`, `go` e `portainer` do seu domínio para o IP da VPS (DNS only, sem AAAA incorreto, portas 80/443 liberadas também no provedor).
3. Rodar `bash scripts/verificar-vps.sh` na VPS e revisar o que existe.
4. No PC: `python install.py` (ou na VPS: `sudo bash install.sh`).
5. Entrar com o superadmin definido no wizard e seguir o [primeiro acesso](docs/uso/primeiro-acesso.md).

O instalador pede só o **caminho** da chave privada, nunca o conteúdo. A configuração privada fica em `/etc/klead/klead.env`, fora do Git. O wizard não tem modo de simulação: rode o diagnóstico antes.

## Orientação para a IA no editor

A IA que conduzir a instalação deve **pedir permissão antes de cada etapa que instala ou altera algo**:

1. Ler este README, o guia do caminho escolhido e, para VPS, o [INSTALL.md](INSTALL.md).
2. Rodar o verificador em modo leitura e mostrar o que falta.
3. Instalar somente o que for autorizado, item por item, e verificar de novo.
4. Na VPS: perguntar o domínio ou nome do aplicativo (seção "Nome do aplicativo" do INSTALL), diagnosticar, apresentar o plano e só então rodar o wizard. No modo local, não exigir domínio público para abrir o painel em `localhost`.
5. Nunca pedir ou copiar a chave SSH privada, enviar credenciais ao GitHub, regenerar `DASHBOARD_SECRET` de uma instalação existente ou apagar dados para contornar conflitos.
6. Ao concluir, separar verificações de infraestrutura dos testes reais de login, WhatsApp, IA e Meta.

O texto sugerido para iniciar está no [guia VPS](docs/instalacao/vps.md#orientação-para-a-ia-no-editor).

## Documentação

| Assunto | Guia |
|---|---|
| Estrutura, ferramentas e conexões | [Arquitetura](docs/arquitetura.md) |
| Ferramentas instaladas e faltantes | [Verificação do PC e da VPS](docs/instalacao/verificacao.md) |
| Rodar no computador | [Instalação local](docs/instalacao/local.md) |
| Rodar em servidor | [Instalação VPS](docs/instalacao/vps.md) · [Referência do wizard (INSTALL.md)](INSTALL.md) |
| Sistemas, versões e contas | [Requisitos](docs/instalacao/requisitos.md) |
| Entrar e começar a usar | [Primeiro acesso](docs/uso/primeiro-acesso.md) |
| WhatsApp, Meta, IA e email | [Integrações](docs/integracoes/configuracao.md) · [Meta detalhado](docs/guia/meta-zapman-crm-advanced-match.md) · [Fluxo Meta simples](docs/meta-fluxo-simples.md) |
| Preservar e recuperar dados | [Backup e restauração](docs/operacao/backup-restauracao.md) |
| Aplicar nova versão | [Atualização](docs/operacao/atualizacao.md) |
| Resolver problemas | [Diagnóstico](docs/operacao/diagnostico.md) |
| Preparar o pacote de venda | [Preparação da distribuição](docs/distribuicao/preparacao.md) · [Auditoria de distribuição](docs/security/distribution-audit-2026-11-01.md) · [Auditoria da instalação](docs/security/installation-audit.md) |
| Modelos legais | [Política de Privacidade](docs/legal/politica-de-privacidade.md) · [Termos de Uso](docs/legal/termos-de-uso.md) |

## Validação do código

```bash
cd dashboard/backend && python -m pytest tests
cd dashboard/frontend && npm run lint && npm run build
python scripts/audit-secrets.py            # --history para revisar commits antigos
```

O scanner não imprime valores e não certifica a ausência de todo segredo. Foram encontrados candidatos no histórico da origem; sua limpeza e a rotação das credenciais precisam ser tratadas antes da distribuição.

## Limitações conhecidas

- `/health` pode responder HTTP 200 com `"postgres": false`; leia o conteúdo.
- Uma conexão do CRM usa a porta 5432 fixa do PostgreSQL.
- O OAuth da Meta pode falhar com `csrf_invalido` em domínios de compradores.
- A troca do nome interno `klead` é manual.
- Não há instalador local automático nem homologação completa do wizard em VPS limpa, com infraestrutura existente, retomada e atualização com dados.

Detalhes em [preparação da distribuição](docs/distribuicao/preparacao.md).

## Roteiro das aulas

| Aula | Resultado esperado |
|---|---|
| 1. Conhecer o produto | Entender CRM, conversas, IA e formulários ([arquitetura](docs/arquitetura.md)) |
| 2. Preparar os arquivos | Abrir o pacote no editor, rodar o verificador e identificar o que pode ser versionado |
| 3. Guardar o código | Criar o repositório privado e revisar o primeiro envio |
| 4. Testar no computador (opcional) | Subir banco, backend e painel pelo [guia local](docs/instalacao/local.md) |
| 5. Preparar a VPS | Criar o servidor, cadastrar a chave pública e conectar por SSH |
| 6. Preparar o domínio | Configurar Cloudflare e conferir os registros DNS |
| 7. Instalar | Diagnosticar, revisar o plano e executar o wizard |
| 8. Primeiro acesso | Entrar, conectar o WhatsApp e testar o Inbox |
| 9. IA e formulários | Configurar atendimento e receber um lead de teste da Meta |
| 10. Uso diário e manutenção | Organizar contatos, backup, restauração e atualização |

Oferta Ativa terá seu próprio roteiro após a análise do repositório correspondente; este repositório não documenta essa instalação.

## Privacidade e licença

Consulte [LICENSE.md](LICENSE.md). A licença atual ainda descreve a oferta SaaS; as condições da edição para instalação pelo comprador precisam de alinhamento antes da distribuição comercial. Os modelos de [Política de Privacidade](docs/legal/politica-de-privacidade.md) e [Termos de Uso](docs/legal/termos-de-uso.md) devem ser adaptados ao responsável por cada instalação antes de publicação.
