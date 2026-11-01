# Preparação do pacote para compradores

## Situação

O objetivo é fornecer instalação na infraestrutura do comprador, Local ou VPS, preservando a operação do fornecedor. Esta revisão organiza documentação; não cria o instalador local, não corrige o código da aplicação e não publica uma nova edição.

A ideia de uma edição simplificada para usuários finais está em avaliação. Não há decisão de duplicação do repositório, lista definitiva de recursos ou nova edição pronta nesta versão.

## Identidade e exemplos

Use somente endereços fictícios nos guias: `app.example.com`, `api.example.com`, `go.example.com`, `portainer.example.com` e emails em `example.com`. Não inclua endereços, IPs, contas ou configuração pessoal do fornecedor.

O comprador informa os próprios valores no ambiente privado. Links oficiais de dependências e ferramentas são referências externas e podem permanecer reais.

Os nomes internos do pacote não são endereços de produção. A personalização de nome descrita em INSTALL ainda exige revisão manual da cópia; o instalador não oferece um campo que renomeie todos os serviços e arquivos.

## Conteúdo do pacote

| Incluir | Manter fora |
|---|---|
| Código da versão revisada, dependências/lockfiles, Dockerfiles e scripts necessários | Ambiente real, chaves SSH, tokens, credenciais e sessões |
| Exemplos fictícios, guias e identificação da versão | Bancos, dumps, backups, logs e dados de clientes |
| Licença/condições aplicáveis à edição entregue | Configuração pessoal de editores/agentes e scripts operacionais exclusivos de outra instalação |

O launcher SSH já filtra parte desses materiais no envio. Isso não equivale a um gerador de release comercial limpo do repositório inteiro. Arquivos ignorados podem continuar rastreados no Git, e apagar um segredo do arquivo atual não remove o histórico.

Use o scanner por padrões antes de distribuir:

```bash
python scripts/audit-secrets.py
python scripts/audit-secrets.py --history
```

O primeiro examina arquivos rastreados atuais; o segundo inclui o histórico. Não cobre todos os formatos de segredo nem substitui revisão do conteúdo do pacote, inclusive arquivos não rastreados. Houve candidatos históricos na auditoria; não distribua esse histórico como se já estivesse tratado.

## Licença e oferta

A [licença atual](../../LICENSE.md) ainda descreve exclusivamente assinatura SaaS e contém restrições incompatíveis com uma oferta genérica de instalação pelo comprador. As condições da edição distribuída precisam ser definidas e alinhadas antes de publicação.

Não altere silenciosamente autoria, direitos, prazo de acesso, licença ou política de suporte ao organizar os guias. Não anuncie transferência de propriedade do software sem definição do titular.

Stripe permanece uma integração da operação particular do fornecedor, conforme o escopo deste trabalho. Não é requisito de instalação de compradores. A interface e os documentos atuais ainda têm elementos de assinatura que precisam ser tratados ao definir a edição distribuída.

## Trabalho de implementação ainda necessário

- Instalador e gerenciamento local completos, com persistência, administrador e manutenção.
- Personalização consistente de identidade e endereços por instalação.
- Remoção das dependências de domínio do fornecedor no código e correção do OAuth Meta em domínio próprio.
- Preflight antes de mutações e validação funcional/readiness no wizard VPS.
- Correção dos riscos de credenciais globais Evolution e do uso consistente da porta PG.
- Backup conjunto, recuperação completa, atualização de pacote e verificação posterior.
- Release limpa, identificação de versão e dependências reproduzíveis.

O detalhamento auditado está em [auditoria de distribuição](../security/distribution-audit-2026-11-01.md). Nenhum desses itens fica concluído apenas pela criação dos guias.

## Homologação antes de oferecer instalação autônoma

| Cenário | Evidência exigida |
|---|---|
| Instalação limpa | Login, administrador e configuração própria sem dependência de outra instalação |
| Infraestrutura existente | Compatibilidade validada e dados/serviços preservados |
| Falha e retomada | Retomada sem apagar volumes ou regenerar segredos existentes |
| Persistência | Dados e credenciais disponíveis após reinício |
| Integrações | Entrada/saída WhatsApp, IA e Meta conforme recursos anunciados |
| Domínio próprio | URLs, cookies e callbacks testados com domínios de teste do comprador |
| Atualização e retorno | Imagem efetiva confirmada e comportamento funcional após mudança |
| Recuperação | Restauração de bancos, arquivos e configuração em ambiente separado |
| Plataformas/arquiteturas | Instalação e operação testadas em cada combinação anunciada |

A homologação deve usar ambientes descartáveis e recursos de teste. Não deve usar a instalação pessoal do fornecedor como destino de testes de instalação.
