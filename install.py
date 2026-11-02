#!/usr/bin/env python3
"""Instala o KLEAD na VPS via OpenSSH. Requer somente Python 3 e ssh/scp locais."""
from __future__ import annotations

import re
import io
import secrets
import os
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
EXCLUDED_DIRS = {".git", "node_modules", ".next", "__pycache__", ".pytest_cache",
                 "data", "logs", "progress", "backups", "knowledge", "tests",
                 ".venv", "venv", "secrets"}
SECRET_PATTERN = re.compile(
    r"-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----|"
    r"\b(?:sk-proj-|sk_live_|sk_test_|ghp_|github_pat_|npm_)[A-Za-z0-9_-]{20,}|"
    r"\bAKIA[0-9A-Z]{16}\b|"
    r"\beyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}"
)


def ask(message: str, default: str = "") -> str:
    while True:
        value = input(f"{message}" + (f" [{default}]" if default else "") + ": ").strip()
        if value or default:
            return value or default


def payload_files():
    for name in ("install.sh", "docker-stack.yml"):
        yield ROOT / name
    for name in ("build-evolution-go.sh", "install-env.py", "pg-backup.sh", "update.sh", "fix-webhook.py", "verificar-vps.sh"):
        yield ROOT / "scripts" / name
    for name in ("traefik.yml", "postgres.yml", "evolution-go.yml", "portainer.yml"):
        yield ROOT / "stacks" / name
    for tree in (ROOT / "dashboard" / "backend", ROOT / "dashboard" / "frontend"):
        for directory, children, filenames in os.walk(tree, followlinks=False):
            children[:] = [name for name in children if name not in EXCLUDED_DIRS
                           and not (Path(directory) / name).is_symlink()]
            for filename in filenames:
                path = Path(directory) / filename
                if not path.is_file() or path.is_symlink():
                    continue
                parts = path.relative_to(tree).parts
                if path.name.startswith((".env", "id_rsa", "id_ed25519", "ssh-key")):
                    continue
                if path.suffix.lower() in {".pem", ".key", ".ppk", ".p12", ".pfx", ".keystore", ".db", ".log", ".bak", ".secret", ".csv", ".xlsx", ".dump"}:
                    continue
                if path.name in {"AGENTS.md", "CLAUDE.md", ".npmrc", ".pypirc", "credentials", "instances_config.json", "conversations.json", "campaign_history.json",
                                 "ai_config.json", "users.json", "revoked_tokens.json", "assignments.json", "patterns.json", "crm.json", "alerts.json"}:
                    continue
                if parts[:2] == ("media", "temp"):
                    continue
                yield path


def make_payload(destination: Path, ssh_key: Path) -> None:
    with tarfile.open(destination, "w:gz") as archive:
        for path in payload_files():
            if path.resolve() == ssh_key:
                continue
            if not path.is_file() or path.is_symlink():
                raise RuntimeError(f"Arquivo necessário ausente ou inválido: {path.relative_to(ROOT)}")
            try:
                content = path.read_text(encoding="utf-8")
            except UnicodeError:
                content = ""
            if SECRET_PATTERN.search(content):
                raise RuntimeError(f"Possível credencial em {path.relative_to(ROOT)}. Envio interrompido; valor oculto.")
            if path.suffix in {".sh", ".py", ".yml"}:
                # LF mesmo quando o instalador é executado num checkout Windows.
                data = content.replace("\r\n", "\n").encode("utf-8")
                info = archive.gettarinfo(str(path), arcname=path.relative_to(ROOT).as_posix())
                info.size = len(data)
                info.uid = info.gid = 0
                info.uname = info.gname = "root"
                archive.addfile(info, io.BytesIO(data))
            else:
                archive.add(path, arcname=path.relative_to(ROOT).as_posix(), recursive=False)


def main() -> int:
    print("KLEAD-CLIENT — instalação guiada na VPS\nTodos os serviços serão instalados na VPS.")
    for executable in ("ssh", "scp"):
        if not shutil.which(executable):
            raise RuntimeError(f"Instale o cliente OpenSSH no computador: comando {executable} não encontrado.")
    host = ask("IP ou hostname da VPS")
    user = ask("Usuário SSH", "root")
    port = ask("Porta SSH", "22")
    key = Path(ask("Caminho da chave privada SSH (a chave permanece neste computador)").strip('"')).expanduser().resolve()
    remote_dir = ask("Diretório de instalação na VPS", "/opt/klead")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.:-]*", host):
        raise RuntimeError("IP/hostname inválido.")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]*", user):
        raise RuntimeError("Usuário SSH inválido.")
    if not port.isdigit() or not 1 <= int(port) <= 65535:
        raise RuntimeError("Porta SSH inválida.")
    if not key.is_file():
        raise RuntimeError("Chave SSH não encontrada.")
    if not re.fullmatch(r"/(?:[A-Za-z0-9_-]+/)*[A-Za-z0-9_-]+", remote_dir) or remote_dir in {"/", "/root", "/opt", "/etc", "/usr", "/var", "/home"}:
        raise RuntimeError("Escolha um diretório dedicado, como /opt/klead.")
    target = f"{user}@{host}"
    # OpenSSH faz a confirmação da identidade do host e solicita a passphrase.
    # Nunca aceitar silenciosamente uma chave de host desconhecida ou alterada.
    ssh = ["ssh", "-i", str(key), "-p", port, "-o", "IdentitiesOnly=yes", "-o", "StrictHostKeyChecking=ask", "-tt", target]
    with tempfile.TemporaryDirectory(prefix="klead-install-") as temp_dir:
        payload = Path(temp_dir) / "klead-source.tar.gz"
        make_payload(payload, key)
        staging = "/tmp/klead-install." + secrets.token_hex(16)
        subprocess.run(ssh + ["umask 077; mkdir -- " + shlex.quote(staging)], check=True)
        try:
            subprocess.run(["scp", "-i", str(key), "-P", port, "-o", "IdentitiesOnly=yes",
                            "-o", "StrictHostKeyChecking=ask", str(payload), f"{target}:{staging}/source.tar.gz"], check=True)
            # Em instalação existente, reaproveita o código sem sobrescrevê-lo.
            # Atualizações têm procedimento próprio, documentado em INSTALL.md.
            script = f"""set -e
if [ -e {shlex.quote(remote_dir)} ]; then
  [ -f {shlex.quote(remote_dir + '/install.sh')} ] || {{ echo 'Diretório existente não é uma instalação KLEAD.'; exit 1; }}
else
  mkdir -p {shlex.quote(remote_dir)}
  tar -xzf {shlex.quote(staging + '/source.tar.gz')} -C {shlex.quote(remote_dir)}
fi
cd {shlex.quote(remote_dir)}
bash install.sh
"""
            command = "bash -c " + shlex.quote(script)
            if user != "root":
                command = "sudo " + command
            return subprocess.run(ssh + [command]).returncode
        finally:
            # Somente o diretório aleatório previamente validado é removido.
            subprocess.run(ssh + ["rm -rf -- " + shlex.quote(staging)], check=False)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, OSError, subprocess.CalledProcessError) as exc:
        print(f"Instalação interrompida: {exc}", file=sys.stderr)
        raise SystemExit(1)
    except KeyboardInterrupt:
        print("\nInstalação interrompida pelo usuário.", file=sys.stderr)
        raise SystemExit(130)
