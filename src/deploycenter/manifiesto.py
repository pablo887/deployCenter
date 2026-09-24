"""Carga y validación del manifiesto de release.

La validación tiene dos capas:

1. El JSON Schema, que cubre forma, tipos y campos obligatorios.
2. Las reglas semánticas de acá abajo, que cubren lo que el schema no puede ver:
   coherencia entre campos, cobertura de healthchecks, pinneo por digest y la
   relación entre migraciones y rollback.

Las dos devuelven la lista completa de problemas en vez de cortar en el primero:
en un pipeline conviene ver todo lo que hay que arreglar de una sola pasada.
"""

import datetime
import json
from pathlib import Path

try:
    from importlib.resources import files as _recursos
except ImportError:  # pragma: no cover - Python < 3.9
    _recursos = None

from . import digests, versiones
from .errores import ErrorArchivo, ErrorValidacion

NOMBRE_SCHEMA = "manifiesto.schema.json"


def ruta_schema():
    if _recursos is not None:
        return Path(str(_recursos("deploycenter.recursos") / NOMBRE_SCHEMA))
    return Path(__file__).resolve().parent / "recursos" / NOMBRE_SCHEMA


def cargar_schema():
    return json.loads(ruta_schema().read_text(encoding="utf-8"))


def cargar(ruta):
    """Lee un manifiesto de disco. No lo valida."""
    ruta = Path(ruta)
    try:
        texto = ruta.read_text(encoding="utf-8")
    except OSError as e:
        raise ErrorArchivo(f"no se pudo leer {ruta}: {e}") from e
    try:
        return json.loads(texto)
    except json.JSONDecodeError as e:
        raise ErrorArchivo(f"{ruta} no es JSON válido: línea {e.lineno}, {e.msg}") from e


def guardar(manifiesto, ruta):
    """Escribe el manifiesto con formato estable, para que el diff sea legible."""
    ruta = Path(ruta)
    texto = json.dumps(manifiesto, indent=2, ensure_ascii=False, sort_keys=False)
    ruta.write_text(texto + "\n", encoding="utf-8")
    return ruta


# --------------------------------------------------------------------------- #
# capa 1: schema
# --------------------------------------------------------------------------- #

def problemas_de_schema(manifiesto):
    try:
        import jsonschema
    except ImportError:  # pragma: no cover
        return ["falta la dependencia jsonschema; instalá el paquete con sus extras"]

    validador = jsonschema.Draft202012Validator(cargar_schema())
    problemas = []
    for e in sorted(validador.iter_errors(manifiesto), key=lambda x: list(x.path)):
        donde = "/".join(str(p) for p in e.path) or "(raíz)"
        problemas.append(f"{donde}: {e.message}")
    return problemas


# --------------------------------------------------------------------------- #
# capa 2: reglas semánticas
# --------------------------------------------------------------------------- #

def problemas_semanticos(manifiesto, raiz=None, exigir_pin=True):
    p = []
    imagenes = manifiesto.get("imagenes") or {}
    checks = manifiesto.get("healthchecks") or []

    # -- fecha real, no solo con forma de fecha
    publicado = manifiesto.get("publicado")
    if isinstance(publicado, str):
        try:
            datetime.date.fromisoformat(publicado)
        except ValueError:
            p.append(f"publicado: {publicado!r} no es una fecha del calendario")

    # -- pinneo por digest
    for servicio, ref in sorted(imagenes.items()):
        if digests.usa_latest(ref):
            p.append(f"imagenes/{servicio}: usa el tag 'latest', que hace el rollback imposible")
        elif exigir_pin and not digests.esta_pinneado(ref):
            p.append(
                f"imagenes/{servicio}: {ref!r} no está pinneada por digest; "
                f"corré 'dc pinear' antes de publicar"
            )

    # -- healthchecks: cobertura en los dos sentidos
    servicios_con_check = {c.get("servicio") for c in checks if isinstance(c, dict)}
    for servicio in sorted(servicios_con_check - set(imagenes)):
        p.append(f"healthchecks: {servicio!r} no es un servicio declarado en imagenes")
    for servicio in sorted(set(imagenes) - servicios_con_check):
        p.append(
            f"imagenes/{servicio}: no tiene healthcheck; sin él no hay criterio de "
            f"éxito ni rollback automático para ese servicio"
        )

    # -- migraciones y rollback
    migra = bool(manifiesto.get("db_migrations"))
    seguro = bool(manifiesto.get("rollback_seguro"))
    compatibles = bool(manifiesto.get("migraciones_compatibles"))
    if migra and seguro and not compatibles:
        p.append(
            "rollback_seguro: no puede ser true con db_migrations en true salvo que "
            "declares migraciones_compatibles (expand/contract); revertir la imagen "
            "contra una base ya migrada deja el sistema peor que el fallo"
        )
    if compatibles and not migra:
        p.append("migraciones_compatibles: solo tiene sentido con db_migrations en true")
    if migra and not seguro and manifiesto.get("critico_seguridad"):
        p.append(
            "critico_seguridad: un parche que se despliega sin mantenimiento vigente "
            "no debería además requerir circuito asistido; separá la migración del parche"
        )

    # -- variables
    nombres = [v.get("nombre") for v in manifiesto.get("variables_nuevas") or []]
    for nombre in sorted({n for n in nombres if nombres.count(n) > 1}):
        p.append(f"variables_nuevas: {nombre!r} está declarada más de una vez")
    for v in manifiesto.get("variables_nuevas") or []:
        if v.get("secreta") and v.get("default") is not None:
            p.append(
                f"variables_nuevas/{v.get('nombre')}: una variable secreta no puede "
                f"traer default; el valor lo pone el cliente en su servidor"
            )

    # -- rango de upgrade
    rango = manifiesto.get("desde_version")
    release = manifiesto.get("release")
    if isinstance(rango, str):
        if not versiones.rango_valido(rango):
            p.append(f"desde_version: {rango!r} no es un rango válido")
        elif isinstance(release, str):
            try:
                for cota in versiones.cotas_inferiores(rango):
                    if not versiones.es_mayor(release, cota):
                        p.append(
                            f"desde_version: el release {release} no es posterior a la "
                            f"cota {cota}; no se puede actualizar hacia atrás"
                        )
            except versiones.VersionInvalida as e:
                p.append(f"desde_version: {e}")

    # -- changelog en disco
    if raiz is not None:
        destino = Path(raiz) / manifiesto.get("changelog", "")
        if not destino.is_file():
            p.append(f"changelog: no existe el archivo {destino}")

    return p


def validar(manifiesto, raiz=None, exigir_pin=True):
    """Devuelve la lista de problemas. Vacía significa que el manifiesto es válido."""
    problemas = problemas_de_schema(manifiesto)
    # si la forma está rota, las reglas semánticas solo agregan ruido
    if problemas:
        return problemas
    return problemas_semanticos(manifiesto, raiz=raiz, exigir_pin=exigir_pin)


def exigir_valido(manifiesto, raiz=None, exigir_pin=True):
    problemas = validar(manifiesto, raiz=raiz, exigir_pin=exigir_pin)
    if problemas:
        raise ErrorValidacion(problemas)
    return manifiesto


# --------------------------------------------------------------------------- #
# consultas que usa el resto de la plataforma
# --------------------------------------------------------------------------- #

def servicios(manifiesto):
    return sorted((manifiesto.get("imagenes") or {}).keys())


def es_autoservicio(manifiesto):
    """Un release con migraciones sale del circuito autoservicio y va al asistido."""
    if not manifiesto.get("db_migrations"):
        return True
    return bool(manifiesto.get("migraciones_compatibles"))


def permite_upgrade_desde(manifiesto, version_instalada):
    return versiones.permite(manifiesto.get("desde_version", "*"), version_instalada)


def habilitado_para(manifiesto, mantenimiento_hasta):
    """Regla comercial: se habilita lo publicado hasta el fin del mantenimiento.

    Se compara contra la fecha de publicación del release y no contra el número de
    versión: si el cliente pagó hasta el 31/12 tiene derecho a todo lo publicado
    hasta ese día, aunque lo instale en marzo.

    Los parches marcados como críticos de seguridad se habilitan igual. Es una
    decisión deliberada: no conviene que un cliente quede expuesto por una factura
    impaga, porque la vulnerabilidad lleva el nombre del producto.
    """
    if manifiesto.get("critico_seguridad"):
        return True
    if mantenimiento_hasta is None:
        return False
    if isinstance(mantenimiento_hasta, str):
        mantenimiento_hasta = datetime.date.fromisoformat(mantenimiento_hasta)
    publicado = datetime.date.fromisoformat(manifiesto["publicado"])
    return publicado <= mantenimiento_hasta
