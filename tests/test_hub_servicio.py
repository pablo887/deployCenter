"""Reglas del hub: quién se enrola, qué se puede ordenar y cuándo."""

import pytest

from deploycenter.hub import servicio as srv


def enrolado(hub, host="srv-dock-01"):
    codigo = hub.emitir_codigo("andino")["codigo"]
    return hub.enrolar(codigo, host, "0.2.0")


def con_mep(hub, agente_id, version="4.6.0", punto_retorno=None, nombre="mep"):
    hub.latido(agente_id, "0.2.0", [{"nombre": nombre, "producto": "mep", "version": version,
                                    "estado": "ok", "punto_retorno": punto_retorno}])


@pytest.fixture
def agente(hub):
    datos = enrolado(hub)
    con_mep(hub, datos["agente_id"])
    return datos


class TestEnrolamiento:
    def test_el_codigo_se_canjea_por_un_token(self, hub):
        datos = enrolado(hub)
        assert datos["agente_id"].startswith("ag-")
        assert datos["tenant"] == "andino"
        assert hub.autenticar(datos["token"])["id"] == datos["agente_id"]

    def test_el_codigo_sirve_una_sola_vez(self, hub):
        codigo = hub.emitir_codigo("andino")["codigo"]
        hub.enrolar(codigo, "uno")
        with pytest.raises(srv.NoAutorizado):
            hub.enrolar(codigo, "dos")

    def test_el_codigo_vence(self, hub, reloj_hub):
        codigo = hub.emitir_codigo("andino", vigencia_h=1)["codigo"]
        reloj_hub.avanzar(3601)
        with pytest.raises(srv.NoAutorizado):
            hub.enrolar(codigo, "tarde")

    def test_el_codigo_se_acepta_sin_guiones_ni_mayusculas(self, hub):
        codigo = hub.emitir_codigo("andino")["codigo"]
        assert hub.enrolar(codigo.lower().replace("-", " "), "srv")["agente_id"]

    def test_el_hub_no_guarda_ni_el_codigo_ni_el_token(self, hub):
        from sqlalchemy import select

        from deploycenter.hub import modelos as m

        codigo = hub.emitir_codigo("andino")["codigo"]
        datos = hub.enrolar(codigo, "srv")
        with hub.sesion() as s:
            agente = s.get(m.Agente, datos["agente_id"])
            registro = s.scalar(select(m.CodigoEnrolamiento))
            assert agente.token_hash != datos["token"]
            assert datos["token"] not in agente.token_hash
            assert codigo not in registro.hash

    def test_un_token_revocado_no_autentica(self, hub, agente):
        hub.revocar_agente(agente["agente_id"])
        with pytest.raises(srv.NoAutorizado):
            hub.autenticar(agente["token"])

    def test_un_token_inventado_no_autentica(self, hub):
        with pytest.raises(srv.NoAutorizado):
            hub.autenticar("cualquier-cosa")

    def test_no_se_emite_codigo_para_un_cliente_inexistente(self, hub):
        with pytest.raises(srv.NoEncontrado):
            hub.emitir_codigo("fantasma")


class TestLatido:
    def test_registra_las_instalaciones(self, hub, agente):
        inst = hub.parque()[0]["instalaciones"]
        assert [(i["nombre"], i["version"]) for i in inst] == [("mep", "4.6.0")]

    def test_lo_que_no_se_reporta_deja_de_estar(self, hub, agente):
        hub.latido(agente["agente_id"], "0.2.0", [])
        assert hub.parque()[0]["instalaciones"] == []

    def test_en_linea_segun_el_ultimo_contacto(self, hub, agente, reloj_hub):
        assert hub.parque()[0]["en_linea"] is True
        reloj_hub.avanzar(srv.UMBRAL_EN_LINEA_S + 1)
        assert hub.parque()[0]["en_linea"] is False


class TestCrearOrden:
    def test_encola_un_despliegue_habilitado(self, hub, agente):
        o = hub.crear_orden(agente["agente_id"], "mep", "desplegar", "4.7.0", "Martín Ríos")
        assert o["estado"] == "pendiente"
        assert (o["producto"], o["release"]) == ("mep", "4.7.0")

    def test_una_sola_orden_abierta_por_instalacion(self, hub, agente):
        hub.crear_orden(agente["agente_id"], "mep", "preflight", "4.7.0", "x")
        with pytest.raises(srv.Conflicto, match="ya hay una orden abierta"):
            hub.crear_orden(agente["agente_id"], "mep", "desplegar", "4.7.0", "x")

    def test_la_base_tambien_impide_dos_ordenes_abiertas(self, hub, agente):
        """Si dos pedidos pasan el chequeo a la vez, el índice único corta el segundo."""
        from sqlalchemy.exc import IntegrityError

        from deploycenter.hub import modelos as m

        hub.crear_orden(agente["agente_id"], "mep", "preflight", "4.7.0", "x")
        with pytest.raises(IntegrityError), hub.sesion() as s:
            s.add(m.Orden(id="ord-carrera", tenant_id="andino", agente_id=agente["agente_id"],
                          instalacion="mep", tipo="desplegar", estado=m.PENDIENTE,
                          pedida_por="x", creada=hub.reloj()))

    def test_con_el_agente_fuera_de_linea_no_se_ordena(self, hub, agente, reloj_hub):
        reloj_hub.avanzar(srv.UMBRAL_EN_LINEA_S + 1)
        with pytest.raises(srv.Rechazado, match="fuera de línea"):
            hub.crear_orden(agente["agente_id"], "mep", "desplegar", "4.7.0", "x")

    def test_publicado_despues_del_mantenimiento(self, hub, agente):
        hub.adquirir("andino", "mep", "2026-09-01")
        with pytest.raises(srv.Rechazado, match="después del fin del mantenimiento"):
            hub.crear_orden(agente["agente_id"], "mep", "desplegar", "4.7.0", "x")

    def test_critico_de_seguridad_pasa_con_mantenimiento_vencido(self, hub, agente,
                                                                 agregar_release):
        agregar_release("4.7.1", publicado="2026-09-20", critico_seguridad=True)
        hub.adquirir("andino", "mep", "2026-09-01")
        o = hub.crear_orden(agente["agente_id"], "mep", "desplegar", "4.7.1", "x")
        assert o["release"] == "4.7.1"

    def test_producto_no_adquirido(self, hub, agente):
        hub.crear_tenant("otro", "Otro banco")
        codigo = hub.emitir_codigo("otro")["codigo"]
        ajeno = hub.enrolar(codigo, "srv-otro")
        con_mep(hub, ajeno["agente_id"])
        with pytest.raises(srv.Rechazado, match="no tiene adquirido"):
            hub.crear_orden(ajeno["agente_id"], "mep", "desplegar", "4.7.0", "x")

    def test_release_con_migraciones_va_asistido(self, hub, agente, agregar_release):
        agregar_release("4.8.0", db_migrations=True, rollback_seguro=False)
        with pytest.raises(srv.Rechazado, match="circuito asistido"):
            hub.crear_orden(agente["agente_id"], "mep", "desplegar", "4.8.0", "x")

    def test_canal_anticipado_solo_para_quien_lo_pidio(self, hub, agente, agregar_release):
        agregar_release("4.8.0-rc1", canal="anticipado")
        with pytest.raises(srv.Rechazado, match="canal anticipado"):
            hub.crear_orden(agente["agente_id"], "mep", "desplegar", "4.8.0-rc1", "x")
        hub.adquirir("andino", "mep", "2026-12-31", canal="anticipado")
        assert hub.crear_orden(agente["agente_id"], "mep", "desplegar", "4.8.0-rc1", "x")

    def test_autoservicio_deshabilitado(self, hub, agente):
        hub.adquirir("andino", "mep", "2026-12-31", autoservicio=False)
        with pytest.raises(srv.Rechazado, match="autoservicio"):
            hub.crear_orden(agente["agente_id"], "mep", "desplegar", "4.7.0", "x")

    def test_ruta_de_upgrade_no_soportada(self, hub, agente):
        con_mep(hub, agente["agente_id"], version="4.4.0")
        with pytest.raises(srv.Rechazado, match="no se puede saltar"):
            hub.crear_orden(agente["agente_id"], "mep", "desplegar", "4.7.0", "x")

    def test_la_version_ya_instalada(self, hub, agente):
        con_mep(hub, agente["agente_id"], version="4.7.0")
        with pytest.raises(srv.Rechazado, match="ya está instalada"):
            hub.crear_orden(agente["agente_id"], "mep", "desplegar", "4.7.0", "x")

    def test_cliente_suspendido_no_despliega(self, hub, agente):
        hub.cambiar_estado_tenant("andino", "suspendido")
        with pytest.raises(srv.Rechazado, match="suspendido"):
            hub.crear_orden(agente["agente_id"], "mep", "desplegar", "4.7.0", "x")

    def test_instalacion_que_el_agente_no_reporta(self, hub, agente):
        with pytest.raises(srv.NoEncontrado):
            hub.crear_orden(agente["agente_id"], "repi", "desplegar", "4.7.0", "x")

    def test_release_inexistente(self, hub, agente):
        with pytest.raises(srv.NoEncontrado):
            hub.crear_orden(agente["agente_id"], "mep", "desplegar", "9.9.9", "x")

    def test_tipo_fuera_del_catalogo(self, hub, agente):
        with pytest.raises(srv.Rechazado):
            hub.crear_orden(agente["agente_id"], "mep", "shell", None, "x")


class TestRollbackNuncaSeBloquea:
    def test_con_mantenimiento_vencido_y_cliente_suspendido(self, hub, agente):
        con_mep(hub, agente["agente_id"], version="4.7.0", punto_retorno="4.6.0")
        hub.adquirir("andino", "mep", "2026-01-01")
        hub.cambiar_estado_tenant("andino", "suspendido")
        o = hub.crear_orden(agente["agente_id"], "mep", "rollback", None, "x")
        assert (o["tipo"], o["release"]) == ("rollback", "4.6.0")

    def test_sin_punto_de_retorno_no_hay_a_donde_volver(self, hub, agente):
        with pytest.raises(srv.Rechazado, match="punto de retorno"):
            hub.crear_orden(agente["agente_id"], "mep", "rollback", None, "x")


class TestLadoDelAgente:
    def test_toma_la_orden_con_su_paquete_una_sola_vez(self, hub, agente):
        creada = hub.crear_orden(agente["agente_id"], "mep", "desplegar", "4.7.0", "x")
        orden = hub.tomar_orden(agente["agente_id"])
        assert orden["id"] == creada["id"]
        assert orden["paquete"]["manifiesto"]["release"] == "4.7.0"
        assert "services" in orden["paquete"]["plantilla"]
        assert orden["paquete"]["firma"]
        assert hub.tomar_orden(agente["agente_id"]) is None

    def test_un_agente_no_ve_las_ordenes_de_otro(self, hub, agente):
        hub.crear_orden(agente["agente_id"], "mep", "desplegar", "4.7.0", "x")
        otro = enrolado(hub, "srv-otro")
        assert hub.tomar_orden(otro["agente_id"]) is None
        orden = hub.tomar_orden(agente["agente_id"])
        with pytest.raises(srv.NoEncontrado):
            hub.registrar_eventos(otro["agente_id"], orden["id"], [{"evento": "x"}])
        with pytest.raises(srv.NoEncontrado):
            hub.cerrar_orden(otro["agente_id"], orden["id"], "ok")

    def test_eventos_y_resultado(self, hub, agente):
        hub.crear_orden(agente["agente_id"], "mep", "desplegar", "4.7.0", "x")
        orden = hub.tomar_orden(agente["agente_id"])
        hub.registrar_eventos(agente["agente_id"], orden["id"],
                              [{"ts": "t1", "evento": "despliegue_iniciado", "datos": {}}])
        hub.cerrar_orden(agente["agente_id"], orden["id"], "ok", "4.7.0 verificada")
        final = hub.orden(orden["id"])
        assert (final["estado"], final["resultado"]) == ("terminada", "ok")
        assert [e["evento"] for e in final["eventos"]] == ["despliegue_iniciado"]

    def test_el_resultado_repetido_no_rompe(self, hub, agente):
        """El buzón del agente puede reenviar si no llegó a ver la respuesta."""
        hub.crear_orden(agente["agente_id"], "mep", "desplegar", "4.7.0", "x")
        orden = hub.tomar_orden(agente["agente_id"])
        hub.cerrar_orden(agente["agente_id"], orden["id"], "ok")
        assert hub.cerrar_orden(agente["agente_id"], orden["id"], "ok")["repetido"] is True

    def test_la_cancelacion_del_rollback_viaja_en_la_respuesta(self, hub, agente):
        hub.crear_orden(agente["agente_id"], "mep", "desplegar", "4.7.0", "x")
        orden = hub.tomar_orden(agente["agente_id"])
        hub.pedir_cancelacion_rollback(orden["id"])
        r = hub.registrar_eventos(agente["agente_id"], orden["id"], [{"evento": "falla"}])
        assert r["cancelar_rollback"] is True
        assert hub.control(agente["agente_id"], orden["id"])["cancelar_rollback"] is True

    def test_una_orden_pendiente_se_puede_retirar(self, hub, agente):
        o = hub.crear_orden(agente["agente_id"], "mep", "desplegar", "4.7.0", "x")
        hub.cancelar_orden(o["id"])
        assert hub.tomar_orden(agente["agente_id"]) is None
        # y libera la instalación para una orden nueva
        assert hub.crear_orden(agente["agente_id"], "mep", "desplegar", "4.7.0", "x")

    def test_una_orden_entregada_ya_no_se_retira(self, hub, agente):
        o = hub.crear_orden(agente["agente_id"], "mep", "desplegar", "4.7.0", "x")
        hub.tomar_orden(agente["agente_id"])
        with pytest.raises(srv.Conflicto):
            hub.cancelar_orden(o["id"])

    def test_revocar_cancela_lo_pendiente(self, hub, agente):
        o = hub.crear_orden(agente["agente_id"], "mep", "desplegar", "4.7.0", "x")
        hub.revocar_agente(agente["agente_id"])
        assert hub.orden(o["id"])["estado"] == "cancelada"
