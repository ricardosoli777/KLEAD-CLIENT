# Backup e restauração

## O que recuperar

Um conjunto completo precisa preservar:

1. Bancos da aplicação e do Evolution GO usados na instalação.
2. Arquivos persistentes do backend, incluindo conhecimento, filas/histórico de campanhas e demais artefatos em seu volume de dados.
3. Dados persistentes/sessões adicionais do Evolution GO conforme a versão utilizada.
4. Configuração privada, especialmente a chave mestra `DASHBOARD_SECRET`.
5. Versão do código/imagens e identificação dos serviços/volumes correspondentes.

Os caminhos abaixo são padrões técnicos do pacote. Em cópia personalizada, use os nomes registrados para aquela instalação. Domínios e credenciais de outras instalações não são exemplos de configuração.

## O que o wizard VPS já automatiza

O wizard instala um timer diário quando não encontra as unidades de backup da instalação. O script `scripts/pg-backup.sh` gera dumps customizados PostgreSQL dos bancos configurados em `INST_PG_DB`, `CRM_PG_DB`, `EVO_PG_DB` e do banco de autenticação do Evolution GO, evitando repetir o dump quando instâncias e CRM usam o mesmo banco.

O padrão é execução diária às 03:00 com atraso aleatório de até cinco minutos, retenção local de 14 dias e arquivos em `/var/backups/klead-postgres`.

Na VPS, confira:

```bash
sudo systemctl status klead-pg-backup.timer
sudo journalctl -u klead-pg-backup.service --since today
```

A saída pode conter informações operacionais; revise-a antes de compartilhar. Um timer ativo não comprova que os dumps foram gerados nem que são restauráveis.

Para solicitar uma execução pelas unidades já instaladas:

```bash
sudo systemctl start klead-pg-backup.service
```

Isso executa o script existente, inclusive sua política de retenção e envio externo quando configurado. Confira o resultado no journal e os arquivos do lote.

## O que ainda não está automatizado

O timer não reúne automaticamente configuração privada e volumes num pacote completo de recuperação. O backup desses itens e o ponto consistente de cópia ainda precisam de procedimento específico para a instalação.

A cópia externa opcional do script usa configuração própria de R2/AWS CLI; sem ela, os dumps permanecem na mesma VPS. Não existe agendamento local completo pronto para computadores de compradores.

Não considere o dump do banco suficiente para recuperar todos os arquivos e configurações da aplicação.

## Preparar a recuperação

Antes de uma atualização ou migração, registre a versão e os nomes reais de bancos/volumes. Gere o lote de dumps, preserve os arquivos e a configuração do mesmo ponto de recuperação e guarde uma cópia protegida fora do equipamento.

Garanta consistência entre bancos e arquivos; se for necessário interromper gravações para copiar, faça isso em uma janela planejada. Preserve os originais até comprovar a recuperação.

## Testar restauração em ambiente separado

1. Prepare um PostgreSQL de recuperação compatível com os dumps, com credenciais próprias e sem dados de uso real.
2. Confira o conteúdo de cada dump com `pg_restore --list ARQUIVO.dump`.
3. Restaure os bancos em destino separado. Para dumps criados com `-Fc`, o formato de comando é `pg_restore -h HOST_DE_RECUPERACAO -U USUARIO_DE_RECUPERACAO -W -C -d postgres ARQUIVO.dump`.
4. Recupere os volumes e a configuração privada, preservando a chave mestra original.
5. Ajuste endereços e acesso ao banco para o ambiente de recuperação e inicie a versão correspondente da aplicação.
6. Confira login, dados, arquivos e leitura das credenciais criptografadas.

Os nomes em maiúsculas são placeholders. O comando cria o banco com o nome contido no dump: use somente um servidor de recuperação isolado. O cliente `pg_restore` precisa estar disponível nesse ambiente; o procedimento não é executado automaticamente pelo wizard.

Impeça que a cópia de teste dispare campanhas, atendimento automático ou reconecte os mesmos canais reais enquanto a instalação original estiver ativa. Valide integrações com recursos de teste apropriados.

## Recuperação concluída

Registre data do backup, versão, itens recuperados, testes e falhas. Recuperação completa e migração Local/VPS continuam pendentes de homologação; não há assistente de restauração fornecido nesta versão.
