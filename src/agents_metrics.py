#!/usr/bin/env python3
"""
Agents Metrics - Sistema de métricas e feedback para agents.

Coleta e analisa dados sobre qual guidance foi mais utilizada e efetiva.
"""

import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Dict, List


class AgentsMetrics:
    """Gerencia métricas de uso de agents."""
    
    def __init__(self, metrics_file: Path):
        """
        Inicializa o sistema de métricas.
        
        Args:
            metrics_file: Caminho para arquivo JSON de métricas
        """
        self.metrics_file = metrics_file
        self.data = self._load_metrics()
    
    def _load_metrics(self) -> dict:
        """Carrega métricas do arquivo."""
        if not self.metrics_file.exists():
            return {
                "_version": "1.0",
                "agents_stats": {},
                "tasks_with_agents": []
            }
        
        try:
            with open(self.metrics_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return {
                "_version": "1.0",
                "agents_stats": {},
                "tasks_with_agents": []
            }
    
    def _save_metrics(self):
        """Salva métricas no arquivo."""
        with open(self.metrics_file, 'w', encoding='utf-8') as f:
            json.dump(self.data, f, indent=2, ensure_ascii=False)
    
    def record_agent_consultation(
        self,
        agent_name: str,
        task_id: int,
        task_title: str
    ):
        """
        Registra consulta de um agent.
        
        Args:
            agent_name: Nome do agent consultado
            task_id: ID da tarefa
            task_title: Título da tarefa
        """
        # Inicializa stats do agent se não existir
        if agent_name not in self.data["agents_stats"]:
            self.data["agents_stats"][agent_name] = {
                "total_consultations": 0,
                "tasks": []
            }
        
        # Incrementa contador
        self.data["agents_stats"][agent_name]["total_consultations"] += 1
        
        # Adiciona task se não existir
        if task_id not in self.data["agents_stats"][agent_name]["tasks"]:
            self.data["agents_stats"][agent_name]["tasks"].append(task_id)
        
        # Registra task com agent
        task_record = {
            "task_id": task_id,
            "task_title": task_title,
            "agent": agent_name,
            "timestamp": datetime.now().isoformat()
        }
        
        # Evita duplicatas (mesma task + mesmo agent)
        existing = [
            t for t in self.data["tasks_with_agents"]
            if t["task_id"] == task_id and t["agent"] == agent_name
        ]
        
        if not existing:
            self.data["tasks_with_agents"].append(task_record)
        
        self._save_metrics()
    
    def get_top_agents(self, limit: int = 10) -> List[tuple]:
        """
        Retorna agents mais consultados.
        
        Args:
            limit: Número máximo de agents a retornar
            
        Returns:
            Lista de tuplas (agent_name, consultation_count)
        """
        stats = [
            (name, data["total_consultations"])
            for name, data in self.data["agents_stats"].items()
        ]
        stats.sort(key=lambda x: x[1], reverse=True)
        return stats[:limit]
    
    def get_agent_stats(self, agent_name: str) -> Dict:
        """
        Retorna estatísticas de um agent específico.
        
        Args:
            agent_name: Nome do agent
            
        Returns:
            Dicionário com estatísticas
        """
        if agent_name not in self.data["agents_stats"]:
            return {
                "total_consultations": 0,
                "unique_tasks": 0,
                "tasks": []
            }
        
        stats = self.data["agents_stats"][agent_name]
        return {
            "total_consultations": stats["total_consultations"],
            "unique_tasks": len(stats["tasks"]),
            "tasks": stats["tasks"]
        }
    
    def generate_dashboard(self) -> str:
        """
        Gera dashboard textual de métricas.
        
        Returns:
            String formatada com dashboard
        """
        top_agents = self.get_top_agents(10)
        
        dashboard = "📊 DASHBOARD DE AGENTS\n"
        dashboard += "="*80 + "\n\n"
        
        if not top_agents:
            dashboard += "Nenhum agent consultado ainda.\n"
            return dashboard
        
        dashboard += "🏆 TOP 10 AGENTS MAIS CONSULTADOS:\n\n"
        for i, (agent, count) in enumerate(top_agents, 1):
            stats = self.get_agent_stats(agent)
            bar = "█" * min(count, 50)
            dashboard += f"{i:2d}. {agent:<30} {bar} {count} consulta(s) em {stats['unique_tasks']} tarefa(s)\n"
        
        dashboard += "\n"
        dashboard += f"📈 TOTAIS:\n"
        dashboard += f"   • Total de consultas: {sum(c for _, c in top_agents)}\n"
        dashboard += f"   • Agents únicos: {len(self.data['agents_stats'])}\n"
        dashboard += f"   • Tarefas com guidance: {len(set(t['task_id'] for t in self.data['tasks_with_agents']))}\n"
        
        return dashboard
