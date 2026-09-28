"""El estándar de MEP: el núcleo (productos/mep) y el stack de un banco
(productos/mep-naranjax), renderizados con los .env de ejemplo.

Lo que se prueba es lo que hace que la adopción sea segura: los contenedores
conservan su nombre, sus redes y sus datos; cada servicio recibe solo sus
secretos; y nada del cliente queda escrito en el compose.
"""

import shutil
import subprocess

import pytest
import yaml

from deploycenter import compose
from deploycenter import manifiesto as mf
from deploycenter import variables as vars_

VERSION = "2026.10.0"


def _cargar(raiz, producto):
    m = mf.cargar(raiz / "productos" / producto / "borradores" / VERSION / "manifiesto.json")
    plantilla = raiz / "productos" / producto / "compose.plantilla.yaml"
    return m, plantilla


def _entorno(raiz, archivo):
    return vars_.leer_env(raiz / "ejemplos" / "cliente-demo" / archivo)


@pytest.fixture
def nucleo(raiz):
    m, plantilla = _cargar(raiz, "mep")
    entorno = _entorno(raiz, ".env.ejemplo")
    texto = compose.render_desde_archivos(plantilla, m, entorno=entorno)
    return m, entorno, texto, yaml.safe_load(texto)


@pytest.fixture
def banco(raiz):
    m, plantilla = _cargar(raiz, "mep-naranjax")
    entorno = _entorno(raiz, ".env.banco.ejemplo")
    texto = compose.render_desde_archivos(plantilla, m, entorno=entorno)
    return m, entorno, texto, yaml.safe_load(texto)


def _servicio_con(datos, variable):
    """Servicios cuyo environment referencia ${variable}."""
    return sorted(n for n, s in datos["services"].items()
                  if f"${{{variable}}}" in yaml.safe_dump(s.get("environment") or {}))


class TestNucleo:
    def test_servicios_y_contenedores_de_siempre(self, nucleo):
        _, _, _, datos = nucleo
        servicios = datos["services"]
        assert set(servicios) == {"mep-redis", "mep-rabbitmq", "mep-api", "mep-worker",
                                  "mep-bcra", "mep-app"}
        # mismo container_name que las instalaciones hechas a mano: el primer
        # despliegue estándar los reemplaza en vez de chocar con ellos
        assert all(s["container_name"] == n for n, s in servicios.items())
        # sin `name:` el proyecto sigue siendo el directorio, como hoy
        assert "name" not in datos

    def test_connector_y_contable_no_estan(self, nucleo):
        _, _, _, datos = nucleo
        assert not {"mep-connector", "mep-contable", "mep-contable-db"} & set(datos["services"])

    def test_redes_externas_de_uniweb(self, nucleo):
        _, _, _, datos = nucleo
        assert datos["networks"] == {
            "uw2-backend": {"name": "uw2-backend", "external": True},
            "uw2-frontend": {"name": "uw2-frontend", "external": True},
        }
        assert datos["services"]["mep-app"]["networks"] == ["uw2-frontend"]

    def test_los_datos_quedan_donde_estaban(self, nucleo):
        _, _, _, datos = nucleo
        assert "${MEP_DATOS:-../../data/apps/mep}/rabbitmq:/var/lib/rabbitmq" in \
            datos["services"]["mep-rabbitmq"]["volumes"]

    def test_cada_secreto_llega_solo_a_quien_lo_usa(self, nucleo):
        _, _, _, datos = nucleo
        assert _servicio_con(datos, "BCRA_PASS") == ["mep-bcra"]
        # la clave de la base va dentro de la cadena de conexión de la api
        api = yaml.safe_dump(datos["services"]["mep-api"]["environment"])
        assert "${CS_PASS}" in api and "${CS_LOG_PASS}" in api
        for otro in ("mep-app", "mep-worker", "mep-bcra"):
            assert "CS_PASS" not in yaml.safe_dump(datos["services"][otro])
        assert not any("env_file" in s for s in datos["services"].values())

    def test_ningun_valor_del_cliente_queda_escrito(self, nucleo):
        _, entorno, texto, _ = nucleo
        for nombre in ("BCRA_PASS", "CS_PASS", "CS_SERVER", "IDP_SERVER_URL",
                       "BCRA_IP_HOMOLOGACION"):
            assert entorno[nombre] not in texto, nombre

    def test_el_front_muestra_la_version_de_cada_servicio(self, nucleo):
        m, _, _, datos = nucleo
        env = datos["services"]["mep-app"]["environment"]
        assert env["MEPAPIVERSION"] == "1.1.36"
        assert env["MEPAPPVERSION"] == "1.1.35"
        assert env["MEPWORKERVERSION"] == "1.0.16"
        assert env["MEPBCRAVERSION"] == "1.0.30"

    def test_la_api_espera_a_redis_y_rabbit(self, nucleo):
        _, _, _, datos = nucleo
        dep = datos["services"]["mep-api"]["depends_on"]
        assert dep == {"mep-redis": {"condition": "service_healthy"},
                       "mep-rabbitmq": {"condition": "service_healthy"}}

    def test_extra_hosts_solo_si_el_banco_lo_necesita(self, raiz, nucleo):
        m, entorno, _, datos = nucleo
        assert datos["services"]["mep-bcra"]["extra_hosts"] == [
            "serviciosmep.homologacion.bcra.sfa:${BCRA_IP_HOMOLOGACION}"]

        _, plantilla = _cargar(raiz, "mep")
        sin_ips = {k: v for k, v in entorno.items() if not k.startswith("BCRA_IP_")}
        otro = yaml.safe_load(compose.render_desde_archivos(plantilla, m, entorno=sin_ips))
        assert "extra_hosts" not in otro["services"]["mep-bcra"]

    def test_bcra_endpoint_es_obligatoria(self, nucleo):
        m, entorno, _, _ = nucleo
        sin = {k: v for k, v in entorno.items() if k != "BCRA_ENDPOINT"}
        assert [v["nombre"] for v in vars_.faltantes(m, sin)] == ["BCRA_ENDPOINT"]
        assert vars_.faltantes(m, entorno) == []

    def test_la_base_se_comprueba_antes_de_desplegar(self, nucleo):
        m, entorno, _, _ = nucleo
        base = next(d for d in m["dependencias"] if d["nombre"] == "base")
        assert mf.resolver_dependencia(base, entorno) == ("sql.demo.invalid", "1433")


class TestBanco:
    def test_contenedores_de_siempre_con_repos_del_banco(self, banco):
        m, _, _, datos = banco
        assert {n: s["container_name"] for n, s in datos["services"].items()} == {
            "mep-connector": "mep-connector",
            "mep-contable": "mep-contable",
            "mep-contable-db": "mep-contable-db",
        }
        assert m["imagenes"]["mep-connector"].startswith(
            "docker.io/accusystechnology/mep-connector-naranjax:")
        assert m["imagenes"]["mep-contable-db"].startswith(
            "docker.io/accusystechnology/mep-contable:")

    def test_misma_red_que_el_nucleo(self, banco):
        _, _, _, datos = banco
        assert datos["networks"] == {"uw2-backend": {"name": "uw2-backend", "external": True}}

    def test_el_secreto_de_dynamics_sale_del_compose(self, banco):
        m, entorno, texto, datos = banco
        assert entorno["CONTABLE_CLIENT_SECRET"] not in texto
        assert _servicio_con(datos, "CONTABLE_CLIENT_SECRET") == ["mep-contable"]
        secretas = {v["nombre"] for v in m["variables_nuevas"] if v.get("secreta")}
        assert {"CONTABLE_CLIENT_SECRET", "CS_PASS", "CLIENT_SECRET_1"} <= secretas

    def test_el_env_de_ejemplo_alcanza(self, banco):
        m, entorno, _, _ = banco
        assert vars_.faltantes(m, entorno) == []

    def test_falta_una_del_connector_y_frena(self, banco):
        m, entorno, _, _ = banco
        sin = {k: v for k, v in entorno.items() if k != "URL_DEBITOS"}
        assert [v["nombre"] for v in vars_.faltantes(m, sin)] == ["URL_DEBITOS"]


@pytest.mark.skipif(shutil.which("docker") is None, reason="sin docker compose")
@pytest.mark.parametrize("producto,archivo", [("mep", ".env.ejemplo"),
                                              ("mep-naranjax", ".env.banco.ejemplo")])
def test_docker_compose_acepta_lo_generado(raiz, tmp_path, producto, archivo):
    m, plantilla = _cargar(raiz, producto)
    (tmp_path / ".env").write_text(
        (raiz / "ejemplos" / "cliente-demo" / archivo).read_text(encoding="utf-8"),
        encoding="utf-8")
    compose.generar(plantilla, m, entorno=_entorno(raiz, archivo),
                    salida=tmp_path / "docker-compose.yml", exigir_pin=False)
    r = subprocess.run(["docker", "compose", "config", "--quiet"], cwd=tmp_path,
                       capture_output=True, text=True, check=False)
    if r.returncode != 0 and "Cannot connect" in r.stderr:  # pragma: no cover
        pytest.skip("docker compose no disponible")
    assert r.returncode == 0, r.stderr
