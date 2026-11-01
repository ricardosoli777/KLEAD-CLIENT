import os
import sys
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("DASHBOARD_SECRET", "a" * 64)

sys.path.insert(0, str(Path(__file__).parent.parent))


def test_upsert_lead_accepts_and_persists_source_meta():
    from services import crm_service

    crm_service._crm = {
        "columns": [{"id": "novo", "name": "Novo", "order": 0, "color": "#000"}],
        "leads": [],
    }

    with patch("services.crm_service._save"):
        lead = crm_service.upsert_lead(
            telefone="5511999990001",
            nome="Lead Meta",
            origem="meta-leads",
            instancia_id="meta-1",
            campanha="Campanha",
            source_meta={
                "leadgen_id": "LG123",
                "form_id": "FORM123",
                "page_id": "PAGE123",
            },
        )

    assert lead["leadgen_id"] == "LG123"
    assert lead["form_id"] == "FORM123"
    assert lead["page_id"] == "PAGE123"
    assert lead["source_meta"]["leadgen_id"] == "LG123"
