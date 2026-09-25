# ruff: noqa: F811  (los fixtures de test_hub_api se importan y se usan como parámetros)
"""Lo que la web necesita del hub: catálogo, clientes, su configuración pública y
los archivos estáticos, servidos desde el mismo origen que la API."""

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("jwt")

from fastapi.testclient import TestClient  # noqa: E402
from test_hub_api import EMISOR, SECRETO, agente, cliente, como, personas  # noqa: E402,F401

from deploycenter.hub.api import crear_app  # noqa: E402
from deploycenter.hub.cli import config_web  # noqa: E402
from deploycenter.hub.identidad import ValidadorJWT  # noqa: E402


class TestCatalogo:
    def test_el_cliente_ve_lo_que_adquirio_con_su_changelog(self, cliente, catalogo):
        import json

        rel = catalogo / "productos" / "mep" / "releases" / "4.7.0"
        ruta = catalogo / json.loads((rel / "manifiesto.json").read_text())["changelog"]
        ruta.parent.mkdir(parents=True, exist_ok=True)
        ruta.write_text("# MEP 4.7.0\n\n## Qué cambia\n\n- Timeout de firma\n"
                        "  configurable.\n- Otra cosa.\n\nTexto suelto.\n")
        productos = cliente.get("/api/v1/catalogo", headers=como("operador")).json()
        assert [p["id"] for p in productos] == ["mep"]
        release = productos[0]["releases"][-1]
        assert release["manifiesto"]["release"] == "4.7.0"
        assert release["changelog"] == ["Timeout de firma configurable.", "Otra cosa."]

    def test_un_cliente_sin_productos_no_ve_ninguno(self, cliente):
        assert cliente.get("/api/v1/catalogo", headers=como("operador_otro")).json() == []

    def test_accusys_ve_todo(self, cliente):
        productos = cliente.get("/api/v1/catalogo", headers=como("soporte")).json()
        assert [p["id"] for p in productos] == ["mep"]

    def test_sin_segundo_factor_no(self, cliente):
        assert cliente.get("/api/v1/catalogo", headers=como("operador", aal="aal1")) \
            .status_code == 403


class TestClientes:
    def test_un_cliente_se_ve_solo_a_si_mismo(self, cliente):
        tenants = cliente.get("/api/v1/tenants", headers=como("lector")).json()
        assert [t["id"] for t in tenants] == ["andino"]
        assert tenants[0]["productos"][0]["producto"] == "mep"

    def test_accusys_ve_todos(self, cliente):
        tenants = cliente.get("/api/v1/tenants", headers=como("comercial")).json()
        assert {t["id"] for t in tenants} == {"andino", "otro"}

    def test_sin_token(self, cliente):
        assert cliente.get("/api/v1/tenants").status_code == 401


@pytest.fixture
def web(tmp_path):
    (tmp_path / "index.html").write_text("<!doctype html><title>deployCenter</title>")
    (tmp_path / "app.js").write_text("console.log(1)")
    return tmp_path


@pytest.fixture
def con_web(hub, personas, web):  # noqa: F811
    validador = ValidadorJWT(secreto=SECRETO, emisor=EMISOR)
    config = {"identidad": "supabase", "supabase_url": "https://proyecto.supabase.co",
              "publishable_key": "sb_publishable_x"}
    return TestClient(crear_app(hub, validador=validador, web=web, config_web=config))


class TestWeb:
    def test_sirve_la_web_con_csp(self, con_web):
        r = con_web.get("/")
        assert r.status_code == 200 and "deployCenter" in r.text
        csp = r.headers["content-security-policy"]
        assert "script-src 'self'" in csp
        assert "connect-src 'self' https://proyecto.supabase.co" in csp
        assert r.headers["x-frame-options"] == "DENY"

    def test_config_publica(self, con_web):
        assert con_web.get("/config.json").json()["publishable_key"] == "sb_publishable_x"

    def test_la_api_sigue_igual(self, con_web):
        assert con_web.get("/api/salud").json() == {"ok": True}
        assert con_web.get("/api/v1/yo", headers=como("operador")).json()["rol"] == "operador"
        assert "content-security-policy" not in con_web.get("/api/salud").headers

    def test_sin_web_no_hay_config(self, cliente):
        assert cliente.get("/config.json").status_code == 404


class TestConfigWeb:
    def test_supabase(self):
        v = ValidadorJWT(jwks_url="https://p.supabase.co/auth/v1/.well-known/jwks.json")
        c = config_web(v, {"SUPABASE_URL": "https://p.supabase.co/",
                           "SUPABASE_PUBLISHABLE_KEY": "sb_publishable_x",
                           "SUPABASE_SECRET_KEY": "sb_secret_no"})
        assert c == {"identidad": "supabase", "supabase_url": "https://p.supabase.co",
                     "publishable_key": "sb_publishable_x"}

    def test_falta_la_publishable_key(self):
        v = ValidadorJWT(jwks_url="https://p.supabase.co/auth/v1/.well-known/jwks.json")
        c = config_web(v, {"SUPABASE_URL": "https://p.supabase.co"})
        assert c["identidad"] is None and "SUPABASE_PUBLISHABLE_KEY" in c["motivo"]

    def test_desarrollo_con_secreto(self):
        assert config_web(ValidadorJWT(secreto=SECRETO), {}) == {"identidad": "token"}

    def test_sin_identidad(self):
        assert config_web(None, {}) == {"identidad": None}
