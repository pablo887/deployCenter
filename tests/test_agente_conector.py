"""El agente conectado al hub, de punta a punta.

El agente real habla con la API real del hub por un transporte en memoria, y
despliega sobre el doble de Docker. Lo que se prueba es el circuito completo:
enrolamiento, latido, orden, ejecución, eventos, resultado — y lo que pasa cuando
el hub manda algo que no cierra o cuando la red se corta.
"""

import json

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from deploycenter.agente import conector as con  # noqa: E402
from deploycenter.hub.api import crear_app  # noqa: E402

URL = "http://localhost"


class Red:
    """Transporte que va a la app del hub, con un interruptor para cortarla."""

    def __init__(self, cliente_http):
        self.http = cliente_http
        self.cortada = False
        self.pedidos = []

    def __call__(self, metodo, url, cuerpo, cabeceras, timeout):
        self.pedidos.append((metodo, url.replace(URL, "")))
        if self.cortada:
            raise con.ErrorConexion("red cortada")
        r = self.http.request(metodo, url.replace(URL, ""), json=cuerpo, headers=cabeceras)
        return r.status_code, (r.json() if r.content else None)


def healthchecks_ok(base):
    respuestas = {hc["url"]: (hc["espera"], "") for hc in base["healthchecks"]}
    return lambda url, timeout: respuestas.get(url, (None, "sin ruta"))


@pytest.fixture
def red(hub):
    return Red(TestClient(crear_app(hub, intervalo_poll_s=0)))


@pytest.fixture
def credenciales(hub, red, tmp_path):
    codigo = hub.emitir_codigo("andino")["codigo"]
    datos = con.ClienteHub(URL, transporte=red).enrolar(codigo, "srv-dock-01")
    ruta = con.Credenciales(tmp_path / "trabajo" / "credenciales.json")
    ruta.guardar(URL, datos["agente_id"], datos["token"], datos["tenant"])
    return datos


@pytest.fixture
def armar_conector(red, credenciales, instalacion, docker_falso, reloj, tmp_path, base):
    def _hacer(**kw):
        kw.setdefault("exigir_firma", False)
        kw.setdefault("cliente_http", healthchecks_ok(base))
        return con.Conector(
            con.ClienteHub(URL, token=credenciales["token"], transporte=red),
            instalacion.directorio.parent, tmp_path / "trabajo", docker=docker_falso,
            espera_poll_s=0, dormir=reloj.dormir, reloj=reloj, **kw)
    return _hacer


@pytest.fixture
def conector(armar_conector):
    return armar_conector()


def ordenar(hub, credenciales, tipo="desplegar", release="4.7.0", instalacion="mep"):
    return hub.crear_orden(credenciales["agente_id"], instalacion, tipo, release, "Martín Ríos")


class TestEnrolamiento:
    def test_las_credenciales_quedan_solo_para_el_usuario(self, credenciales, tmp_path):
        ruta = tmp_path / "trabajo" / "credenciales.json"
        assert oct(ruta.stat().st_mode & 0o777) == "0o600"
        assert json.loads(ruta.read_text())["token"] == credenciales["token"]

    def test_http_solo_contra_localhost(self):
        with pytest.raises(con.ErrorDeployCenter, match="https"):
            con.ClienteHub("http://deploy.accusys.com.ar")
        con.ClienteHub("https://deploy.accusys.com.ar")
        con.ClienteHub("http://127.0.0.1:8000")


class TestLatido:
    def test_informa_lo_que_corre_en_el_host(self, conector, hub):
        conector.ciclo(espera=0)
        inst = hub.parque()[0]["instalaciones"]
        assert [(i["nombre"], i["producto"], i["version"]) for i in inst] == [
            ("mep", "mep", "4.6.0")]


class TestDespliegueDesdeElHub:
    def test_despliega_verifica_y_reporta(self, conector, hub, credenciales, instalacion):
        conector.latido()
        orden = ordenar(hub, credenciales)
        conector.ciclo(espera=0)

        assert instalacion.version_instalada() == "4.7.0"
        final = hub.orden(orden["id"])
        assert (final["estado"], final["resultado"]) == ("terminada", "ok")
        eventos = [e["evento"] for e in final["eventos"]]
        assert eventos[0] == "orden_recibida"
        assert "despliegue_iniciado" in eventos and "despliegue_ok" in eventos
        # el latido posterior ya informa la versión nueva y el punto de retorno
        inst = hub.parque()[0]["instalaciones"][0]
        assert (inst["version"], inst["punto_retorno"]) == ("4.7.0", "4.6.0")

    def test_preflight_no_toca_el_stack(self, conector, hub, credenciales, instalacion,
                                        docker_falso):
        conector.latido()
        orden = ordenar(hub, credenciales, tipo="preflight")
        conector.ciclo(espera=0)

        assert instalacion.version_instalada() == "4.6.0"
        assert docker_falso.veces("up") == 0
        final = hub.orden(orden["id"])
        assert final["resultado"] == "ok"
        assert final["resumen"]["preflight"]["ok"] is True
        assert not instalacion.bloqueada()

    def test_falla_la_verificacion_y_vuelve_atras(self, armar_conector, hub, credenciales,
                                                  instalacion, docker_falso):
        # la versión nueva no levanta; la anterior, restaurada, sí
        docker_falso.salud_tras_up = [False, True]
        conector = armar_conector(cliente_http=lambda url, t: (None, "sin red"))
        conector.latido()
        orden = ordenar(hub, credenciales)
        conector.ciclo(espera=0)

        final = hub.orden(orden["id"])
        assert final["resultado"] == "revertido"
        assert instalacion.version_instalada() == "4.6.0"
        eventos = [e["evento"] for e in final["eventos"]]
        # el aviso sale antes que el rollback
        assert eventos.index("despliegue_falla") < eventos.index("rollback_ok")

    def test_la_web_frena_el_rollback(self, armar_conector, hub, credenciales, instalacion,
                                      docker_falso):
        docker_falso.salud_tras_up = [False, True]
        conector = armar_conector(cliente_http=lambda url, t: (None, "sin red"))
        conector.latido()
        orden = ordenar(hub, credenciales)

        # el operador aprieta "cancelar rollback" apenas llega el aviso de falla
        evento_original = conector._evento

        def espiar(orden_id, evento, **datos):
            evento_original(orden_id, evento, **datos)
            if evento == "despliegue_falla":
                hub.pedir_cancelacion_rollback(orden_id)

        conector._evento = espiar
        conector.ciclo(espera=0)

        final = hub.orden(orden["id"])
        assert final["resultado"] == "degradado"
        assert "canceló" in final["detalle"]
        assert instalacion.version_instalada() == "4.6.0"  # no se revirtió ni se marcó 4.7.0
        assert instalacion.estado()["estado"] == "degradado"

    def test_rollback_ordenado_desde_el_hub(self, conector, hub, credenciales, instalacion):
        conector.latido()
        ordenar(hub, credenciales)
        conector.ciclo(espera=0)
        assert instalacion.version_instalada() == "4.7.0"

        orden = ordenar(hub, credenciales, tipo="rollback", release=None)
        conector.ciclo(espera=0)
        assert hub.orden(orden["id"])["resultado"] == "revertido"
        assert instalacion.version_instalada() == "4.6.0"


class TestElAgenteNoConfiaEnElHub:
    """Un hub comprometido puede encolar órdenes, no hacer desplegar cualquier cosa."""

    def _con_paquete_alterado(self, hub, monkeypatch, **cambios):
        original = hub.catalogo.paquete

        def alterado(producto, version):
            p = original(producto, version)
            if "plantilla" in cambios:
                p["plantilla"] = cambios["plantilla"]
            if "firma" in cambios:
                p["firma"] = cambios["firma"]
            if "release" in cambios:
                p["manifiesto"] = dict(p["manifiesto"], release=cambios["release"])
            return p

        monkeypatch.setattr(hub.catalogo, "paquete", alterado)

    def test_sin_clave_publica_no_despliega(self, armar_conector, hub, credenciales,
                                            instalacion, docker_falso):
        conector = armar_conector(exigir_firma=True, clave_publica=None)
        conector.latido()
        orden = ordenar(hub, credenciales)
        conector.ciclo(espera=0)
        final = hub.orden(orden["id"])
        assert final["resultado"] == "rechazada"
        assert "clave pública" in final["detalle"]
        assert docker_falso.veces("up") == 0

    def test_con_firma_valida_despliega(self, armar_conector, hub, credenciales,
                                        instalacion, firma_falsa, tmp_path):
        clave = tmp_path / "cosign.pub"
        clave.write_text("clave")
        conector = armar_conector(exigir_firma=True, clave_publica=str(clave))
        conector.latido()
        orden = ordenar(hub, credenciales)
        conector.ciclo(espera=0)
        assert hub.orden(orden["id"])["resultado"] == "ok", hub.orden(orden["id"])["detalle"]

    def test_con_firma_falsa_no_despliega(self, armar_conector, hub, credenciales,
                                          instalacion, firma_falsa, tmp_path, monkeypatch,
                                          docker_falso):
        import base64
        self._con_paquete_alterado(hub, monkeypatch,
                                   firma=base64.b64encode(b"otra firma").decode())
        clave = tmp_path / "cosign.pub"
        clave.write_text("clave")
        conector = armar_conector(exigir_firma=True, clave_publica=str(clave))
        conector.latido()
        orden = ordenar(hub, credenciales)
        conector.ciclo(espera=0)
        final = hub.orden(orden["id"])
        assert final["resultado"] == "abortado"
        assert "firma" in final["detalle"]
        assert docker_falso.veces("up") == 0
        assert instalacion.version_instalada() == "4.6.0"

    def test_plantilla_alterada(self, armar_conector, hub, credenciales, instalacion,
                                monkeypatch, docker_falso):
        maliciosa = "services:\n  api:\n    image: x\n    privileged: true\n"
        self._con_paquete_alterado(hub, monkeypatch, plantilla=maliciosa)
        conector = armar_conector()
        conector.latido()
        orden = ordenar(hub, credenciales)
        conector.ciclo(espera=0)
        final = hub.orden(orden["id"])
        assert final["resultado"] == "rechazada"
        assert "huella" in final["detalle"]
        assert docker_falso.veces("up") == 0

    def test_paquete_que_no_corresponde_a_la_orden(self, armar_conector, hub, credenciales,
                                                   instalacion, monkeypatch):
        self._con_paquete_alterado(hub, monkeypatch, release="9.9.9")
        conector = armar_conector()
        conector.latido()
        orden = ordenar(hub, credenciales)
        conector.ciclo(espera=0)
        assert hub.orden(orden["id"])["resultado"] == "rechazada"

    @pytest.mark.parametrize("nombre", ["../etc", "/etc", "MEP", "..", "a/b", ""])
    def test_no_sale_de_la_raiz(self, conector, nombre):
        with pytest.raises(con.OrdenRechazada):
            conector._instalacion(nombre)

    def test_un_detalle_largo_llega_recortado(self, conector, hub, credenciales,
                                              monkeypatch):
        """Un 422 por tamaño mandaría el resultado a rechazados: el hub no se enteraría."""
        conector.latido()
        orden = ordenar(hub, credenciales, tipo="preflight")
        monkeypatch.setattr(con.Conector, "_ejecutar",
                            lambda self, o: {"resultado": "error", "detalle": "x" * 10000})
        conector.ciclo(espera=0)
        final = hub.orden(orden["id"])
        assert final["resultado"] == "error"
        assert len(final["detalle"]) <= 4000

    def test_tipo_fuera_del_catalogo(self, conector):
        r = conector.ejecutar({"id": "ord-abc123", "tipo": "shell", "instalacion": "mep"})
        assert r["resultado"] == "rechazada"


class TestRedCortada:
    def test_el_resultado_espera_en_el_buzon(self, conector, hub, credenciales, red,
                                             instalacion):
        conector.latido()
        orden = ordenar(hub, credenciales)
        pedido = conector.cliente.siguiente_orden(0)

        red.cortada = True
        conector.ejecutar(pedido)            # despliega igual: ya tiene la orden
        assert instalacion.version_instalada() == "4.7.0"
        assert hub.orden(orden["id"])["estado"] == "entregada"
        assert conector.buzon.pendientes()

        red.cortada = False
        conector.ciclo(espera=0)             # al volver la red, el buzón se vacía
        final = hub.orden(orden["id"])
        assert final["resultado"] == "ok"
        assert "despliegue_ok" in [e["evento"] for e in final["eventos"]]
        assert conector.buzon.pendientes() == []

    def test_sin_red_no_se_reintenta_en_cada_evento(self, conector, red):
        red.cortada = True
        for n in range(5):
            conector._evento("ord-x1", f"evento{n}")
        intentos = [p for p in red.pedidos if "/eventos" in p[1]]
        assert len(intentos) == 1
        assert len(conector.buzon.pendientes()) == 5

    def test_el_loop_espera_cada_vez_mas(self, conector, red, reloj):
        red.cortada = True
        vueltas = {"n": 0}

        def parar():
            vueltas["n"] += 1
            return vueltas["n"] > 4

        antes = reloj.t
        conector.correr(parar=parar)
        assert reloj.t - antes >= 1 + 2 + 4 + 8

    def test_token_revocado_detiene_el_agente(self, conector, hub, credenciales):
        hub.revocar_agente(credenciales["agente_id"])
        with pytest.raises(con.CredencialesInvalidas):
            conector.correr()

    def test_un_mensaje_rechazado_no_traba_la_cola(self, conector, hub, credenciales):
        conector.buzon.depositar("resultado", "ord-inexistente", {"resultado": "ok"})
        conector.buzon.depositar("eventos", "ord-inexistente2", {"eventos": [{"evento": "x"}]})
        conector.ciclo(espera=0)
        assert conector.buzon.pendientes() == []
        assert len(list(conector.buzon.dir_rechazados.iterdir())) == 2

    def test_un_mensaje_corrupto_no_traba_la_cola(self, conector):
        conector.buzon.dir.mkdir(parents=True, exist_ok=True)
        (conector.buzon.dir / "00000000000000000001-000001-eventos.json").write_text("{roto")
        conector.ciclo(espera=0)
        assert conector.buzon.pendientes() == []


class TestLimpieza:
    def test_conserva_solo_los_ultimos_paquetes(self, conector, tmp_path):
        carpeta = tmp_path / "trabajo" / "paquetes"
        for n in range(con.PAQUETES_CONSERVADOS + 5):
            (carpeta / f"ord-{n:03d}").mkdir(parents=True)
        conector._limpiar_paquetes()
        assert len(list(carpeta.iterdir())) == con.PAQUETES_CONSERVADOS

