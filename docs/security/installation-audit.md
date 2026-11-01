# Auditoria da instalação — 2026-10-08

> Registro histórico preservado. A revisão de documentação/distribuição de 2026-11-01 está na [auditoria atual](distribution-audit-2026-11-01.md), que explicita limitações de checks, backup, domínio próprio e fluxo local. Os guias novos não significam correção dessas pendências de implementação.

## Escopo e evidência

Comparação entre README, INSTALL.md, instalador, stacks, Dockerfiles,
primeiro acesso e configuração do Evolution GO. Inspeção de arquivos
versionados e patches do histórico Git sem exibir valores de credenciais.

## Fluxo implementado

- `python install.py`: coleta acesso SSH e inicia o wizard na VPS.
- A chave privada não é enviada; o OpenSSH verifica a identidade do host.
- Código enviado exclui ambiente, chaves, dados, logs, backups e `.git`.
- Credenciais ficam em `/etc/klead/klead.env`, fora do checkout, modo 600.
- Parser preserva configuração e não executa conteúdo do arquivo como shell.
- Evolution GO oficial é construído na VPS na revisão
  `9337afc47e10b86cc896a6f432240e40fee95dd1`.
- Bancos, bootstrap do superadmin, domínios, APIs opcionais e backups
  fazem parte do wizard. Verificações finais não se limitam ao backend.
- Domínio do Evolution GO deixa de ser fixado nas telas de criação/credenciais.

## Credenciais em versões antigas

Foram encontrados 12 candidatos a senha em URLs de banco adicionadas ao
README nos commits `603e84a`, `43965b3` e `cc8178f`.
Os valores não são reproduzidos neste relatório. Não correspondem aos
marcadores usuais de exemplo verificados na análise.

Esses valores já não constam nos arquivos atuais. A auditoria por padrões
do conteúdo versionado atual terminou sem candidatos. Isso não certifica
a ausência de todos os formatos possíveis de segredo.

Remover os valores dos commits anteriores exige reescrever o histórico
e tratar branches/tags e cópias remotas. A operação precisa preservar as
alterações locais e ser coordenada antes de atualizar o GitHub. Credenciais
que possam ter sido usadas precisam ser rotacionadas; limpeza do Git não
revoga uma senha publicada. O histórico ainda não foi reescrito.

## Limites da validação

Sintaxe Python, Bash e YAML e integridade do diff foram conferidas localmente.
Nenhuma VPS foi acessada, nenhum deploy foi executado e não foi realizada
uma instalação completa em VPS limpa. Build e compatibilidade operacional
do Evolution GO oficial com o KLEAD precisam dessa validação antes de
declarar o instalador validado em produção.
