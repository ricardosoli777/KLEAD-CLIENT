# Instalação em VPS do comprador

O wizard VPS está implementado. A instalação completa em VPS limpa, com infraestrutura existente e nas arquiteturas oferecidas ainda precisa de homologação. O passo a passo detalhado permanece no [INSTALL.md](../../INSTALL.md).

## 1. Separar os dados da nova instalação

Use os domínios, contas, configuração e chave SSH do comprador. Exemplos fictícios:

| Serviço | Endereço ilustrativo |
|---|---|
| Painel | `https://app.example.com` |
| API e webhooks | `https://api.example.com` |
| Evolution GO | `https://go.example.com` |
| Portainer, quando provisionado pelo wizard | `https://portainer.example.com` |
| Administrador | `admin@example.com` |

Substitua os exemplos antes de instalar. Endereços e credenciais de outras instalações não fazem parte do pacote. Links oficiais de ferramentas e dependências permanecem referências externas.

Leia os [requisitos](requisitos.md). Escolha o nome conforme a seção de identificação do INSTALL; a personalização do nome ainda é manual e deve ser aplicada somente à cópia destinada à nova instalação.

## 2. Diagnosticar antes de provisionar

No terminal da VPS, faça primeiro leituras:

```bash
cat /etc/os-release
uname -m
free -h
df -h
sudo ss -lnt
```

Se Docker já existir:

```bash
docker version
sudo docker info
sudo docker ps -a
sudo docker service ls
sudo docker network ls
sudo docker volume ls
```

Um erro em `docker service ls` pode significar ausência de Swarm ou falta de acesso ao manager; não comprova que o servidor esteja vazio.

Confira DNS/IPv6, portas 80/443, firewall do provedor, proxy, rede, banco e versão do Evolution GO. A detecção por nome/imagem do wizard não substitui uma avaliação de compatibilidade. Containers Compose e proxies fora de Swarm não são convertidos automaticamente.

O instalador não oferece modo de simulação e começa a preparar dependências/configuração antes de terminar as perguntas. Execute-o somente após revisar o plano para aquela VPS.

## 3. Iniciar pelo computador

Na pasta do pacote, no computador:

```bash
python install.py
```

Em Linux/macOS, use `python3` se esse for o comando disponível. O launcher pede host, usuário, porta, caminho da chave e diretório dedicado. O OpenSSH faz a conexão e solicita passphrase quando necessário. A chave privada permanece no computador.

Se já estiver conectado à VPS com o pacote disponível, execute na raiz dele:

```bash
sudo bash install.sh
```

Essas são duas entradas para o mesmo fluxo; não são duas instalações sucessivas obrigatórias.

## 4. Entender o que será provisionado

O wizard prepara Docker/Swarm, rede, Traefik, PostgreSQL e bancos, Evolution GO, backend, frontend, Portainer e timer de backup quando os respectivos componentes não são reutilizados.

A infraestrutura opcional na arquitetura do produto não é necessariamente opcional no wizard atual: em servidor novo ele provisiona Evolution GO e Portainer mesmo que o comprador queira inicialmente usar outro canal de WhatsApp.

A configuração privada padrão fica em `/etc/klead/klead.env`. Caminhos e nomes internos mostrados aqui são os padrões técnicos do pacote; ajuste-os à identificação de uma cópia personalizada. Não renomeie uma instalação existente.

A criação das contas Meta, IA e email é responsabilidade do comprador. O instalador permite configurar parte das credenciais depois.

## 5. Validar a instalação

O wizard verifica conexão ao banco durante o provisionamento, respostas HTTP/HTTPS e `/health`. Abrir `/login` não comprova autenticação. O `/health` atual pode devolver HTTP 200 com `postgres=false`; confira o conteúdo e a persistência.

Faça o [primeiro uso](../uso/primeiro-acesso.md), teste as [integrações](../integracoes/configuracao.md) e confirme dados após reinício controlado. Consulte [diagnóstico](../operacao/diagnostico.md) se uma etapa falhar.

## 6. Retomar ou atualizar

Reexecutar o launcher em diretório existente preserva o código remoto e executa o wizard que já estiver lá. Isso não envia uma nova versão.

Para atualização, siga o [procedimento próprio](../operacao/atualizacao.md). Preserve segredos, volumes e o [conjunto de backup](../operacao/backup-restauracao.md) antes de alterações.
