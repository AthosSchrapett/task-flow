#!/usr/bin/env python3
"""
Task Flow - Fluxo de análise e execução de tarefas Azure ↔ Obsidian.

Sincroniza execução de tarefas do Azure DevOps com documentação no Obsidian Vault,
mantendo log completo de atividades, commits e decisões.

Uso:
    python task_flow.py start <task_id>       # Iniciar execução de tarefa
    python task_flow.py log <task_id> "msg"   # Adicionar entrada de log
    python task_flow.py commit <task_id> <hash> [msg]  # Registrar commit
    python task_flow.py files <task_id> <file> <action>  # Registrar arquivo alterado
    python task_flow.py decision <task_id> "decisão"  # Registrar decisão
    python task_flow.py status <task_id>      # Ver status atual
    python task_flow.py finish <task_id>      # Finalizar tarefa
    python task_flow.py list                  # Listar execuções em andamento

Requer:
    - Python 3.10+
    - Acesso às ferramentas Azure MCP (via Copilot CLI ou Claude)
"""

import argparse
import io
import json
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

# Import agents engine
from agents_engine import AgentsEngine
from agents_metrics import AgentsMetrics  # FASE 4

# Garantir output UTF-8 no Windows
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# Diretórios
SRC_DIR = Path(__file__).parent.resolve()
ROOT = SRC_DIR.parent  # task-flow/
PROJECT_ROOT = ROOT.parent.parent  # Arenar/ (tools/task-flow → tools → Arenar)
VAULT_PATH = PROJECT_ROOT / "arenar-vault"
EXECUCOES_PATH = VAULT_PATH / "09-execucoes"
TEMPLATES_PATH = VAULT_PATH / "_templates"
SPRINTS_PATH = VAULT_PATH / "06-sprints"
CONFIG_PATH = ROOT / "config.json"
METRICS_PATH = ROOT / "agents_metrics.json"  # FASE 4
PLANOS_PATH = VAULT_PATH / "10-planos"


@dataclass
class LogEntry:
    """Entrada de log de execução."""
    timestamp: str
    message: str
    entry_type: str = "log"  # log, commit, decision, file


@dataclass
class CommitInfo:
    """Informações de um commit."""
    hash: str
    message: str
    timestamp: str
    files: list[str] = field(default_factory=list)


@dataclass
class FileChange:
    """Registro de arquivo alterado."""
    path: str
    action: str  # created, modified, deleted
    commit_hash: Optional[str] = None


@dataclass
class TaskExecution:
    """Representa uma execução de tarefa."""
    task_id: int
    title: str
    description: str
    task_type: str
    status: str
    assigned_to: str
    sprint: str
    story_points: int
    azure_url: str
    started_at: str
    finished_at: Optional[str] = None
    time_spent_minutes: Optional[int] = None
    confirmed_by: Optional[str] = None
    logs: list[LogEntry] = field(default_factory=list)
    commits: list[CommitInfo] = field(default_factory=list)
    files: list[FileChange] = field(default_factory=list)
    decisions: list[str] = field(default_factory=list)
    agents_consulted: list[str] = field(default_factory=list)  # NOVO: agents consultados
    
    def to_markdown(self) -> str:
        """Gera o conteúdo markdown da nota de execução."""
        template = self._load_template()
        
        # Substituir placeholders básicos
        content = template
        replacements = {
            "{{TASK_ID}}": str(self.task_id),
            "{{TITLE}}": self.title,
            "{{TYPE}}": self.task_type,
            "{{STATUS}}": self.status,
            "{{ASSIGNED_TO}}": self.assigned_to,
            "{{DESCRIPTION}}": self.description or "Sem descrição",
            "{{STARTED_AT}}": self.started_at,
            "{{FINISHED_AT}}": self.finished_at or "",
            "{{TIME_SPENT_MINUTES}}": str(self.time_spent_minutes) if self.time_spent_minutes else "",
            "{{TIME_SPENT}}": self._format_time_spent(),
            "{{SPRINT}}": self.sprint,
            "{{SPRINT_FILE}}": self._get_sprint_file(),
            "{{SPRINT_TAG}}": self._get_sprint_tag(),
            "{{TYPE_TAG}}": self.task_type.lower().replace(" ", "-"),
            "{{STORY_POINTS}}": str(self.story_points),
            "{{CONFIRMED_BY}}": self.confirmed_by or "",
            "{{AZURE_URL}}": self.azure_url,
        }

        for placeholder, value in replacements.items():
            content = content.replace(placeholder, value)

        # Substituir seções dinâmicas
        content = self._inject_logs(content)
        content = self._inject_commits(content)
        content = self._inject_files(content)
        content = self._inject_decisions(content)
        content = self._inject_guidance(content)

        # Marcar resultado ao finalizar
        if self.status == "done":
            content = content.replace(
                "- [ ] Tarefa concluída com sucesso",
                "- [x] Tarefa concluída com sucesso"
            )
            content = content.replace(
                "- [ ] Código revisado",
                "- [x] Código revisado"
            )
            content = content.replace(
                "- [ ] Testes passando",
                "- [x] Testes passando"
            )
            if self.confirmed_by and self.confirmed_by != "Pendente confirmação":
                content = content.replace(
                    f"- [ ] Confirmado por: {self.confirmed_by}",
                    f"- [x] Confirmado por: {self.confirmed_by}"
                )

        # Remove placeholder de observações finais se vazio
        content = content.replace("<!-- FINAL_NOTES_PLACEHOLDER -->", "")
        content = content.replace("{{FINAL_NOTES}}", "")

        return content
    
    def _load_template(self) -> str:
        """Carrega o template de execução."""
        template_file = TEMPLATES_PATH / "template-execucao.md"
        if template_file.exists():
            return template_file.read_text(encoding="utf-8")
        return self._default_template()
    
    def _default_template(self) -> str:
        """Template padrão caso o arquivo não exista."""
        return """# Execução: {{TITLE}}

**Task ID:** {{TASK_ID}}
**Status:** {{STATUS}}
**Início:** {{STARTED_AT}}

## Log de Execução
<!-- LOG_ENTRIES_PLACEHOLDER -->

## Commits
<!-- COMMITS_PLACEHOLDER -->

## Arquivos
<!-- FILES_PLACEHOLDER -->
"""
    
    def _inject_logs(self, content: str) -> str:
        """Injeta entradas de log no conteúdo."""
        # Filtra logs para não incluir o log inicial do template (já está no template)
        # e remove duplicatas de finalização (mantém apenas o último)
        _skip_types = {"início", "inicio"}
        _skip_msgs = {"tarefa iniciada", "tarefa iniciada. status alterado"}
        _finish_msg = "tarefa finalizada. tempo total:"

        logs_to_inject = [
            log for log in self.logs
            if log.entry_type.lower() not in _skip_types
            and not any(s in log.message.lower() for s in _skip_msgs)
        ]

        # Mantém apenas a última entrada de finalização
        finish_logs = [l for l in logs_to_inject if _finish_msg in l.message.lower()]
        if len(finish_logs) > 1:
            for dup in finish_logs[:-1]:
                logs_to_inject.remove(dup)

        if not logs_to_inject:
            return content.replace("<!-- LOG_ENTRIES_PLACEHOLDER -->", "")

        log_text = "\n".join([
            f"### {log.timestamp} — {log.entry_type.title()}\n- {log.message}"
            for log in logs_to_inject
        ])

        return content.replace("<!-- LOG_ENTRIES_PLACEHOLDER -->", log_text)
    
    def _inject_commits(self, content: str) -> str:
        """Injeta commits no conteúdo."""
        if not self.commits:
            return content.replace("<!-- COMMITS_PLACEHOLDER -->", "")

        commit_rows = "\n".join([
            f"| `{c.hash[:7]}` | {c.message} | {c.timestamp} |"
            for c in self.commits
        ])

        return content.replace("<!-- COMMITS_PLACEHOLDER -->", commit_rows)

    def _inject_files(self, content: str) -> str:
        """Injeta arquivos alterados no conteúdo."""
        if not self.files:
            return content.replace("<!-- FILES_PLACEHOLDER -->", "")

        file_rows = "\n".join([
            f"| `{f.path}` | {f.action} | {f.commit_hash or '-'} |"
            for f in self.files
        ])

        return content.replace("<!-- FILES_PLACEHOLDER -->", file_rows)

    def _inject_decisions(self, content: str) -> str:
        """Injeta decisões no conteúdo."""
        if not self.decisions:
            return content.replace("<!-- DECISIONS_PLACEHOLDER -->", "")

        decisions_text = "\n".join([f"- {d}" for d in self.decisions])
        return content.replace("<!-- DECISIONS_PLACEHOLDER -->", decisions_text)
    
    def _inject_guidance(self, content: str) -> str:
        """Injeta agents consultados no conteúdo."""
        if not self.agents_consulted:
            placeholder_text = "_Nenhum agent foi consultado durante esta execução._"
            return content.replace("<!-- GUIDANCE_PLACEHOLDER -->", placeholder_text)
        
        # Remover duplicatas mantendo ordem
        unique_agents = list(dict.fromkeys(self.agents_consulted))
        
        guidance_text = "| Agent | Consultado em |\n|-------|---------------|\n"
        for agent in unique_agents:
            # Extrai timestamp se existir no formato "agent_name@timestamp"
            if "@" in agent:
                name, timestamp = agent.split("@", 1)
                guidance_text += f"| `{name}` | {timestamp} |\n"
            else:
                guidance_text += f"| `{agent}` | Durante execução |\n"
        
        return content.replace("<!-- GUIDANCE_PLACEHOLDER -->", guidance_text)
    
    def _format_time_spent(self) -> str:
        """Formata o tempo gasto."""
        if not self.time_spent_minutes:
            return "Em andamento"
        
        hours = self.time_spent_minutes // 60
        minutes = self.time_spent_minutes % 60
        
        if hours > 0:
            return f"{hours}h {minutes}min"
        return f"{minutes}min"
    
    def _get_sprint_file(self) -> str:
        """Retorna o nome do arquivo da sprint."""
        # Converte "Sprint 1 — Autenticação" para "sprint-1-autenticacao"
        sprint_lower = self.sprint.lower()
        sprint_lower = re.sub(r'[—–-]+', '-', sprint_lower)
        sprint_lower = re.sub(r'\s+', '-', sprint_lower)
        sprint_lower = re.sub(r'[^\w-]', '', sprint_lower)
        return sprint_lower

    def _get_sprint_tag(self) -> str:
        """Retorna tag da sprint: 'Sprint 1' → 'sprint-1', 'Iteration Path' → ''."""
        if self.sprint in ('Iteration Path', 'Unknown', ''):
            return ''
        m = re.search(r'sprint[^\d]*(\d+)', self.sprint, re.IGNORECASE)
        return f'sprint-{m.group(1)}' if m else ''

    @property
    def sprint_folder(self) -> str:
        """Retorna nome da subpasta da sprint para organizar execuções."""
        return self._get_sprint_tag()


class TaskFlowManager:
    """Gerenciador do fluxo de execução de tarefas."""
    
    def __init__(self):
        self.config = self._load_config()
        self._ensure_directories()
    
    def _load_config(self) -> dict:
        """Carrega configuração do task flow."""
        if CONFIG_PATH.exists():
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        return {
            "default_assignee": "Athos Schrapett",
            "azure_org": "athosschrapett",
            "azure_project": "Arenar",
        }
    
    def _save_config(self):
        """Salva configuração."""
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(self.config, f, indent=2, ensure_ascii=False)
    
    def _ensure_directories(self):
        """Garante que os diretórios necessários existem."""
        EXECUCOES_PATH.mkdir(parents=True, exist_ok=True)
    
    def _get_execution_file(self, task_id: int) -> Path:
        """Retorna o caminho do arquivo de execução."""
        # Busca arquivo existente (recursivo — arquivos ficam em sprint-N/)
        for f in EXECUCOES_PATH.rglob(f"*-task-{task_id}.md"):
            if "_arquivo" not in f.parts:
                return f

        # Novo arquivo — determina subpasta pelo sprint atual (se disponível)
        date_str = datetime.now().strftime("%Y-%m-%d")
        return EXECUCOES_PATH / f"{date_str}-task-{task_id}.md"
    
    def _load_execution(self, task_id: int) -> Optional[TaskExecution]:
        """Carrega execução existente do arquivo markdown."""
        exec_file = self._get_execution_file(task_id)
        if not exec_file.exists():
            return None
        
        content = exec_file.read_text(encoding="utf-8")
        return self._parse_execution_from_markdown(content, task_id)
    
    def _parse_execution_from_markdown(self, content: str, task_id: int) -> TaskExecution:
        """Parse do markdown para objeto TaskExecution."""
        # Extrai frontmatter (primeiro bloco --- ... --- no início do arquivo)
        frontmatter = {}
        fm_match = re.match(r'^---\n(.*?)\n---', content, re.DOTALL)
        if fm_match:
            for line in fm_match.group(1).split('\n'):
                if ':' in line:
                    key, value = line.split(':', 1)
                    frontmatter[key.strip()] = value.strip().strip('"')
        
        # Extrai logs existentes
        logs = []
        log_pattern = r'### (\d{4}-\d{2}-\d{2} \d{2}:\d{2}) — (\w+)\n- (.+?)(?=\n###|\n---|\Z)'
        for match in re.finditer(log_pattern, content, re.DOTALL):
            logs.append(LogEntry(
                timestamp=match.group(1),
                message=match.group(3).strip(),
                entry_type=match.group(2).lower()
            ))
        
        # Extrai commits existentes
        commits = []
        commit_pattern = r'\| `([a-f0-9]+)` \| (.+?) \| (\d{4}-\d{2}-\d{2} \d{2}:\d{2}) \|'
        for match in re.finditer(commit_pattern, content):
            commits.append(CommitInfo(
                hash=match.group(1),
                message=match.group(2),
                timestamp=match.group(3)
            ))
        
        # Extrai arquivos existentes
        files = []
        file_pattern = r'\| `([^`]+)` \| (\w+) \| ([^|]+) \|'
        for match in re.finditer(file_pattern, content):
            if match.group(1) not in ['Arquivo', 'Hash']:  # Skip headers
                files.append(FileChange(
                    path=match.group(1),
                    action=match.group(2),
                    commit_hash=match.group(3).strip() if match.group(3).strip() != '-' else None
                ))
        
        # Extrai decisões existentes
        decisions = []
        decision_section = re.search(r'## Decisões\n\n>.*?\n\n(.*?)(?=\n##|\n---|\Z)', content, re.DOTALL)
        if decision_section:
            for line in decision_section.group(1).split('\n'):
                if line.startswith('- ['):
                    decisions.append(line[2:])
        
        # FASE 2: Extrai agents consultados
        agents_consulted = []
        guidance_section = re.search(r'## Guidance Utilizado\n\n>.*?\n\n(.*?)(?=\n##|\n---|\Z)', content, re.DOTALL)
        if guidance_section:
            guidance_content = guidance_section.group(1).strip()
            if guidance_content and not guidance_content.startswith('_Nenhum'):
                # Parse tabela: | `agent_name` | timestamp |
                agent_pattern = r'\| `([^`]+)` \| ([^|]+) \|'
                for match in re.finditer(agent_pattern, guidance_content):
                    agent_name = match.group(1).strip()
                    timestamp = match.group(2).strip()
                    if agent_name not in ['Agent']:  # Skip header
                        if timestamp != "Durante execução":
                            agents_consulted.append(f"{agent_name}@{timestamp}")
                        else:
                            agents_consulted.append(agent_name)
        
        # Extrai description da seção "## Contexto"
        description = ""
        ctx_match = re.search(r'## Contexto\n\n> (.+?)(?=\n\n|\n#|\Z)', content, re.DOTALL)
        if ctx_match:
            raw = ctx_match.group(1).strip()
            if raw and raw != "Sem descrição":
                description = raw

        # Parse started_at com fallback
        started_at = frontmatter.get('started_at', '')
        if not started_at:
            # Tenta extrair do primeiro log
            if logs:
                started_at = logs[0].timestamp
            else:
                started_at = datetime.now().strftime("%Y-%m-%d %H:%M")

        execution = TaskExecution(
            task_id=task_id,
            title=frontmatter.get('title', f'Task {task_id}'),
            description=description,
            task_type=frontmatter.get('type', 'Task'),
            status=frontmatter.get('status', 'in-progress'),
            assigned_to=frontmatter.get('assigned_to', self.config['default_assignee']),
            sprint=frontmatter.get('sprint', 'Unknown'),
            story_points=int(frontmatter.get('story_points', 0) or 0),
            azure_url=frontmatter.get('azure_url', ''),
            started_at=started_at,
            finished_at=frontmatter.get('finished_at') or None,
            time_spent_minutes=int(frontmatter['time_spent_minutes']) if frontmatter.get('time_spent_minutes') else None,
            logs=logs,
            commits=commits,
            files=files,
            decisions=decisions,
            agents_consulted=agents_consulted,  # FASE 2: novo campo
        )
        
        return execution
    
    def _save_execution(self, execution: TaskExecution):
        """Salva execução no arquivo markdown."""
        exec_file = self._get_execution_file(execution.task_id)
        # Se o arquivo ainda não existe, coloca na subpasta do sprint
        if not exec_file.exists():
            folder = execution.sprint_folder
            if folder:
                target_dir = EXECUCOES_PATH / folder
                target_dir.mkdir(parents=True, exist_ok=True)
                exec_file = target_dir / exec_file.name
        content = execution.to_markdown()
        exec_file.write_text(content, encoding="utf-8", newline='\n')
        return exec_file
    
    def _get_active_state(self, task_type: str) -> str:
        """Retorna o estado 'ativo' correto conforme o tipo de work item do Azure DevOps."""
        t = task_type.lower()
        if "task" in t:
            return "In Progress"          # Task: To Do → In Progress → Done
        if "bug" in t:
            return "Committed"            # Bug: New → Committed → Done
        return "Active"                   # PBI, Story, Feature, Epic: New → Active → Done

    def _resolve_assignee(self, task_info: Optional[dict], current_user: str = "") -> str:
        """Resolve responsável priorizando displayName (nome legível) sobre email."""
        info = task_info or {}

        def _is_email(value: str) -> bool:
            return "@" in value and "." in value.split("@")[-1]

        def _is_valid_name(value: str) -> bool:
            return bool(value) and not _is_email(value)

        candidates = [
            current_user,
            info.get("current_user"),
            info.get("azure_user"),
            os.getenv("AZURE_DEVOPS_AUTHENTICATED_USER", ""),
            os.getenv("AZURE_DEVOPS_USER", ""),
            self.config.get("default_assignee", ""),
        ]

        # Primeira passagem: prefere nomes (sem @)
        for candidate in candidates:
            if isinstance(candidate, str) and _is_valid_name(candidate.strip()):
                return candidate.strip()

        # Segunda passagem: aceita email só se não houver nenhum nome disponível
        for candidate in candidates:
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()

        return self.config.get("default_assignee", "")

    def start(self, task_id: int, task_info: Optional[dict] = None, current_user: str = "") -> str:
        """
        Inicia execução de uma tarefa.
        
        Se task_info não for fornecido, retorna instrução para buscar do Azure.
        """
        # Verifica se já existe execução
        existing = self._load_execution(task_id)
        if existing and existing.status == "in-progress":
            return f"⚠️ Tarefa {task_id} já está em execução desde {existing.started_at}"
        
        if not task_info:
            # Retorna instrução para agente buscar info do Azure
            return json.dumps({
                "action": "fetch_azure_task",
                "task_id": task_id,
                "instruction": (
                    f"Use Azure-getWorkItem para buscar tarefa {task_id} e então chame "
                    f"task_flow.py start {task_id} --info <json> --current-user <usuario_azure_autenticado>"
                )
            })
        
        # Cria nova execução
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        assignee = self._resolve_assignee(task_info, current_user)
        task_type = task_info.get("type", "Task")
        active_state = self._get_active_state(task_type)

        execution = TaskExecution(
            task_id=task_id,
            title=task_info.get("title", f"Task {task_id}"),
            description=task_info.get("description", ""),
            task_type=task_type,
            status="in-progress",
            assigned_to=assignee,
            sprint=task_info.get("sprint", "Iteration Path"),
            story_points=task_info.get("story_points", 0),
            azure_url=f"https://dev.azure.com/{self.config['azure_org']}/{self.config['azure_project']}/_workitems/edit/{task_id}",
            started_at=now,
        )

        # Adiciona log inicial
        execution.logs.append(LogEntry(
            timestamp=now,
            message=f"Tarefa iniciada. Status alterado para '{active_state}'.",
            entry_type="log"
        ))
        
        # 🔍 FASE 0: LEMBRETE DO PATTERN SNAPSHOT
        if (PROJECT_ROOT / ".claude" / "task-flow.yaml").exists():
            print("\n" + "="*80)
            print("🔍 FASE 0 — Antes de implementar, execute:")
            print("   py task-flow/src/task_flow.py discover")
            print("="*80)

        # 🎯 FASE 1: DETECÇÃO AUTOMÁTICA DE AGENTS
        try:
            agents_engine = AgentsEngine(PROJECT_ROOT)
            suggestions = agents_engine.suggest_agents(
                task_title=execution.title,
                task_description=execution.description,
                top_n=3
            )
            
            if suggestions:
                print("\n" + "="*80)
                print("📚 AGENTS RECOMENDADOS PARA ESTA TAREFA")
                print("="*80)
                print(agents_engine.format_suggestions(suggestions))
                print("\nPara consultar um agent durante o desenvolvimento:")
                print(f"  py src/task_flow.py guidance {task_id}")
                print("="*80 + "\n")
        except Exception as e:
            # Não bloqueia a execução se houver erro na sugestão de agents
            print(f"⚠️  Erro ao sugerir agents: {e}")
        
        # Salva arquivo
        exec_file = self._save_execution(execution)

        # Monta children_azure_update para tasks filhas informadas em task_info["children"]
        children_ids = task_info.get("children", [])
        children_updates = [
            {
                "task_id": child_id,
                "new_status": "In Progress",
                "assigned_to": assignee,
                "instruction": (
                    f"OBRIGATÓRIO: mcp__azure-devops__updateWorkItem("
                    f"id: {child_id}, state='In Progress', assignedTo='{assignee}')"
                )
            }
            for child_id in children_ids
        ]

        # Retorna instruções para atualizar Azure
        return json.dumps({
            "success": True,
            "message": f"✅ Execução iniciada para tarefa {task_id}",
            "file": str(exec_file.relative_to(PROJECT_ROOT)),
            "azure_update": {
                "action": "update_status",
                "task_id": task_id,
                "new_status": active_state,
                "assigned_to": assignee,
                "instruction": (
                    f"OBRIGATÓRIO: execute imediatamente "
                    f"mcp__azure-devops__updateWorkItem("
                    f"id: {task_id}, state='{active_state}', assignedTo='{assignee}')"
                )
            },
            "children_azure_update": children_updates,
            "sprint_update": {
                "action": "update_sprint_doc",
                "sprint": execution.sprint,
                "task_id": task_id,
                "new_status": active_state
            },
            "commit_policy": {
                "require_user_confirmation": True,
                "instruction": "OBRIGATÓRIO: antes de executar qualquer 'git commit', apresente ao usuário o resumo das mudanças (arquivos alterados + mensagem proposta) e aguarde confirmação explícita ('sim'/'confirmo'). NÃO faça commit automaticamente sem aprovação do usuário."
            }
        })

    def log(self, task_id: int, message: str) -> str:
        """Adiciona entrada de log à execução."""
        execution = self._load_execution(task_id)
        if not execution:
            return f"❌ Nenhuma execução encontrada para tarefa {task_id}"
        
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        execution.logs.append(LogEntry(timestamp=now, message=message, entry_type="log"))
        
        exec_file = self._save_execution(execution)
        return f"✅ Log adicionado à tarefa {task_id}\n📝 {message}"
    
    def commit(self, task_id: int, commit_hash: str, message: str = "") -> str:
        """Registra um commit na execução."""
        execution = self._load_execution(task_id)
        if not execution:
            return f"❌ Nenhuma execução encontrada para tarefa {task_id}"
        
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        execution.commits.append(CommitInfo(
            hash=commit_hash,
            message=message or f"Commit {commit_hash[:7]}",
            timestamp=now
        ))
        
        # Também adiciona ao log
        execution.logs.append(LogEntry(
            timestamp=now,
            message=f"Commit registrado: {commit_hash[:7]} - {message}",
            entry_type="commit"
        ))
        
        exec_file = self._save_execution(execution)
        return f"✅ Commit {commit_hash[:7]} registrado na tarefa {task_id}"
    
    def add_file(self, task_id: int, filepath: str, action: str, commit_hash: str = "") -> str:
        """Registra arquivo alterado."""
        execution = self._load_execution(task_id)
        if not execution:
            return f"❌ Nenhuma execução encontrada para tarefa {task_id}"
        
        execution.files.append(FileChange(
            path=filepath,
            action=action,
            commit_hash=commit_hash or None
        ))
        
        exec_file = self._save_execution(execution)
        return f"✅ Arquivo registrado: {filepath} ({action})"
    
    def decision(self, task_id: int, decision_text: str) -> str:
        """Registra uma decisão tomada."""
        execution = self._load_execution(task_id)
        if not execution:
            return f"❌ Nenhuma execução encontrada para tarefa {task_id}"
        
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        execution.decisions.append(f"[{now}] {decision_text}")
        
        # Também adiciona ao log
        execution.logs.append(LogEntry(
            timestamp=now,
            message=f"Decisão: {decision_text}",
            entry_type="decision"
        ))
        
        exec_file = self._save_execution(execution)
        
        # Verifica se deve criar ADR
        adr_keywords = ["arquitetura", "padrão", "framework", "tecnologia", "design"]
        should_adr = any(kw in decision_text.lower() for kw in adr_keywords)
        
        result = f"✅ Decisão registrada na tarefa {task_id}"
        if should_adr:
            result += "\n⚠️ Considere criar um ADR para esta decisão: [[04-adrs/]]"
        
        return result
    
    def status(self, task_id: int) -> str:
        """Retorna status atual da execução."""
        execution = self._load_execution(task_id)
        if not execution:
            return f"❌ Nenhuma execução encontrada para tarefa {task_id}"
        
        exec_file = self._get_execution_file(task_id)
        
        # Calcula tempo decorrido
        started = datetime.strptime(execution.started_at, "%Y-%m-%d %H:%M")
        elapsed = datetime.now() - started
        elapsed_str = f"{elapsed.seconds // 3600}h {(elapsed.seconds % 3600) // 60}min"
        
        return f"""📋 Status da Tarefa {task_id}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📝 Título: {execution.title}
📊 Status: {execution.status}
👤 Responsável: {execution.assigned_to}
🏃 Sprint: {execution.sprint}
⏱️ Tempo decorrido: {elapsed_str}
📁 Arquivo: {exec_file.name}

📈 Progresso:
  • Logs: {len(execution.logs)} entradas
  • Commits: {len(execution.commits)}
  • Arquivos: {len(execution.files)}
  • Decisões: {len(execution.decisions)}

🔗 Azure: {execution.azure_url}
"""
    
    def finish(self, task_id: int, confirmed_by: str = "", children: Optional[list[int]] = None) -> str:
        """Finaliza execução da tarefa."""
        execution = self._load_execution(task_id)
        if not execution:
            return f"❌ Nenhuma execução encontrada para tarefa {task_id}"
        
        if execution.status == "done":
            return f"⚠️ Tarefa {task_id} já está finalizada"

        # Validação: bloqueia finish sem commits registrados
        if not execution.commits:
            return json.dumps({
                "success": False,
                "error": "PROCESSO INCOMPLETO",
                "message": (
                    f"❌ Tarefa {task_id} não pode ser finalizada sem commits registrados.\n"
                    "Execute o git commit e depois:\n"
                    f"  py src/task_flow.py commit {task_id} <hash> \"<mensagem>\"\n"
                    "Então chame finish novamente."
                ),
                "missing": ["commit"]
            })

        # Aviso: muitos arquivos sem decisões registradas
        warnings = []
        if len(execution.files) >= 3 and not execution.decisions:
            warnings.append("Nenhuma decisão técnica registrada para uma tarefa com múltiplos arquivos alterados.")

        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        
        # Calcula tempo total
        started = datetime.strptime(execution.started_at, "%Y-%m-%d %H:%M")
        ended = datetime.now()
        elapsed_minutes = int((ended - started).total_seconds() / 60)
        
        # Atualiza execução
        execution.status = "done"
        execution.finished_at = now
        execution.time_spent_minutes = elapsed_minutes
        execution.confirmed_by = confirmed_by or None
        
        # Adiciona log final
        execution.logs.append(LogEntry(
            timestamp=now,
            message=f"Tarefa finalizada. Tempo total: {execution._format_time_spent()}",
            entry_type="log"
        ))
        
        exec_file = self._save_execution(execution)
        assignee = execution.assigned_to

        # Monta children_azure_update
        children_list = children or []
        children_updates = [
            {
                "task_id": child_id,
                "new_status": "Done",
                "assigned_to": assignee,
                "instruction": (
                    f"OBRIGATÓRIO: mcp__azure-devops__updateWorkItem("
                    f"id: {child_id}, state='Done', assignedTo='{assignee}')"
                )
            }
            for child_id in children_list
        ]

        return json.dumps({
            "success": True,
            "message": f"✅ Tarefa {task_id} finalizada!",
            "file": str(exec_file.relative_to(PROJECT_ROOT)),
            "warnings": warnings,
            "summary": {
                "title": execution.title,
                "time_spent": execution._format_time_spent(),
                "commits": len(execution.commits),
                "files_changed": len(execution.files),
                "decisions": len(execution.decisions),
                "agents_consulted": len(set([a.split('@')[0] if '@' in a else a for a in execution.agents_consulted])) if execution.agents_consulted else 0
            },
            "azure_update": {
                "action": "update_status",
                "task_id": task_id,
                "new_status": "Done",
                "assigned_to": assignee,
                "instruction": (
                    f"OBRIGATÓRIO: execute "
                    f"mcp__azure-devops__updateWorkItem("
                    f"id: {task_id}, state='Done', assignedTo='{assignee}')"
                )
            },
            "children_azure_update": children_updates,
            "sprint_update": {
                "action": "update_sprint_doc",
                "sprint": execution.sprint,
                "task_id": task_id,
                "new_status": "Done ✅"
            },
            "confirmation_needed": not confirmed_by,
            "confirmation_instruction": (
                "BLOQUEIO: NÃO execute mcp__azure-devops__updateWorkItem nem marque nenhum item como Done "
                "sem receber confirmação explícita ('sim'/'confirmo') do usuário nesta conversa. "
                "Exiba o resumo da execução e aguarde resposta antes de qualquer atualização no Azure DevOps."
            ) if not confirmed_by else None
        })
    
    def reprocess(self, task_id: int) -> str:
        """Re-renderiza o arquivo de execução com o template atual sem alterar dados."""
        execution = self._load_execution(task_id)
        if not execution:
            return f"❌ Nenhuma execução encontrada para tarefa {task_id}"

        exec_file = self._save_execution(execution)
        return f"✅ Tarefa {task_id} re-processada: {exec_file.name}"

    def list_executions(self) -> str:
        """Lista todas as execuções."""
        executions = []
        
        for exec_file in EXECUCOES_PATH.glob("*-task-*.md"):
            # Extrai task_id do nome do arquivo
            match = re.search(r'task-(\d+)\.md$', exec_file.name)
            if match:
                task_id = int(match.group(1))
                execution = self._load_execution(task_id)
                if execution:
                    executions.append(execution)
        
        if not executions:
            return "📋 Nenhuma execução encontrada."
        
        # Separa em andamento e concluídas
        in_progress = [e for e in executions if e.status == "in-progress"]
        done = [e for e in executions if e.status == "done"]
        
        result = "📋 Execuções de Tarefas\n" + "━" * 40 + "\n\n"
        
        if in_progress:
            result += "🔄 EM ANDAMENTO:\n"
            for e in in_progress:
                result += f"  • [{e.task_id}] {e.title} (desde {e.started_at})\n"
            result += "\n"
        
        if done:
            result += f"✅ CONCLUÍDAS ({len(done)}):\n"
            for e in done[-5:]:  # Últimas 5
                result += f"  • [{e.task_id}] {e.title} ({e._format_time_spent()})\n"
        
        return result
    
    def regen_index(self) -> str:
        """Regenera _index.md a partir dos frontmatters de todos os arquivos de execução."""
        from collections import defaultdict

        index_file = EXECUCOES_PATH / "_index.md"
        arquivo_dir = EXECUCOES_PATH / "_arquivo"

        all_files = [
            f for f in EXECUCOES_PATH.rglob("*-task-*.md")
            if arquivo_dir not in f.parents and f != index_file
        ]

        executions = []
        for exec_file in all_files:
            try:
                content = exec_file.read_text(encoding="utf-8")
                m = re.search(r'task-(\d+)\.md$', exec_file.name)
                if not m:
                    continue
                task_id = int(m.group(1))

                fm: dict[str, str] = {}
                fm_match = re.match(r'^---\n(.*?)\n---', content, re.DOTALL)
                if fm_match:
                    for line in fm_match.group(1).split('\n'):
                        if ':' in line:
                            k, v = line.split(':', 1)
                            fm[k.strip()] = v.strip().strip('"')

                rel_str = str(exec_file.relative_to(EXECUCOES_PATH)).replace('\\', '/')
                executions.append({
                    'task_id': task_id,
                    'title': fm.get('title', f'Task {task_id}'),
                    'sprint': fm.get('sprint', 'Sem sprint'),
                    'status': fm.get('status', 'done'),
                    'started_at': fm.get('started_at', ''),
                    'time_spent_minutes': fm.get('time_spent_minutes', ''),
                    'assigned_to': fm.get('assigned_to', ''),
                    'rel_path': rel_str,
                })
            except Exception:
                continue

        def fmt_time(minutes_str: str) -> str:
            try:
                mins = int(minutes_str)
                return f"{mins // 60}h {mins % 60}min" if mins >= 60 else f"{mins} min"
            except Exception:
                return "-"

        now = datetime.now().strftime("%Y-%m-%d")
        total = len(executions)
        in_progress = [e for e in executions if e['status'] == 'in-progress']
        done_execs = [e for e in executions if e['status'] == 'done']
        sorted_all = sorted(executions, key=lambda x: x['started_at'], reverse=True)

        lines = [
            "# Indice de Execucoes",
            "",
            "#execucao #indice #processos",
            "",
            "> Registro de todas as execucoes de tarefas do projeto Arenar.",
            f"> **Ultima atualizacao:** {now} (auto-gerado)",
            "",
            "---",
            "",
            "## Estatisticas",
            "",
            "| Metrica | Valor |",
            "|---------|-------|",
            f"| Total de execucoes | {total} |",
            f"| Em andamento | {len(in_progress)} |",
            f"| Concluidas | {len(done_execs)} |",
            "",
            "---",
            "",
            "## Execucoes em Andamento",
            "",
            "| ID | Tarefa | Sprint | Inicio | Responsavel |",
            "|----|--------|--------|--------|-------------|",
        ]
        for e in in_progress:
            lines.append(f"| [[{e['rel_path']}|{e['task_id']}]] | {e['title'][:45]} | {e['sprint']} | {e['started_at']} | {e['assigned_to']} |")

        lines += [
            "",
            "---",
            "",
            "## Execucoes Recentes (ultimas 10)",
            "",
            "| ID | Tarefa | Sprint | Tempo | Status |",
            "|----|--------|--------|-------|--------|",
        ]
        for e in sorted_all[:10]:
            status_label = "Done" if e['status'] == 'done' else "Em andamento"
            lines.append(f"| [[{e['rel_path']}|{e['task_id']}]] | {e['title'][:45]} | {e['sprint']} | {fmt_time(e['time_spent_minutes'])} | {status_label} |")

        by_sprint: dict = defaultdict(list)
        for e in executions:
            by_sprint[e['sprint']].append(e)

        def _sprint_sort_key(s: str) -> tuple:
            m = re.search(r'(\d+)', s)
            return (0, int(m.group(1))) if m else (1, s)

        lines += ["", "---", "", "## Por Sprint", ""]
        for sprint in sorted(by_sprint.keys(), key=_sprint_sort_key):
            lines.append(f"### {sprint}")
            for e in sorted(by_sprint[sprint], key=lambda x: x['started_at']):
                marker = "Done" if e['status'] == 'done' else "In Progress"
                lines.append(f"- [[{e['rel_path']}|Task #{e['task_id']}]] -- {e['title'][:55]} {marker}")
            lines.append("")

        index_file.write_text("\n".join(lines), encoding="utf-8")
        return f"✅ _index.md regenerado: {total} execuções ({len(in_progress)} em andamento, {len(done_execs)} concluídas)"

    def guidance(self, task_id: int, agent_name: str = "") -> str:
        """
        Mostra agents recomendados ou conteúdo de um agent específico.
        
        Args:
            task_id: ID da tarefa em execução
            agent_name: Nome do agent específico (opcional)
        """
        execution = self._load_execution(task_id)
        if not execution:
            return f"❌ Nenhuma execução encontrada para tarefa {task_id}"
        
        agents_engine = AgentsEngine(PROJECT_ROOT)
        
        if agent_name:
            # Mostrar conteúdo do agent específico
            content = agents_engine.get_agent_content(agent_name)
            if not content:
                return f"❌ Agent '{agent_name}' não encontrado"
            
            # FASE 2: Registrar que este agent foi consultado
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
            agent_record = f"{agent_name}@{timestamp}"
            
            if agent_record not in execution.agents_consulted:
                execution.agents_consulted.append(agent_record)
                self._save_execution(execution)
                
                # FASE 4: Registrar em métricas
                try:
                    metrics = AgentsMetrics(METRICS_PATH)
                    metrics.record_agent_consultation(
                        agent_name=agent_name,
                        task_id=task_id,
                        task_title=execution.title
                    )
                except Exception as e:
                    # Não bloqueia se houver erro em métricas
                    print(f"⚠️  Erro ao registrar métrica: {e}")
                
                print(f"✅ Consulta de '{agent_name}' registrada\n")
            
            return f"📚 {agent_name}\n" + "="*80 + "\n\n" + content
        else:
            # Mostrar sugestões
            suggestions = agents_engine.suggest_agents(
                task_title=execution.title,
                task_description=execution.description,
                top_n=5
            )
            
            if not suggestions:
                return "ℹ️  Nenhum agent sugerido para esta tarefa"
            
            result = f"📚 Agents Recomendados para Tarefa {task_id}\n"
            result += "="*80 + "\n\n"
            result += agents_engine.format_suggestions(suggestions)
            result += "\n\nPara ver conteúdo completo:"
            result += f"\n  py src/task_flow.py guidance {task_id} --agent <nome>"
            
            return result
    
    def list_agents(self, agent_type: str = "") -> str:
        """
        Lista todos os agents disponíveis.
        
        Args:
            agent_type: "backend", "frontend" ou vazio (todos)
        """
        agents_engine = AgentsEngine(PROJECT_ROOT)
        
        # Validar tipo
        valid_type = None
        if agent_type:
            if agent_type.lower() in ["backend", "be"]:
                valid_type = "backend"
            elif agent_type.lower() in ["frontend", "fe"]:
                valid_type = "frontend"
        
        agents = agents_engine.list_all_agents(valid_type)
        
        if not agents:
            return "❌ Nenhum agent encontrado"
        
        # Agrupar por tipo
        backend = [a for a in agents if a.agent_type == "backend"]
        frontend = [a for a in agents if a.agent_type == "frontend"]
        
        result = "📚 Agents Especializados Disponíveis\n" + "="*80 + "\n\n"
        
        if backend:
            result += "🔧 BACKEND:\n"
            for agent in backend:
                result += f"  • {agent.display_name}\n"
            result += "\n"
        
        if frontend:
            result += "🎨 FRONTEND:\n"
            for agent in frontend:
                result += f"  • {agent.display_name}\n"
        
        result += "\nPara ver conteúdo de um agent:"
        result += "\n  py src/task_flow.py guidance <task_id> --agent <nome>"
        
        return result
    
    def discover(self) -> str:
        """Exibe o Pattern Snapshot do projeto lido de .claude/task-flow.yaml."""
        try:
            import yaml
        except ImportError:
            return "❌ PyYAML não instalado. Execute: pip install pyyaml"

        config_path = PROJECT_ROOT / ".claude" / "task-flow.yaml"
        if not config_path.exists():
            return (
                "⚠️  .claude/task-flow.yaml não encontrado.\n"
                "Crie-o para ativar o Pattern Snapshot automático."
            )

        try:
            with open(config_path, encoding="utf-8") as f:
                config = yaml.safe_load(f) or {}
        except Exception as e:
            return f"❌ Erro ao ler task-flow.yaml: {e}"

        project   = config.get("project", {})
        patterns  = config.get("patterns", {})
        naming    = config.get("naming", {})
        rules     = config.get("custom_rules", [])
        approved  = config.get("approved_libs", {})
        blocked   = config.get("blocked_libs", {})

        lines = [
            f"PATTERN SNAPSHOT — {project.get('name', 'Projeto')}",
            "━" * 60,
            f"Stack:        {project.get('stack', '?')}",
            f"Architecture: {project.get('architecture', '?')}",
            "",
        ]

        # CQRS
        cqrs = patterns.get("cqrs", {})
        if cqrs:
            mediator = cqrs.get("mediator", "?")
            note = cqrs.get("note", "")
            cqrs_line = f"CQRS:         {mediator}"
            if note:
                cqrs_line += f" — {note}"
            lines.append(cqrs_line)

        # Pipeline
        pipeline = patterns.get("pipeline", {})
        if pipeline.get("order"):
            lines.append(f"Pipeline:     {' → '.join(pipeline['order'])}")
            if pipeline.get("note_uow"):
                lines.append(f"  ⚠️  {pipeline['note_uow']}")

        # Persistence
        persistence = patterns.get("persistence", {})
        if persistence:
            uow = "UnitOfWork automático (PersistenceBehavior)" if persistence.get("unit_of_work_automatic") else (
                "UnitOfWork manual" if persistence.get("unit_of_work") else "Sem UnitOfWork"
            )
            lines.append(f"Persistence:  {uow}, {persistence.get('orm', '?')}, repos {persistence.get('repository', '?')}")

        # Error handling
        errors = patterns.get("error_handling", {})
        if errors:
            result_type = errors.get("result_type", "")
            types_str = ", ".join(errors.get("error_types", []))
            lines.append(f"Errors:       {errors.get('style', '?')} ({result_type})")
            if types_str:
                lines.append(f"  Types:      {types_str}")

        # Domain
        domain = patterns.get("domain", {})
        if domain:
            style = domain.get("style", "?")
            features = []
            if domain.get("private_constructors"): features.append("construtores privados")
            if domain.get("factory_methods"):       features.append(f"Create() → {domain.get('factory_return', 'Result<T>')}")
            if domain.get("value_objects"):         features.append(domain.get("value_object_style", "Value Objects"))
            if domain.get("domain_events"):         features.append(domain.get("domain_event_style", "Domain Events"))
            lines.append(f"Domain:       {style}")
            for f in features:
                lines.append(f"  • {f}")

        # Validation
        validation = patterns.get("validation", {})
        if validation:
            pipeline_note = "pipeline automático" if validation.get("pipeline") else "manual"
            lines.append(f"Validation:   {validation.get('library', '?')} ({pipeline_note})")

        # Testing
        testing = patterns.get("testing", {})
        if testing:
            lines.append(f"Testing:      {testing.get('framework', '?')} + {testing.get('mocking', '?')}")
            if testing.get("naming"):
                lines.append(f"  Naming:     {testing['naming']}")
            if testing.get("pattern"):
                lines.append(f"  Padrão:     {testing['pattern']}")

        # Naming
        be_naming = naming.get("backend", {})
        if be_naming:
            lines.append("")
            lines.append("NAMING (backend):")
            for k, v in be_naming.items():
                lines.append(f"  {k:<12} {v}")

        # Custom rules
        if rules:
            lines.append("")
            lines.append("REGRAS CUSTOMIZADAS:")
            for rule in rules:
                lines.append(f"  • {rule}")

        # Libs
        def _fmt_libs(libs_config):
            if isinstance(libs_config, dict):
                return libs_config.get("backend", []), libs_config.get("frontend", [])
            if isinstance(libs_config, list):
                return libs_config, []
            return [], []

        ok_be, ok_fe = _fmt_libs(approved)
        bl_be, _ = _fmt_libs(blocked)

        if ok_be:
            lines.append("")
            lines.append(f"LIBS APROVADAS (backend):  {', '.join(ok_be)}")
        if ok_fe:
            lines.append(f"LIBS APROVADAS (frontend): {', '.join(ok_fe)}")
        if bl_be:
            lines.append(f"LIBS PROIBIDAS (backend):  {', '.join(bl_be)}")

        return "\n".join(lines)

    def plan(self, work_item_id: int) -> str:
        """
        Gera instruções para Claude criar um plano de implementação.

        O CLI retorna um JSON com instruções detalhadas. Claude executa os passos:
        1. Busca o work item e seus filhos no Azure DevOps
        2. Lê overviews de domínio relevantes do vault
        3. Gera o documento de plano seguindo o template
        4. Salva em 10-planos/ via mcp__obsidian__write_file
        5. Pede confirmação para executar
        """
        PLANOS_PATH.mkdir(parents=True, exist_ok=True)
        date_str = datetime.now().strftime("%Y-%m-%d")
        plan_filename = f"plan-{date_str}-{work_item_id}.md"
        plan_path = PLANOS_PATH / plan_filename
        vault_rel = f"C:\\\\Users\\\\Athos\\\\OneDrive\\\\Documentos\\\\projetos\\\\Arenar\\\\arenar-vault\\\\10-planos\\\\{plan_filename}"

        instruction = {
            "action": "generate_plan",
            "work_item_id": work_item_id,
            "plan_file": str(plan_path),
            "steps": [
                f"1. BUSCAR WORK ITEM: mcp__azure-devops__getWorkItem(id: {work_item_id})",
                f"2. BUSCAR FILHOS: mcp__azure-devops__queryWorkItems com 'Parent = {work_item_id}' para obter tasks filhas",
                "3. LER DOMÍNIO: para cada domínio afetado, ler o overview em arenar-vault/02-dominios/<dominio>/<dominio>-overview.md",
                "4. GERAR PLANO: seguir o template em arenar-vault/_templates/template-plano.md",
                f"5. SALVAR: mcp__obsidian__write_file(path: '{vault_rel}', content: <plano_gerado>)",
                "6. CONFIRMAR: após salvar, exibir o plano ao usuário e perguntar: 'Confirma execução deste plano? (sim/não)'",
                "   - Se sim: chamar task-flow start <pbi_id> --info <json_com_children> UMA ÚNICA VEZ para o PBI raiz, passando a lista de tasks filhas em info['children']. NÃO chamar start separadamente para cada task filha.",
                "   - Se não: informar que o plano foi salvo e pode ser editado antes de executar",
            ],
            "template_path": str(TEMPLATES_PATH / "template-plano.md"),
            "plan_template_rules": [
                "Frontmatter com: plan_id, work_item_id, title, type, sprint, status: draft, created_at, tasks (lista de IDs)",
                "Seção Contexto: descrição do work item do Azure",
                "Seção Escopo: tabela com todos os work items (PBI + tasks filhas)",
                "Seção Implementação Detalhada: uma subseção por task com arquivos (checkboxes com caminhos reais), passos numerados e critério de aceite",
                "Seção Ordem de Execução: sequência recomendada com justificativa de dependências",
                "Seção Riscos e Decisões: checkboxes de pontos de atenção",
                "Seção Testes Necessários: checkboxes de testes a criar/executar",
                "Rodapé com comando para executar: py ../tools/task-flow/src/task_flow.py start <task_id>",
                "Usar caminhos reais do projeto (arenar-backend/src/Modules/...) inferidos do contexto",
            ],
        }
        return json.dumps(instruction, ensure_ascii=False, indent=2)

    def metrics_dashboard(self) -> str:
        """
        Exibe dashboard de métricas de agents (Fase 4).
        
        Returns:
            Dashboard formatado
        """
        try:
            metrics = AgentsMetrics(METRICS_PATH)
            return metrics.generate_dashboard()
        except Exception as e:
            return f"❌ Erro ao gerar dashboard: {e}"


def main():
    parser = argparse.ArgumentParser(
        description="Task Flow - Fluxo de execução de tarefas Azure ↔ Obsidian"
    )
    sub = parser.add_subparsers(dest="command", help="Comando")
    
    # start
    p_start = sub.add_parser("start", help="Iniciar execução de tarefa")
    p_start.add_argument("task_id", type=int, help="ID da tarefa no Azure DevOps")
    p_start.add_argument("--info", type=str, help="JSON com informações da tarefa")
    p_start.add_argument("--current-user", type=str, default="", help="Usuário autenticado no Azure DevOps")
    
    # log
    p_log = sub.add_parser("log", help="Adicionar entrada de log")
    p_log.add_argument("task_id", type=int, help="ID da tarefa")
    p_log.add_argument("message", help="Mensagem de log")
    
    # commit
    p_commit = sub.add_parser("commit", help="Registrar commit")
    p_commit.add_argument("task_id", type=int, help="ID da tarefa")
    p_commit.add_argument("hash", help="Hash do commit")
    p_commit.add_argument("message", nargs="?", default="", help="Mensagem do commit")
    
    # files
    p_files = sub.add_parser("files", help="Registrar arquivo alterado")
    p_files.add_argument("task_id", type=int, help="ID da tarefa")
    p_files.add_argument("filepath", help="Caminho do arquivo")
    p_files.add_argument("action", choices=["created", "modified", "deleted"], help="Ação")
    p_files.add_argument("--commit", default="", help="Hash do commit relacionado")
    
    # decision
    p_decision = sub.add_parser("decision", help="Registrar decisão")
    p_decision.add_argument("task_id", type=int, help="ID da tarefa")
    p_decision.add_argument("text", help="Texto da decisão")
    
    # status
    p_status = sub.add_parser("status", help="Ver status da execução")
    p_status.add_argument("task_id", type=int, help="ID da tarefa")
    
    # finish
    p_finish = sub.add_parser("finish", help="Finalizar execução")
    p_finish.add_argument("task_id", type=int, help="ID da tarefa")
    p_finish.add_argument("--confirmed-by", default="", help="Nome de quem confirmou")
    p_finish.add_argument("--children", default="", help="IDs das tasks filhas separados por vírgula (ex: 869,870)")
    
    # reprocess
    p_reprocess = sub.add_parser("reprocess", help="Re-renderizar arquivo com template atual")
    p_reprocess.add_argument("task_id", type=int, help="ID da tarefa")

    # list
    sub.add_parser("list", help="Listar execuções")

    # regen-index
    sub.add_parser("regen-index", help="Regenerar 09-execucoes/_index.md")
    
    # guidance
    p_guidance = sub.add_parser("guidance", help="Ver agents recomendados ou conteúdo de agent")
    p_guidance.add_argument("task_id", type=int, help="ID da tarefa")
    p_guidance.add_argument("--agent", default="", help="Nome do agent específico")
    
    # agents
    p_agents = sub.add_parser("agents", help="Listar agents disponíveis")
    p_agents.add_argument("--type", default="", help="Tipo: backend, frontend ou vazio (todos)")
    
    # discover
    sub.add_parser("discover", help="Exibir Pattern Snapshot do projeto (.claude/task-flow.yaml)")

    # plan
    p_plan = sub.add_parser("plan", help="Gerar plano de implementação para um work item")
    p_plan.add_argument("work_item_id", type=int, help="ID do PBI ou Task no Azure DevOps")

    # metrics (FASE 4)
    sub.add_parser("metrics", help="Ver dashboard de métricas de agents")
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        sys.exit(1)
    
    manager = TaskFlowManager()
    
    if args.command == "start":
        task_info = json.loads(args.info) if args.info else None
        print(manager.start(args.task_id, task_info, args.current_user))
    
    elif args.command == "log":
        print(manager.log(args.task_id, args.message))
    
    elif args.command == "commit":
        print(manager.commit(args.task_id, args.hash, args.message))
    
    elif args.command == "files":
        print(manager.add_file(args.task_id, args.filepath, args.action, args.commit))
    
    elif args.command == "decision":
        print(manager.decision(args.task_id, args.text))
    
    elif args.command == "status":
        print(manager.status(args.task_id))
    
    elif args.command == "finish":
        children_list = [int(x.strip()) for x in args.children.split(",") if x.strip()] if args.children else []
        print(manager.finish(args.task_id, args.confirmed_by, children_list))
    
    elif args.command == "reprocess":
        print(manager.reprocess(args.task_id))

    elif args.command == "list":
        print(manager.list_executions())

    elif args.command == "regen-index":
        print(manager.regen_index())
    
    elif args.command == "guidance":
        print(manager.guidance(args.task_id, args.agent))
    
    elif args.command == "agents":
        print(manager.list_agents(args.type))
    
    elif args.command == "discover":
        print(manager.discover())

    elif args.command == "plan":
        print(manager.plan(args.work_item_id))

    elif args.command == "metrics":
        print(manager.metrics_dashboard())


if __name__ == "__main__":
    main()
