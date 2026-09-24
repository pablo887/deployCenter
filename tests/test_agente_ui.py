"""Tests de la UI local.

El registro de operaciones se instancia con `lanzar_hilo=False`, así cada POST
corre la operación en línea y el test puede afirmar sobre el resultado sin
carreras. El camino con hilo se prueba aparte.
"""

import json

import pytest

from deploycenter.agente.ui import servidor as srv
from deploycenter.agente.ui.operaciones import CORRIENDO, Operacion, Registro

TOKEN = "token-de-prueba"


@pytest.fixture
def raiz(tmp_path, instalacion):
    """La raíz que contiene el stack de la instalación de prueba."""
    return instalacion.directorio.parent


@pytest.fixture
def dir_paquetes(paquete):
    """La UI corre con relojes reales —no los inyecta, y está bien que no lo
    haga— así que el manifiesto de prueba usa timeouts cortos. Si no, el test
    del camino de fallo espera los 120s de verdad."""
    m = json.loads(paquete.ruta_manifiesto.read_text(encoding="utf-8"))
    for hc in m["healthchecks"]:
        hc["timeout_s"] = 5
    paquete.ruta_manifiesto.write_text(json.dumps(m), encoding="utf-8")
    return paquete.directorio.parent


@pytest.fixture
def registro():
    return Registro(lanzar_hilo=False)


@pytest.fixture
def app(raiz, dir_paquetes, docker_falso, registro):
    aplicacion = srv.crear_app(raiz, dir_paquetes, token=TOKEN,
                               docker=docker_falso, registro=registro,
                               espera_cancelacion_s=0)
    aplicacion.config.update(TESTING=True)
    return aplicacion


@pytest.fixture
def cliente(app):
    return app.test_client()


def post(cliente, url, cuerpo=None, token=TOKEN):
    cabeceras = {"X-DC-Token": token} if token else {}
    return cliente.post(url, json=cuerpo or {}, headers=cabeceras)


class TestDescubrimiento:
    def test_encuentra_las_instalaciones(self, raiz, instalacion):
        nombres = [i.directorio.name for i in srv.descubrir_instalaciones(raiz)]
        assert instalacion.directorio.name in nombres

    def test_ignora_directorios_que_no_son_stacks(self, raiz):
        (raiz / "cualquier-cosa").mkdir()
        assert "cualquier-cosa" not in [i.directorio.name
                                        for i in srv.descubrir_instalaciones(raiz)]

    def test_raiz_inexistente_no_explota(self, tmp_path):
        assert srv.descubrir_instalaciones(tmp_path / "no-existe") == []

    def test_lista_los_paquetes(self, dir_paquetes):
        paquetes = srv.paquetes_disponibles(dir_paquetes)
        assert [p["release"] for p in paquetes] == ["4.7.0"]
        assert paquetes[0]["autoservicio"] is True

    def test_filtra_por_producto(self, dir_paquetes):
        assert srv.paquetes_disponibles(dir_paquetes, producto="otro") == []

    def test_ignora_directorios_sin_manifiesto(self, dir_paquetes):
        (dir_paquetes / "basura").mkdir()
        assert len(srv.paquetes_disponibles(dir_paquetes)) == 1


class TestSeguridad:
    def test_los_get_no_piden_token(self, cliente):
        assert cliente.get("/api/salud").status_code == 200

    def test_un_post_sin_token_se_rechaza(self, cliente, instalacion):
        r = post(cliente, f"/api/instalaciones/{instalacion.directorio.name}/preflight",
                 {"paquete": "paquete-4.7.0"}, token=None)
        assert r.status_code == 403

    def test_un_post_con_token_equivocado_se_rechaza(self, cliente, instalacion):
        r = post(cliente, f"/api/instalaciones/{instalacion.directorio.name}/rollback",
                 token="otro")
        assert r.status_code == 403

    def test_rechaza_un_host_ajeno(self, cliente):
        """Contra DNS rebinding: un dominio que resuelve a 127.0.0.1 no alcanza."""
        r = cliente.get("/api/salud", headers={"Host": "malicioso.example.com"})
        assert r.status_code == 403

    def test_acepta_localhost(self, cliente):
        assert cliente.get("/api/salud", headers={"Host": "localhost:9000"}).status_code == 200

    def test_no_se_puede_salir_del_directorio_de_paquetes(self, cliente, instalacion):
        r = post(cliente, f"/api/instalaciones/{instalacion.directorio.name}/preflight",
                 {"paquete": "../../etc"})
        assert r.status_code in (400, 404)

    def test_instalacion_inexistente(self, cliente):
        assert cliente.get("/api/instalaciones/fantasma").status_code == 404


class TestPagina:
    def test_se_renderiza_con_el_token(self, cliente):
        html = cliente.get("/").get_data(as_text=True)
        assert TOKEN in html
        assert "deploy" in html

    def test_no_carga_nada_de_internet(self, cliente):
        """El servidor del cliente no tiene salida: la página tiene que ser
        autosuficiente."""
        html = cliente.get("/").get_data(as_text=True)
        for prohibido in ("https://fonts.", "cdn.", "//unpkg", "//cdnjs"):
            assert prohibido not in html

    def test_avisa_cuando_esta_expuesta(self, raiz, dir_paquetes, docker_falso):
        app = srv.crear_app(raiz, dir_paquetes, token=TOKEN, host="0.0.0.0",
                            docker=docker_falso)
        html = app.test_client().get("/", headers={"Host": "0.0.0.0:9000"}).get_data(
            as_text=True)
        assert "no tiene login" in html


class TestListado:
    def test_devuelve_estado_y_version(self, cliente, instalacion):
        datos = cliente.get("/api/instalaciones").get_json()
        fila = next(i for i in datos["instalaciones"]
                    if i["nombre"] == instalacion.directorio.name)
        assert fila["version"] == "4.6.0"
        assert fila["estado"] == "ok"

    def test_detalle_trae_servicios_paquetes_e_historial(self, cliente, instalacion):
        instalacion.registrar("despliegue", "ok", desde="4.5.0", hacia="4.6.0")
        d = cliente.get(f"/api/instalaciones/{instalacion.directorio.name}").get_json()
        assert [s["servicio"] for s in d["servicios"]] == ["api", "web"]
        assert [p["release"] for p in d["paquetes"]] == ["4.7.0"]
        assert d["historial"][0]["resultado"] == "ok"

    def test_los_logs_salen_de_docker(self, cliente, instalacion):
        d = cliente.get(
            f"/api/instalaciones/{instalacion.directorio.name}/logs?servicio=api").get_json()
        assert "logs de prueba" in d["logs"]


class TestPreflight:
    def test_devuelve_202_y_el_informe(self, cliente, instalacion):
        r = post(cliente, f"/api/instalaciones/{instalacion.directorio.name}/preflight",
                 {"paquete": "paquete-4.7.0"})
        assert r.status_code == 202
        op = r.get_json()
        assert op["resultado"]["ok"] is True

    def test_no_toca_el_compose(self, cliente, instalacion):
        antes = instalacion.ruta_compose.read_text(encoding="utf-8")
        post(cliente, f"/api/instalaciones/{instalacion.directorio.name}/preflight",
             {"paquete": "paquete-4.7.0"})
        assert instalacion.ruta_compose.read_text(encoding="utf-8") == antes


class TestDespliegue:
    def test_camino_feliz(self, cliente, instalacion, docker_falso, base):
        respuestas = {hc["url"]: (hc["espera"], "") for hc in base["healthchecks"]}
        r = post(cliente, f"/api/instalaciones/{instalacion.directorio.name}/desplegar",
                 {"paquete": "paquete-4.7.0"})
        assert r.status_code == 202
        assert instalacion.version_instalada() == "4.7.0"
        assert respuestas  # el smoke queda omitido: el agente no alcanza la red

    def test_una_sola_operacion_por_instalacion(self, cliente, instalacion, registro):
        registro.crear("despliegue", instalacion.directorio.name)  # queda corriendo
        r = post(cliente, f"/api/instalaciones/{instalacion.directorio.name}/desplegar",
                 {"paquete": "paquete-4.7.0"})
        assert r.status_code == 409

    def test_el_fallo_deja_la_operacion_con_su_resultado(self, cliente, instalacion,
                                                         docker_falso):
        docker_falso.salud_tras_up = [False, True]
        r = post(cliente, f"/api/instalaciones/{instalacion.directorio.name}/desplegar",
                 {"paquete": "paquete-4.7.0"})
        op = r.get_json()
        assert op["resultado"]["estado"] == "revertido"
        assert [e["evento"] for e in op["eventos"]].count("despliegue_falla") == 1


class TestRollback:
    def test_sin_punto_de_retorno_devuelve_400(self, cliente, instalacion):
        r = post(cliente, f"/api/instalaciones/{instalacion.directorio.name}/rollback")
        assert r.status_code == 400

    def test_con_punto_de_retorno_vuelve(self, cliente, instalacion, manifiesto_previo):
        post(cliente, f"/api/instalaciones/{instalacion.directorio.name}/desplegar",
             {"paquete": "paquete-4.7.0"})
        assert instalacion.version_instalada() == "4.7.0"

        r = post(cliente, f"/api/instalaciones/{instalacion.directorio.name}/rollback")
        assert r.status_code == 202
        assert instalacion.version_instalada() == "4.6.0"


class TestOperaciones:
    def test_se_puede_consultar_por_id(self, cliente, instalacion):
        r = post(cliente, f"/api/instalaciones/{instalacion.directorio.name}/preflight",
                 {"paquete": "paquete-4.7.0"})
        id_op = r.get_json()["id"]
        assert cliente.get(f"/api/operaciones/{id_op}").get_json()["id"] == id_op

    def test_id_desconocido(self, cliente):
        assert cliente.get("/api/operaciones/nada").status_code == 404

    def test_no_se_cancela_lo_que_no_es_cancelable(self, cliente, instalacion, registro):
        op = registro.crear("despliegue", instalacion.directorio.name)
        assert post(cliente, f"/api/operaciones/{op.id}/cancelar").status_code == 409


class TestRegistroDeOperaciones:
    def test_la_cuenta_regresiva_hace_cancelable(self):
        import time
        op = Operacion("despliegue", "mep")
        assert not op.cancelable
        op.cancelable_hasta = time.time() + 30
        assert op.cancelable
        assert 0 < op.segundos_restantes <= 30

    def test_vencida_deja_de_ser_cancelable(self):
        import time
        op = Operacion("despliegue", "mep")
        op.cancelable_hasta = time.time() - 1
        assert not op.cancelable
        assert op.segundos_restantes == 0

    def test_cancelar_marca_la_bandera(self):
        op = Operacion("despliegue", "mep")
        assert not op.cancelado()
        op.cancelar()
        assert op.cancelado()

    def test_cerrar_con_error(self):
        op = Operacion("despliegue", "mep")
        op.cerrar(error="explotó")
        assert op.estado == "fallida"
        assert op.cancelable_hasta is None

    def test_una_excepcion_no_mata_el_hilo(self, registro):
        op = registro.crear("despliegue", "mep")
        registro.ejecutar(op, lambda o: 1 / 0)
        assert op.estado == "fallida"
        assert "ZeroDivisionError" in op.error

    def test_solo_guarda_las_ultimas(self):
        r = Registro(maximo=3, lanzar_hilo=False)
        ids = [r.crear("x", "mep").id for _ in range(5)]
        assert r.obtener(ids[0]) is None
        assert r.obtener(ids[-1]) is not None

    def test_activa_de_solo_mira_esa_instalacion(self, registro):
        registro.crear("despliegue", "mep")
        assert registro.activa_de("mep").estado == CORRIENDO
        assert registro.activa_de("repi") is None

    def test_a_dict_es_serializable(self, registro):
        op = registro.crear("despliegue", "mep")
        op.registrar("despliegue_iniciado", {"hacia": "4.7.0"})
        assert json.loads(json.dumps(op.a_dict()))["tipo"] == "despliegue"


class TestConHilo:
    """El camino real: la operación corre en un hilo y la UI la sigue por polling."""

    def test_la_operacion_termina_en_su_hilo(self, raiz, dir_paquetes, docker_falso,
                                             instalacion):
        app = srv.crear_app(raiz, dir_paquetes, token=TOKEN, docker=docker_falso,
                            registro=Registro())
        cliente = app.test_client()
        r = post(cliente, f"/api/instalaciones/{instalacion.directorio.name}/preflight",
                 {"paquete": "paquete-4.7.0"})
        id_op = r.get_json()["id"]

        import time
        for _ in range(100):
            estado = cliente.get(f"/api/operaciones/{id_op}").get_json()
            if estado["estado"] != CORRIENDO:
                break
            time.sleep(0.05)
        assert estado["estado"] == "terminada"
