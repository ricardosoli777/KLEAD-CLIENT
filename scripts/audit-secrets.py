#!/usr/bin/env python3
"""Auditoria sem exibir valores; --history inspeciona os patches do Git."""
from __future__ import annotations
import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PATTERNS = {
    "chave privada": re.compile(r"-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----"),
    "token de provedor": re.compile(r"\b(?:sk-proj-|sk_live_|sk_test_|ghp_|github_pat_|glpat-)[A-Za-z0-9_-]{20,}"),
    "AWS access key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "JWT": re.compile(r"\beyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}"),
    "credencial em URL": re.compile(r"(?:postgres(?:ql)?|mysql|redis)://[^\s/:]+:[^\s@]{8,}@"),
}
SENSITIVE_FILE = re.compile(r"(?:^|/)(?:\.env(?:\..*)?|id_rsa.*|id_ed25519.*|.*\.(?:pem|key|p12|pfx))$")


def scan_text(name: str, content: str) -> int:
    count = 0
    for number, line in enumerate(content.splitlines(), 1):
        for label, pattern in PATTERNS.items():
            for match in pattern.finditer(line):
                # Templates usam ${VAR}/placeholders; não são credenciais reais.
                if "$" in match.group() or "${" in line[:match.end()]:
                    continue
                print(f"{name}:{number}: possível {label}; valor oculto")
                count += 1
    return count


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history", action="store_true")
    args = parser.parse_args()
    git = ["git", "-c", f"safe.directory={ROOT.as_posix()}", "-C", str(ROOT)]
    files = subprocess.check_output(git + ["ls-files", "-z"], text=True).split("\0")
    count = 0
    for name in filter(None, files):
        path = ROOT / name
        if SENSITIVE_FILE.search(name) and not name.endswith(".env.example"):
            print(f"{name}: arquivo sensível versionado; conteúdo oculto")
            count += 1
        if not path.is_file():
            continue
        try:
            content = path.read_text(encoding="utf-8-sig")
        except (UnicodeError, OSError):
            continue
        count += scan_text(name, content)
    if args.history:
        command = git + ["log", "--all", "--format=commit %h", "--no-ext-diff", "--no-renames", "-p"]
        process = subprocess.Popen(command, stdout=subprocess.PIPE, text=True, encoding="utf-8", errors="replace")
        commit, name, number = "", "", 0
        assert process.stdout is not None
        for line in process.stdout:
            if line.startswith("commit "):
                commit = line.strip().split()[-1]
            elif line.startswith("+++ b/"):
                name, number = line[6:].strip(), 0
                if SENSITIVE_FILE.search(name) and not name.endswith(".env.example"):
                    print(f"histórico {commit}:{name}: arquivo sensível; conteúdo oculto")
                    count += 1
            elif line.startswith("+") and not line.startswith("+++"):
                number += 1
                count += scan_text(f"histórico {commit}:{name}:adição {number}", line[1:])
        if process.wait() != 0:
            raise RuntimeError("Falha ao ler o histórico Git.")
    print(f"Auditoria concluída: {count} candidato(s). Nenhum valor de credencial foi exibido.")
    return 1 if count else 0


if __name__ == "__main__":
    raise SystemExit(main())
