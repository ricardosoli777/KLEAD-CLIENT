# Diagnóstico e solicitação de suporte

## Identificar o ambiente

Registre: versão do pacote, Local ou VPS, sistema/arquitetura, etapa em que falhou e horário. Para VPS, use os nomes reais dos serviços e da instalação; os nomes abaixo são os padrões técnicos do pacote.

O fluxo local ainda não possui instalador ou gerenciador de diagnóstico completo. Não execute o wizard VPS no computador para tentar corrigir uma instalação local.

## Leituras na VPS

```bash
sudo docker stack services klead
sudo docker service ps klead_backend --no-trunc
sudo docker service ps klead_frontend --no-trunc
sudo docker service ps evolution_go_evolution_go --no-trunc
sudo docker service logs klead_backend --tail 50
```

Logs podem incluir contexto de clientes e integrações. Remova dados pessoais, tokens, segredos, URLs com parâmetros sensíveis e endereços privados antes de compartilhá-los.

## Sintomas

| Sintoma | O que conferir |
|---|---|
| Instalação falha antes do upload | Python, `ssh`/`scp`, host/porta e caminho da chave |
| SSH recusa conexão | Acesso de rede, usuário, chave e identidade do servidor; não desative a verificação de identidade |
| Build para ou falta memória | RAM, disco e saída do build; não há requisito mínimo numérico homologado |
| Backend reinicia | Host/porta/senha/permissões do banco e acesso pela rede configurada |
| `postgres=false` no `/health` | Disponibilidade da persistência; HTTP 200 não significa banco saudável |
| Painel sem login | Bootstrap do administrador, senha definida, cookies compatíveis com HTTP/HTTPS e proxy do frontend |
| HTTPS não abre | DNS A/AAAA, portas, firewall, Traefik, rede e certificado |
| Evolution GO responde mas mensagens não chegam | Canal conectado, webhook, segredo e alcance do backend pela rede do Evolution GO |
| IA não responde | Provedor/chave, saldo/limites, IA de entrada e atendimento assumido por humano |
| Convite/recuperação não chega | Configuração e entrega SMTP, endereço destinatário e spam |
| Meta retorna `csrf_invalido` | Limitação atual de cookie entre domínios/subdomínios; ver guia de integrações |
| Configuração do CRM falha em banco com porta customizada | Existe conexão no router CRM ainda fixada em 5432; requer correção de código para suporte consistente a outra porta |
| Reinício parece ignorar mudanças de tela | Conferir se houve build e se o serviço executa a imagem nova |
| Dados somem após reinício | Configuração PG, bancos corretos e volumes; interrompa o uso até identificar a persistência |

## Interpretar as verificações

O endpoint `/health` atual retorna um objeto com `status` e `postgres`; ele não funciona como verificação completa de prontidão com status HTTP de erro para todos os problemas de banco. O instalador confere respostas HTTP e conexão PG em etapas distintas.

Uma tela de login acessível não comprova login. QR Code não comprova entrega de mensagens. Timer de backup ativo não comprova restauração. Use o [primeiro acesso](../uso/primeiro-acesso.md) e os testes de cada recurso.

## Recuperar webhook

Existe `scripts/fix-webhook.py` para corrigir o webhook do Evolution GO pela configuração da instalação. Ele altera o banco do serviço e o reinicia; não é comando de diagnóstico somente de leitura. Use após confirmar que esse é o problema e planejar a interrupção.

## Enviar ao suporte

Informe versão, sistema/arquitetura, etapa, horário, sintoma e um trecho revisado do erro. Explique quais verificações foram feitas. Use placeholders como `api.example.com` ao descrever exemplos.

Não envie o arquivo de ambiente, chave SSH privada, dumps, sessão do WhatsApp ou logs sem revisão. O caminho de configuração pode ser informado; o conteúdo secreto não é necessário para relatar o problema.
