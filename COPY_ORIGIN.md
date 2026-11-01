# KLEAD-CLIENT — origem e escopo

Criado em 2026-11-01 por solicitação do titular do KLEAD.

- Origem: repositório KLEAD, commit `16a9332119aee066cbb3673d1fbb950348f0c1a4`.
- Destino: https://github.com/ricardosoli777/KLEAD-CLIENT, branch `main`.
- Histórico: novo commit inicial; commits antigos da origem não foram importados.
- Base funcional: backend, frontend, testes, instaladores existentes, stacks padrão, patches e documentação revisada.
- Identificação: KLEAD-CLIENT no README, títulos e nome exibido do painel/PWA.

## Separação da operação original

Não foram copiados arquivos privados de agentes/editores, contatos processados, ambientes reais, dados, sessões, backups, instruções de acesso/deploy de outra instalação ou a configuração auxiliar de pgAdmin. Endereços pessoais identificados no código foram substituídos por exemplos sob `example.com`. Links de dependências oficiais foram preservados. A origem e sua operação não foram alteradas.

Os nomes técnicos internos de banco, cookies, variáveis e serviços permanecem os do pacote-base para preservar compatibilidade. O wizard permite a configuração dos endereços próprios da instalação. Não há troca automática de todos os namespaces.

## Estado da edição

Esta cópia mantém os recursos existentes. A redução para uma experiência de usuário final, o instalador local e as correções de implementação listadas na auditoria ainda precisam de trabalho. Não houve deploy ou conexão a serviços reais.

A autoria e a licença original foram preservadas; as condições comerciais da edição distribuída ainda precisam de definição pelo titular. Consulte [preparação da distribuição](docs/distribuicao/preparacao.md).

## Validação da cópia inicial

- 480 testes do backend passaram em ambiente isolado de credenciais e persistência real.
- Frontend: lint e build de produção concluídos, incluindo TypeScript.
- Links locais dos guias conferidos; scanner por padrões sem candidatos a credenciais no pacote.
- Validação local com Python 3.13 e Node 24; não foi realizado build Docker ou homologação de instalação/integrações reais.
- Arquivos de licença e patches de terceiros preservados byte a byte em relação à origem.
