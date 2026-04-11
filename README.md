# Task Flow

Ferramentas de automação para o projeto Arenar: sincronização Azure DevOps ↔ Obsidian Vault **com sugestão automática de agents especializados**.

## ✨ Novidade: Sugestão Automática de Agents (Fase 1 Implementada!)

Ao iniciar uma tarefa, o sistema agora:
- 🔍 Detecta automaticamente o tipo (Backend/Frontend)
- 📚 Carrega 12 agents especializados disponíveis
- 🎯 Sugere top 3-5 agents mais relevantes
- ⭐ Calcula score de relevância baseado em keywords

### Agents Disponíveis

**Backend (8 agents):**
- `dotnet-cqrs` - Commands, Queries, Handlers, Validators
- `dotnet-endpoints` - Controllers, API routes
- `dotnet-domain` - Entities, Value Objects, Domain Events
- `dotnet-integrations` - APIs externas, validações
- `dotnet-persistence` - EF Core, migrations, repositories
- `dotnet-testing` - Unit tests, mocking
- `dotnet-modules` - Estrutura modular, DI
- `dotnet-geo` - Geolocalização

**Frontend (4 agents):**
- `rn-architecture` - Feature-Sliced Design
- `rn-components` - React Native patterns
- `rn-patterns` - Custom hooks, state management
- `rn-performance` - Otimizações

## 🚀 Uso Rápido

### Iniciar Tarefa (com sugestões automáticas)
```bash
py src/task_flow.py start <task_id> --info '<json>' --current-user "<usuario_azure_autenticado>"
```

**Output esperado:**
```
================================================================================
📚 AGENTS RECOMENDADOS PARA ESTA TAREFA
================================================================================
   ⭐⭐⭐ dotnet-cqrs                      Relevância: 73%
   ⭐⭐  dotnet-endpoints                 Relevância: 66%
   ⭐   dotnet-integrations              Relevância: 45%

Para consultar um agent durante o desenvolvimento:
  py src/task_flow.py guidance <task_id>
================================================================================
```

### Consultar Agents Durante Desenvolvimento

```bash
# Ver sugestões novamente
py src/task_flow.py guidance <task_id>

# Ver conteúdo completo de um agent
py src/task_flow.py guidance <task_id> --agent dotnet-cqrs

# Listar todos agents disponíveis
py src/task_flow.py agents

# Listar apenas backend ou frontend
py src/task_flow.py agents --type backend
py src/task_flow.py agents --type frontend
```

## Ferramentas

| Ferramenta | Descrição |
|------------|-----------|
| `task_flow.py` | Fluxo de execução de tarefas com log completo |
| `vault_query.py` | Consultas e manipulação de notas do vault |

## Requisitos

- Python 3.10+
- Obsidian Vault configurado (padrão: `../arenar-vault`)
- Acesso ao Azure DevOps (via Copilot CLI, Claude ou API direta)

## Instalação

```bash
cd task-flow
# Edite config.json se necessário
```

---

## 1. Task Flow - Execução de Tarefas

Sincronização bidirecional Azure ↔ Obsidian com log completo.

```bash
# Iniciar execução de tarefa
py src/task_flow.py start 758 --info '{"title": "...", "type": "Task", ...}' --current-user "Nome Sobrenome"

# Adicionar entrada de log
py src/task_flow.py log 758 "Implementada validação de CPF"

# Registrar commit
py src/task_flow.py commit 758 abc1234 "feat: adiciona validação"

# Registrar arquivo alterado
py src/task_flow.py files 758 "src/file.ts" modified --commit abc1234

# Registrar decisão técnica
py src/task_flow.py decision 758 "Usar React Query para cache"

# Ver status atual
py src/task_flow.py status 758

# Finalizar tarefa
py src/task_flow.py finish 758 --confirmed-by "Product Owner"

# Listar execuções
py src/task_flow.py list
```

### Fluxo de Trabalho

```
┌─────────────────────────────────────────────────────────┐
│                    INÍCIO DA TAREFA                      │
│  Azure: Buscar tarefa → Atribuir → Status "In Progress"  │
│  Vault: Criar nota 09-execucoes/ → Atualizar sprint      │
└─────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────┐
│                   DURANTE EXECUÇÃO                       │
│  • Log de passos executados                              │
│  • Commits registrados                                   │
│  • Arquivos alterados                                    │
│  • Decisões técnicas → ADR se necessário                 │
└─────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────┐
│                  FINALIZAÇÃO DA TAREFA                   │
│  • Solicitar confirmação do solicitante                  │
│  • Calcular tempo total                                  │
│  • Azure: Status "Done"                                  │
│  • Vault: Atualizar sprint, backlog, índice              │
└─────────────────────────────────────────────────────────┘
```

## Estrutura do Projeto

```
task-flow/
├── src/
│   ├── task_flow.py      # Execução de tarefas Azure
│   └── vault_query.py    # Consultas ao vault
├── config.json           # Configuração unificada
├── task-flow.cmd         # Wrapper Windows
├── vault-query.cmd       # Wrapper Windows
├── .gitignore
└── README.md
```

### Estrutura no Vault (criada automaticamente)

```
arenar-vault/
├── 09-execucoes/              # Logs de execução
│   ├── _index.md              # Índice geral
│   └── YYYY-MM-DD-task-XXX.md # Execução individual
├── _templates/
│   └── template-execucao.md   # Template
└── 05-processos/
    └── fluxo-execucao-tarefas.md  # Documentação
```

## Configuração

Edite `config.json`:

```json
{
  "default_assignee": "Seu Nome (fallback)",
  "azure_org": "sua-org",
  "azure_project": "SeuProjeto",
  "vault_path": "../arenar-vault",
  "execucoes_folder": "09-execucoes",
  "sprints_folder": "06-sprints",
  "adr_folder": "04-adrs",
  "auto_sync_sprint": true,
  "require_confirmation": true
}
```

## Integração com Agentes AI

O sistema é projetado para funcionar com:
- **GitHub Copilot CLI** - Usando ferramentas Azure MCP
- **Claude** - Via comandos shell
- **Outros agentes** - Qualquer sistema que execute comandos Python

### Exemplo de uso com Copilot/Claude

```
"Execute a tarefa 758 do Azure"

→ Agente busca: Azure-getWorkItem(id: 758)
→ Agente executa: py src/task_flow.py start 758 --info '...' --current-user '<usuario_azure_autenticado>'
→ Agente atualiza: Azure-updateWorkItem(id: 758, state: "In Progress", assignedTo: "<usuario_azure_autenticado>")
```

---

## 2. Vault Query - Consultas ao Vault

Ferramenta CLI para buscar, listar e manipular notas do Obsidian.

```bash
# Buscar texto em todas as notas
py src/vault_query.py search "autenticação"

# Ver conteúdo de uma nota
py src/vault_query.py get "01-visao/visao-produto"

# Listar notas de uma pasta
py src/vault_query.py list "06-sprints"

# Criar nova nota
py src/vault_query.py create "02-dominios/novo-dominio.md" "# Conteúdo"

# Adicionar conteúdo a nota existente
py src/vault_query.py append "05-processos/backlog.md" "Nova linha"
```

---

## Licença

MIT
