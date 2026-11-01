# Instalação do KLEAD em uma VPS

Este é o procedimento do wizard VPS existente. Para execução no computador, veja [Local](docs/instalacao/local.md): o instalador local completo ainda não está disponível. Confira também [requisitos](docs/instalacao/requisitos.md), [visão do fluxo VPS](docs/instalacao/vps.md), [primeiro acesso](docs/uso/primeiro-acesso.md) e [pendências de distribuição](docs/distribuicao/preparacao.md).

**Limite de validação:** o fluxo foi revisado por leitura e sintaxe; a homologação completa em ambientes limpos e nas plataformas anunciadas permanece pendente. O script não possui modo de simulação e pode instalar dependências, criar configuração, alterar firewall e inicializar Swarm. Faça o inventário e revise o plano antes de executá-lo.

## Antes de começar

Todos os endereços de aplicativo usados neste guia são fictícios. Substitua `app.example.com`, `api.example.com`, `go.example.com` e `portainer.example.com` pelos domínios da nova instalação; para emails de exemplo, use `admin@example.com` e `contato@example.com` apenas como referência. Os endereços da instalação pessoal do fornecedor não fazem parte da documentação para compradores. Links oficiais de dependências e ferramentas permanecem como referências externas.

Você precisa de uma VPS Ubuntu 22.04/24.04 ou Debian 12 (amd64 ou ARM64), acesso SSH por chave e um usuário root ou com sudo. Tenha Python 3 e o cliente OpenSSH (`ssh` e `scp`) no computador que inicia a instalação. Não é necessário instalar Docker, Node, FastAPI ou Evolution GO no computador.

Baixe e extraia o repositório KLEAD ou clone-o. Abra o terminal na pasta que contém `install.py`.

Crie registros DNS A para os domínios de frontend, API/backend, Evolution GO e Portainer, apontando para a VPS. Login e planos podem usar o mesmo domínio do frontend. Libere portas 80/443 no firewall do provedor, além da porta SSH escolhida. Na Oracle Cloud, configure também Security Lists/NSGs; o instalador não consegue alterar o painel do provedor. Remova registros AAAA incorretos antes de emitir SSL.

Os builds de Next.js e Go usam CPU, RAM e disco. Caso a VPS fique sem memória, aumente os recursos ou configure swap antes de repetir. O wizard não promete um prazo fixo de conclusão.

## Nome do aplicativo (instrução obrigatória para a IA instaladora)

Os arquivos deste pacote usam internamente o nome do produto (`klead`, `KLEAD`) em caminhos, serviços, imagens, unidades de backup e variáveis. Esse nome pertence ao fornecedor do software e **não deve ser mantido na instalação do cliente**.

Se você é uma IA conduzindo a instalação, **antes de executar qualquer comando ou editar qualquer arquivo**, pergunte ao cliente:

> Qual domínio ou nome você vai usar para o seu aplicativo? (exemplo fictício: `app.minhaempresa.example.com` ou `minhaempresa`)

1. Derive o **nome da instalação** a partir da resposta: use somente a parte do nome, em minúsculas, apenas `a-z`, `0-9` e hífen, sem ponto, protocolo ou terminação (`app.minhaempresa.example.com` → `minhaempresa`). Mostre o nome ao cliente e só continue após a confirmação.
2. Na **cópia do pacote usada para instalar** (nunca no repositório original do fornecedor), substitua de forma consistente, em todos os arquivos que serão enviados à VPS (`install.py`, `install.sh`, `docker-stack.yml`, `stacks/`, `scripts/`, `dashboard/`, `.env.example` e esta documentação):
   - `klead` → nome da instalação (ex.: `/etc/klead/klead.env` → `/etc/minhaempresa/minhaempresa.env`, `klead_backend` → `minhaempresa_backend`, `klead-pg-backup` → `minhaempresa-pg-backup`, `/opt/klead` → `/opt/minhaempresa`);
   - `KLEAD` → o mesmo nome em maiúsculas, trocando hífen por `_` (ex.: `KLEAD_CONFIG_FILE` → `MINHAEMPRESA_CONFIG_FILE`).
3. Não altere referências externas: a URL do repositório `evolution-foundation/evolution-go` e o commit fixado, nomes de pacotes de terceiros e imagens públicas.
4. O nome precisa ser **idêntico em todos os pontos**. Em especial, `WEBHOOK_BASE_URL` e `EVO_MEDIA_BASE` apontam para `<nome>_backend`, que é o nome do serviço criado pela stack; divergência impede o Evolution GO de alcançar o backend.
5. Antes de instalar, rode `grep -ril klead .` na cópia e confirme que nada restou nos arquivos enviados à VPS. Os endereços das páginas de contato, privacidade e termos devem pertencer ao responsável pela nova instalação: pergunte ao cliente quais emails e domínio usar. Na documentação para compradores, use somente exemplos fictícios, como `contato@example.com` e `privacidade@example.com`, sem reproduzir endereços pessoais do fornecedor.
6. Registre o nome escolhido para usar nas atualizações futuras (`/etc/<nome>/<nome>.env`, `scripts/update.sh`). Em uma instalação existente, **não renomeie**: mantenha o nome já em uso.

Se a substituição não for feita, a instalação funciona, mas usa os nomes do fornecedor.

**Procedimento manual:** o wizard não coleta um nome e não executa essa renomeação automaticamente. Revise a cópia inteira e as referências entre serviços antes de executá-la, incluindo as atualizações futuras; uma troca textual sem revisão não é uma personalização homologada. Preserve as alterações existentes do projeto e a identidade de instalações já em uso.

## Uma entrada para a instalação completa

```bash
python install.py
```

Em Linux/macOS, o comando pode ser `python3 install.py`. No Windows, instale o recurso Cliente OpenSSH se `ssh` ou `scp` não estiverem disponíveis.

O instalador solicita, nesta ordem:

1. IP ou hostname da VPS, usuário SSH, porta, caminho da chave privada e diretório dedicado na VPS (padrão `/opt/klead`).
2. O OpenSSH confirma a identidade do servidor na primeira conexão e solicita a passphrase da chave, se houver. Confirme o fingerprint por uma fonte confiável. A chave não é enviada nem persistida pelo instalador.
3. Para usuários diferentes de root, o sudo pode solicitar a senha do usuário da VPS.
4. Na VPS, o wizard detecta serviços Swarm existentes e solicita rede overlay, dados do Postgres e domínios dos serviços.
5. Solicita email para SSL, email/senha do superadmin KLEAD, senha do Portainer e segredos internos. Enter gera senhas aleatórias onde indicado; segredos existentes não são regenerados.
6. Solicita, com entrada oculta, chaves opcionais de OpenAI, Anthropic e Groq. Você pode configurar depois no dashboard. Se informar chaves, escolha provedor/modelo padrão.
7. Oferece configuração opcional de SMTP e App Meta/OAuth, com seus respectivos segredos e IDs.

O pacote enviado contém somente o código necessário. Exclui `.env*`, chaves SSH, dados locais, logs, backups, testes, `node_modules`, `.git` e instruções pessoais de operação. Padrões reconhecidos de tokens/chaves privadas interrompem o envio. A auditoria automatizada deve ser complementada pela revisão do código.

Se o diretório da VPS já contiver uma instalação, o launcher executa o wizard existente e preserva o código. Ele não é uma ferramenta para atualizar o código: veja a seção de atualização.

## Componentes instalados na VPS

- Dependências do wizard: Python 3, Git, curl, OpenSSL e certificados CA.
- Docker Engine e Swarm, em um único manager.
- Rede overlay attachable.
- Traefik v3 para HTTPS e certificados Let's Encrypt.
- PostgreSQL 14 e bancos separados `klead`, `evolution_go_users` e `evolution_go_auth`.
- Evolution GO, compilado **na VPS** a partir do repositório oficial [evolution-foundation/evolution-go](https://github.com/evolution-foundation/evolution-go), revisão `9337afc47e10b86cc896a6f432240e40fee95dd1`, imagem `klead-evolution-go:9337afc47e10`.
- Backend FastAPI: Dockerfile instala Python 3.12 e `requirements.txt`.
- Frontend Next.js: Dockerfile instala Node, dependências npm e executa o build.
- Portainer e backup diário do Postgres, quando ainda não existentes.

A revisão do Evolution GO fica fixada no script e na stack. Para atualizar essa dependência, revise o código e altere SHA/tag de forma consistente; não copie volumes ou imagens personalizadas com dados de clientes.

O instalador cria bancos se necessário e valida a conexão usando a mesma rede e credenciais do app. Não aceita prosseguir se essa validação PG falhar. Também verifica respostas HTTP/HTTPS da API, página de login, Evolution GO (`/server/ok`) e Portainer. Não testa autenticação real ou persistência após reinício. O `/health` atual pode retornar HTTP 200 com `postgres=false`; confira o conteúdo e conclua os testes de primeiro uso.

Configurar contas externas continua sendo necessário: o wizard não cria Apps Meta nem chaves dos provedores de IA. Use as URLs e segredos da sua instalação nos respectivos painéis.

## Infraestrutura existente

A detecção se aplica a serviços Docker Swarm. Containers Compose isolados não são convertidos automaticamente.

Ao reutilizar Postgres, informe host/alias, porta, usuário e senha válidos na rede escolhida. O usuário precisa conseguir criar os bancos e as tabelas do app. A integração e os dumps usam PostgreSQL 14; outras versões precisam de avaliação própria.

**Limitação conhecida:** uma conexão no router CRM ainda fixa a porta 5432. Embora o wizard aceite outra porta, o suporte consistente a porta customizada depende de correção de código. A detecção de serviços por nome/imagem também não comprova versão, disponibilidade ou compatibilidade; confira esses itens antes de reutilizar infraestrutura.

Ao reutilizar Traefik, informe o entrypoint HTTPS e o resolver SSL existentes. Ele precisa usar o provider Swarm e enxergar a rede do KLEAD. A rede overlay precisa ser attachable; o wizard interrompe se a conexão do cliente de banco não puder ser feita.

Ao reutilizar Evolution GO, informe seu domínio e Global API Key. Garanta acesso ao backend via `WEBHOOK_BASE_URL` e à mídia via `EVO_MEDIA_BASE` na rede overlay.

O instalador preserva serviços já detectados e unidades de backup existentes. Confirme que o backup existente cobre todos os bancos usados pelo KLEAD.

## Configuração privada

Arquivo padrão: `/etc/klead/klead.env`, diretório com permissão `700` e arquivo `600`. Hash do Portainer: `/etc/klead/secrets/portainer_admin_password`.

Reexecuções preservam todas as variáveis, inclusive integrações opcionais, e criam backup do arquivo antes de atualizá-lo. Valores com espaços ou caracteres especiais são gravados com aspas; o parser não executa o conteúdo como comandos. Senhas usadas na URI Postgres do Evolution GO são codificadas para URL.

Instalações antigas com `.env` na raiz podem ser migradas. A cópia antiga permanece preservada e ignorada pelo Git; depois de conferir a migração, guarde-a fora do checkout ou remova-a manualmente.

Nunca coloque segredos em `.env.example`, commits, Dockerfiles ou build arguments. A configuração é injetada nos containers em runtime. Não publique a saída de comandos que exibem o ambiente dos serviços.

Se precisar editar a configuração privada na VPS:

```bash
sudoedit /etc/klead/klead.env
sudo bash scripts/update.sh all --config
```

O segundo comando aplica o ambiente às stacks preservando as imagens em uso. `docker service update --force` sozinho não relê o arquivo.

O Swarm não carrega `.env` automaticamente; o helper exporta somente as variáveis do arquivo privado antes do deploy. Referência: [Docker — interpolação](https://docs.docker.com/compose/how-tos/environment-variables/variable-interpolation/).

## Primeiro acesso

Acesse o domínio do frontend e use **SUPERADMIN_EMAIL + SUPERADMIN_PASSWORD** definidos no wizard. `DASHBOARD_SECRET` é a chave interna do app, não a senha de login. As senhas não são impressas no resumo; um root pode consultá-las no arquivo privado.

Crie a primeira instância e confira o domínio do Evolution GO preenchido pelo ambiente. Escaneie o QR Code mostrado pelo KLEAD. Configure a IA em Configurações/IA Global ou Contexto/SDR da instância e habilite IA Inbound.

As chaves de IA do wizard têm prioridade como configuração de ambiente. Para passar a administrar uma chave exclusivamente pelo dashboard, remova a variável correspondente do arquivo privado e reaplique a configuração.

Meta: conclua a configuração do App no painel Meta for Developers e dos webhooks exibidos pelo KLEAD. SMTP é necessário para envio de emails de convite e recuperação.

## Instalação diretamente na VPS

Se você já tem o repositório e uma sessão SSH na VPS:

```bash
sudo bash install.sh
```

Nesse caso, o wizard não solicita novamente host/chave SSH porque já está rodando no servidor.

## Atualização e rollback

Atualize primeiro os arquivos de código na VPS. Em um checkout Git, confira alterações locais e use `git pull --ff-only`. Para instalações enviadas por arquivo, envie a versão revisada preservando os dados privados em `/etc/klead`; o launcher de instalação não sobrescreve uma pasta existente.

Na raiz do projeto na VPS:

```bash
sudo bash scripts/update.sh backend
sudo bash scripts/update.sh frontend
sudo bash scripts/update.sh all
```

O script salva uma tag da imagem anterior, executa Docker build na VPS e atualiza o serviço com uma tag nova. O frontend sempre é recompilado.

O script não cria backup conjunto de bancos/arquivos/configuração nem executa teste funcional posterior. Os componentes são alterados em sequência, portanto uma falha pode deixar atualização parcial. Confira o resultado conforme o [guia de atualização](docs/operacao/atualizacao.md).

Para voltar à imagem anterior registrada:

```bash
sudo bash scripts/update.sh backend --rollback
sudo bash scripts/update.sh frontend --rollback
```

Rollback restaura a imagem anterior, não desfaz dados, configuração nem alterações no schema. As imagens anteriores precisam continuar presentes na VPS.

O script legado `scripts/deploy.py` não faz parte do fluxo indicado ao comprador. A auditoria identificou diferenças de layout remoto, filtragem e tratamento de falhas; ele precisa de revisão antes de qualquer uso. Utilize o procedimento documentado com `scripts/update.sh` para o fluxo VPS atual.

## Backup e restauração

O wizard instala `klead-pg-backup.service` e `klead-pg-backup.timer`, se ainda não existirem. O primeiro dump é executado durante a instalação; os seguintes são diários às 03:00, com atraso aleatório de até cinco minutos.

Dumps ficam em `/var/backups/klead-postgres`, com retenção local de 14 dias. Eles contêm dados de clientes e devem permanecer fora do repositório. A cópia externa para R2 é opcional e exige AWS CLI, profile `r2` em `/root/.aws/credentials` e endpoint em `/root/.aws/klead-r2.env`; esses arquivos nunca são versionados. Sem isso, o script informa que o backup é somente local.

Esse timer cobre bancos, não uma recuperação completa de volumes e configuração privada. O destino externo deve usar recursos do comprador e precisa de revisão própria antes de habilitar. Consulte [backup e restauração](docs/operacao/backup-restauracao.md) para o conjunto necessário e os limites da automação existente.

Para inspecionar:

```bash
sudo systemctl status klead-pg-backup.timer
sudo journalctl -u klead-pg-backup.service --since today
```

Valide dumps em um Postgres descartável antes de restaurar produção. Copie o dump para o ambiente de recuperação e use `pg_restore -C -d postgres ARQUIVO.dump`; o banco de destino deve estar vazio e usar credenciais próprias. Preserve o `DASHBOARD_SECRET` original em armazenamento seguro: ele é necessário para descriptografar as credenciais restauradas.

## Diagnóstico

```bash
sudo docker stack services klead
sudo docker service ps klead_backend --no-trunc
sudo docker service logs klead_backend --tail 50
sudo docker service ps evolution_go_evolution_go --no-trunc
```

- Banco inacessível: confira host, senha, banco, permissões e rede overlay.
- HTTPS indisponível: confira DNS, IPv6, firewall do provedor, Traefik e certificados.
- Evolution GO sem imagem: confira o erro do build e a revisão fixada em `scripts/build-evolution-go.sh`; planeje a retomada antes de reexecutar o wizard, que pode alterar outros componentes.
- IA sem resposta: configure uma API key válida e habilite o SDR na instância.
- Webhook perdido: execute `sudo python3 scripts/fix-webhook.py`. Em serviço Evolution GO reutilizado com outro nome, use `--service NOME_DO_SERVICO`. O script mantém credenciais no container e reinicia o serviço via Swarm.

O script de webhook altera o serviço e causa reinício; use somente após confirmar o problema e planejar a interrupção. Para leituras e outros sintomas, consulte o [guia de diagnóstico](docs/operacao/diagnostico.md).

## Auditoria de credenciais

```bash
python scripts/audit-secrets.py
python scripts/audit-secrets.py --history
```

O scanner não imprime os valores. Ele identifica padrões comuns; não certifica sozinho a ausência de todo segredo. Remover uma senha do arquivo atual não a remove dos commits anteriores. Segredos publicados precisam ser rotacionados e o histórico deve ser tratado em operação própria.
