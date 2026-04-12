#!/usr/bin/env python3
"""
Agents Engine - Sistema de sugestão de agents especializados.

Carrega agents do .claude/commands e sugere os mais relevantes
baseado no tipo e escopo da tarefa.
"""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Tuple

try:
    import yaml as _yaml
    _YAML_AVAILABLE = True
except ImportError:
    _YAML_AVAILABLE = False


@dataclass
class Agent:
    """Representa um agent especializado."""
    name: str
    filepath: Path
    content: str
    agent_type: str  # "backend" ou "frontend"
    
    @property
    def display_name(self) -> str:
        """Nome formatado para exibição."""
        return self.name.replace(".md", "")
    
    def extract_keywords(self) -> set[str]:
        """Extrai keywords do conteúdo do agent."""
        # Remove markdown headers e code blocks
        content = re.sub(r'```.*?```', '', self.content, flags=re.DOTALL)
        content = re.sub(r'#+ ', '', content)
        
        # Extrai palavras relevantes (alfanuméricas, mínimo 3 chars)
        words = re.findall(r'\b[a-zA-Z]{3,}\b', content.lower())
        
        # Remove palavras comuns (stopwords básicas)
        stopwords = {
            'the', 'and', 'for', 'are', 'this', 'that', 'with', 'from',
            'use', 'you', 'can', 'will', 'has', 'have', 'been', 'your',
            'using', 'should', 'example', 'code', 'file', 'class', 'function'
        }
        
        return {w for w in words if w not in stopwords}


class AgentsEngine:
    """Engine para detecção e sugestão de agents."""
    
    def __init__(self, project_root: Path):
        """
        Inicializa o engine.

        Args:
            project_root: Raiz do projeto (contém .claude/task-flow.yaml, ou subpastas com agents)
        """
        self.project_root = project_root

        config = self._load_task_flow_config()
        agents_config = config.get("agents", {})
        backend_rel = agents_config.get("backend_path", "arenar-backend/.claude/commands")
        frontend_rel = agents_config.get("frontend_path", "arenar-frontend/.claude/commands")

        self.backend_agents_path = project_root / backend_rel
        self.frontend_agents_path = project_root / frontend_rel

        self._backend_agents: List[Agent] = []
        self._frontend_agents: List[Agent] = []
        self._loaded = False

    def _load_task_flow_config(self) -> dict:
        """Carrega .claude/task-flow.yaml se existir."""
        config_path = self.project_root / ".claude" / "task-flow.yaml"
        if not config_path.exists() or not _YAML_AVAILABLE:
            return {}
        try:
            with open(config_path, encoding="utf-8") as f:
                return _yaml.safe_load(f) or {}
        except Exception:
            return {}
    
    def load_agents(self) -> None:
        """Carrega todos os agents disponíveis."""
        if self._loaded:
            return
        
        # Carregar backend agents
        if self.backend_agents_path.exists():
            for agent_file in self.backend_agents_path.glob("*.md"):
                try:
                    content = agent_file.read_text(encoding="utf-8")
                    agent = Agent(
                        name=agent_file.name,
                        filepath=agent_file,
                        content=content,
                        agent_type="backend"
                    )
                    self._backend_agents.append(agent)
                except Exception as e:
                    print(f"⚠️  Erro ao carregar {agent_file.name}: {e}")
        
        # Carregar frontend agents
        if self.frontend_agents_path.exists():
            for agent_file in self.frontend_agents_path.glob("*.md"):
                try:
                    content = agent_file.read_text(encoding="utf-8")
                    agent = Agent(
                        name=agent_file.name,
                        filepath=agent_file,
                        content=content,
                        agent_type="frontend"
                    )
                    self._frontend_agents.append(agent)
                except Exception as e:
                    print(f"⚠️  Erro ao carregar {agent_file.name}: {e}")
        
        self._loaded = True
    
    def detect_task_type(self, title: str) -> str:
        """
        Detecta o tipo de tarefa (backend/frontend/fullstack) pelo título.
        
        Args:
            title: Título da tarefa
            
        Returns:
            "backend", "frontend" ou "fullstack"
        """
        title_lower = title.lower()
        
        # Detecção explícita por tag
        if "[be]" in title_lower or "[backend]" in title_lower:
            return "backend"
        if "[fe]" in title_lower or "[frontend]" in title_lower:
            return "frontend"
        
        # Detecção por keywords
        backend_keywords = {"api", "endpoint", "controller", "handler", "command", "query", 
                          "database", "migration", "entity", "repository", "service"}
        frontend_keywords = {"screen", "component", "ui", "interface", "navigation", 
                           "expo", "react", "screen", "tela"}
        
        has_backend = any(kw in title_lower for kw in backend_keywords)
        has_frontend = any(kw in title_lower for kw in frontend_keywords)
        
        if has_backend and has_frontend:
            return "fullstack"
        if has_backend:
            return "backend"
        if has_frontend:
            return "frontend"
        
        return "fullstack"  # Default quando incerto
    
    def suggest_agents(
        self,
        task_title: str,
        task_description: str = "",
        top_n: int = 3
    ) -> List[Tuple[Agent, float]]:
        """
        Sugere agents mais relevantes para a tarefa.
        
        Args:
            task_title: Título da tarefa
            task_description: Descrição da tarefa (opcional)
            top_n: Número de agents a retornar
            
        Returns:
            Lista de tuplas (Agent, relevance_score) ordenadas por relevância
        """
        self.load_agents()
        
        # Detectar tipo de tarefa
        task_type = self.detect_task_type(task_title)
        
        # Selecionar pool de agents
        if task_type == "backend":
            agents_pool = self._backend_agents
        elif task_type == "frontend":
            agents_pool = self._frontend_agents
        else:  # fullstack
            agents_pool = self._backend_agents + self._frontend_agents
        
        if not agents_pool:
            return []
        
        # Extrair keywords da tarefa
        task_text = f"{task_title} {task_description}".lower()
        task_keywords = set(re.findall(r'\b[a-zA-Z]{3,}\b', task_text))
        
        # Calcular relevância para cada agent
        scored_agents = []
        for agent in agents_pool:
            relevance = self._calculate_relevance(agent, task_keywords)
            if relevance > 0.1:  # Threshold mínimo
                scored_agents.append((agent, relevance))
        
        # Ordenar por relevância e retornar top N
        scored_agents.sort(key=lambda x: x[1], reverse=True)
        return scored_agents[:top_n]
    
    def _calculate_relevance(self, agent: Agent, task_keywords: set[str]) -> float:
        """
        Calcula score de relevância entre agent e tarefa.
        
        Args:
            agent: Agent a avaliar
            task_keywords: Keywords extraídas da tarefa
            
        Returns:
            Score de 0.0 a 1.0
        """
        agent_keywords = agent.extract_keywords()
        
        if not task_keywords or not agent_keywords:
            return 0.0
        
        # Interseção de keywords
        common_keywords = task_keywords & agent_keywords
        
        # Boost para matches em nome do agent
        name_lower = agent.name.lower().replace(".md", "").replace("-", " ")
        name_matches = sum(1 for kw in task_keywords if kw in name_lower)
        
        # Boost especial para keywords críticas
        critical_keywords = {
            "cqrs": ["command", "handler", "validator", "query"],
            "endpoints": ["endpoint", "controller", "api", "route"],
            "domain": ["entity", "valueobject", "domain"],
            "integrations": ["integration", "external", "token", "validation"],
            "persistence": ["database", "repository", "migration"],
            "testing": ["test", "mock", "unit"],
            "architecture": ["architecture", "structure", "module"],
            "components": ["component", "screen", "interface"],
            "patterns": ["hook", "pattern", "state"],
            "performance": ["performance", "optimization"]
        }
        
        # Detectar agent type do nome (dotnet-cqrs -> cqrs, rn-components -> components)
        agent_key = agent.name.lower().replace(".md", "").split("-")[-1]
        
        critical_boost = 0.0
        if agent_key in critical_keywords:
            critical_words_in_task = task_keywords & set(critical_keywords[agent_key])
            if critical_words_in_task:
                critical_boost = 0.4  # Grande boost se keywords críticas aparecem
        
        # Score base: proporção de overlap
        if len(common_keywords) == 0:
            base_score = 0.0
        else:
            # Usa tamanho menor como denominador para dar mais peso a matches
            base_score = len(common_keywords) / min(len(task_keywords), len(agent_keywords))
        
        # Boost de até 25% para matches no nome
        name_boost = min(0.25, name_matches * 0.15)
        
        # Score final (cap em 1.0)
        final_score = min(1.0, base_score + name_boost + critical_boost)
        
        return final_score
    
    def get_agent_content(self, agent_name: str) -> str | None:
        """
        Retorna o conteúdo de um agent específico.
        
        Args:
            agent_name: Nome do agent (com ou sem .md)
            
        Returns:
            Conteúdo do agent ou None se não encontrado
        """
        self.load_agents()
        
        # Normalizar nome
        if not agent_name.endswith(".md"):
            agent_name += ".md"
        
        # Buscar em todos os agents
        all_agents = self._backend_agents + self._frontend_agents
        for agent in all_agents:
            if agent.name == agent_name:
                return agent.content
        
        return None
    
    def list_all_agents(self, agent_type: str | None = None) -> List[Agent]:
        """
        Lista todos os agents disponíveis.
        
        Args:
            agent_type: "backend", "frontend" ou None (todos)
            
        Returns:
            Lista de agents
        """
        self.load_agents()
        
        if agent_type == "backend":
            return self._backend_agents.copy()
        elif agent_type == "frontend":
            return self._frontend_agents.copy()
        else:
            return self._backend_agents + self._frontend_agents
    
    def format_suggestions(
        self,
        suggestions: List[Tuple[Agent, float]],
        show_stars: bool = True
    ) -> str:
        """
        Formata sugestões para exibição no CLI.
        
        Args:
            suggestions: Lista de (Agent, score)
            show_stars: Se deve mostrar estrelas de relevância
            
        Returns:
            String formatada para CLI
        """
        if not suggestions:
            return "   Nenhum agent sugerido."
        
        lines = []
        for agent, score in suggestions:
            # Estrelas baseadas no score
            if show_stars:
                stars = self._score_to_stars(score)
                relevance_pct = int(score * 100)
                lines.append(
                    f"   {stars} {agent.display_name:<30} "
                    f"Relevância: {relevance_pct}%"
                )
            else:
                lines.append(f"   • {agent.display_name}")
        
        return "\n".join(lines)
    
    @staticmethod
    def _score_to_stars(score: float) -> str:
        """Converte score para representação visual de estrelas."""
        if score >= 0.8:
            return "⭐⭐⭐"
        elif score >= 0.6:
            return "⭐⭐ "
        elif score >= 0.4:
            return "⭐  "
        else:
            return "   "
