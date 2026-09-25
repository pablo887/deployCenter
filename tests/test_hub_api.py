"""La capa HTTP del hub: credenciales, códigos de respuesta y aislamiento."""

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from deploycenter.hub.api import crear_app  # noqa: E402

TOKEN_ADMIN = "admin-de-prueba"


@pytest.fixture
def cliente(hub):
    return TestClient(crear_app(hub, token_admin=TOKEN_ADMIN, intervalo_poll_s=0))


def admin():
    return {"Authorization": f"Bearer {TOKEN_ADMIN}"}


def como(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def agente(cliente):
    codigo = cliente.post("/api/v1/tenants/andino/codigos", json={}, headers=admin()).json()
    datos = cliente.post("/api/agente/v1/enrolar",
                         json={"codigo": codigo["codigo"], "host": "srv-dock-01"}).json()
    cliente.post("/api/agente/v1/latido", headers=como(datos["token"]), json={
        "version": "0.2.0",
        "instalaciones": [{"nombre": "mep", "producto": "mep", "version": "4.6.0"}]})
    return datos


class TestCredenciales:
    def test_salud_sin_credenciales(self, cliente):
        assert cliente.get("/api/salud").json() == {"ok": True}

    def test_el_canal_del_agente_pide_token(self, cliente):
        assert cliente.post("/api/agente/v1/latido", json={}).status_code == 401

    def test_token_de_agente_invalido(self, cliente):
        r = cliente.get("/api/agente/v1/ordenes/siguiente", headers=como("inventado"))
        assert r.status_code == 401
        assert "inválido" in r.json()["detalle"]

    def test_la_api_de_la_web_pide_token_de_administracion(self, cliente):
        assert cliente.get("/api/v1/parque").status_code == 401
        assert cliente.get("/api/v1/parque", headers=como("otro")).status_code == 401

    def test_un_agente_no_entra_a_la_api_de_la_web(self, cliente, agente):
        assert cliente.get("/api/v1/parque", headers=como(agente["token"])).status_code == 401

    def test_sin_token_configurado_la_web_queda_cerrada(self, hub):
        c = TestClient(crear_app(hub, token_admin=None))
        assert c.get("/api/v1/parque", headers=admin()).status_code == 403

    def test_codigo_invalido(self, cliente):
        r = cliente.post("/api/agente/v1/enrolar", json={"codigo": "DC-NOPE-NOPE", "host": "x"})
        assert r.status_code == 401


class TestCircuito:
    def test_sin_orden_el_long_poll_devuelve_204(self, cliente, agente):
        r = cliente.get("/api/agente/v1/ordenes/siguiente?espera=0",
                        headers=como(agente["token"]))
        assert r.status_code == 204

    def test_orden_de_punta_a_punta(self, cliente, agente):
        creada = cliente.post("/api/v1/ordenes", headers=admin(), json={
            "agente": agente["agente_id"], "instalacion": "mep", "tipo": "desplegar",
            "release": "4.7.0", "pedida_por": "Martín Ríos"})
        assert creada.status_code == 201, creada.text
        orden_id = creada.json()["id"]

        r = cliente.get("/api/agente/v1/ordenes/siguiente?espera=0",
                        headers=como(agente["token"]))
        assert r.status_code == 200
        assert r.json()["paquete"]["manifiesto"]["release"] == "4.7.0"

        r = cliente.post(f"/api/agente/v1/ordenes/{orden_id}/eventos",
                         headers=como(agente["token"]),
                         json={"eventos": [{"evento": "despliegue_iniciado", "datos": {}}]})
        assert r.json() == {"cancelar_rollback": False}

        r = cliente.post(f"/api/agente/v1/ordenes/{orden_id}/resultado",
                         headers=como(agente["token"]),
                         json={"resultado": "ok", "detalle": "verificada"})
        assert r.status_code == 200

        final = cliente.get(f"/api/v1/ordenes/{orden_id}", headers=admin()).json()
        assert final["resultado"] == "ok"
        assert final["eventos"][0]["evento"] == "despliegue_iniciado"

    def test_la_regla_rechazada_llega_con_su_motivo(self, cliente, agente, hub):
        hub.adquirir("andino", "mep", "2026-01-01")
        r = cliente.post("/api/v1/ordenes", headers=admin(), json={
            "agente": agente["agente_id"], "instalacion": "mep", "tipo": "desplegar",
            "release": "4.7.0", "pedida_por": "x"})
        assert r.status_code == 422
        assert "mantenimiento" in r.json()["detalle"]

    def test_la_segunda_orden_es_un_conflicto(self, cliente, agente):
        pedido = {"agente": agente["agente_id"], "instalacion": "mep", "tipo": "preflight",
                  "release": "4.7.0", "pedida_por": "x"}
        assert cliente.post("/api/v1/ordenes", headers=admin(), json=pedido).status_code == 201
        assert cliente.post("/api/v1/ordenes", headers=admin(), json=pedido).status_code == 409

    def test_revocado_deja_de_autenticar(self, cliente, agente):
        cliente.post(f"/api/v1/agentes/{agente['agente_id']}/revocar", headers=admin())
        r = cliente.get("/api/agente/v1/ordenes/siguiente", headers=como(agente["token"]))
        assert r.status_code == 401

    def test_resultado_desconocido(self, cliente, agente):
        orden = cliente.post("/api/v1/ordenes", headers=admin(), json={
            "agente": agente["agente_id"], "instalacion": "mep", "tipo": "preflight",
            "release": "4.7.0", "pedida_por": "x"}).json()
        cliente.get("/api/agente/v1/ordenes/siguiente", headers=como(agente["token"]))
        r = cliente.post(f"/api/agente/v1/ordenes/{orden['id']}/resultado",
                         headers=como(agente["token"]), json={"resultado": "inventado"})
        assert r.status_code == 422
