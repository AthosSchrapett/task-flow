#!/usr/bin/env python3
"""
Vault Query - Ferramenta CLI para consultar notas do Obsidian vault.

Busca em todos os vaults configurados (docs/ e arenar-vault/).

Uso:
    python vault_query.py search "termo"
    python vault_query.py get "01-projeto/visao-geral"
    python vault_query.py list "03-decisoes"
    python vault_query.py create "02-features/nova-feature.md" "conteudo"
    python vault_query.py append "01-projeto/visao-geral.md" "conteudo extra"
"""

import argparse
import io
import json
import os
import sys
from pathlib import Path
from typing import Optional

# Garantir output UTF-8 no Windows
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# Diretorio raiz do projeto
SRC_DIR = Path(__file__).parent.resolve()
ROOT = SRC_DIR.parent  # task-flow/
PROJECT_ROOT = ROOT.parent  # Arenar/

# Carregar configuracao
CONFIG_PATH = ROOT / "config.json"

def load_config() -> dict:
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"vault_paths": ["./arenar-vault"]}


def get_vault_paths() -> list[Path]:
    config = load_config()
    paths = config.get("vault_paths", [config.get("vault_path", "./arenar-vault")])
    if isinstance(paths, str):
        paths = [paths]
    return [PROJECT_ROOT / p for p in paths]


def search_notes(query: str) -> list[dict]:
    """Busca por texto em todos os arquivos .md dos vaults."""
    results = []
    query_lower = query.lower()
    for vault in get_vault_paths():
        if not vault.exists():
            continue
        for md_file in vault.rglob("*.md"):
            # Ignorar pastas .obsidian e _templates
            rel = md_file.relative_to(vault)
            parts = rel.parts
            if any(p.startswith(".") for p in parts):
                continue
            try:
                content = md_file.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            if query_lower in content.lower():
                # Encontrar linhas com match
                matches = []
                for i, line in enumerate(content.splitlines(), 1):
                    if query_lower in line.lower():
                        matches.append({"line": i, "text": line.strip()})
                results.append({
                    "vault": vault.name,
                    "path": str(rel),
                    "matches": matches[:5],
                })
    return results


def get_note(path: str) -> Optional[str]:
    """Retorna o conteudo de uma nota pelo caminho relativo."""
    if not path.endswith(".md"):
        path += ".md"
    for vault in get_vault_paths():
        full = vault / path
        if full.exists():
            return full.read_text(encoding="utf-8")
    return None


def list_notes(folder: str = "") -> list[dict]:
    """Lista notas de uma pasta especifica ou de todo o vault."""
    notes = []
    for vault in get_vault_paths():
        target = vault / folder if folder else vault
        if not target.exists():
            continue
        for md_file in sorted(target.rglob("*.md")):
            rel = md_file.relative_to(vault)
            parts = rel.parts
            if any(p.startswith(".") for p in parts):
                continue
            stat = md_file.stat()
            notes.append({
                "vault": vault.name,
                "path": str(rel),
                "size": stat.st_size,
            })
    return notes


def create_note(path: str, content: str) -> str:
    """Cria uma nova nota no primeiro vault configurado."""
    if not path.endswith(".md"):
        path += ".md"
    vault = get_vault_paths()[0]
    full = vault / path
    if full.exists():
        return f"Erro: nota ja existe em {full}"
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(content, encoding="utf-8")
    return f"Nota criada: {full.relative_to(ROOT)}"


def append_to_note(path: str, content: str) -> str:
    """Adiciona conteudo ao final de uma nota existente."""
    if not path.endswith(".md"):
        path += ".md"
    for vault in get_vault_paths():
        full = vault / path
        if full.exists():
            with open(full, "a", encoding="utf-8") as f:
                f.write("\n" + content)
            return f"Conteudo adicionado a: {full.relative_to(ROOT)}"
    return f"Erro: nota nao encontrada: {path}"


def main():
    parser = argparse.ArgumentParser(
        description="Consulta notas do Obsidian vault do projeto Arenar"
    )
    sub = parser.add_subparsers(dest="command", help="Comando")

    # search
    p_search = sub.add_parser("search", help="Busca texto em todas as notas")
    p_search.add_argument("query", help="Termo de busca")

    # get
    p_get = sub.add_parser("get", help="Exibe conteudo de uma nota")
    p_get.add_argument("path", help="Caminho relativo da nota (ex: 01-projeto/visao-geral)")

    # list
    p_list = sub.add_parser("list", help="Lista notas de uma pasta")
    p_list.add_argument("folder", nargs="?", default="", help="Pasta (ex: 03-decisoes)")

    # create
    p_create = sub.add_parser("create", help="Cria nova nota")
    p_create.add_argument("path", help="Caminho da nota")
    p_create.add_argument("content", help="Conteudo da nota")

    # append
    p_append = sub.add_parser("append", help="Adiciona conteudo a uma nota")
    p_append.add_argument("path", help="Caminho da nota")
    p_append.add_argument("content", help="Conteudo a adicionar")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    if args.command == "search":
        results = search_notes(args.query)
        if not results:
            print(f"Nenhum resultado para: '{args.query}'")
            sys.exit(0)
        for r in results:
            print(f"\n[{r['vault']}] {r['path']}")
            for m in r["matches"]:
                print(f"  L{m['line']}: {m['text']}")

    elif args.command == "get":
        content = get_note(args.path)
        if content is None:
            print(f"Nota nao encontrada: {args.path}")
            sys.exit(1)
        print(content)

    elif args.command == "list":
        notes = list_notes(args.folder)
        if not notes:
            print(f"Nenhuma nota encontrada em: {args.folder or 'todo o vault'}")
            sys.exit(0)
        for n in notes:
            print(f"[{n['vault']}] {n['path']}  ({n['size']} bytes)")

    elif args.command == "create":
        print(create_note(args.path, args.content))

    elif args.command == "append":
        print(append_to_note(args.path, args.content))


if __name__ == "__main__":
    main()
