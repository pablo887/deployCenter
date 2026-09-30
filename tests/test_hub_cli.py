"""La consola del hub para el enrolamiento por llave: lo mismo que hace Soporte
en la web, para operar sin ella."""

import json

import pytest

pytest.importorskip("sqlalchemy")

from deploycenter.hub import cli  # noqa: E402
from deploycenter.hub.servicio import Hub  # noqa: E402


@pytest.fixture
def base(tmp_path, catalogo, monkeypatch):
    for k in ("SUPABASE_URL", "SUPABASE_SECRET_KEY"):
        monkeypatch.delenv(k, raising=False)
    url = f"sqlite:///{tmp_path / 'hub.db'}"
    return ["--sin-color", "--db", url, "--catalogo", str(catalogo)], url, catalogo


def salida_json(capsys):
    return json.loads(capsys.readouterr().out)


def test_llave_pedido_y_aceptacion(base, capsys):
    opciones, url, catalogo = base
    assert cli.main([*opciones, "tenant", "andino", "Banco Andino"]) == 0
    capsys.readouterr()

    assert cli.main([*opciones, "llave", "andino", "--nombre", "producción"]) == 0
    captura = capsys.readouterr()
    llave = json.loads(captura.out)["llave"]
    assert "DC_LLAVE=" + llave in captura.err

    from deploycenter.hub.catalogo import Catalogo
    pedido = Hub.desde_url(url, Catalogo(catalogo)).solicitar_conexion(
        "andino", llave, "srv-01", "produccion")

    assert cli.main([*opciones, "solicitudes", "--estado", "pendiente"]) == 0
    (sol,) = salida_json(capsys)
    assert sol["id"] == pedido["solicitud"] and sol["host"] == "srv-01"

    assert cli.main([*opciones, "aceptar", sol["id"]]) == 0
    assert salida_json(capsys)["estado"] == "aceptada"
    assert cli.main([*opciones, "rechazar", sol["id"]]) != 0
