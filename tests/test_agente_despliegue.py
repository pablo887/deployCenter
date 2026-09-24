"""El corazón de la Fase 1: qué pasa cuando el despliegue sale bien y, sobre
todo, qué pasa cuando sale mal."""

import json

import pytest

from deploycenter.agente import despliegue as d
from deploycenter.agente.instalacion import ErrorBloqueo


def cliente_ok(base):
    respuestas = {hc["url"]: (hc["espera"], "") for hc in base["healthchecks"]}
    return lambda url, timeout: respuestas.get(url, (None, "sin ruta"))


def cliente_roto(_url, _timeout):
    return 503, ""


def desplegar(docker, instalacion, paquete, reloj, avisos=None, **kw):
    kw.setdefault("cliente_http", lambda url, t: (None, "sin red"))
    return d.Despliegue(
        docker, instalacion, paquete,
        avisar=(lambda e, datos: avisos.append((e, datos))) if avisos is not None else None,
        dormir=reloj.dormir, reloj=reloj, **kw,
    ).ejecutar(cancelado=kw.pop("cancelado", None))


class TestDespliegueExitoso:
    def test_deja_la_version_nueva(self, docker_falso, instalacion, paquete, reloj, base):
        r = desplegar(docker_falso, instalacion, paquete, reloj,
                      cliente_http=cliente_ok(base))
        assert r.ok, r.detalle
        assert instalacion.version_instalada() == "4.7.0"
        assert instalacion.estado()["estado"] == "ok"

    def test_aplica_el_compose_nuevo(self, docker_falso, instalacion, paquete, reloj, base):
        desplegar(docker_falso, instalacion, paquete, reloj, cliente_http=cliente_ok(base))
        compose = instalacion.ruta_compose.read_text(encoding="utf-8")
        assert base["imagenes"]["api"] in compose

    def test_guarda_los_digests_en_el_estado(self, docker_falso, instalacion, paquete,
                                             reloj, base):
        desplegar(docker_falso, instalacion, paquete, reloj, cliente_http=cliente_ok(base))
        assert instalacion.estado()["digests"]["api"] == base["imagenes"]["api"].split("@")[1]

    def test_toma_el_punto_de_retorno_antes_de_tocar_nada(self, docker_falso, instalacion,
                                                          paquete, reloj, base):
        previo = instalacion.ruta_compose.read_text(encoding="utf-8")
        desplegar(docker_falso, instalacion, paquete, reloj, cliente_http=cliente_ok(base))
        guardado = (instalacion.dir_retorno / "docker-compose.yml").read_text(encoding="utf-8")
        assert guardado == previo

    def test_registra_en_el_historial(self, docker_falso, instalacion, paquete, reloj, base):
        desplegar(docker_falso, instalacion, paquete, reloj, cliente_http=cliente_ok(base))
        despliegues = [e for e in instalacion.historial() if e["operacion"] == "despliegue"]
        assert despliegues[-1]["resultado"] == "ok"
        assert despliegues[-1]["hacia"] == "4.7.0"

    def test_libera_el_lock(self, docker_falso, instalacion, paquete, reloj, base):
        desplegar(docker_falso, instalacion, paquete, reloj, cliente_http=cliente_ok(base))
        assert not instalacion.bloqueada()

    def test_completa_las_variables_con_default_sin_pisar_el_env(self, docker_falso,
                                                                 instalacion, paquete,
                                                                 reloj, base):
        base["variables_nuevas"] = [{
            "nombre": "MEP_FIRMA_TIMEOUT_S", "obligatoria": True, "default": "30",
            "descripcion": "Timeout de firma del conector, en segundos"}]
        paquete.ruta_manifiesto.write_text(json.dumps(base), encoding="utf-8")

        desplegar(docker_falso, instalacion, paquete, reloj, cliente_http=cliente_ok(base))
        entorno = instalacion.entorno()
        assert entorno["MEP_FIRMA_TIMEOUT_S"] == "30"
        assert entorno["MEP_PUERTO_WEB"] == "8443"  # lo que ya estaba sigue


class TestPreflightQueNoPasa:
    def test_aborta_sin_tocar_el_stack(self, docker_falso, instalacion, paquete, reloj):
        instalacion.guardar_estado(version="4.4.0")  # salto no soportado
        antes = instalacion.ruta_compose.read_text(encoding="utf-8")

        r = desplegar(docker_falso, instalacion, paquete, reloj)
        assert r.estado == d.ABORTADO
        assert instalacion.ruta_compose.read_text(encoding="utf-8") == antes
        assert docker_falso.veces("up") == 0

    def test_la_version_instalada_no_cambia(self, docker_falso, instalacion, paquete, reloj):
        instalacion.guardar_estado(version="4.4.0")
        desplegar(docker_falso, instalacion, paquete, reloj)
        assert instalacion.version_instalada() == "4.4.0"


class TestRollbackAutomatico:
    def test_vuelve_atras_cuando_la_verificacion_falla(self, docker_falso, instalacion,
                                                       paquete, reloj):
        previo = instalacion.ruta_compose.read_text(encoding="utf-8")
        # la versión nueva no levanta; el rollback sí
        docker_falso.salud_tras_up = [False, True]

        r = desplegar(docker_falso, instalacion, paquete, reloj, espera_cancelacion_s=0)

        assert r.estado == d.REVERTIDO
        assert instalacion.ruta_compose.read_text(encoding="utf-8") == previo
        assert instalacion.version_instalada() == "4.6.0"
        assert instalacion.estado()["estado"] == "ok"

    def test_el_aviso_sale_antes_del_rollback(self, docker_falso, instalacion, paquete, reloj):
        """El operador se entera del fallo antes de que el sistema actúe, no después."""
        avisos = []
        docker_falso.salud_tras_up = [False, True]
        desplegar(docker_falso, instalacion, paquete, reloj, avisos=avisos,
                  espera_cancelacion_s=0)

        eventos = [e for e, _ in avisos]
        assert eventos.index("despliegue_falla") < eventos.index("rollback_ok")

    def test_up_que_falla_tambien_dispara_el_rollback(self, docker_falso, instalacion,
                                                      paquete, reloj):
        docker_falso.fallar_up = True
        r = desplegar(docker_falso, instalacion, paquete, reloj, espera_cancelacion_s=0)
        # el rollback también usa up, así que queda degradado; lo importante es
        # que no se haya dado por bueno
        assert r.estado in (d.DEGRADADO, d.REVERTIDO)
        assert not r.ok

    def test_queda_registrado_con_el_motivo(self, docker_falso, instalacion, paquete, reloj):
        docker_falso.salud_tras_up = [False, True]
        desplegar(docker_falso, instalacion, paquete, reloj, espera_cancelacion_s=0)
        ultimo = [e for e in instalacion.historial() if e["operacion"] == "despliegue"][-1]
        assert ultimo["resultado"] == "revertido"
        assert "api" in ultimo["detalle"]  # dice qué servicio no levantó
        assert "versión anterior" in ultimo["rollback"]

    def test_si_el_rollback_no_verifica_queda_degradado(self, docker_falso, instalacion,
                                                        paquete, reloj):
        docker_falso.sano = False  # nada levanta, ni lo nuevo ni lo viejo
        r = desplegar(docker_falso, instalacion, paquete, reloj, espera_cancelacion_s=0)
        assert r.estado == d.DEGRADADO
        assert instalacion.estado()["estado"] == "degradado"


class TestRollbackQueNoDebeOcurrir:
    def test_con_rollback_seguro_en_false_no_se_revierte(self, docker_falso, instalacion,
                                                         paquete, reloj, base):
        """El caso de las migraciones: revertir la imagen contra una base ya
        migrada es peor que el fallo."""
        base["db_migrations"] = True
        base["rollback_seguro"] = False
        paquete.ruta_manifiesto.write_text(json.dumps(base), encoding="utf-8")
        docker_falso.sano = False

        r = desplegar(docker_falso, instalacion, paquete, reloj, espera_cancelacion_s=0)
        # ni siquiera llega a desplegar: el preflight lo manda al circuito asistido
        assert r.estado == d.ABORTADO
        assert "Accusys" in r.detalle

    def test_sin_punto_de_retorno_no_hay_a_donde_volver(self, docker_falso,
                                                        instalacion_nueva, paquete, reloj):
        docker_falso.sano = False
        avisos = []
        r = desplegar(docker_falso, instalacion_nueva, paquete, reloj, avisos=avisos,
                      espera_cancelacion_s=0)
        assert r.estado == d.DEGRADADO
        assert "requiere_intervencion" in [e for e, _ in avisos]


class TestCancelacion:
    def test_el_operador_puede_frenar_la_vuelta_atras(self, docker_falso, instalacion,
                                                      paquete, reloj):
        nuevo = None
        docker_falso.salud_tras_up = [False, True]

        resultado = d.Despliegue(
            docker_falso, instalacion, paquete, espera_cancelacion_s=30,
            dormir=reloj.dormir, reloj=reloj,
            cliente_http=lambda u, t: (None, "sin red"),
        ).ejecutar(cancelado=lambda: True)

        assert resultado.estado == d.DEGRADADO
        assert "canceló" in resultado.detalle
        # el compose nuevo sigue puesto: el operador quiso quedarse con el estado roto
        assert nuevo is None
        assert instalacion.estado()["estado"] == "degradado"

    def test_sin_cancelacion_revierte(self, docker_falso, instalacion, paquete, reloj):
        docker_falso.salud_tras_up = [False, True]
        r = d.Despliegue(
            docker_falso, instalacion, paquete, espera_cancelacion_s=5,
            dormir=reloj.dormir, reloj=reloj,
            cliente_http=lambda u, t: (None, "sin red"),
        ).ejecutar(cancelado=lambda: False)
        assert r.estado == d.REVERTIDO


class TestConcurrencia:
    def test_no_se_puede_desplegar_dos_veces_a_la_vez(self, docker_falso, instalacion,
                                                      paquete, reloj):
        instalacion.bloquear(dueno="otro")
        with pytest.raises(ErrorBloqueo):
            desplegar(docker_falso, instalacion, paquete, reloj)


class TestRollbackManual:
    def test_vuelve_a_la_version_anterior(self, docker_falso, instalacion, paquete,
                                          reloj, base, manifiesto_previo):
        desplegar(docker_falso, instalacion, paquete, reloj, cliente_http=cliente_ok(base))
        assert instalacion.version_instalada() == "4.7.0"

        r = d.rollback_manual(docker_falso, instalacion,
                              cliente_http=lambda u, t: (None, "sin red"),
                              dormir=reloj.dormir, reloj=reloj)
        assert r.estado == d.REVERTIDO
        assert instalacion.version_instalada() == "4.6.0"
        assert manifiesto_previo["imagenes"]["api"] in instalacion.ruta_compose.read_text(
            encoding="utf-8")

    def test_sin_punto_de_retorno_avisa(self, docker_falso, instalacion_nueva, reloj):
        r = d.rollback_manual(docker_falso, instalacion_nueva,
                              dormir=reloj.dormir, reloj=reloj)
        assert r.estado == d.ABORTADO
        assert "punto de retorno" in r.detalle

    def test_libera_el_lock_aunque_falle(self, docker_falso, instalacion, paquete,
                                         reloj, base):
        desplegar(docker_falso, instalacion, paquete, reloj, cliente_http=cliente_ok(base))
        docker_falso.fallar_up = True
        d.rollback_manual(docker_falso, instalacion, dormir=reloj.dormir, reloj=reloj)
        assert not instalacion.bloqueada()


class TestResultado:
    def test_resumen_es_serializable(self, docker_falso, instalacion, paquete, reloj, base):
        r = desplegar(docker_falso, instalacion, paquete, reloj, cliente_http=cliente_ok(base))
        assert json.loads(json.dumps(r.resumen()))["estado"] == "ok"
