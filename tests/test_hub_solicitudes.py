"""Enrolamiento al revés: el agente se presenta con la llave de su cliente y
Soporte acepta el pedido en la web.

Lo que importa probar:
- la llave sola no conecta nada: hace falta que alguien acepte;
- el token lo recibe solo quien tiene el secreto del pedido, una vez;
- una llave de otro cliente, revocada o inventada da siempre el mismo error;
- nadie más que Soporte acepta, y el cliente ve pero no resuelve.
"""

import datetime
import uuid

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("jwt")

from fastapi.testclient import TestClient  # noqa: E402

from deploycenter.hub import servicio as srv  # noqa: E402
from deploycenter.hub.api import crear_app  # noqa: E402
from deploycenter.hub.identidad import ValidadorJWT, token_de_desarrollo  # noqa: E402

SECRETO = "secreto-de-prueba-de-al-menos-32-bytes!!"
EMISOR = "https://proyecto.supabase.co/auth/v1"
U = {n: str(uuid.uuid4()) for n in ("soporte", "comercial", "operador", "aprobador",
                                     "operador_otro")}


def como(quien):
    return {"Authorization": "Bearer " + token_de_desarrollo(SECRETO, U[quien], aal="aal2",
                                                              emisor=EMISOR)}


@pytest.fixture
def cliente(hub):
    hub.crear_tenant("otro", "Otro Banco")
    hub.asignar_usuario(U["soporte"], "soporte", nombre="Sofía Herrera")
    hub.asignar_usuario(U["comercial"], "comercial")
    hub.asignar_usuario(U["operador"], "operador", "andino")
    hub.asignar_usuario(U["aprobador"], "aprobador", "andino")
    hub.asignar_usuario(U["operador_otro"], "operador", "otro")
    return TestClient(crear_app(hub, validador=ValidadorJWT(secreto=SECRETO, emisor=EMISOR),
                                intervalo_poll_s=0))


@pytest.fixture
def llave(cliente):
    r = cliente.post("/api/v1/tenants/andino/llaves", headers=como("soporte"),
                     json={"nombre": "servidores de producción"})
    assert r.status_code == 201, r.text
    return r.json()


def pedir(cliente, llave, host="srv-mep-01", ambiente="produccion", tenant="andino"):
    return cliente.post("/api/agente/v1/solicitudes", json={
        "cliente": tenant, "llave": llave, "host": host, "ambiente": ambiente,
        "version": "0.3.0"})


def consultar(cliente, pedido):
    return cliente.get(f"/api/agente/v1/solicitudes/{pedido['solicitud']}",
                       headers={"X-DC-Solicitud": pedido["secreto"]})


class TestLlaves:
    def test_se_ve_una_sola_vez(self, cliente, llave):
        assert llave["llave"].startswith("DCK-") and len(llave["llave"]) == 28
        listado = cliente.get("/api/v1/tenants/andino/llaves", headers=como("soporte")).json()
        assert [x["nombre"] for x in listado] == ["servidores de producción"]
        assert "llave" not in listado[0] and listado[0]["prefijo"] == llave["llave"][:8]

    def test_solo_soporte_la_genera(self, cliente):
        for quien in ("comercial", "operador", "aprobador"):
            r = cliente.post("/api/v1/tenants/andino/llaves", headers=como(quien),
                             json={"nombre": "x"})
            assert r.status_code == 403, quien

    def test_el_cliente_no_ve_las_llaves(self, cliente, llave):
        assert cliente.get("/api/v1/tenants/andino/llaves",
                           headers=como("operador")).status_code == 403

    def test_revocar_rechaza_los_pendientes(self, cliente, llave):
        pedido = pedir(cliente, llave["llave"]).json()
        r = cliente.post(f"/api/v1/llaves/{llave['id']}/revocar", headers=como("soporte"))
        assert r.status_code == 200 and r.json()["revocada"]
        assert consultar(cliente, pedido).json() == {"estado": "rechazada"}
        assert pedir(cliente, llave["llave"]).status_code == 401


class TestPedido:
    def test_queda_pendiente_hasta_que_lo_aceptan(self, cliente, llave, hub):
        r = pedir(cliente, llave["llave"])
        assert r.status_code == 202
        pedido = r.json()
        assert pedido["estado"] == "pendiente" and "token" not in pedido
        assert consultar(cliente, pedido).json() == {"estado": "pendiente"}
        # la llave sola no da de alta ningún agente
        assert hub.parque("andino") == []

    def test_aceptado_recibe_el_token_una_sola_vez(self, cliente, llave, hub):
        pedido = pedir(cliente, llave["llave"]).json()
        r = cliente.post(f"/api/v1/solicitudes/{pedido['solicitud']}/aceptar",
                         headers=como("soporte"))
        assert r.status_code == 200 and r.json()["estado"] == "aceptada"
        assert r.json()["resuelta_por"].startswith("Sofía Herrera")

        datos = consultar(cliente, pedido).json()
        assert datos["estado"] == "conectada" and datos["tenant"] == "andino"
        # el token sirve para el canal del agente
        lat = cliente.post("/api/agente/v1/latido", json={"instalaciones": []},
                           headers={"Authorization": f"Bearer {datos['token']}"})
        assert lat.status_code == 200
        # y no se vuelve a entregar
        assert consultar(cliente, pedido).json() == {"estado": "conectada"}

        (agente,) = hub.parque("andino")
        assert agente["host"] == "srv-mep-01" and agente["ambiente"] == "produccion"

    def test_rechazado(self, cliente, llave, hub):
        pedido = pedir(cliente, llave["llave"]).json()
        cliente.post(f"/api/v1/solicitudes/{pedido['solicitud']}/rechazar",
                     headers=como("soporte"))
        assert consultar(cliente, pedido).json() == {"estado": "rechazada"}
        assert hub.parque("andino") == []

    def test_no_se_resuelve_dos_veces(self, cliente, llave):
        pedido = pedir(cliente, llave["llave"]).json()
        url = f"/api/v1/solicitudes/{pedido['solicitud']}"
        cliente.post(url + "/rechazar", headers=como("soporte"))
        assert cliente.post(url + "/aceptar", headers=como("soporte")).status_code == 409

    @pytest.mark.parametrize("tenant,clave", [
        ("otro", None),            # llave válida, cliente equivocado
        ("andino", "DCK-XXXX-XXXX-XXXX-XXXX-XXXX"),  # llave inventada
        ("nadie", None),           # cliente inexistente
    ])
    def test_siempre_el_mismo_error(self, cliente, llave, tenant, clave):
        r = pedir(cliente, clave or llave["llave"], tenant=tenant)
        assert r.status_code == 401
        assert r.json()["detalle"] == "la llave no corresponde a ese cliente o fue revocada"

    def test_cliente_suspendido(self, cliente, llave, hub):
        hub.cambiar_estado_tenant("andino", "suspendido")
        assert pedir(cliente, llave["llave"]).status_code == 401

    def test_la_llave_se_acepta_sin_guiones_ni_mayusculas(self, cliente, llave):
        normalizada = llave["llave"].replace("-", "").lower()
        assert pedir(cliente, normalizada).status_code == 202

    def test_ambiente_invalido(self, cliente, llave):
        r = pedir(cliente, llave["llave"], ambiente="Producción!")
        assert r.status_code in (400, 422)

    def test_secreto_equivocado(self, cliente, llave):
        pedido = pedir(cliente, llave["llave"]).json()
        r = consultar(cliente, {**pedido, "secreto": "otro"})
        assert r.status_code == 401

    def test_el_mismo_servidor_reemplaza_su_pedido(self, cliente, llave):
        primero = pedir(cliente, llave["llave"]).json()
        segundo = pedir(cliente, llave["llave"]).json()
        assert consultar(cliente, primero).json() == {"estado": "reemplazada"}
        assert consultar(cliente, segundo).json() == {"estado": "pendiente"}
        # otro ambiente del mismo host es otro pedido
        tercero = pedir(cliente, llave["llave"], ambiente="homologacion").json()
        assert consultar(cliente, segundo).json() == {"estado": "pendiente"}
        assert consultar(cliente, tercero).json() == {"estado": "pendiente"}

    def test_tope_de_pendientes_por_llave(self, cliente, llave):
        for i in range(srv.MAX_SOLICITUDES_PENDIENTES):
            assert pedir(cliente, llave["llave"], host=f"srv-{i}").status_code == 202
        r = pedir(cliente, llave["llave"], host="srv-de-mas")
        assert r.status_code == 422 and "demasiados" in r.json()["detalle"]

    def test_vence_sin_respuesta(self, cliente, llave, reloj_hub):
        pedido = pedir(cliente, llave["llave"]).json()
        reloj_hub.avanzar(srv.VIGENCIA_SOLICITUD_H * 3600 + 1)
        assert consultar(cliente, pedido).json() == {"estado": "vencida"}
        r = cliente.post(f"/api/v1/solicitudes/{pedido['solicitud']}/aceptar",
                         headers=como("soporte"))
        assert r.status_code == 409

    def test_aceptada_y_nunca_retirada_vence(self, cliente, llave, reloj_hub, hub):
        pedido = pedir(cliente, llave["llave"]).json()
        cliente.post(f"/api/v1/solicitudes/{pedido['solicitud']}/aceptar",
                     headers=como("soporte"))
        reloj_hub.avanzar(srv.VIGENCIA_SOLICITUD_H * 3600 + 1)
        assert consultar(cliente, pedido).json() == {"estado": "vencida"}
        assert hub.parque("andino") == []


class TestQuienVeYQuienResuelve:
    def test_soporte_ve_todos(self, cliente, llave):
        pedir(cliente, llave["llave"])
        r = cliente.get("/api/v1/solicitudes?estado=pendiente", headers=como("soporte"))
        (sol,) = r.json()
        assert sol["host"] == "srv-mep-01" and sol["ambiente"] == "produccion"
        assert sol["ip"] and "secreto" not in sol

    def test_el_cliente_ve_los_suyos_y_no_los_ajenos(self, cliente, llave):
        pedir(cliente, llave["llave"])
        assert len(cliente.get("/api/v1/solicitudes", headers=como("operador")).json()) == 1
        assert cliente.get("/api/v1/solicitudes", headers=como("operador_otro")).json() == []

    @pytest.mark.parametrize("quien", ["operador", "aprobador", "comercial"])
    def test_solo_soporte_resuelve(self, cliente, llave, quien):
        pedido = pedir(cliente, llave["llave"]).json()
        r = cliente.post(f"/api/v1/solicitudes/{pedido['solicitud']}/aceptar",
                         headers=como(quien))
        assert r.status_code == 403

    def test_queda_en_la_auditoria(self, cliente, llave, hub):
        pedido = pedir(cliente, llave["llave"]).json()
        cliente.post(f"/api/v1/solicitudes/{pedido['solicitud']}/aceptar",
                     headers=como("soporte"))
        acciones = [a["accion"] for a in hub.auditoria()]
        assert {"llave_emitida", "conexion_solicitada", "conexion_aceptada"} <= set(acciones)


def test_vigencia_es_una_semana():
    assert datetime.timedelta(hours=srv.VIGENCIA_SOLICITUD_H).days == 7
