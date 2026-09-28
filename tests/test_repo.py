"""Tests sobre el contenido del repo, no sobre el código.

Esto es lo que corre el pipeline en cada push: que todo manifiesto versionado sea
válido, esté pinneado y su plantilla renderice. Un release que no pasa por acá no
se publica.

Los borradores (`productos/<p>/borradores/<version>/`) pasan por lo mismo salvo
el pinneo: todavía están por tag. El hub no los ofrece; se publican pineándolos
y moviéndolos a `releases/`.
"""

import pytest
import yaml

from deploycenter import compose, digests
from deploycenter import manifiesto as mf


def descubrir_productos(raiz):
    return sorted((raiz / "productos").glob("*/producto.yaml"))


def descubrir(raiz, tipo):
    return sorted((raiz / "productos").glob(f"*/{tipo}/*/manifiesto.json"))


def _producto(raiz, nombre):
    return yaml.safe_load((raiz / "productos" / nombre / "producto.yaml").read_text("utf-8"))


def _plantilla(raiz, nombre):
    return raiz / "productos" / nombre / _producto(raiz, nombre).get(
        "plantilla", "compose.plantilla.yaml")


def test_hay_al_menos_un_producto(raiz):
    assert descubrir_productos(raiz), "no hay ningún producto en productos/"


def test_todo_producto_tiene_un_release_o_un_borrador(raiz):
    for ruta in descubrir_productos(raiz):
        d = ruta.parent
        assert list(d.glob("releases/*/manifiesto.json")) or list(
            d.glob("borradores/*/manifiesto.json")), f"{d.name} no tiene ningún release"


def _ids_productos():
    from conftest import RAIZ
    return [p.parent.name for p in descubrir_productos(RAIZ)]


def _ids(tipo):
    from conftest import RAIZ
    return [f"{tipo}:{r.parents[2].name}/{r.parent.name}" for r in descubrir(RAIZ, tipo)]


@pytest.mark.parametrize("nombre", _ids_productos())
class TestProducto:
    def test_el_codigo_coincide_con_la_carpeta(self, raiz, nombre):
        assert _producto(raiz, nombre)["codigo"] == nombre

    def test_declara_servicios_con_healthcheck(self, raiz, nombre):
        servicios = _producto(raiz, nombre).get("servicios") or {}
        assert servicios, f"{nombre} no declara servicios"
        for servicio, cfg in servicios.items():
            assert (cfg or {}).get("healthcheck"), f"{nombre}/{servicio} no declara healthcheck"

    def test_la_plantilla_existe(self, raiz, nombre):
        assert _plantilla(raiz, nombre).is_file(), f"falta la plantilla de {nombre}"


@pytest.mark.parametrize("caso", _ids("releases") + _ids("borradores"))
class TestRelease:
    def _manifiesto(self, raiz, caso):
        tipo, ruta_rel = caso.split(":")
        producto, version = ruta_rel.split("/")
        ruta = raiz / "productos" / producto / tipo / version / "manifiesto.json"
        return tipo == "releases", producto, version, mf.cargar(ruta), ruta

    def test_es_valido(self, raiz, caso):
        """Un release publicado además tiene que estar pinneado."""
        publicado, _, _, m, ruta = self._manifiesto(raiz, caso)
        problemas = mf.validar(m, raiz=raiz, exigir_pin=publicado)
        assert problemas == [], f"{ruta}:\n  " + "\n  ".join(problemas)

    def test_el_producto_y_la_version_coinciden_con_la_ruta(self, raiz, caso):
        _, producto, version, m, _ = self._manifiesto(raiz, caso)
        assert m["producto"] == producto
        assert m["release"] == version

    def test_las_imagenes_apuntan_al_registry_del_producto(self, raiz, caso):
        _, producto, _, m, _ = self._manifiesto(raiz, caso)
        datos = _producto(raiz, producto)
        servicios = datos.get("servicios") or {}
        for servicio, ref in m["imagenes"].items():
            repo = (servicios.get(servicio) or {}).get("repo", servicio)
            esperado = f"{datos['registry']}/{repo}"
            assert digests.separar(ref)[0] == esperado, (
                f"{servicio}: {ref} no es del repo {esperado}")

    def test_la_plantilla_renderiza_y_verifica(self, raiz, caso):
        publicado, producto, _, m, _ = self._manifiesto(raiz, caso)
        texto = compose.render_desde_archivos(_plantilla(raiz, producto), m, entorno={})
        problemas = compose.problemas_del_compose(texto, manifiesto=m, exigir_pin=publicado)
        assert problemas == [], f"{caso}:\n  " + "\n  ".join(problemas)

    def test_la_huella_de_la_plantilla_coincide(self, raiz, caso):
        """Sin huella, el agente conectado al hub no despliega el release: no tiene
        cómo saber que la plantilla que recibe es la que publicó Accusys."""
        _, producto, _, m, _ = self._manifiesto(raiz, caso)
        plantilla = _plantilla(raiz, producto)
        assert m.get("plantilla_sha256") == compose.huella(plantilla.read_bytes()), (
            f"{caso}: la huella no coincide con la plantilla; "
            f"corré 'dc sellar ... --escribir' y volvé a firmar")
