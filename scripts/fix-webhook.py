#!/usr/bin/env python3
"""Reaplica webhooks pelo Postgres dentro do backend e reinicia o serviço Swarm."""
import argparse
import subprocess
import sys

# Credenciais permanecem no container; nenhum token/URL com secret sai no stdout.
CONTAINER_SCRIPT = r"""
import os
import psycopg2
from services import instances_pg_service
ok, instances = instances_pg_service.load_all()
if not ok:
    raise SystemExit("Falha ao ler instâncias do Postgres; detalhes ocultos.")
base = os.environ.get("WEBHOOK_BASE_URL") or "http://klead_backend:8000"
conn = psycopg2.connect(
    host=os.environ["EVO_PG_HOST"],
    port=int(os.environ.get("EVO_PG_PORT", "5432")),
    user=os.environ.get("EVO_PG_USER", "postgres"),
    password=os.environ["EVO_PG_PASSWORD"],
    dbname=os.environ.get("EVO_PG_DB", "evolution_go_users"),
    connect_timeout=10,
)
updated = 0
with conn:
    with conn.cursor() as cursor:
        for iid, cfg in instances.items():
            if not cfg.get("api_url") or not cfg.get("api_key") or not cfg.get("webhook_secret"):
                continue
            name = cfg.get("evo_instance_name") or iid
            url = base.rstrip("/") + "/webhook/" + iid + "?token=" + cfg["webhook_secret"]
            cursor.execute("UPDATE instances SET webhook=%s WHERE name=%s", (url, name))
            updated += cursor.rowcount
conn.close()
print("Webhooks atualizados:", updated, "(tokens não exibidos)")
if not updated:
    raise SystemExit(2)
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--service", default="evolution_go_evolution_go", help="Nome do serviço Swarm Evolution GO")
    args = parser.parse_args()
    result = subprocess.run(["docker", "ps", "--filter", "name=klead_backend", "--format", "{{.ID}}"],
                            capture_output=True, text=True, check=True)
    containers = result.stdout.strip().splitlines()
    if len(containers) != 1:
        raise RuntimeError("Esperado um container de backend ativo neste manager.")
    # Confere o serviço antes de qualquer atualização no banco.
    subprocess.run(["docker", "service", "inspect", args.service],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    result = subprocess.run(["docker", "exec", "-i", containers[0], "python", "-"],
                            input=CONTAINER_SCRIPT, text=True, capture_output=True)
    if result.returncode:
        raise RuntimeError("Falha ao reaplicar webhooks. Confira conectividade e configurações no dashboard; detalhes com credenciais foram ocultados.")
    print(result.stdout.strip())
    subprocess.run(["docker", "service", "update", "--force", args.service], check=True)


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"Operação interrompida: {exc}", file=sys.stderr)
        raise SystemExit(1)
