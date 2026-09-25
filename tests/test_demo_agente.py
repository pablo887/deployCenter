"""La demo del agente (ejemplos/demo-agente): su catálogo cumple las mismas reglas
que el real, y el circuito que se ve en la web funciona de punta a punta.

El agente real habla con el hub real por HTTP en memoria y despliega sobre el
doble de Docker, con la instalación preparada igual que la prepara agente.sh.
"""

import json

import pytest
import yaml

from deploycenter import compose
from deploycenter import manifiesto as mf

pytest.importorskip("fastapi")

from conftest import RAIZ, DockerFalso, Reloj  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from test_agente_conector import URL, Red  # noqa: E402

from deploycenter.agente import conector as con  # noqa: E402
from deploycenter.hub.api import crear_app  # noqa: E402
from deploycenter.hub.catalogo import Catalogo  # noqa: E402
from deploycenter.hub.servicio import Hub  # noqa: E402

DEMO = RAIZ / "ejemplos" / "demo-agente" / "catalogo"
VERSIONES = ["1.0.0", "2.0.0", "3.0.0"]


def _manifiesto(version):
    return mf.cargar(DEMO / "productos" / "demo" / "releases" / version / "manifiesto.json")


class TestCatalogoDeLaDemo:
    @pytest.mark.parametrize("version", VERSIONES)
    def test_es_valido_y_esta_pinneado(self, version):
        assert mf.validar(_manifiesto(version), raiz=DEMO, exigir_pin=True) == []

    @pytest.mark.parametrize("version", VERSIONES)
    def test_la_plantilla_renderiza_y_la_huella_coincide(self, version):
        plantilla = DEMO / "productos" / "demo" / "compose.plantilla.yaml"
        m = _manifiesto(version)
        texto = compose.render_desde_archivos(plantilla, m, entorno={"DEMO_PUERTO": "18080"})
        assert compose.problemas_del_compose(texto, manifiesto=m) == []
        assert f"demo {version}" in texto
        assert m["plantilla_sha256"] == compose.huella(plantilla.read_bytes())

    def test_el_producto_declara_healthcheck(self):
        datos = yaml.safe_load((DEMO / "productos" / "demo" / "producto.yaml").read_text())
        assert datos["codigo"] == "demo" and datos["servicios"]["web"]["healthcheck"]

    def test_no_esta_en_el_catalogo_real(self):
        assert "demo" not in Catalogo(RAIZ).productos()

    def test_se_suma_como_catalogo_extra(self):
        c = Catalogo(RAIZ, extras=[DEMO])
        assert {"mep", "demo"} <= set(c.productos())
        paquete = c.paquete("demo", "2.0.0")
        assert paquete["manifiesto"]["release"] == "2.0.0"
        assert "Demo 2.0.0" in paquete["changelog"]
        assert c.novedades("demo", "3.0.0")[0].startswith("**Release roto")
        assert c.release("mep", "4.7.0") is not None


@pytest.fixture
def hub(tmp_path):
    h = Hub.desde_url(f"sqlite:///{tmp_path / 'hub.db'}", Catalogo(RAIZ, extras=[DEMO]))
    h.crear_tenant("andino", "Banco Andino")
    h.adquirir("andino", "demo", "2099-12-31")
    return h


@pytest.fixture
def instalacion(tmp_path):
    """Lo mismo que hace agente.sh la primera vez."""
    d = tmp_path / "stacks" / "demo"
    (d / ".deploycenter").mkdir(parents=True)
    (d / ".deploycenter" / "estado.json").write_text(json.dumps({"producto": "demo"}))
    (d / ".env").write_text("DEMO_PUERTO=18080\n")
    return d


@pytest.fixture
def docker():
    return DockerFalso(servicios=("web",))


@pytest.fixture
def agente(hub, instalacion, docker, tmp_path):
    red = Red(TestClient(crear_app(hub, intervalo_poll_s=0)))
    codigo = hub.emitir_codigo("andino", host="demo-01")["codigo"]
    datos = con.ClienteHub(URL, transporte=red).enrolar(codigo, "demo-01")
    reloj = Reloj()
    conector = con.Conector(
        con.ClienteHub(URL, token=datos["token"], transporte=red), instalacion.parent,
        tmp_path / "trabajo", docker=docker, exigir_firma=False, espera_poll_s=0,
        espera_cancelacion_s=1, dormir=reloj.dormir, reloj=reloj,
        cliente_http=lambda url, timeout: (None, "sin ruta desde el test"))
    conector.latido()
    return conector, datos["agente_id"]


def ordenar_y_correr(hub, agente, tipo, release=None):
    conector, agente_id = agente
    orden = hub.crear_orden(agente_id, "demo", tipo, release, "Operador de prueba")
    conector.ciclo(espera=0)
    return hub.orden(orden["id"])


def instalada(hub):
    return hub.parque()[0]["instalaciones"][0]


class TestCircuitoDeLaDemo:
    def test_el_hub_ve_la_instalacion_sin_version(self, hub, agente):
        i = instalada(hub)
        assert (i["nombre"], i["producto"], i["version"]) == ("demo", "demo", None)

    def test_instalacion_upgrade_y_vuelta_atras(self, hub, agente, docker):
        preflight = ordenar_y_correr(hub, agente, "preflight", "1.0.0")
        assert preflight["resultado"] == "ok", preflight["detalle"]

        assert ordenar_y_correr(hub, agente, "desplegar", "1.0.0")["resultado"] == "ok"
        assert instalada(hub)["version"] == "1.0.0"

        segunda = ordenar_y_correr(hub, agente, "desplegar", "2.0.0")
        assert segunda["resultado"] == "ok"
        assert (instalada(hub)["version"], instalada(hub)["punto_retorno"]) == ("2.0.0", "1.0.0")

        # 3.0.0 no levanta; el stack anterior sí
        docker.salud_tras_up = [False, True]
        rota = ordenar_y_correr(hub, agente, "desplegar", "3.0.0")
        assert rota["resultado"] == "revertido", rota["detalle"]
        assert [e["evento"] for e in rota["eventos"]][-2:] == ["despliegue_falla", "rollback_ok"]
        assert instalada(hub)["version"] == "2.0.0"

    def test_la_ruta_de_upgrade_la_frena_el_hub(self, hub, agente):
        from deploycenter.hub.servicio import Rechazado

        ordenar_y_correr(hub, agente, "desplegar", "1.0.0")
        with pytest.raises(Rechazado, match="admite >=2.0.0"):
            hub.crear_orden(agente[1], "demo", "desplegar", "3.0.0", "Operador de prueba")
