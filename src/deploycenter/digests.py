"""Resolución de referencias de imagen a digest.

Regla de la plataforma: en un manifiesto publicado las imágenes van pinneadas por
digest, nunca por tag. Volver a ``api:4.6.0`` es volver a un nombre que pudo haber
sido reescrito; volver a ``api@sha256:...`` es volver al mismo binario.

Este módulo consulta el registry y reescribe ``repo:tag`` como ``repo@sha256:...``.
El ejecutor de comandos se inyecta para poder testear sin registry.
"""

import json
import re
import shutil
import subprocess

from .errores import ErrorHerramienta

RE_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")


def ejecutar_real(comando, timeout=120):
    """Corre un comando externo. Devuelve (codigo, salida, error)."""
    try:
        p = subprocess.run(
            comando,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as e:
        raise ErrorHerramienta(f"no se encontró el ejecutable {comando[0]!r}") from e
    except subprocess.TimeoutExpired as e:
        raise ErrorHerramienta(f"{comando[0]} no respondió en {timeout}s") from e
    return p.returncode, p.stdout.strip(), p.stderr.strip()


def separar(referencia):
    """'reg/ns/api:1.2.3' -> ('reg/ns/api', '1.2.3', None)
    'reg/ns/api@sha256:ab..' -> ('reg/ns/api', None, 'sha256:ab..')
    'reg/ns/api:1.2.3@sha256:ab..' -> ('reg/ns/api', '1.2.3', 'sha256:ab..')

    Tiene en cuenta que el host del registry puede traer puerto (``host:5000/api``),
    que es el caso donde un split ingenuo por ':' se equivoca.
    """
    ref = (referencia or "").strip()
    if not ref:
        raise ValueError("referencia de imagen vacía")
    nombre, _, digest = ref.partition("@")
    # el tag es lo que va después del último ':' siempre que no haya '/' después
    corte = nombre.rfind(":")
    if corte > 0 and "/" not in nombre[corte:]:
        return nombre[:corte], nombre[corte + 1:], digest or None
    return nombre, None, digest or None


def tag_de(referencia):
    """El tag legible de la referencia, si lo tiene. Con ``repo:tag@digest`` el
    digest manda al descargar y el tag queda para mostrar qué versión es."""
    return separar(referencia)[1]


def esta_pinneado(referencia):
    _, _, digest = separar(referencia)
    return bool(digest) and bool(RE_DIGEST.match(digest))


def usa_latest(referencia):
    _, tag, _ = separar(referencia)
    return tag == "latest"


def _digest_con_docker(referencia, ejecutar):
    codigo, salida, error = ejecutar([
        "docker", "buildx", "imagetools", "inspect",
        "--format", "{{json .Manifest.Digest}}",
        referencia,
    ])
    if codigo != 0:
        return None, error or salida
    texto = salida.strip().strip('"')
    return (texto, None) if RE_DIGEST.match(texto) else (None, f"respuesta inesperada: {salida!r}")


def _digest_con_skopeo(referencia, ejecutar):
    codigo, salida, error = ejecutar([
        "skopeo", "inspect", "--format", "{{.Digest}}", f"docker://{referencia}",
    ])
    if codigo != 0:
        return None, error or salida
    texto = salida.strip().strip('"')
    return (texto, None) if RE_DIGEST.match(texto) else (None, f"respuesta inesperada: {salida!r}")


def herramienta_disponible():
    """Devuelve 'docker', 'skopeo' o None."""
    if shutil.which("docker"):
        return "docker"
    if shutil.which("skopeo"):
        return "skopeo"
    return None


def resolver(referencia, ejecutar=None, herramienta=None):
    """Devuelve la referencia pinneada por digest, conservando el tag:
    ``repo:1.2.3`` -> ``repo:1.2.3@sha256:...``. Docker descarga por el digest e
    ignora el tag, así que el binario sigue siendo exacto; el tag queda para que
    una persona (y el front del producto) sepa qué versión es.

    Si ya venía pinneada la devuelve tal cual: resolver es idempotente y correrlo
    de nuevo sobre un manifiesto ya publicado no lo cambia.
    """
    if esta_pinneado(referencia):
        return referencia
    repo, tag, _ = separar(referencia)
    if not tag:
        raise ErrorHerramienta(
            f"{referencia!r} no tiene tag ni digest: no hay nada que resolver"
        )

    ejecutar = ejecutar or ejecutar_real
    herramienta = herramienta or herramienta_disponible()
    if herramienta is None:
        raise ErrorHerramienta(
            "hace falta docker o skopeo para resolver digests contra el registry"
        )

    if herramienta == "docker":
        digest, problema = _digest_con_docker(referencia, ejecutar)
    else:
        digest, problema = _digest_con_skopeo(referencia, ejecutar)

    if digest is None:
        raise ErrorHerramienta(
            f"no se pudo resolver el digest de {referencia!r}: {problema}"
        )
    return f"{repo}:{tag}@{digest}"


def pinear_manifiesto(manifiesto, ejecutar=None, herramienta=None):
    """Devuelve (manifiesto_nuevo, cambios) sin mutar el original.

    `cambios` es una lista de (servicio, antes, despues) con lo que se reescribió.
    """
    nuevo = json.loads(json.dumps(manifiesto))
    cambios = []
    for servicio, referencia in sorted(nuevo.get("imagenes", {}).items()):
        resuelta = resolver(referencia, ejecutar=ejecutar, herramienta=herramienta)
        if resuelta != referencia:
            cambios.append((servicio, referencia, resuelta))
            nuevo["imagenes"][servicio] = resuelta
    return nuevo, cambios
