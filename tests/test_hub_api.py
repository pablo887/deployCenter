"""La capa HTTP del hub: identidad de las personas, permisos por rol y el canal
de los agentes."""

import uuid

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("jwt")

from fastapi.testclient import TestClient  # noqa: E402

from deploycenter.hub.api import crear_app  # noqa: E402
from deploycenter.hub.identidad import ValidadorJWT, token_de_desarrollo  # noqa: E402

SECRETO = "secreto-de-prueba-de-al-menos-32-bytes!!"
EMISOR = "https://proyecto.supabase.co/auth/v1"

U = {n: str(uuid.uuid4()) for n in (
    "operador", "aprobador", "lector", "operador_otro", "soporte", "comercial", "sin_alta")}


def token(quien, aal="aal2", **kw):
    return token_de_desarrollo(SECRETO, U[quien], aal=aal, emisor=EMISOR, **kw)


def como(quien, **kw):
    return {"Authorization": f"Bearer {token(quien, **kw)}"}


def bearer(t):
    return {"Authorization": f"Bearer {t}"}


@pytest.fixture
def personas(hub):
    hub.crear_tenant("otro", "Otro Banco")
    hub.asignar_usuario(U["operador"], "operador", "andino", nombre="Martín Ríos")
    hub.asignar_usuario(U["aprobador"], "aprobador", "andino", nombre="Carla Benítez")
    hub.asignar_usuario(U["lector"], "lector", "andino")
    hub.asignar_usuario(U["operador_otro"], "operador", "otro")
    hub.asignar_usuario(U["soporte"], "soporte", nombre="Sofía Herrera")
    hub.asignar_usuario(U["comercial"], "comercial")
    return U


@pytest.fixture
def cliente(hub, personas):
    validador = ValidadorJWT(secreto=SECRETO, emisor=EMISOR)
    return TestClient(crear_app(hub, validador=validador, intervalo_poll_s=0))


@pytest.fixture
def agente(cliente, hub):
    codigo = hub.emitir_codigo("andino")["codigo"]
    datos = cliente.post("/api/agente/v1/enrolar",
                         json={"codigo": codigo, "host": "srv-dock-01"}).json()
    cliente.post("/api/agente/v1/latido", headers=bearer(datos["token"]), json={
        "version": "0.2.0",
        "instalaciones": [{"nombre": "mep", "producto": "mep", "version": "4.6.0",
                           "punto_retorno": "4.5.0"}]})
    return datos


def ordenar(cliente, agente, quien="operador", tipo="desplegar", release="4.7.0"):
    return cliente.post("/api/v1/ordenes", headers=como(quien), json={
        "agente": agente["agente_id"], "instalacion": "mep", "tipo": tipo, "release": release})


class TestIdentidad:
    def test_yo(self, cliente):
        r = cliente.get("/api/v1/yo", headers=como("operador"))
        assert r.status_code == 200
        assert r.json()["rol"] == "operador" and r.json()["tenant"] == "andino"

    def test_sin_token(self, cliente):
        assert cliente.get("/api/v1/yo").status_code == 401

    def test_firma_de_otro_secreto(self, cliente):
        falso = token_de_desarrollo("otro-secreto-de-al-menos-32-bytes!!!!", U["operador"],
                                    emisor=EMISOR)
        assert cliente.get("/api/v1/yo", headers=bearer(falso)).status_code == 401

    def test_token_vencido(self, cliente):
        assert cliente.get("/api/v1/yo", headers=como("operador", horas=-1)).status_code == 401

    def test_otro_emisor(self, cliente):
        t = token_de_desarrollo(SECRETO, U["operador"], emisor="https://otro.example")
        assert cliente.get("/api/v1/yo", headers=bearer(t)).status_code == 401

    def test_sin_segundo_factor(self, cliente):
        r = cliente.get("/api/v1/yo", headers=como("operador", aal="aal1"))
        assert r.status_code == 403
        assert "segundo factor" in r.json()["detalle"]

    def test_usuario_sin_alta(self, cliente):
        r = cliente.get("/api/v1/yo", headers=como("sin_alta"))
        assert r.status_code == 403
        assert "alta" in r.json()["detalle"]

    def test_quitar_el_rol_corta_el_acceso_con_el_mismo_token(self, cliente, hub):
        t = como("operador")
        assert cliente.get("/api/v1/yo", headers=t).status_code == 200
        hub.quitar_usuario(U["operador"])
        assert cliente.get("/api/v1/yo", headers=t).status_code == 403

    def test_sin_validador_la_web_queda_cerrada(self, hub):
        c = TestClient(crear_app(hub))
        assert c.get("/api/v1/yo", headers=como("operador")).status_code == 403

    def test_el_token_de_un_agente_no_sirve_en_la_web(self, cliente, agente):
        assert cliente.get("/api/v1/parque", headers=bearer(agente["token"])).status_code == 401


class TestCanalDelAgente:
    def test_salud_sin_credenciales(self, cliente):
        assert cliente.get("/api/salud").json() == {"ok": True}

    def test_pide_token(self, cliente):
        assert cliente.post("/api/agente/v1/latido", json={}).status_code == 401

    def test_el_jwt_de_una_persona_no_sirve_en_el_canal(self, cliente):
        r = cliente.get("/api/agente/v1/ordenes/siguiente", headers=como("soporte"))
        assert r.status_code == 401

    def test_codigo_invalido(self, cliente):
        r = cliente.post("/api/agente/v1/enrolar", json={"codigo": "DC-NOPE-NOPE", "host": "x"})
        assert r.status_code == 401

    def test_sin_orden_el_long_poll_devuelve_204(self, cliente, agente):
        r = cliente.get("/api/agente/v1/ordenes/siguiente?espera=0",
                        headers=bearer(agente["token"]))
        assert r.status_code == 204


class TestOrdenesPorRol:
    def test_el_operador_ordena_y_queda_su_nombre(self, cliente, agente):
        r = ordenar(cliente, agente)
        assert r.status_code == 201, r.text
        assert r.json()["pedida_por"] == "Martín Ríos"

    @pytest.mark.parametrize("quien", ["lector", "aprobador", "comercial"])
    def test_otros_roles_no_ordenan(self, cliente, agente, quien):
        assert ordenar(cliente, agente, quien).status_code == 403

    def test_el_operador_de_otro_cliente_ni_ve_el_agente(self, cliente, agente):
        assert ordenar(cliente, agente, "operador_otro").status_code == 404

    def test_soporte_necesita_habilitacion_del_cliente(self, cliente, agente):
        r = ordenar(cliente, agente, "soporte")
        assert r.status_code == 403
        assert "habilitación" in r.json()["detalle"]

        hab = cliente.post("/api/v1/habilitaciones", headers=como("aprobador"),
                           json={"horas": 2, "motivo": "ventana asistida"})
        assert hab.status_code == 201, hab.text
        assert ordenar(cliente, agente, "soporte").status_code == 201

        orden = cliente.get("/api/v1/ordenes", headers=como("soporte")).json()[0]
        cliente.post(f"/api/v1/ordenes/{orden['id']}/cancelar", headers=como("soporte"))
        cliente.post(f"/api/v1/habilitaciones/{hab.json()['id']}/revocar",
                     headers=como("aprobador"))
        assert ordenar(cliente, agente, "soporte").status_code == 403

    def test_la_habilitacion_no_se_la_da_soporte(self, cliente):
        r = cliente.post("/api/v1/habilitaciones", headers=como("soporte"), json={"horas": 2})
        assert r.status_code == 403

    def test_el_rollback_lo_pide_el_operador(self, cliente, agente):
        r = ordenar(cliente, agente, tipo="rollback", release=None)
        assert r.status_code == 201 and r.json()["release"] == "4.5.0"

    def test_la_orden_de_otro_cliente_no_existe(self, cliente, agente):
        orden_id = ordenar(cliente, agente).json()["id"]
        assert cliente.get(f"/api/v1/ordenes/{orden_id}",
                           headers=como("operador_otro")).status_code == 404
        assert cliente.post(f"/api/v1/ordenes/{orden_id}/cancelar",
                            headers=como("operador_otro")).status_code == 404

    def test_el_lector_ve_pero_no_cancela(self, cliente, agente):
        orden_id = ordenar(cliente, agente).json()["id"]
        assert cliente.get(f"/api/v1/ordenes/{orden_id}",
                           headers=como("lector")).status_code == 200
        assert cliente.post(f"/api/v1/ordenes/{orden_id}/cancelar",
                            headers=como("lector")).status_code == 403

    def test_la_regla_de_mantenimiento_llega_con_su_motivo(self, cliente, agente, hub):
        hub.adquirir("andino", "mep", "2026-01-01")
        r = ordenar(cliente, agente)
        assert r.status_code == 422 and "mantenimiento" in r.json()["detalle"]


class TestParqueYParametria:
    def test_cada_cliente_ve_su_parque(self, cliente, agente, hub):
        codigo = hub.emitir_codigo("otro")["codigo"]
        cliente.post("/api/agente/v1/enrolar", json={"codigo": codigo, "host": "srv-otro"})
        assert [a["tenant"] for a in cliente.get("/api/v1/parque",
                                                 headers=como("operador")).json()] == ["andino"]
        assert sorted(a["tenant"] for a in cliente.get(
            "/api/v1/parque", headers=como("soporte")).json()) == ["andino", "otro"]
        # pedir otro tenant por parámetro no cambia nada
        assert [a["tenant"] for a in cliente.get(
            "/api/v1/parque?tenant=otro", headers=como("operador")).json()] == ["andino"]

    def test_la_parametria_la_carga_comercial(self, cliente):
        pedido = {"mantenimiento_hasta": "2027-06-30", "autoservicio": True, "canal": "estable"}
        assert cliente.put("/api/v1/tenants/andino/productos/mep", headers=como("operador"),
                           json=pedido).status_code == 403
        r = cliente.put("/api/v1/tenants/andino/productos/mep", headers=como("comercial"),
                        json=pedido)
        assert r.status_code == 200
        assert r.json()["productos"][0]["mantenimiento_hasta"] == "2027-06-30"

    def test_alta_de_cliente(self, cliente):
        assert cliente.post("/api/v1/tenants", headers=como("soporte"),
                            json={"id": "nuevo", "nombre": "Nuevo"}).status_code == 403
        assert cliente.post("/api/v1/tenants", headers=como("comercial"),
                            json={"id": "nuevo", "nombre": "Nuevo"}).status_code == 201

    def test_codigos_y_revocacion_los_maneja_soporte(self, cliente, agente):
        assert cliente.post("/api/v1/tenants/andino/codigos", headers=como("operador"),
                            json={}).status_code == 403
        assert cliente.post("/api/v1/tenants/andino/codigos", headers=como("soporte"),
                            json={}).status_code == 200
        r = cliente.post(f"/api/v1/agentes/{agente['agente_id']}/revocar",
                         headers=como("soporte"))
        assert r.status_code == 200
        assert cliente.get("/api/agente/v1/ordenes/siguiente",
                           headers=bearer(agente["token"])).status_code == 401


class TestUsuarios:
    def test_el_aprobador_administra_su_organizacion(self, cliente):
        nuevo = str(uuid.uuid4())
        r = cliente.put(f"/api/v1/usuarios/{nuevo}", headers=como("aprobador"),
                        json={"rol": "operador", "tenant": "andino", "email": "n@andino.example"})
        assert r.status_code == 200
        emails = [u["email"] for u in cliente.get("/api/v1/usuarios",
                                                  headers=como("aprobador")).json()]
        assert "n@andino.example" in emails

    def test_no_en_otra_organizacion(self, cliente):
        r = cliente.put(f"/api/v1/usuarios/{uuid.uuid4()}", headers=como("aprobador"),
                        json={"rol": "operador", "tenant": "otro"})
        assert r.status_code == 403

    def test_no_se_sube_el_rol_a_si_mismo(self, cliente):
        r = cliente.put(f"/api/v1/usuarios/{U['aprobador']}", headers=como("aprobador"),
                        json={"rol": "operador", "tenant": "andino"})
        assert r.status_code == 403

    def test_nadie_crea_usuarios_de_accusys_desde_la_web(self, cliente):
        for quien in ("aprobador", "comercial"):
            r = cliente.put(f"/api/v1/usuarios/{uuid.uuid4()}", headers=como(quien),
                            json={"rol": "soporte"})
            assert r.status_code == 403

    def test_el_operador_no_administra(self, cliente):
        r = cliente.put(f"/api/v1/usuarios/{uuid.uuid4()}", headers=como("operador"),
                        json={"rol": "lector", "tenant": "andino"})
        assert r.status_code == 403


class TestAuditoria:
    def test_queda_quien_hizo_que(self, cliente, agente):
        ordenar(cliente, agente)
        registros = cliente.get("/api/v1/auditoria", headers=como("aprobador")).json()
        orden = [r for r in registros if r["accion"] == "orden"][0]
        assert orden["usuario"] == "Martín Ríos"

    def test_cada_cliente_ve_su_auditoria(self, cliente, hub):
        hub.adquirir("otro", "mep", "2027-01-01")   # consola, tenant "otro"
        registros = cliente.get("/api/v1/auditoria", headers=como("operador")).json()
        assert all(r["tenant"] == "andino" for r in registros)
