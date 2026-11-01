#!/usr/bin/env python3
"""Lê/escreve configuração sem executar conteúdo do arquivo como shell."""
import os
import re
import shlex
import sys
import tempfile
from pathlib import Path


def read_env(path: Path) -> dict[str, str]:
    values = {}
    if not path.exists():
        return values
    for number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:]
        key, sep, raw = line.partition("=")
        key = key.strip()
        if not sep or not re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
            raise ValueError(f"Configuração inválida na linha {number} (conteúdo oculto).")
        # '#' dentro de uma senha não inicia comentário. Comentários inline
        # precisam estar fora de aspas e ser precedidos por whitespace.
        quote = None
        escaped = False
        end = len(raw)
        for position, character in enumerate(raw):
            if escaped:
                escaped = False
                continue
            if character == "\\" and quote != "'":
                escaped = True
                continue
            if character in {"'", '"'}:
                if quote is None:
                    quote = character
                elif quote == character:
                    quote = None
            elif character == "#" and quote is None and (position == 0 or raw[position - 1].isspace()):
                end = position
                break
        tokens = shlex.split(raw[:end], comments=False, posix=True)
        if len(tokens) > 1:
            raise ValueError(f"Valor com espaços sem aspas na linha {number} (conteúdo oculto).")
        values[key] = tokens[0] if tokens else ""
    return values


def main():
    operation, filename, *keys = sys.argv[1:]
    path = Path(filename)
    values = read_env(path)
    if operation == "export":
        for key, value in values.items():
            print(f"export {key}={shlex.quote(value)}")
    elif operation == "save":
        for key in keys:
            if not re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
                raise ValueError("Nome de variável inválido.")
            values[key] = os.environ.get(key, "")
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, temporary = tempfile.mkstemp(prefix=".klead-env-", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
                stream.write("# Configuração privada do KLEAD. Não versionar nem copiar para imagens.\n")
                for key, value in values.items():
                    # Compatível com Bash e com este parser; $/crases não executam.
                    stream.write(f"{key}={shlex.quote(value)}\n")
            os.chmod(temporary, 0o600)
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    else:
        raise ValueError("Operação inválida.")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError) as exc:
        print(f"Erro ao ler/gravar configuração: {exc}", file=sys.stderr)
        raise SystemExit(1)
