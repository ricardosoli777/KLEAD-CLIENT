# Requisitos e escolha do ambiente

Revisado em 2026-11-01 com base nos arquivos desta versão. Endereços sob `example.com` são fictícios. Links de documentação e repositórios de terceiros continuam apontando para as fontes oficiais.

## Escolher onde executar

| Item | No computador do comprador | Em VPS do comprador |
|---|---|---|
| Situação nesta versão | Ambiente local ainda sem instalador completo | Wizard por SSH implementado; homologação completa pendente |
| Onde os serviços executam | No computador | No servidor |
| Disponibilidade | Depende de computador, rede e serviços ligados; suspensão interrompe o atendimento | Depende de VPS, rede e serviços ligados |
| Endereço do painel | Planejado: `http://localhost:3000` | Exemplo: `https://app.example.com` |
| Endereço para integrações externas | Precisa de publicação HTTPS quando o provedor envia callbacks | Domínio público HTTPS da instalação |
| Dados | Devem permanecer em banco e volumes persistentes locais | PostgreSQL e volumes persistentes na VPS |

Veja [Local](local.md) ou [VPS](vps.md). Instalar no computador não significa funcionar offline: WhatsApp, Meta, provedores de IA e downloads dependem de internet.

## O que o computador precisa para iniciar o wizard VPS

- Python 3 e cliente OpenSSH com os comandos `ssh` e `scp`.
- Pacote de código da versão recebida e uma chave SSH própria, mantida fora dele.
- Acesso à rede da VPS e ao arquivo da chave privada. O instalador pede o caminho, não seu conteúdo.

Os serviços da aplicação e seus builds ficam na VPS. Node, PostgreSQL e Evolution GO não precisam ser instalados no computador que apenas inicia esse wizard.

## O que a VPS precisa

| Requisito | Situação no código |
|---|---|
| Sistema | O wizard aceita Ubuntu 22.04, Ubuntu 24.04 ou Debian 12 |
| Arquitetura | Aceita x86_64/amd64 e aarch64/arm64; a aceitação no script não comprova homologação de cada arquitetura |
| Acesso | Usuário root ou com sudo e autenticação SSH por chave |
| Runtime | Docker Engine e Swarm em manager; o wizard instala/inicializa quando necessário |
| Rede | Overlay attachable acessível pelos serviços e pelos clientes de validação/backup |
| Persistência | PostgreSQL acessível; instâncias, CRM e configurações devem usar o banco da aplicação |
| HTTPS | Domínios do comprador, DNS e portas 80/443 disponíveis |
| Downloads | Acesso aos registros de imagens, pacotes e repositório oficial do Evolution GO |

Antes de executar o instalador, inventarie recursos, portas, serviços e volumes. Ele pode alterar firewall e instalar componentes; não possui opção de simulação somente de leitura.

## Versões usadas pelas imagens

| Componente | Referência desta versão |
|---|---|
| Backend | Python 3.12 e dependências de `dashboard/backend/requirements.txt` |
| Frontend | Node 20 na imagem e versões de `dashboard/frontend/package.json` e lockfile |
| Banco | PostgreSQL 14 |
| Evolution GO | Código oficial na revisão fixada pelo script de build |

Essas são referências dos arquivos, não uma declaração de homologação ou de atualização automática. Alterar uma versão exige testar o pacote novamente.

## CPU, memória e disco

Ainda não há mínimo de hardware medido e homologado para compradores. Não há garantia de número de contatos, conexões ou tempo de build por máquina.

Antes de definir a oferta, medir em ambiente limpo: pico de RAM e disco durante builds, consumo em repouso, carga de conversas, espaço de banco/mídias/backups e tempo de recuperação. Publicar os requisitos numéricos somente com essa evidência.

Para o ambiente local, Docker com containers Linux e Compose é a base técnica proposta. Windows/WSL, Linux e macOS precisam de validação própria antes de serem anunciados como suportados pelo instalador local.

## Contas e custos externos

Domínio, VPS quando escolhida, provedor de IA, serviços Meta/WhatsApp e email são configurados nas contas do comprador. Os custos e limites dessas contas devem ser verificados por ele. O wizard não cria essas contas nem inclui créditos.

## Antes de distribuir

O estado das implementações e as verificações pendentes estão em [preparação da distribuição](../distribuicao/preparacao.md). A possibilidade de uma edição simplificada para usuários finais continua em avaliação; ela não está implementada nesta versão.
