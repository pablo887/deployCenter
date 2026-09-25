"""El hub sobre Postgres, con la RLS activa.

Los mismos circuitos que en SQLite, pero cada operación de una persona corre
con su identidad en la base. Prueba dos cosas: que el hub funciona con las
políticas y los permisos por columna (el ORM no pide lo que no puede leer), y
que si el chequeo de la aplicación fallara, la base igual rechaza.
"""

import datetime
import uuid

import pytest

pytest.importorskip("psycopg")

from sqlalchemy.exc import DBAPIError  # noqa: E402
from sqlalchemy.orm.exc import StaleDataError  # noqa: E402

from deploycenter.hub import servicio as srv  # noqa: E402

U = {n: str(uuid.uuid4()) for n in (
    "operador", "aprobador", "lector", "operador_otro", "soporte", "comercial")}


def reloj_real():
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None, microsecond=0)


@pytest.fixture
def hub_pg(postgres_url, catalogo):
    hub = srv.Hub(srv.conectar(postgres_url), catalogo, reloj=reloj_real)
    hub.crear_tenant("andino", "Banco Andino")
    hub.crear_tenant("otro", "Otro Banco")
    hub.adquirir("andino", "mep", "2026-12-31")
    hub.asignar_usuario(U["operador"], "operador", "andino", nombre="Martín Ríos")
    hub.asignar_usuario(U["aprobador"], "aprobador", "andino")
    hub.asignar_usuario(U["lector"], "lector", "andino")
    hub.asignar_usuario(U["operador_otro"], "operador", "otro")
    hub.asignar_usuario(U["soporte"], "soporte")
    hub.asignar_usuario(U["comercial"], "comercial")
    yield hub
    hub.engine.dispose()


def perfil(hub, quien):
    return hub.perfil({"sub": U[quien], "aal": "aal2", "role": "authenticated"})


@pytest.fixture
def agente(hub_pg):
    codigo = hub_pg.emitir_codigo("andino")["codigo"]
    datos = hub_pg.enrolar(codigo, "srv-dock-01")
    hub_pg.latido(datos["agente_id"], "0.2.0", [
        {"nombre": "mep", "producto": "mep", "version": "4.6.0", "punto_retorno": "4.5.0"}])
    return datos


class TestCircuitoConRLS:
    def test_el_operador_ordena_y_el_agente_ejecuta(self, hub_pg, agente):
        op = perfil(hub_pg, "operador")
        o = hub_pg.crear_orden(agente["agente_id"], "mep", "desplegar", "4.7.0", perfil=op)
        assert o["pedida_por"] == "Martín Ríos"

        tomada = hub_pg.tomar_orden(agente["agente_id"])
        assert tomada["id"] == o["id"]
        hub_pg.registrar_eventos(agente["agente_id"], o["id"], [{"evento": "despliegue_ok"}])
        hub_pg.cerrar_orden(agente["agente_id"], o["id"], "ok")

        final = hub_pg.orden(o["id"], perfil=op)
        assert final["resultado"] == "ok"
        assert [e["evento"] for e in final["eventos"]] == ["despliegue_ok"]

    def test_parque_y_listados_con_la_identidad_de_cada_uno(self, hub_pg, agente):
        hub_pg.enrolar(hub_pg.emitir_codigo("otro")["codigo"], "srv-otro")
        assert [a["tenant"] for a in hub_pg.parque(perfil=perfil(hub_pg, "operador"))] == [
            "andino"]
        assert sorted(a["tenant"] for a in hub_pg.parque(perfil=perfil(hub_pg, "soporte"))) == [
            "andino", "otro"]
        assert hub_pg.parque(perfil=perfil(hub_pg, "operador_otro"))[0]["instalaciones"] == []

    def test_soporte_con_habilitacion(self, hub_pg, agente):
        sop = perfil(hub_pg, "soporte")
        with pytest.raises(srv.Prohibido):
            hub_pg.crear_orden(agente["agente_id"], "mep", "preflight", "4.7.0", perfil=sop)
        hub_pg.habilitar_asistencia(perfil(hub_pg, "aprobador"), 2, "ventana asistida")
        o = hub_pg.crear_orden(agente["agente_id"], "mep", "preflight", "4.7.0", perfil=sop)
        assert o["estado"] == "pendiente"

    def test_soporte_emite_codigos_y_revoca(self, hub_pg, agente):
        sop = perfil(hub_pg, "soporte")
        assert hub_pg.emitir_codigo("andino", perfil=sop)["codigo"].startswith("DC-")
        hub_pg.revocar_agente(agente["agente_id"], perfil=sop)
        with pytest.raises(srv.NoAutorizado):
            hub_pg.autenticar(agente["token"])

    def test_comercial_carga_parametria(self, hub_pg):
        com = perfil(hub_pg, "comercial")
        hub_pg.crear_tenant("nuevo", "Nuevo Banco", perfil=com)
        hub_pg.adquirir("nuevo", "mep", "2027-03-31", perfil=com)
        assert hub_pg.parametria("nuevo", perfil=com)["productos"][0]["producto"] == "mep"

    def test_el_aprobador_administra_usuarios(self, hub_pg):
        apr = perfil(hub_pg, "aprobador")
        nuevo = str(uuid.uuid4())
        hub_pg.asignar_usuario(nuevo, "operador", "andino", perfil=apr)
        hub_pg.asignar_usuario(U["lector"], "operador", "andino", perfil=apr)
        roles = {u["usuario_id"]: u["rol"] for u in hub_pg.usuarios(perfil=apr)}
        assert roles[nuevo] == "operador" and roles[U["lector"]] == "operador"
        hub_pg.quitar_usuario(nuevo, perfil=apr)

    def test_la_auditoria_queda_con_la_persona(self, hub_pg, agente):
        hub_pg.crear_orden(agente["agente_id"], "mep", "preflight", "4.7.0",
                           perfil=perfil(hub_pg, "operador"))
        registros = hub_pg.auditoria(perfil=perfil(hub_pg, "lector"))
        assert any(r["accion"] == "orden" and r["usuario"] == "Martín Ríos" for r in registros)
        assert all(r["tenant"] == "andino" for r in registros)


class TestLaBaseEsLaSegundaBarrera:
    """Se apaga el chequeo de la aplicación y se comprueba que la base rechaza."""

    @pytest.fixture
    def sin_chequeos(self, monkeypatch):
        monkeypatch.setattr(srv.Hub, "_exigir_operar", lambda self, s, perfil, t: None)
        monkeypatch.setattr(srv.Hub, "_exigir", staticmethod(lambda perfil, *roles: None))
        monkeypatch.setattr(srv.Hub, "_ve", staticmethod(lambda perfil, t: True))

    def test_un_lector_no_ordena_aunque_el_hub_lo_deje(self, hub_pg, agente, sin_chequeos):
        with pytest.raises(DBAPIError, match="row-level security"):
            hub_pg.crear_orden(agente["agente_id"], "mep", "preflight", "4.7.0",
                               perfil=perfil(hub_pg, "lector"))

    def test_otro_cliente_no_ve_el_agente_aunque_el_hub_lo_deje(self, hub_pg, agente,
                                                                sin_chequeos):
        # para la base, el agente de otro cliente no existe
        with pytest.raises(srv.NoEncontrado):
            hub_pg.crear_orden(agente["agente_id"], "mep", "preflight", "4.7.0",
                               perfil=perfil(hub_pg, "operador_otro"))

    def test_soporte_sin_habilitacion_no_ordena_aunque_el_hub_lo_deje(self, hub_pg, agente,
                                                                      sin_chequeos):
        with pytest.raises(DBAPIError, match="row-level security"):
            hub_pg.crear_orden(agente["agente_id"], "mep", "preflight", "4.7.0",
                               perfil=perfil(hub_pg, "soporte"))

    def test_el_operador_no_carga_parametria_aunque_el_hub_lo_deje(self, hub_pg, sin_chequeos):
        # un UPDATE que la política no admite no toca filas: el ORM lo ve como StaleDataError
        with pytest.raises(StaleDataError):
            hub_pg.adquirir("andino", "mep", "2030-01-01", perfil=perfil(hub_pg, "operador"))
        assert hub_pg.parametria("andino")["productos"][0]["mantenimiento_hasta"] == "2026-12-31"

    def test_el_operador_no_emite_codigos_aunque_el_hub_lo_deje(self, hub_pg, sin_chequeos):
        with pytest.raises(DBAPIError, match="row-level security"):
            hub_pg.emitir_codigo("andino", perfil=perfil(hub_pg, "operador"))
