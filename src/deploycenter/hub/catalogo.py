"""Catálogo de releases que el hub puede ordenar.

Lee el mismo árbol `productos/` que valida el pipeline: lo que está publicado en
el repo es lo que el hub ofrece. Arma el paquete con la misma forma que deja
`dc empaquetar`, así que el agente recibe exactamente lo que ya sabe consumir.
"""

import base64
import functools
from pathlib import Path

import yaml

from .. import manifiesto as mf
from .. import versiones
from ..errores import ErrorArchivo


class Catalogo:
    """`raiz` es el repo (con `productos/`). `extras` son otras raíces con la misma
    forma, que se suman: sirven para un catálogo de demostración que no tiene por
    qué vivir en el real. Si un producto está en dos, gana la raíz principal."""

    def __init__(self, raiz, extras=()):
        self.raiz = Path(raiz)
        self.raices = [self.raiz, *(Path(e) for e in extras)]

    def _raiz_de(self, producto):
        for raiz in self.raices:
            if (raiz / "productos" / producto / "producto.yaml").is_file():
                return raiz
        return self.raiz

    def _dir_producto(self, producto):
        # el código de producto viene de la base o de una petición: no se deja
        # que arme una ruta fuera de productos/
        if not producto or "/" in producto or "\\" in producto or producto.startswith("."):
            raise ErrorArchivo(f"código de producto inválido: {producto!r}")
        return self._raiz_de(producto) / "productos" / producto

    def productos(self):
        """Los códigos de producto con `producto.yaml`, en orden."""
        return sorted({d.name for raiz in self.raices if (raiz / "productos").is_dir()
                       for d in (raiz / "productos").iterdir()
                       if (d / "producto.yaml").is_file() and not d.name.startswith(".")})

    def novedades(self, producto, version):
        """Los ítems del changelog (las líneas de lista), para mostrar en la web."""
        manifiesto = self.release(producto, version)
        raiz = self._raiz_de(producto)
        ruta = raiz / (manifiesto or {}).get("changelog", "")
        if not manifiesto or not ruta.is_file() or not ruta.resolve().is_relative_to(
                raiz.resolve()):
            return []
        items = []
        for linea in ruta.read_text(encoding="utf-8").splitlines():
            if linea.startswith(("- ", "* ")):
                items.append(linea[2:].strip())
            elif items and linea.startswith("  ") and linea.strip():
                items[-1] += " " + linea.strip()
        return items

    def datos_producto(self, producto):
        ruta = self._dir_producto(producto) / "producto.yaml"
        if not ruta.is_file():
            raise ErrorArchivo(f"no existe el producto {producto!r}")
        datos = yaml.safe_load(ruta.read_text(encoding="utf-8"))
        if not isinstance(datos, dict):
            raise ErrorArchivo(f"{ruta} debería ser un mapeo YAML")
        return datos

    def releases(self, producto):
        """Manifiestos del producto, del más viejo al más nuevo."""
        carpeta = self._dir_producto(producto) / "releases"
        encontrados = []
        for ruta in carpeta.glob("*/manifiesto.json"):
            try:
                encontrados.append(mf.cargar(ruta))
            except ErrorArchivo:
                continue
        orden = functools.cmp_to_key(versiones.comparar)
        return sorted(encontrados, key=lambda m: orden(m["release"]))

    def release(self, producto, version):
        if not version or "/" in version or "\\" in version or version.startswith("."):
            return None
        ruta = self._dir_producto(producto) / "releases" / version / "manifiesto.json"
        if not ruta.is_file():
            return None
        return mf.cargar(ruta)

    def paquete(self, producto, version):
        """El paquete tal como lo consume el agente, listo para viajar en JSON."""
        manifiesto = self.release(producto, version)
        if manifiesto is None:
            raise ErrorArchivo(f"no existe el release {producto} {version}")
        carpeta = self._dir_producto(producto) / "releases" / version
        plantilla = self._dir_producto(producto) / self.datos_producto(producto).get(
            "plantilla", "compose.plantilla.yaml")
        firma = carpeta / "manifiesto.json.sig"
        changelog = self._raiz_de(producto) / manifiesto.get("changelog", "")

        return {
            "manifiesto": manifiesto,
            "plantilla": plantilla.read_text(encoding="utf-8"),
            "firma": base64.b64encode(firma.read_bytes()).decode("ascii")
            if firma.is_file() else None,
            "changelog": changelog.read_text(encoding="utf-8")
            if changelog.is_file() else None,
        }
