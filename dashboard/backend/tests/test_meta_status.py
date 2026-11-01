import os
import sys
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("DASHBOARD_SECRET", "a" * 64)

sys.path.insert(0, str(Path(__file__).parent.parent))


def test_whatsapp_status_uses_evolution_for_meta_provider():
    from fastapi.testclient import TestClient
    import main as m

    client = TestClient(m.app)

    with patch.dict(
        "main.instances",
        {
            "meta-1": {
                "provider": "meta",
                "api_url": "http://evolution:4000",
                "global_api_key": "GLOBAL",
                "evo_instance_name": "meta-on-evolution",
            }
        },
        clear=True,
    ), patch(
        "routers.instances.get_instance_info",
        return_value={"name": "meta-on-evolution", "connected": True},
    ) as get_instance_info, \
       patch("services.auth_service.verify_token", return_value={
           "funcao": "superadmin", "email": "test-superadmin@test.com", "allowed_instances": [],
       }):
        response = client.get(
            "/instance/meta-1/whatsapp-status",
            headers={"X-API-Key": os.environ["DASHBOARD_SECRET"], "X-Auth-Token": "fake-superadmin-token"},
        )

    assert response.status_code == 200
    data = response.json()
    assert data == {
        "instance_id": "meta-1",
        "state": "open",
        "connected": True,
        "detail": "",
    }
    get_instance_info.assert_called_once_with(
        "http://evolution:4000", "GLOBAL", "meta-on-evolution"
    )


def test_wa_states_uses_evolution_for_meta_provider():
    from fastapi.testclient import TestClient
    import main as m

    client = TestClient(m.app)

    with patch.dict(
        "main.instances",
        {
            "meta-1": {
                "provider": "meta",
                "api_url": "http://evolution:4000",
                "global_api_key": "GLOBAL",
                "evo_instance_name": "meta-on-evolution",
            }
        },
        clear=True,
    ), patch(
        "routers.instances.get_all_instances",
        return_value=[{"name": "meta-on-evolution", "connected": True}],
    ) as get_all_instances, patch(
        "services.auth_service.verify_token",
        return_value={
            "funcao": "superadmin",
            "email": "test-superadmin@test.com",
            "allowed_instances": ["meta-1"],
        },
    ):
        response = client.get(
            "/instances/wa-states",
            headers={
                "X-API-Key": os.environ["DASHBOARD_SECRET"],
                "X-Auth-Token": "fake-superadmin-token",
            },
        )

    assert response.status_code == 200
    assert response.json() == {"states": {"meta-1": "open"}}
    get_all_instances.assert_called_once_with("http://evolution:4000", "GLOBAL")
