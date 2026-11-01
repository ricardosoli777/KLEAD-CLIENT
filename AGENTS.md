# KLEAD-CLIENT — orientações de trabalho

Este repositório é uma cópia independente do KLEAD. Consulte README.md, COPY_ORIGIN.md e os guias em docs/ antes de alterar os fluxos de instalação.

- Preserve alterações locais e confira git status antes de commit ou atualização.
- Apresente o plano antes de mudanças de escopo; alterações de código desta edição não autorizam acesso ou deploy em outras instalações.
- Exemplos públicos usam somente endereços fictícios sob example.com; credenciais, contatos, sessões e arquivos pessoais não pertencem ao Git.
- Backend: FastAPI/Python, em dashboard/backend. Frontend: Next.js/React, em dashboard/frontend. Persistência: PostgreSQL.
- A interface acessa o backend pelo proxy do frontend; API_KEY permanece no servidor.
- Preserve o fail-fast quando PostgreSQL configurado estiver indisponível e a réplica única do backend com estado em memória.
- Valide o backend com python -m pytest tests no diretório dashboard/backend e o frontend com npm run lint e npm run build.
- O instalador local e a simplificação funcional ainda não estão implementados. Não descreva propostas como recursos prontos.
