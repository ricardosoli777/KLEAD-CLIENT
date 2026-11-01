#!/usr/bin/env python3
"""
Deploy KLEAD para o VPS via SSH/SFTP.

Uso:
    python scripts/deploy.py              # backend + frontend
    python scripts/deploy.py --backend    # só backend
    python scripts/deploy.py --frontend   # só frontend

Fluxo:
  1. Lê a tag atual dos serviços em produção
  2. Gera nova tag com timestamp (YYYYMMDD_HHMMSS)
  3. Sobe os arquivos via SFTP (tar.gz, excluindo lixo)
  4. docker build --no-cache -t <servico>:<nova_tag>
  5. docker service update --image <servico>:<nova_tag>
  6. Verifica convergência

Configuração via variáveis de ambiente (nenhuma credencial fica no código):
  KLEAD_VPS_HOST      IP/host do VPS (obrigatório)
  KLEAD_VPS_USER      usuário SSH (obrigatório)
  KLEAD_VPS_KEY_PATH  caminho da chave privada SSH (obrigatório)
  KLEAD_VPS_REMOTE_DIR diretório do projeto no VPS (default: /root/klead)
"""

import argparse
import io
import os
import sys
import tarfile
from datetime import datetime

import paramiko

# ── Configuração ──────────────────────────────────────────────────────────────
# Sem defaults de host/usuário/credencial: força passar tudo via env var, pra
# nunca mais um servidor ou segredo antigo ficar hardcoded e esquecido no git.

VPS_HOST     = os.getenv("KLEAD_VPS_HOST", "")
VPS_USER     = os.getenv("KLEAD_VPS_USER", "")
VPS_KEY_PATH = os.getenv("KLEAD_VPS_KEY_PATH", "")
REMOTE_DIR   = os.getenv("KLEAD_VPS_REMOTE_DIR", "/root/klead")

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LOCAL_DASHBOARD = os.path.join(_SCRIPT_DIR, "..", "dashboard")

# Pastas/arquivos a ignorar ao empacotar (nunca entram no tar)
BACKEND_EXCLUDES  = {"__pycache__", ".pytest_cache", "data", "progress", "knowledge", ".env"}
FRONTEND_EXCLUDES = {"node_modules", ".next", ".git", "dist", ".env.local", ".env"}
COMMON_FILE_EXCLUDES = {".pyc", ".pyo", ".DS_Store", ".log"}


# ── SSH helpers ───────────────────────────────────────────────────────────────

def connect() -> paramiko.SSHClient:
    missing = [name for name, val in (
        ("KLEAD_VPS_HOST", VPS_HOST), ("KLEAD_VPS_USER", VPS_USER), ("KLEAD_VPS_KEY_PATH", VPS_KEY_PATH)
    ) if not val]
    if missing:
        print(f"Faltam variáveis de ambiente: {', '.join(missing)}")
        sys.exit(1)
    if not os.path.isfile(VPS_KEY_PATH):
        print(f"Chave SSH não encontrada: {VPS_KEY_PATH}")
        sys.exit(1)
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(VPS_HOST, username=VPS_USER, key_filename=VPS_KEY_PATH, timeout=15)
    return ssh


def run(ssh: paramiko.SSHClient, cmd: str, timeout: int = 600) -> str:
    label = cmd.split("|")[0].strip()[:90]
    print(f"\n$ {label}")
    _, stdout, stderr = ssh.exec_command(cmd, get_pty=False, timeout=timeout)
    out  = stdout.read().decode("utf-8", errors="replace")
    err  = stderr.read().decode("utf-8", errors="replace")
    code = stdout.channel.recv_exit_status()
    lines = (out + err).splitlines()
    for line in lines[-20:]:
        print("  " + line)
    if code != 0:
        print(f"  [ERRO exit={code}]")
        sys.exit(1)
    print("  [ok]")
    return out.strip()


def get_service_tag(ssh: paramiko.SSHClient, service: str) -> str:
    """Retorna a tag atual da imagem do serviço (ex: '20260524_014956')."""
    _, stdout, _ = ssh.exec_command(
        f"docker service inspect {service} --format {{{{.Spec.TaskTemplate.ContainerSpec.Image}}}}"
    )
    image = stdout.read().decode("utf-8", errors="replace").strip()
    return image.split(":")[-1] if ":" in image else "latest"


# ── Empacotamento e upload ────────────────────────────────────────────────────

def _should_exclude(name: str, is_dir: bool, dir_excludes: set) -> bool:
    if is_dir and name in dir_excludes:
        return True
    if not is_dir:
        ext = os.path.splitext(name)[1]
        if ext in COMMON_FILE_EXCLUDES:
            return True
    return False


def make_tar(local_path: str, dir_excludes: set) -> io.BytesIO:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for root, dirs, files in os.walk(local_path):
            dirs[:] = [d for d in dirs if not _should_exclude(d, True, dir_excludes)]
            for fname in files:
                if _should_exclude(fname, False, dir_excludes):
                    continue
                fpath   = os.path.join(root, fname)
                arcname = os.path.relpath(fpath, local_path)
                tar.add(fpath, arcname=arcname)
    buf.seek(0)
    return buf


def upload_and_extract(ssh: paramiko.SSHClient, local_path: str, remote_path: str,
                       dir_excludes: set, label: str) -> None:
    print(f"\nEmpacotando {label}...")
    buf      = make_tar(local_path, dir_excludes)
    size_kb  = len(buf.getvalue()) // 1024
    print(f"  {size_kb} KB comprimidos")

    remote_tar = f"/tmp/zapman_{label}.tar.gz"
    sftp = ssh.open_sftp()
    sftp.putfo(buf, remote_tar)
    sftp.close()
    print(f"  Enviado -> {remote_tar}")

    run(ssh, f"cd {remote_path} && tar xzf {remote_tar} --overwrite && rm -f {remote_tar}")
    print(f"  Extraído em {remote_path}")


# ── Deploy ────────────────────────────────────────────────────────────────────

def deploy_backend(ssh: paramiko.SSHClient, new_tag: str) -> None:
    print("\n" + "=" * 60)
    print("BACKEND")
    print("=" * 60)

    local  = os.path.join(LOCAL_DASHBOARD, "backend")
    remote = f"{REMOTE_DIR}/backend"
    upload_and_extract(ssh, local, remote, BACKEND_EXCLUDES, "backend")

    run(ssh,
        f"cd {REMOTE_DIR} && docker build --no-cache -t klead_backend:{new_tag} ./backend 2>&1 | tail -8",
        timeout=300)
    run(ssh,
        f"docker service update --force --image klead_backend:{new_tag} klead_backend 2>&1 | tail -6",
        timeout=120)


def deploy_frontend(ssh: paramiko.SSHClient, new_tag: str) -> None:
    print("\n" + "=" * 60)
    print("FRONTEND")
    print("=" * 60)

    local  = os.path.join(LOCAL_DASHBOARD, "frontend")
    remote = f"{REMOTE_DIR}/frontend"
    upload_and_extract(ssh, local, remote, FRONTEND_EXCLUDES, "frontend")

    run(ssh,
        f"cd {REMOTE_DIR} && docker build --no-cache -t klead_frontend:{new_tag} ./frontend 2>&1 | tail -8",
        timeout=600)
    run(ssh,
        f"docker service update --force --image klead_frontend:{new_tag} klead_frontend 2>&1 | tail -6",
        timeout=120)


def status(ssh: paramiko.SSHClient) -> None:
    print("\n" + "=" * 60)
    print("STATUS")
    print("=" * 60)
    run(ssh, "docker service ls | grep zapman")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Deploy KLEAD para o VPS")
    parser.add_argument("--backend",  action="store_true", help="Deploy apenas o backend")
    parser.add_argument("--frontend", action="store_true", help="Deploy apenas o frontend")
    args = parser.parse_args()

    do_backend  = args.backend  or not args.frontend
    do_frontend = args.frontend or not args.backend

    new_tag = datetime.now().strftime("%Y%m%d_%H%M%S")
    print(f"Nova tag: {new_tag}")

    print("Conectando ao VPS...")
    ssh = connect()
    print(f"  Conectado em {VPS_HOST}")

    # Mostra tags atuais antes do deploy
    if do_backend:
        old = get_service_tag(ssh, "klead_backend")
        print(f"  Backend atual : klead_backend:{old}")
    if do_frontend:
        old = get_service_tag(ssh, "klead_frontend")
        print(f"  Frontend atual: klead_frontend:{old}")

    if do_backend:
        deploy_backend(ssh, new_tag)
    if do_frontend:
        deploy_frontend(ssh, new_tag)

    status(ssh)
    ssh.close()

    print(f"\nDeploy concluido! Tag: {new_tag}")


if __name__ == "__main__":
    main()
