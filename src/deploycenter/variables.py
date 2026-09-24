"""Variables de entorno del cliente.

Esto es lo que reemplaza al "agregale esta línea" de la reunión nocturna: el
manifiesto declara qué variables necesita la versión nueva y acá se compara contra
lo que el cliente ya tiene, antes de tocar nada.

Los valores viven en el servidor del cliente y no viajan al hub. Este módulo los
lee localmente para decidir si el preflight pasa; nunca los publica.
"""

import re
from pathlib import Path

from .errores import ErrorArchivo

RE_LINEA = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$")


def _desenvolver(valor):
    v = valor.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in ("'", '"'):
        return v[1:-1]
    # comentario al final de línea, solo si está separado por espacio
    corte = v.find(" #")
    if corte != -1:
        v = v[:corte].rstrip()
    return v


def leer_env(ruta):
    """Parsea un archivo .env a dict. Ignora comentarios y líneas vacías."""
    ruta = Path(ruta)
    try:
        texto = ruta.read_text(encoding="utf-8")
    except OSError as e:
        raise ErrorArchivo(f"no se pudo leer {ruta}: {e}") from e
    return parsear_env(texto)


def parsear_env(texto):
    entorno = {}
    for numero, linea in enumerate(texto.splitlines(), start=1):
        if not linea.strip() or linea.lstrip().startswith("#"):
            continue
        m = RE_LINEA.match(linea)
        if not m:
            raise ErrorArchivo(
                f"línea {numero} del archivo de entorno no es NOMBRE=valor: {linea!r}")
        entorno[m.group(1)] = _desenvolver(m.group(2))
    return entorno


def escribir_env(entorno, ruta):
    """Escribe un .env ordenado. Útil para dejar el archivo del cliente prolijo."""
    ruta = Path(ruta)
    lineas = [f"{k}={v}" for k, v in sorted(entorno.items())]
    ruta.write_text("\n".join(lineas) + "\n", encoding="utf-8")
    return ruta


def declaradas(manifiesto):
    return list(manifiesto.get("variables_nuevas") or [])


def faltantes(manifiesto, entorno):
    """Obligatorias que el cliente no tiene y que no se pueden completar solas.

    Estas son las que bloquean el preflight: hay que pedírselas a alguien.
    """
    return [
        v for v in declaradas(manifiesto)
        if v.get("obligatoria")
        and v.get("nombre") not in entorno
        and v.get("default") is None
    ]


def completables(manifiesto, entorno):
    """Faltan pero traen default, así que el preflight las puede resolver solo."""
    return [
        v for v in declaradas(manifiesto)
        if v.get("nombre") not in entorno and v.get("default") is not None
    ]


def ya_presentes(manifiesto, entorno):
    return [v for v in declaradas(manifiesto) if v.get("nombre") in entorno]


def aplicar_defaults(manifiesto, entorno):
    """Devuelve un entorno nuevo con los defaults aplicados. No muta el original."""
    salida = dict(entorno)
    for v in completables(manifiesto, entorno):
        salida[v["nombre"]] = v["default"]
    return salida


def informe(manifiesto, entorno):
    """Resumen para el CLI y, más adelante, para el formulario de la web."""
    faltan = faltantes(manifiesto, entorno)
    return {
        "faltantes": faltan,
        "completables": completables(manifiesto, entorno),
        "presentes": ya_presentes(manifiesto, entorno),
        "bloquea": bool(faltan),
    }
