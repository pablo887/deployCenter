"""Salida por consola, compartida entre el CLI de publicación y el del agente.

La consola de Windows arranca en cp1252 y no sabe escribir U+2713. Se intenta
pasar los flujos a UTF-8 y, si no se puede, se cae a símbolos ASCII: es
preferible una salida más fea que un stack trace sobre un comando que funcionó.
"""

import sys

OK, FALLA_VALIDACION, ERROR_USO = 0, 1, 2

VERDE, ROJO, AMARILLO, GRIS, FIN = "\033[32m", "\033[31m", "\033[33m", "\033[90m", "\033[0m"

SIMBOLOS_UNICODE = {"ok": "✓", "mal": "✗", "punto": "·", "mas": "+", "aviso": "!"}
SIMBOLOS_ASCII = {"ok": "OK", "mal": "XX", "punto": "-", "mas": "+", "aviso": "!"}


def preparar_salida():
    for flujo in (sys.stdout, sys.stderr):
        try:
            flujo.reconfigure(encoding="utf-8")
        except (AttributeError, OSError, ValueError):  # pragma: no cover
            pass


def simbolos():
    codificacion = getattr(sys.stdout, "encoding", None) or "ascii"
    try:
        "".join(SIMBOLOS_UNICODE.values()).encode(codificacion)
        return SIMBOLOS_UNICODE
    except (UnicodeEncodeError, LookupError):  # pragma: no cover - depende de la consola
        return SIMBOLOS_ASCII


def paleta(activo):
    if activo:
        return VERDE, ROJO, AMARILLO, GRIS, FIN
    return "", "", "", "", ""


def usar_color(args):
    es_tty = bool(getattr(sys.stdout, "isatty", lambda: False)())
    return es_tty and not getattr(args, "sin_color", False)
