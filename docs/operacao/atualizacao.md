# Atualização e retorno à imagem anterior

## Alcance desta versão

Os comandos deste guia se aplicam ao fluxo VPS com Swarm. O gerenciador de atualização local ainda não existe. Nomes de serviços e caminhos mostrados são padrões técnicos do pacote; uma cópia personalizada deve usar sua identificação registrada.

## Antes de atualizar

1. Leia as mudanças da versão e eventuais mudanças de dados/schema.
2. Confira alterações locais e preserve o trabalho existente.
3. Faça e confira o [backup conjunto](backup-restauracao.md) de bancos, arquivos e configuração.
4. Registre a versão atual e confirme a disponibilidade das imagens para recuperação.
5. Planeje uma janela e os testes de primeiro acesso e persistência.

Na raiz do projeto da VPS, para uma instalação com Git:

```bash
git status --short
```

Se houver alterações locais, revise-as antes de atualizar; não use reset para descartá-las. Em checkout limpo, com origem e branch da versão confirmados, atualize com `git pull --ff-only`.

Se a instalação recebeu código por arquivo, ela pode não ter `.git`. Receba a versão revisada e atualize somente o código, preservando configuração privada e volumes. Não há, nesta versão, um atualizador automático de pacotes enviados por arquivo.

Reexecutar `install.py` não entrega código novo a um diretório remoto existente. Reexecutar o wizard não substitui o procedimento de atualização.

## Construir e aplicar

Depois de atualizar os arquivos, execute na raiz do projeto da VPS:

```bash
sudo bash scripts/update.sh all
```

Para atualizar apenas um componente, use `backend` ou `frontend` no lugar de `all`. O script registra uma tag da imagem anterior, constrói uma nova imagem com tag própria e solicita a atualização do serviço.

O frontend precisa de build para incorporar alterações. Reiniciar o serviço, sozinho, não recompila a interface.

## Conferir o resultado

O script não faz backup dos dados nem teste funcional posterior. Confira convergência dos serviços, logs, versão de imagem efetiva, login, banco e permanência dos dados. Depois teste os recursos usados naquela instalação.

Os componentes são atualizados em sequência. Se uma etapa falhar, registre quais componentes foram alterados antes de tentar novamente; não presuma que toda a atualização foi revertida.

Use os comandos de leitura do [diagnóstico](diagnostico.md). `/health` com HTTP 200, isoladamente, não comprova disponibilidade do banco ou êxito da atualização.

## Retornar à imagem registrada

Na raiz do projeto da VPS:

```bash
sudo bash scripts/update.sh backend --rollback
sudo bash scripts/update.sh frontend --rollback
```

Execute para o componente que precisa recuperar. O script usa o registro da imagem anterior em `rollback/`, junto da configuração privada; as imagens precisam permanecer na VPS.

Esse retorno não restaura configuração, bancos, schema ou arquivos. Se houve mudança incompatível de dados, a recuperação exige o conjunto de backup e um plano próprio. Uma nova atualização pode substituir o registro anterior de rollback.

Confirme novamente login, persistência e serviços após retornar.

## Alterar somente a configuração privada

Para a instalação padrão, na VPS:

```bash
sudoedit /etc/klead/klead.env
sudo bash scripts/update.sh all --config
```

Guarde uma cópia privada do arquivo antes de editar. O modo `--config` reaplica a stack usando as imagens atuais dos serviços; não faz rebuild nem rollback da configuração. `docker service update --force` sozinho não relê esse arquivo.

Preserve `DASHBOARD_SECRET` e os demais valores existentes, a menos que haja um procedimento específico de mudança. Não substitua a configuração de uma instalação por um arquivo de exemplo.
