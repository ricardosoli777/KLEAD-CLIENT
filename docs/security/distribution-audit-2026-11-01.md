# Auditoria de documentação e distribuição — 2026-11-01

## Escopo e método

Leitura local de README, INSTALL, instaladores, Compose/stacks, Dockerfiles, bootstrap de autenticação, OAuth, backup, atualização, licença e auditoria anterior. Scanner de credenciais executado com valores ocultos. Nenhum SSH, deploy ou instalação completa foi executado.

A documentação foi reorganizada para compradores; os achados que exigem código e homologação permanecem pendentes. As alterações locais preexistentes foram preservadas.

## Achados

| ID | Evidência no pacote | Impacto | Situação |
|---|---|---|---|
| D01 | `install.py` inicia somente SSH; Compose sem banco/Evolution, portas e bootstrap completos | Não há instalação local autônoma pronta | Guia local explicita a lacuna; implementação pendente |
| D02 | INSTALL prescreve renomeação manual e scripts usam nomes fixos | Personalização depende de edição/revisão técnica | Limitação documentada; parametrização pendente |
| D03 | Frontend e fallback de webhook mantêm endereços do fornecedor | Instalação pode exibir/gerar URL de outra operação | Exemplos públicos corrigidos; código pendente |
| D04 | Cookie CSRF do OAuth depende do domínio do fornecedor | Painel/callback em subdomínios de comprador podem falhar | Inferência de código, sem teste real com Meta; correção pendente |
| D05 | `/health` pode retornar HTTP 200 com `postgres=false`; wizard verifica sucesso HTTP | Instalação pode passar no check sem comprovar disponibilidade PG atual | Limite documentado; readiness e checks funcionais pendentes |
| D06 | Wizard modifica sistema antes de concluir as perguntas e detecta serviços sobretudo por nome/imagem | Requer inventário e revisão antes de execução | Preflight manual documentado; automação pendente |
| D07 | Router CRM ainda fixa uma conexão em 5432 | Suporte inconsistente a porta PG customizada | Documentado; correção pendente |
| D08 | `update.sh` constrói/troca imagens sem teste funcional posterior; launcher preserva código existente | Não há atualização completa e verificada de pacote por arquivo | Fluxo/limites documentados; automação pendente |
| D09 | Timer cobre dumps PG, não o conjunto de configuração e volumes | Dump isolado não recupera toda a instalação | Guia de recuperação documentado; automação/homologação pendentes |
| D10 | Histórico Git contém candidatos a credenciais e há materiais internos rastreados | Clone/ZIP irrestrito não é release limpa | Regras de empacotamento documentadas; tratamento pendente |
| D11 | Licença/termos descrevem SaaS recorrente; oferta autohospedada em discussão | Documentos e edição comercial precisam de alinhamento | Registrado sem reescrever direitos ou definir modelo comercial |
| D12 | Homologação Local/VPS limpa, reutilização, retomada, ARM64 e restore sem evidência completa | Não anunciar suporte amplo como validado | Matriz de aceite documentada; execução pendente |

## Segurança relevante para distribuição

Os padrões R01/R02 do [diagnóstico anterior](main-review-2026-10-08.md) continuam visíveis no fluxo `create_channel` de `routers/instances.py`: uso de chave global com URL fornecida pelo usuário e escrita de chave fornecida na configuração global. A inspeção deste trabalho não reexecutou os testes de exploração anteriores. Precisam de correção e regressão antes de distribuir para uso com equipes.

O scanner reencontrou 12 ocorrências candidatas de credenciais em URLs no histórico, nos commits `603e84a`, `43965b3` e `cc8178f`. As ocorrências podem repetir valores; não significam 12 segredos distintos. Nenhum valor foi exibido. Ausência de outros candidatos não comprova ausência de todos os formatos de segredo ou dado privado.

## Entrega documental

- Navegação Local/VPS e requisitos.
- Guias de primeiro uso, integrações, backup/restauração, atualização e diagnóstico.
- Regras de endereços fictícios e preparação de distribuição.
- Separação entre comportamento implementado, limitações e critérios ainda não homologados.

Documentação não corrige comportamento de runtime. Não houve mudança de instaladores, aplicação, stacks, configuração real, credenciais, licença ou operação do fornecedor nesta etapa.
