"""Comparación de versiones y rangos de upgrade.

El manifiesto declara ``desde_version`` como el rango de versiones desde las que se
puede saltar directo a este release. Acá se resuelve si una versión instalada entra
en ese rango. Es deliberadamente chico: no hace falta semver completo, hacen falta
cuatro operadores y que el resultado sea obvio de leer.

Gramática soportada::

    *                 cualquier versión
    >=4.5.0           mayor o igual
    >4.5.0            mayor estricto
    <=4.9.3 / <5.0.0  menor o igual / menor estricto
    ==4.5.0           exacta
    >=4.5.0, <5.0.0   varias cláusulas separadas por coma, todas deben cumplirse
"""

import re

_VERSION = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?$")
_CLAUSULA = re.compile(r"^(>=|<=|==|=|>|<)?\s*(.+)$")

OPERADORES = (">=", "<=", "==", "=", ">", "<")


class VersionInvalida(ValueError):
    pass


def parsear(version):
    """'4.7.1-rc1' -> (4, 7, 1, 'rc1'). Lanza VersionInvalida si no es semver."""
    texto = (version or "").strip()
    m = _VERSION.match(texto)
    if not m:
        raise VersionInvalida(f"versión inválida: {version!r}")
    mayor, menor, parche, pre = m.groups()
    return (int(mayor), int(menor), int(parche), pre)


def _clave(version):
    """Clave ordenable. Una pre-release ordena por debajo del release final."""
    mayor, menor, parche, pre = parsear(version)
    # sin pre-release -> 1 (posterior); con pre-release -> 0 (anterior)
    return (mayor, menor, parche, 1 if pre is None else 0, pre or "")


def comparar(a, b):
    """-1 si a<b, 0 si a==b, 1 si a>b."""
    ka, kb = _clave(a), _clave(b)
    return (ka > kb) - (ka < kb)


def es_mayor(a, b):
    return comparar(a, b) > 0


def clausulas(rango):
    """Parte un rango en (operador, version). '*' devuelve lista vacía."""
    texto = (rango or "").strip()
    if texto == "*":
        return []
    salida = []
    for parte in texto.split(","):
        parte = parte.strip()
        if not parte:
            continue
        m = _CLAUSULA.match(parte)
        operador, version = m.group(1), m.group(2).strip()
        if operador is None:
            operador = "=="
        elif operador == "=":
            operador = "=="
        parsear(version)  # valida, lanza si no es semver
        salida.append((operador, version))
    if not salida:
        raise VersionInvalida(f"rango vacío: {rango!r}")
    return salida


def rango_valido(rango):
    try:
        clausulas(rango)
        return True
    except (VersionInvalida, AttributeError):
        return False


def permite(rango, version):
    """True si `version` entra en `rango`. Un rango '*' acepta todo."""
    cs = clausulas(rango)
    if not cs:
        return True
    for operador, referencia in cs:
        c = comparar(version, referencia)
        if operador == ">=" and c < 0:
            return False
        if operador == ">" and c <= 0:
            return False
        if operador == "<=" and c > 0:
            return False
        if operador == "<" and c >= 0:
            return False
        if operador == "==" and c != 0:
            return False
    return True


def cotas_inferiores(rango):
    """Versiones que el rango exige superar o alcanzar. Sirve para chequear
    que el release sea posterior a la versión más vieja desde la que se sube."""
    return [v for operador, v in clausulas(rango) if operador in (">=", ">", "==")]
