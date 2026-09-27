"""Generación del docker-compose.yml desde la plantilla versionada del producto.

El compose deja de editarse a mano. Se genera a partir de tres entradas:

    plantilla del producto  (Accusys, versionada)
  + manifiesto del release  (Accusys, firmado)
  + entorno del cliente     (vive en el servidor del cliente)
  = docker-compose.yml

La plantilla usa Jinja con ``StrictUndefined``: si referencia algo que no está,
falla al renderizar en vez de generar un compose incompleto que recién explota
cuando el contenedor no levanta. Corre en el sandbox de Jinja porque en Fase 2 la
plantilla viaja desde el hub: aunque el agente comprueba su huella contra el
manifiesto firmado, renderizarla no tiene por qué poder ejecutar código.

Los secretos no se interpolan en el compose. La plantilla pasa el archivo de
entorno con ``env_file`` y solo interpola lo que define la topología (puertos,
rutas de volumen). Así el compose generado se puede leer y versionar sin que
lleve valores del cliente adentro.
"""

import hashlib
from pathlib import Path

import yaml
from jinja2 import StrictUndefined, TemplateError
from jinja2.sandbox import SandboxedEnvironment

from . import digests
from .errores import ErrorArchivo, ErrorValidacion

REGISTRY_PROPIO = "registry.accusys.com.ar"


def huella(contenido):
    """SHA-256 de la plantilla, tal como la declara `plantilla_sha256` del manifiesto."""
    if isinstance(contenido, str):
        contenido = contenido.encode("utf-8")
    return hashlib.sha256(contenido).hexdigest()


def _entorno_jinja():
    env = SandboxedEnvironment(
        undefined=StrictUndefined,
        keep_trailing_newline=True,
        trim_blocks=True,
        lstrip_blocks=True,
        autoescape=False,
    )
    return env


def render(plantilla_texto, manifiesto, entorno=None, extra=None):
    """Renderiza la plantilla. Devuelve el texto del compose."""
    contexto = {
        "producto": manifiesto.get("producto"),
        "release": manifiesto.get("release"),
        "imagenes": dict(manifiesto.get("imagenes") or {}),
        "env": dict(entorno or {}),
    }
    if extra:
        contexto.update(extra)
    try:
        return _entorno_jinja().from_string(plantilla_texto).render(**contexto)
    except TemplateError as e:
        raise ErrorArchivo(f"no se pudo renderizar la plantilla: {e}") from e


def render_desde_archivos(ruta_plantilla, manifiesto, entorno=None, extra=None):
    ruta = Path(ruta_plantilla)
    try:
        texto = ruta.read_text(encoding="utf-8")
    except OSError as e:
        raise ErrorArchivo(f"no se pudo leer {ruta}: {e}") from e
    return render(texto, manifiesto, entorno=entorno, extra=extra)


def problemas_del_compose(texto, manifiesto=None, registry=REGISTRY_PROPIO):
    """Revisa el compose generado antes de dárselo a nadie."""
    p = []
    try:
        datos = yaml.safe_load(texto)
    except yaml.YAMLError as e:
        return [f"el compose generado no es YAML válido: {e}"]

    if not isinstance(datos, dict):
        return ["el compose generado no es un mapeo YAML"]

    servicios = datos.get("services")
    if not isinstance(servicios, dict) or not servicios:
        return ["el compose generado no declara services"]

    for nombre, definicion in sorted(servicios.items()):
        if not isinstance(definicion, dict):
            p.append(f"services/{nombre}: definición inválida")
            continue
        imagen = definicion.get("image")
        if not imagen:
            if "build" in definicion:
                p.append(
                    f"services/{nombre}: usa 'build'; en el servidor del cliente se "
                    f"despliegan imágenes publicadas, no builds locales"
                )
            else:
                p.append(f"services/{nombre}: no declara image")
            continue
        if digests.usa_latest(imagen):
            p.append(f"services/{nombre}: usa el tag 'latest'")
        elif imagen.startswith(registry) and not digests.esta_pinneado(imagen):
            p.append(
                f"services/{nombre}: la imagen {imagen!r} es del registry propio y "
                f"no está pinneada por digest"
            )

    if manifiesto is not None:
        declarados = set((manifiesto.get("imagenes") or {}).keys())
        faltan = declarados - set(servicios)
        if faltan:
            p.append(
                "el compose no incluye servicios declarados en el manifiesto: "
                + ", ".join(sorted(faltan))
            )

    return p


def generar(ruta_plantilla, manifiesto, entorno=None, salida=None, verificar=True):
    """Renderiza, verifica y opcionalmente escribe el compose."""
    texto = render_desde_archivos(ruta_plantilla, manifiesto, entorno=entorno)
    if verificar:
        problemas = problemas_del_compose(texto, manifiesto=manifiesto)
        if problemas:
            raise ErrorValidacion(problemas)
    if salida is not None:
        Path(salida).write_text(texto, encoding="utf-8")
    return texto
