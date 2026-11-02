# Verificar o computador e a VPS

Antes de instalar, confira o que já existe e o que falta. Os verificadores **só leem** o sistema. Com a opção de instalação, eles oferecem instalar cada item ausente e **só executam depois de você responder `s`**.

| Onde | Comando (somente leitura) | Com instalação guiada |
|---|---|---|
| PC Windows | `powershell -ExecutionPolicy Bypass -File scripts\verificar-pc.ps1` | acrescente `-Instalar` |
| PC Linux/macOS | `bash scripts/verificar-pc.sh` | acrescente `--instalar` |
| VPS | `bash scripts/verificar-vps.sh app.example.com api.example.com go.example.com` | acrescente `--instalar` |

Escolha o modo no PC com `-Modo local`, `-Modo vps` ou `-Modo ambos` (padrão). No Linux/macOS use `--modo`. Os comandos rodam a partir da pasta do projeto.

Leia o resultado assim: `[OK]` está pronto, `[FALTA]` bloqueia a instalação, `[AVISO]` merece atenção mas não bloqueia, `[INFO]` é só informação. O resumo final lista o que falta e o comando para resolver.

## O que cada verificador confere

### PC — caminho local

| Item | Mínimo | Por que | Instalação guiada (Windows / Linux / macOS) |
|---|---|---|---|
| Git | qualquer versão atual | Baixar e versionar o código | `winget Git.Git` / `apt git` / `brew git` |
| Python | 3.12 ou mais novo (testado em 3.12 e 3.13) | Backend FastAPI | `winget Python.Python.3.12` / `apt python3 python3-venv` / `brew python@3.12` |
| Node.js + npm | 20.9 ou mais novo (testado em 20 e 24) | Frontend Next.js 16 | `winget OpenJS.NodeJS.LTS` / site nodejs.org ou nvm / `brew node` |
| Docker | Engine em execução, containers Linux | PostgreSQL 14 local | `winget Docker.DockerDesktop` (exige WSL 2) / `apt docker.io` / Docker Desktop |
| WSL 2 (Windows) | ativo | Requisito do Docker Desktop | manual: `wsl --install` como administrador |
| Portas 3000, 8000, 5432 | livres | Painel, API e banco | — |
| cloudflared | opcional | Receber webhooks da Meta no PC | `winget Cloudflare.cloudflared` |
| Estado do projeto | — | Mostra quais passos do [guia local](local.md) já foram feitos | — |

### PC — iniciar instalação em VPS

| Item | Por que |
|---|---|
| Python 3 | Executar `install.py` |
| Cliente OpenSSH (`ssh`, `scp`) | Conectar e enviar o pacote. No Windows: Configurações > Sistema > Recursos opcionais > Cliente OpenSSH |
| Chave SSH pública em `~/.ssh` | Cadastrar na VPS. Criar: `ssh-keygen -t ed25519 -f ~/.ssh/klead_vps` |
| `install.py`, `install.sh`, `docker-stack.yml` | Confirmar que o pacote está completo |

### VPS

| Item | O que o verificador mostra |
|---|---|
| Sistema | Ubuntu 22.04/24.04 ou Debian 12; outro sistema aparece como `[FALTA]` |
| Arquitetura | x86_64 ou ARM64 (ARM64 ainda sem homologação) |
| RAM, swap e disco | Aviso abaixo de ~4 GB de RAM sem swap ou de 15 GB livres |
| Pré-requisitos do wizard | python3, git, curl, openssl e certificados CA; com `--instalar`, oferece `apt-get install` |
| Portas 80/443 | Livres ou qual processo as usa (conflito com outro proxy) |
| Firewall | `ufw status` e lembrete do firewall do provedor |
| Docker e Swarm | Versão, daemon, estado do Swarm, stacks, serviços, containers fora do Swarm, redes overlay e volumes |
| Instalação existente | `/etc/klead/klead.env` (só existência e permissão, nunca o conteúdo), timer de backup, `/health` local |
| DNS | Para cada domínio informado: IPv4 resolvido e alerta de registro AAAA |

Com `--instalar`, depois dos pacotes o verificador pergunta se deve iniciar o wizard (`sudo bash install.sh`). O wizard pode instalar Docker, inicializar o Swarm e alterar o firewall; não há modo de simulação. Responda `s` somente depois de revisar o inventário.

## Comandos manuais equivalentes

Use estes comandos se não puder executar os scripts.

### Windows (PowerShell)

```powershell
git --version
python --version          # 3.12+ ; o atalho da Microsoft Store não conta
node --version; npm --version
docker --version; docker info --format "{{.OSType}}"; docker compose version
wsl --status
ssh -V; Get-Command scp
Get-NetTCPConnection -State Listen -LocalPort 3000,8000,5432 -ErrorAction SilentlyContinue
```

### Linux/macOS

```bash
git --version; python3 --version; python3 -m venv --help >/dev/null && echo venv-ok
node --version; npm --version
docker --version; docker info >/dev/null && echo docker-ok; docker compose version
ssh -V; command -v scp
ss -lnt | grep -E ':(3000|8000|5432)\b'    # macOS: lsof -iTCP -sTCP:LISTEN -P
```

### VPS

```bash
cat /etc/os-release; uname -m; free -h; df -h /
command -v python3 git curl openssl
sudo ss -lntp | grep -E ':(80|443)\b'
sudo ufw status
docker version; sudo docker info --format '{{.Swarm.LocalNodeState}} {{.Swarm.ControlAvailable}}'
sudo docker stack ls; sudo docker service ls; sudo docker ps -a
sudo docker network ls --filter driver=overlay; sudo docker volume ls
sudo ls -l /etc/klead/klead.env
systemctl status klead-pg-backup.timer
getent ahostsv4 app.example.com; getent ahostsv6 app.example.com
```

`docker service ls` falha quando o Swarm não está ativo; isso não significa que a VPS esteja vazia.

## Conduzir a instalação com uma IA

Se uma IA no editor conduzir a instalação, ela deve seguir esta ordem e **pedir sua permissão antes de cada etapa que instala ou altera algo**:

1. Rodar o verificador em modo leitura e mostrar o resultado.
2. Apresentar a lista do que falta e o comando de cada item.
3. Instalar somente os itens que você autorizar, um por vez, e rodar o verificador de novo.
4. Seguir o guia [local](local.md) ou [VPS](vps.md), pedindo permissão antes de criar containers, arquivos de configuração, bancos ou de iniciar o wizard.
5. Ao final, separar o que foi verificado (serviços respondendo) do que ainda precisa de teste real (login, WhatsApp, IA, Meta).
