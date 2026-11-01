# KLEAD no computador do comprador

## Situação desta versão

**Ainda não existe instalador local completo neste pacote.** Esta página registra o escopo e os requisitos de preparação; não é um procedimento local homologado.

`python install.py` inicia instalação em uma VPS por SSH. `install.sh` provisiona Linux/Swarm e pode alterar o sistema em que é executado. Esses comandos não são atalhos para instalar o KLEAD no computador pessoal.

O `docker-compose.yml` existente também não é um ambiente local completo: depende de uma rede externa do Evolution GO, não inclui PostgreSQL ou Evolution GO, não publica as portas de acesso e não fornece todas as variáveis do primeiro administrador. O túnel presente nesse arquivo depende de configuração externa.

## O que a experiência local precisa entregar

1. Verificar sistema, runtime de containers Linux, Compose, recursos e portas disponíveis.
2. Criar configuração privada própria e preservar seus valores nas reexecuções.
3. Subir PostgreSQL com banco da aplicação e volume persistente.
4. Subir backend e painel, criar o primeiro administrador e informar como entrar.
5. Oferecer Evolution GO para conexão por QR Code ou orientar a configuração da API oficial.
6. Oferecer publicação HTTPS quando uma integração externa precisar alcançar o computador.
7. Validar primeiro acesso, persistência e serviços; informar falhas sem anunciar sucesso.

Essas etapas são requisitos de implementação. Não há, por enquanto, comando `install-local` nem gerenciador local fornecido pelo pacote.

## Endereços e rede

| Uso | Exemplo de preparação |
|---|---|
| Painel no próprio computador | `http://localhost:3000` |
| API no próprio computador | `http://localhost:8000` |
| API publicada para callbacks, quando necessária | `https://api.example.com` |

`example.com` é fictício. `localhost` identifica o próprio computador e não é alcançável por um serviço externo. Um domínio público só será necessário para os recursos que dependam dessa publicação; não é requisito para simplesmente abrir um painel local.

O endereço usado por containers para conversar entre si pode diferir do endereço usado pelo navegador e da URL pública. O futuro instalador precisa gerar essas configurações de forma consistente.

## Primeiro acesso e dados

O comprador deve definir email e senha próprios para o primeiro administrador. `DASHBOARD_SECRET` é uma chave interna de autenticação/criptografia; não é senha de login. Preservá-la é necessário para recuperar credenciais criptografadas.

Para HTTP local, o ambiente precisa configurar cookies de forma compatível com HTTP; para publicação HTTPS, deve usar cookies seguros. Essa escolha deve ser feita pelo instalador conforme o modo de acesso, sem reaproveitar configuração de outra instalação.

O modo de desenvolvimento sem PostgreSQL não substitui persistência. Reiniciar, atualizar ou remover containers não deve apagar banco, sessões, conhecimento ou configuração privada.

## Uso diário

O computador precisa permanecer ligado e com os serviços ativos para atender. Suspensão, desligamento e perda de internet interrompem recursos de atendimento e integrações.

A experiência local ainda precisa de ações documentadas e testadas para iniciar, parar, reiniciar, atualizar, fazer backup, restaurar e desinstalar preservando dados. Não existe agendamento de backup local pronto neste pacote.

## Critérios para publicar o instalador local

- Instalação limpa no sistema anunciado, com credenciais próprias e sem configuração prévia de outra instalação.
- Login, troca de senha e criação de instância.
- Dados preservados após reinício do computador e dos serviços.
- Mensagens recebidas/enviadas e IA, quando configurada.
- Callbacks externos testados com os endereços do comprador, quando configurados.
- Falha e retomada sem regenerar segredos ou apagar volumes.
- Atualização e recuperação de uma versão anterior.
- Restauração de bancos, arquivos e configuração em outro ambiente.

Até essa homologação, use o [fluxo VPS existente](vps.md) se o comprador precisar do procedimento atualmente implementado. Consulte também [requisitos](requisitos.md) e [pendências de distribuição](../distribuicao/preparacao.md).
