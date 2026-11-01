"""Testes do Disparador — limpeza de contatos e persistência de fila."""
from utils.cleaner import clean_contacts
import services.campaign_service as camp


def test_clean_contacts_dedupes_validates_and_normalizes_name():
    raw = [
        {"name": "joao silva", "number": "11987654321"},
        {"name": "JOAO SILVA", "number": "11987654321"},  # duplicado
        {"name": "maria", "number": "123"},  # inválido
        {"name": "pedro", "number": "5511912345678"},  # já com DDI
    ]
    cleaned, rejected = clean_contacts(raw)
    assert len(cleaned) == 2
    assert len(rejected) == 2
    assert cleaned[0]["name"] == "Joao Silva"


def test_queue_persists_and_reloads_across_restart():
    test_iid = "test-disparador-seguro"
    camp.clear_queue(test_iid)
    fake_queue = [{"number": "5511987654321", "name": "Teste", "message": "Ola"}]
    camp.save_queue(test_iid, fake_queue)
    try:
        assert camp.load_queue(test_iid) == fake_queue
    finally:
        camp.clear_queue(test_iid)


def test_clear_queue_removes_persisted_file():
    test_iid = "test-disparador-seguro-2"
    camp.save_queue(test_iid, [{"number": "551900000000"}])
    camp.clear_queue(test_iid)
    assert camp.load_queue(test_iid) == []
