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

# Garantir output UTF-8 no Windows
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# Diretórios
SRC_DIR = Path(__file__).parent.resolve()
ROOT = SRC_DIR.parent  # task-flow/
PROJECT_ROOT = ROOT.parent  # Arenar/
VAULT_PATH = PROJECT_ROOT / "arenar-vault"
EXECUCOES_PATH = VAULT_PATH / "09-execucoes"
TEMPLATES_PATH = VAULT_PATH / "_templates"
SPRINTS_PATH = VAULT_PATH / "06-sprints"
CONFIG_PATH = ROOT / "config.json"


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
            "{{DESCRIPTION}}": self.description or "Sem descrição",
            "{{STARTED_AT}}": self.started_at,
            "{{FINISHED_AT}}": self.finished_at or "",
            "{{TIME_SPENT_MINUTES}}": str(self.time_spent_minutes) if self.time_spent_minutes else "",
            "{{TIME_SPENT}}": self._format_time_spent(),
            "{{SPRINT}}": self.sprint,
            "{{SPRINT_FILE}}": self._get_sprint_file(),
            "{{STORY_POINTS}}": str(self.story_points),
            "{{CONFIRMED_BY}}": self.confirmed_by or "",
        }

        for placeholder, value in replacements.items():
            content = content.replace(placeholder, value)

        # Substituir seções dinâmicas
        content = self._inject_logs(content)
        content = self._inject_commits(content)
        content = self._inject_files(content)
        content = self._inject_decisions(content)

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
            f"### {log.timestamp} — {log.entry_type.title()}\n- {log.message}\n"
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
        # Busca arquivo existente
        for f in EXECUCOES_PATH.glob(f"*-task-{task_id}.md"):
            return f
        
        # Novo arquivo
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
        # Extrai frontmatter (procura primeiro bloco --- ... ---)
        frontmatter = {}
        # Procura o bloco de frontmatter após tags ou no início
        fm_match = re.search(r'\n---\n(.*?)\n---', content, re.DOTALL)
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
        decision_section = re.search(r'## Decisões\n\n>.*?\n\n(.*?)(?=\n---|\Z)', content, re.DOTALL)
        if decision_section:
            for line in decision_section.group(1).split('\n'):
                if line.startswith('- ['):
                    decisions.append(line[2:])
        
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
            description="",
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
        )
        
        return execution
    
    def _save_execution(self, execution: TaskExecution):
        """Salva execução no arquivo markdown."""
        exec_file = self._get_execution_file(execution.task_id)
        content = execution.to_markdown()
        exec_file.write_text(content, encoding="utf-8")
        return exec_file
    
    def start(self, task_id: int, task_info: Optional[dict] = None) -> str:
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
                "instruction": f"Use Azure-getWorkItem para buscar tarefa {task_id} e então chame task_flow.py start {task_id} --info <json>"
            })
        
        # Cria nova execução
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        
        execution = TaskExecution(
            task_id=task_id,
            title=task_info.get("title", f"Task {task_id}"),
            description=task_info.get("description", ""),
            task_type=task_info.get("type", "Task"),
            status="in-progress",
            assigned_to=self.config["default_assignee"],
            sprint=task_info.get("sprint", "Iteration Path"),
            story_points=task_info.get("story_points", 0),
            azure_url=f"https://dev.azure.com/{self.config['azure_org']}/{self.config['azure_project']}/_workitems/edit/{task_id}",
            started_at=now,
        )
        
        # Adiciona log inicial
        execution.logs.append(LogEntry(
            timestamp=now,
            message="Tarefa iniciada. Status alterado para 'In Progress'.",
            entry_type="log"
        ))
        
        # Salva arquivo
        exec_file = self._save_execution(execution)
        
        # Retorna instruções para atualizar Azure
        return json.dumps({
            "success": True,
            "message": f"✅ Execução iniciada para tarefa {task_id}",
            "file": str(exec_file.relative_to(PROJECT_ROOT)),
            "azure_update": {
                "action": "update_status",
                "task_id": task_id,
                "new_status": "In Progress",
                "assigned_to": self.config["default_assignee"],
                "instruction": f"Use Azure-updateWorkItem para atualizar tarefa {task_id}: state='In Progress', assignedTo='{self.config['default_assignee']}'"
            },
            "sprint_update": {
                "action": "update_sprint_doc",
                "sprint": execution.sprint,
                "task_id": task_id,
                "new_status": "In Progress"
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
    
    def finish(self, task_id: int, confirmed_by: str = "") -> str:
        """Finaliza execução da tarefa."""
        execution = self._load_execution(task_id)
        if not execution:
            return f"❌ Nenhuma execução encontrada para tarefa {task_id}"
        
        if execution.status == "done":
            return f"⚠️ Tarefa {task_id} já está finalizada"
        
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        
        # Calcula tempo total
        started = datetime.strptime(execution.started_at, "%Y-%m-%d %H:%M")
        ended = datetime.now()
        elapsed_minutes = int((ended - started).total_seconds() / 60)
        
        # Atualiza execução
        execution.status = "done"
        execution.finished_at = now
        execution.time_spent_minutes = elapsed_minutes
        execution.confirmed_by = confirmed_by or "Pendente confirmação"
        
        # Adiciona log final
        execution.logs.append(LogEntry(
            timestamp=now,
            message=f"Tarefa finalizada. Tempo total: {execution._format_time_spent()}",
            entry_type="log"
        ))
        
        exec_file = self._save_execution(execution)
        
        return json.dumps({
            "success": True,
            "message": f"✅ Tarefa {task_id} finalizada!",
            "file": str(exec_file.relative_to(PROJECT_ROOT)),
            "summary": {
                "title": execution.title,
                "time_spent": execution._format_time_spent(),
                "commits": len(execution.commits),
                "files_changed": len(execution.files),
                "decisions": len(execution.decisions)
            },
            "azure_update": {
                "action": "update_status",
                "task_id": task_id,
                "new_status": "Done",
                "instruction": f"Use Azure-updateWorkItemState para atualizar tarefa {task_id} para 'Done'"
            },
            "sprint_update": {
                "action": "update_sprint_doc",
                "sprint": execution.sprint,
                "task_id": task_id,
                "new_status": "Done ✅"
            },
            "confirmation_needed": not confirmed_by,
            "confirmation_instruction": "Solicite confirmação do solicitante antes de marcar como concluída" if not confirmed_by else None
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


def main():
    parser = argparse.ArgumentParser(
        description="Task Flow - Fluxo de execução de tarefas Azure ↔ Obsidian"
    )
    sub = parser.add_subparsers(dest="command", help="Comando")
    
    # start
    p_start = sub.add_parser("start", help="Iniciar execução de tarefa")
    p_start.add_argument("task_id", type=int, help="ID da tarefa no Azure DevOps")
    p_start.add_argument("--info", type=str, help="JSON com informações da tarefa")
    
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
    
    # reprocess
    p_reprocess = sub.add_parser("reprocess", help="Re-renderizar arquivo com template atual")
    p_reprocess.add_argument("task_id", type=int, help="ID da tarefa")

    # list
    sub.add_parser("list", help="Listar execuções")
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        sys.exit(1)
    
    manager = TaskFlowManager()
    
    if args.command == "start":
        task_info = json.loads(args.info) if args.info else None
        print(manager.start(args.task_id, task_info))
    
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
        print(manager.finish(args.task_id, args.confirmed_by))
    
    elif args.command == "reprocess":
        print(manager.reprocess(args.task_id))

    elif args.command == "list":
        print(manager.list_executions())


if __name__ == "__main__":
    main()
