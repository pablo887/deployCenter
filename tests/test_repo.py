"""Tests sobre el contenido del repo, no sobre el código.

Esto es lo que corre el pipeline en cada push: que todo manifiesto versionado sea
válido, esté pinneado y su plantilla renderice. Un release que no pasa por acá no
se publica.
"""

import pytest
import yaml

from deploycenter import compose
from deploycenter import manifiesto as mf


def descubrir_productos(raiz):
    return sorted((raiz / "productos").glob("*/producto.yaml"))


def descubrir_releases(raiz):
    return sorted((raiz / "productos").glob("*/releases/*/manifiesto.json"))


def test_hay_al_menos_un_producto(raiz):
    assert descubrir_productos(raiz), "no hay ningún producto en productos/"


def test_hay_al_menos_un_release(raiz):
    assert descubrir_releases(raiz), "no hay ningún release versionado"


def _ids_productos():
    from conftest import RAIZ
    return [p.parent.name for p in descubrir_productos(RAIZ)]


def _ids_releases():
    from conftest import RAIZ
    return [f"{r.parents[2].name}/{r.parent.name}" for r in descubrir_releases(RAIZ)]


@pytest.mark.parametrize("nombre", _ids_productos())
class TestProducto:
    def test_el_codigo_coincide_con_la_carpeta(self, raiz, nombre):
        datos = yaml.safe_load((raiz / "productos" / nombre / "producto.yaml").read_text("utf-8"))
        assert datos["codigo"] == nombre

    def test_declara_servicios_con_healthcheck(self, raiz, nombre):
        datos = yaml.safe_load((raiz / "productos" / nombre / "producto.yaml").read_text("utf-8"))
        servicios = datos.get("servicios") or {}
        assert servicios, f"{nombre} no declara servicios"
        for servicio, cfg in servicios.items():
            assert (cfg or {}).get("healthcheck"), f"{nombre}/{servicio} no declara healthcheck"

    def test_la_plantilla_existe(self, raiz, nombre):
        datos = yaml.safe_load((raiz / "productos" / nombre / "producto.yaml").read_text("utf-8"))
        plantilla = raiz / "productos" / nombre / datos.get("plantilla", "compose.plantilla.yaml")
        assert plantilla.is_file(), f"falta la plantilla de {nombre}"


@pytest.mark.parametrize("ruta_rel", _ids_releases())
class TestRelease:
    def _manifiesto(self, raiz, ruta_rel):
        producto, version = ruta_rel.split("/")
        ruta = raiz / "productos" / producto / "releases" / version / "manifiesto.json"
        return mf.cargar(ruta), ruta

    def test_es_valido_y_esta_pinneado(self, raiz, ruta_rel):
        m, ruta = self._manifiesto(raiz, ruta_rel)
        problemas = mf.validar(m, raiz=raiz, exigir_pin=True)
        assert problemas == [], f"{ruta}:\n  " + "\n  ".join(problemas)

    def test_el_producto_y_la_version_coinciden_con_la_ruta(self, raiz, ruta_rel):
        producto, version = ruta_rel.split("/")
        m, _ = self._manifiesto(raiz, ruta_rel)
        assert m["producto"] == producto
        assert m["release"] == version

    def test_las_imagenes_apuntan_al_registry_del_producto(self, raiz, ruta_rel):
        producto, _ = ruta_rel.split("/")
        datos = yaml.safe_load(
            (raiz / "productos" / producto / "producto.yaml").read_text("utf-8"))
        base = datos["registry"]
        m, _ = self._manifiesto(raiz, ruta_rel)
        for servicio, ref in m["imagenes"].items():
            assert ref.startswith(f"{base}/{servicio}@"), (
                f"{servicio}: {ref} no cuelga de {base}/{servicio}")

    def test_la_plantilla_renderiza_y_verifica(self, raiz, ruta_rel):
        producto, _ = ruta_rel.split("/")
        datos = yaml.safe_load(
            (raiz / "productos" / producto / "producto.yaml").read_text("utf-8"))
        plantilla = raiz / "productos" / producto / datos.get(
            "plantilla", "compose.plantilla.yaml")
        m, _ = self._manifiesto(raiz, ruta_rel)

        texto = compose.render_desde_archivos(plantilla, m, entorno={})
        problemas = compose.problemas_del_compose(texto, manifiesto=m)
        assert problemas == [], f"{ruta_rel}:\n  " + "\n  ".join(problemas)

    def test_la_huella_de_la_plantilla_coincide(self, raiz, ruta_rel):
        """Sin huella, el agente conectado al hub no despliega el release: no tiene
        cómo saber que la plantilla que recibe es la que publicó Accusys."""
        producto, _ = ruta_rel.split("/")
        datos = yaml.safe_load(
            (raiz / "productos" / producto / "producto.yaml").read_text("utf-8"))
        plantilla = raiz / "productos" / producto / datos.get(
            "plantilla", "compose.plantilla.yaml")
        m, _ = self._manifiesto(raiz, ruta_rel)
        assert m.get("plantilla_sha256") == compose.huella(plantilla.read_bytes()), (
            f"{ruta_rel}: la huella no coincide con la plantilla; "
            f"corré 'dc sellar ... --escribir' y volvé a firmar")
