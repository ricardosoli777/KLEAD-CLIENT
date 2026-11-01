![KLEAD — Qualificação de leads com IA, WhatsApp e CRM](docs/assets/klead-banner.png)

# KLEAD-CLIENT

Cópia independente do KLEAD para evolução da edição destinada a usuários finais. Base: commit `16a9332` de 2026-11-01. Veja [origem e escopo da cópia](COPY_ORIGIN.md).

Esta é uma cópia-base com os recursos atuais; a simplificação de funcionalidades e o instalador local ainda não foram implementados.

CRM para organizar contatos, acompanhar oportunidades, centralizar conversas do WhatsApp e receber cadastros dos formulários da Meta. A IA pode atender e qualificar contatos, enquanto a equipe acompanha o histórico e assume as conversas quando necessário.

Este guia é o ponto de partida para usar uma cópia na infraestrutura do comprador. Escolha abaixo entre execução no computador e em VPS. Os exemplos são ilustrativos: substitua os campos indicados pelos dados da sua instalação.

**Endereços nos exemplos:** `example.com` e seus subdomínios são fictícios. Use os domínios, IPs e emails definidos pelo responsável pela nova instalação. A documentação para compradores não deve conter endereços da instalação pessoal do fornecedor. Links oficiais de ferramentas e dependências são referências externas, não endereços do aplicativo.

> **Instalador disponível:** execute `python install.py` no computador para iniciar o wizard na VPS. O fluxo foi revisado localmente; a homologação completa em VPS limpa e com infraestrutura existente permanece pendente. Veja [INSTALL.md](INSTALL.md) e o [relatório de auditoria](docs/security/installation-audit.md).

## Escolha seu caminho

| Objetivo | Guia | Situação nesta versão |
|---|---|---|
| Executar no computador do comprador | [Instalação local](docs/instalacao/local.md) | Requisitos documentados; instalador local completo ainda não disponível |
| Executar na VPS do comprador | [Instalação VPS](docs/instalacao/vps.md) e [passo a passo](INSTALL.md) | Wizard implementado; homologação completa pendente |
| Conferir sistema, recursos e contas | [Requisitos](docs/instalacao/requisitos.md) | Versões de referência e limites conhecidos |
| Entrar e começar a usar | [Primeiro acesso](docs/uso/primeiro-acesso.md) | Login, canal, IA e persistência |
| Conectar serviços externos | [Integrações](docs/integracoes/configuracao.md) | WhatsApp, Meta, IA e email; inclui limitações atuais |
| Preservar e recuperar dados | [Backup e restauração](docs/operacao/backup-restauracao.md) | Distingue dumps existentes de recuperação completa |
| Aplicar uma nova versão | [Atualização](docs/operacao/atualizacao.md) | Procedimento VPS, verificações e limites de rollback |
| Resolver um problema | [Diagnóstico](docs/operacao/diagnostico.md) | Sintomas, leituras e suporte sem segredos |

Instalar localmente não significa usar offline. Atendimento depende de serviços ativos e internet; recursos com callbacks externos precisam de acesso público compatível. A possível edição simplificada para usuários finais ainda não foi criada.

Para quem prepara o pacote de venda, consulte [preparação da distribuição](docs/distribuicao/preparacao.md) e a [auditoria de distribuição](docs/security/distribution-audit-2026-11-01.md). As seções detalhadas abaixo descrevem o fluxo VPS existente.

## O que você vai usar

| Componente | Para que serve |
|---|---|
| KLEAD | Interface do CRM, contatos, conversas e configurações |
| PostgreSQL | Armazenar os dados da aplicação |
| Evolution GO | Conectar o WhatsApp por QR Code |
| Meta Cloud API | Alternativa para mensagens pela API oficial do WhatsApp |
| Meta Lead Ads | Receber os cadastros dos formulários da Meta |
| Traefik | Encaminhar os acessos e atender por HTTPS |
| Docker / Swarm | Executar e organizar os serviços na VPS |
| Provedor de IA | Gerar respostas conforme a configuração de cada instância |

Receber formulários da Meta e conectar o WhatsApp são configurações distintas. Você pode usar formulários da Meta e Evolution GO na mesma instalação. Portainer é uma ferramenta auxiliar de administração, não uma função do CRM.

O código utiliza FastAPI/Python no backend e Next.js/React na interface. A configuração depende da versão escolhida; preserve as versões e os patches necessários ao distribuir o produto.

## Caminho da instalação em VPS

1. Baixar o pacote de código e abrir a pasta no VS Code ou Antigravity.
2. Conferir os arquivos e guardar uma cópia em um repositório privado do seu GitHub.
3. Criar a VPS e configurar o acesso com chave SSH.
4. Comprar seu domínio, adicioná-lo à Cloudflare e configurar o DNS.
5. Diagnosticar o que já existe na VPS e preparar o plano de instalação.
6. Configurar os componentes ausentes e reaproveitar os compatíveis.
7. Abrir o KLEAD e testar login, mensagens e formulários.
8. Preparar backup e procedimento de atualização.

**Computador** é onde ficam o editor, o código de trabalho e sua chave SSH privada. **VPS** é o servidor onde a aplicação funciona. **GitHub** guarda o código versionado; não guarda as credenciais da instalação.

## 1. Preparar o código no computador

Extraia o ZIP em uma pasta própria e use **Abrir pasta** no editor. Confirme que a pasta contém `README.md`, `install.py`, `install.sh`, `docker-stack.yml`, `dashboard/` e `stacks/`.

O pacote para clientes deve conter código, exemplos sem dados reais e instruções. Não distribua uma cópia da pasta de produção com bancos, sessões, backups ou arquivos privados.

Crie um repositório **privado na sua conta** do GitHub. Publique somente depois de revisar os arquivos selecionados para commit. O fato de o repositório ser privado não torna adequado guardar senhas nele.

Antes de enviar, confira no terminal do editor:

```bash
git status --short
git diff --cached --name-only
```

Se ainda não inicializou o Git, use o controle de versão do editor para inicializar a pasta. Na revisão, exclua arquivos `.env` com valores reais, chaves privadas, dumps de banco, backups, logs, dados de clientes e configurações pessoais do editor. Uma regra no `.gitignore` não remove arquivos que já foram versionados.

Use seu próprio repositório como destino. Não coloque tokens ou senhas na URL do repositório e não envie capturas de tela com segredos visíveis.

## 2. Criar a VPS e acessar por chave SSH

O procedimento existente foi escrito para Ubuntu 22.04/24.04 ou Debian 12. Confira a imagem, a arquitetura, os recursos e as regras de rede no provedor escolhido, como Oracle ou Contabo. O build da aplicação consome mais recursos do que apenas manter os serviços em execução; a capacidade final deve ser validada com a carga prevista.

Use uma chave SSH existente sob seu controle ou gere uma chave dedicada. Ao criar uma nova, escolha um arquivo fora do projeto, dentro da pasta de chaves do seu usuário, e não sobrescreva uma chave existente.

- **Chave pública**, normalmente terminada em `.pub`: cadastre na criação da VPS ou no acesso autorizado do servidor.
- **Chave privada**: permanece no seu computador, protegida por senha quando possível. Não copie para a VPS, o GitHub, o pacote do produto ou o chat da IA.

No terminal **do computador**, substitua os três campos abaixo:

```bash
ssh -i CAMINHO_DA_CHAVE_PRIVADA USUARIO_DA_VPS@IP_DA_VPS
```

Use o usuário informado pelo provedor; não presuma que seja `root`. Confira a identidade do servidor na primeira conexão. Depois de conectar, os comandos daquele terminal serão executados na VPS.

**O instalador solicita somente o caminho do arquivo da chave no computador. Nunca pede, copia, armazena ou envia seu conteúdo.** O acesso é estabelecido pelo cliente OpenSSH do computador, que também solicita a passphrase quando necessária. As senhas da aplicação são outra categoria de configuração e também ficam fora do GitHub.

## 3. Configurar domínio e Cloudflare

1. Registre um domínio sob seu controle.
2. Adicione esse domínio à sua conta da Cloudflare.
3. No registrador, configure os nameservers fornecidos pela Cloudflare para esse domínio.
4. Crie os registros dos serviços apontando para o IP público da sua VPS.

| Exemplo ilustrativo | Serviço |
|---|---|
| `app.example.com` | Painel do KLEAD |
| `api.example.com` | API, webhooks e callbacks |
| `go.example.com` | Evolution GO |
| `portainer.example.com` | Administração Docker |

Para a configuração inicial e a conferência dos certificados, mantenha esses registros em **DNS only**. Confira também registros AAAA: um IPv6 incorreto pode encaminhar parte dos acessos para outro destino. A propagação não tem um prazo fixo garantido.

O servidor deve aceitar os acessos necessários em 80/443, tanto no firewall do sistema quanto no painel do provedor. Preserve as regras existentes. Ao habilitar o proxy da Cloudflare depois, valide o certificado da origem e use **Full (strict)**.

Use sempre os domínios desta instalação. Endereços de demonstração, IPs internos e domínios do proprietário não são valores padrão para clientes.

## 4. Diagnosticar antes de instalar

Abra a pasta do projeto na VPS e faça primeiro um inventário **somente de leitura**. Se usar uma IA, ela deve ler este README e os scripts antes de executar alterações.

Exemplos de diagnóstico, no terminal **da VPS**:

```bash
cat /etc/os-release
uname -m
free -h
df -h
sudo ss -lnt
```

Se Docker estiver instalado:

```bash
docker version
sudo docker info
sudo docker ps -a
sudo docker service ls
sudo docker network ls
sudo docker volume ls
```

`docker service ls` depende de um Swarm ativo e acesso ao manager. Um erro nesse comando, sozinho, não significa que a VPS esteja vazia. Confira também serviços do sistema e containers iniciados com Compose.

| Situação encontrada | Conduta necessária |
|---|---|
| VPS vazia | Planejar Docker, rede, proxy, banco, Evolution GO e aplicação |
| Docker compatível em funcionamento | Reaproveitar; não reinstalar automaticamente |
| Docker antigo ou com falha | Explicar a incompatibilidade e planejar a correção considerando os outros serviços |
| Traefik ou outro proxy existente | Conferir portas, rede e configuração; evitar instalar outro na mesma porta |
| PostgreSQL existente | Confirmar host, porta, credenciais e acesso pela rede da aplicação |
| Evolution GO existente | Confirmar versão, URL, chave e compatibilidade com a aplicação |
| Instalação parcial | Recuperar configuração e preservar volumes e segredos antes de retomar |

Encontrar um serviço pelo nome não comprova que ele seja compatível. Não remova containers, redes ou volumes para resolver um conflito sem entender a instalação existente.

## 5. Configuração particular de cada instalação

O domínio e o nome de uma imagem Docker têm funções diferentes. A imagem identifica uma versão do software; o domínio informa por onde o serviço será acessado. O cliente pode usar a mesma versão testada da stack com os próprios endereços.

A distribuição deve utilizar nomes neutros para as imagens e referências versionadas. Todos os serviços e builds são executados na VPS. O Evolution GO vem do [repositório oficial do criador](https://github.com/evolution-foundation/evolution-go), em revisão fixada; o instalador constrói a imagem na VPS antes de subir a stack.

Cada instalação precisa definir, entre outros:

- Domínios do painel, API e Evolution GO.
- Rede, host, porta, usuário e senha do PostgreSQL.
- Email e senha do administrador.
- Chave mestra `DASHBOARD_SECRET` e segredos dos webhooks.
- URL e chave do Evolution GO, quando utilizado.
- Credenciais e URLs de retorno da Meta, quando utilizada.

**Configuração privada:** o wizard grava `/etc/klead/klead.env`, fora do repositório, com permissão `600`. Reexecuções preservam as variáveis e fazem backup desse arquivo. Um `.env` antigo pode ser migrado; sua cópia original é preservada e deve permanecer fora da distribuição.

Preserve a chave mestra e os valores da instalação ao atualizar ou retomar. Trocar o domínio de um cliente não autoriza alterar os endereços das outras instalações. O `.env.example` serve como referência de campos e também precisa ser revisado antes da distribuição.

## Instalar com o wizard

No terminal **do computador**, na pasta do projeto:

```bash
python install.py
```

Em Linux/macOS, use `python3` se necessário. O computador precisa apenas de Python 3 e do cliente OpenSSH (`ssh` e `scp`). O wizard solicita IP/host da VPS, usuário, porta e caminho da chave privada. A chave permanece no computador; usuários sem root podem receber uma solicitação de senha do sudo.

Na VPS, o wizard solicita os domínios do frontend, API/backend, Evolution GO e Portainer, email de SSL, acesso do superadmin, dados do banco e segredos internos. Chaves de OpenAI/Anthropic/Groq, SMTP e Meta OAuth são opcionais e têm entrada oculta. As contas externas precisam ter sido criadas por você.

O instalador provisiona Docker/Swarm, rede overlay, Traefik, Postgres e bancos, Evolution GO oficial, backend FastAPI/Python, frontend Next.js/Node e Portainer. As dependências do app ficam nas imagens, sem instalar esses serviços no computador. Configura também backup diário do Postgres quando ainda não existe um timer da instalação.

O pacote enviado exclui ambiente, chaves SSH, dados locais, logs, backups e `.git`. Padrões reconhecidos de credenciais interrompem o envio. Reutilização automática aplica-se a serviços Swarm compatíveis, em rede overlay attachable; o wizard solicita host/porta do Postgres e entrypoint/resolver do Traefik existente.

Se já estiver na VPS com o projeto baixado, execute `sudo bash install.sh`. Nesse modo, o acesso SSH já foi estabelecido. Veja o passo a passo e a configuração de integrações em [INSTALL.md](INSTALL.md).

O primeiro login usa **email e senha do superadmin definidos no wizard**. `DASHBOARD_SECRET` é a chave interna de criptografia/autenticação, não a senha de login. As senhas não são impressas no resumo.

## Validação antes da distribuição

O fluxo verifica conexão ao banco durante o provisionamento e respostas HTTP/HTTPS de backend, frontend, Evolution GO e Portainer. Falhas detectadas retornam erro. Os checks não autenticam o administrador nem comprovam persistência após reinício; o `/health` atual pode devolver HTTP 200 mesmo com `postgres=false`. Complete as verificações de [primeiro acesso](docs/uso/primeiro-acesso.md) e [diagnóstico](docs/operacao/diagnostico.md). A auditoria e a revisão de sintaxe locais estão em [docs/security/installation-audit.md](docs/security/installation-audit.md).

A homologação precisa incluir uma VPS vazia, uma VPS com serviços existentes, uma interrupção com retomada e uma atualização com dados. ARM64, quando oferecido, precisa de build e execução testados nessa arquitetura. Os testes reais de login, WhatsApp, IA e Meta continuam necessários.

Credenciais não pertencem ao Git nem às imagens. Para auditar padrões nos arquivos versionados sem exibir valores, execute `python scripts/audit-secrets.py`; acrescente `--history` para revisar commits antigos. Foram encontrados candidatos no histórico; sua limpeza e a rotação das credenciais ainda precisam ser tratadas antes da distribuição.

## Orientação para a IA no editor

**Para conduzir a instalação em VPS, a IA deve perguntar ao cliente qual domínio ou nome ele usará para o aplicativo e seguir a seção "Nome do aplicativo" do [INSTALL.md](INSTALL.md). A personalização do nome ainda é manual e deve ser revisada na cópia destinada ao comprador. Para o modo local, consulte o estado da implementação no guia próprio; não exija domínio público apenas para abrir um painel em localhost.**

Você pode iniciar o trabalho com este texto, sem informar segredos:

> Leia o README e o INSTALL.md e analise os arquivos desta versão antes de executar. Primeiro, pergunte-me qual domínio ou nome usarei para o meu aplicativo e aplique esse nome no lugar do nome interno do pacote, como descrito em INSTALL.md. Faça primeiro um diagnóstico somente de leitura da VPS acessada por SSH. Identifique sistema, arquitetura, recursos, Docker, serviços, portas, redes e volumes existentes. Apresente o que será reaproveitado, criado ou corrigido e prossiga dentro da autorização recebida. Use os domínios e a configuração desta instalação. Nunca peça ou copie minha chave SSH privada, envie credenciais ao GitHub, regenere a chave mestra de uma instalação existente ou remova dados para contornar conflitos. Confira os limites documentados do instalador e corrija incompatibilidades reais antes de instalar. Ao concluir, diferencie verificações de infraestrutura dos testes reais de login, WhatsApp e Meta.

## Primeiro uso

### Entrar e conectar o WhatsApp

Acesse o domínio do seu painel e entre com o administrador definido para a instalação. Crie uma instância e escolha Evolution GO ou Meta Cloud API.

No Evolution GO, use a URL da sua instalação e conclua a vinculação por QR Code. Na Meta Cloud API, configure número, credenciais e webhooks exigidos para a sua conta.

Envie uma mensagem de outro número e confira a chegada no Inbox. Responda pelo painel e confirme o recebimento do outro lado. Abrir a página por HTTPS não comprova que o WhatsApp esteja conectado.

### Configurar a IA e o atendimento

Escolha o provedor, informe a chave no campo apropriado e defina instruções de atendimento, critérios de qualificação e quando transferir a conversa para uma pessoa.

Teste a resposta automática, a ação **Assumir** e a ação **Devolver à IA**. Confira também saldo e limites na conta do provedor.

### Receber formulários da Meta

Configure o app e os ativos da Meta do responsável pela instalação. Use as URLs de webhook e callback correspondentes ao seu domínio e confira as permissões liberadas para sua conta.

Envie um lead de teste e confirme nome, contato, formulário, origem e instância de destino no CRM. Repita o teste para verificar o tratamento de duplicidades. Configurar Lead Ads não configura automaticamente WhatsApp Cloud API ou Conversions API.

### Conferir o CRM

Abra um contato, acompanhe a conversa, mova a oportunidade de etapa e registre uma observação. Depois de um reinício controlado da aplicação, confira que os dados continuam disponíveis.

## Backup e atualização

Antes de atualizar, preserve os bancos, os arquivos operacionais e a configuração privada. Guarde uma cópia protegida fora da VPS e teste a restauração em ambiente separado.

Atualizar código, construir uma imagem e publicar na VPS são etapas diferentes. Reiniciar um container não recompila a interface. Registre as versões das imagens e o ponto de recuperação antes de substituir serviços.

Não envie backups, sessões, logs ou credenciais ao GitHub. Em uma instalação já em uso, siga um procedimento conferido para aquela versão; evite comandos que presumam nomes fixos de containers, IPs internos ou caminhos de outra VPS.

Após atualizar os arquivos do projeto na VPS, execute na raiz:

```bash
sudo bash scripts/update.sh all
```

Use `backend` ou `frontend` no lugar de `all` para atualizar um componente. O script compila uma imagem nova e registra a anterior para rollback:

```bash
sudo bash scripts/update.sh backend --rollback
sudo bash scripts/update.sh frontend --rollback
```

Para reaplicar somente configuração privada, use `sudo bash scripts/update.sh all --config`. O backup diário mantém dumps protegidos em `/var/backups/klead-postgres`, com retenção local de 14 dias; cópia externa exige configuração adicional. Instruções completas estão em [INSTALL.md](INSTALL.md).

O script não faz backup conjunto de dados/configuração nem teste funcional após atualizar. O retorno à imagem anterior não desfaz alterações de banco, arquivos ou configuração. Use os guias de [atualização](docs/operacao/atualizacao.md) e [backup/restauração](docs/operacao/backup-restauracao.md) para preparar e conferir a operação.


## Roteiro das aulas

| Aula | Resultado esperado |
|---|---|
| 1. Conhecer o produto | Entender CRM, conversas, IA e formulários |
| 2. Preparar os arquivos | Abrir o pacote no editor e identificar o que pode ser versionado |
| 3. Guardar o código | Criar o repositório privado e revisar o primeiro envio |
| 4. Preparar a VPS | Criar o servidor, cadastrar a chave pública e conectar por SSH |
| 5. Preparar o domínio | Configurar Cloudflare e conferir os registros DNS |
| 6. Instalar | Diagnosticar, revisar o plano e executar o procedimento homologado |
| 7. Primeiro acesso | Entrar, conectar o WhatsApp e testar o Inbox |
| 8. IA e formulários | Configurar atendimento e receber um lead de teste da Meta |
| 9. Uso diário | Organizar contatos, assumir conversas e acompanhar oportunidades |
| 10. Manutenção | Fazer backup, conferir restauração e atualizar preservando a configuração |

Oferta Ativa terá seu próprio roteiro de instalação e uso após a análise do repositório correspondente. Este repositório não documenta uma instalação desse produto ainda não verificada.

## Privacidade e licença

Consulte [LICENSE.md](LICENSE.md). A licença atual ainda descreve a oferta SaaS; as condições da edição para instalação pelo comprador precisam de alinhamento antes da distribuição comercial. Esta revisão documental não altera os direitos ou o modelo de licença. Os modelos de [Política de Privacidade](docs/legal/politica-de-privacidade.md) e [Termos de Uso](docs/legal/termos-de-uso.md) devem ser adaptados ao responsável por cada instalação antes de publicação.
