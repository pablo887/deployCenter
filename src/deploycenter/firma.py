"""Firma y verificación del manifiesto con cosign.

Es la pieza que sostiene el argumento de seguridad frente a un cliente: el agente
valida la firma antes de aplicar nada, así que un hub comprometido puede encolar
órdenes pero no puede hacer desplegar una imagen que Accusys no firmó.

Por eso mismo la clave privada no vive en el hub ni en este repo. Acá solo está el
envoltorio; de dónde sale la clave en el pipeline es una decisión de
infraestructura que está abierta (ver pendientes del documento de arquitectura).

La firma se calcula sobre el manifiesto serializado en forma canónica, para que
reordenar claves o cambiar la indentación no invalide la firma.
"""

import json
import shutil
from pathlib import Path

from .digests import ejecutar_real
from .errores import ErrorHerramienta

SUFIJO_FIRMA = ".sig"


def canonico(manifiesto):
    """Bytes sobre los que se firma: JSON con claves ordenadas y sin el campo firma."""
    sin_firma = {k: v for k, v in manifiesto.items() if k != "firma"}
    texto = json.dumps(sin_firma, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return texto.encode("utf-8")


def cosign_disponible():
    return shutil.which("cosign") is not None


def _exigir_cosign():
    if not cosign_disponible():
        raise ErrorHerramienta(
            "cosign no está instalado. Es requisito para publicar un release; "
            "ver README, sección 'Publicar'."
        )


def firmar(manifiesto, clave, destino, ejecutar=None, exigir_binario=True):
    """Firma el manifiesto y deja la firma en `destino`. Devuelve la ruta."""
    if exigir_binario:
        _exigir_cosign()
    ejecutar = ejecutar or ejecutar_real

    destino = Path(destino)
    payload = destino.with_suffix(destino.suffix + ".payload")
    payload.write_bytes(canonico(manifiesto))
    try:
        codigo, salida, error = ejecutar([
            "cosign", "sign-blob", "--yes",
            "--key", str(clave),
            "--output-signature", str(destino),
            str(payload),
        ])
    finally:
        payload.unlink(missing_ok=True)

    if codigo != 0:
        raise ErrorHerramienta(f"cosign sign-blob falló: {error or salida}")
    return destino


def verificar(manifiesto, clave_publica, firma, ejecutar=None, exigir_binario=True):
    """True si la firma corresponde al manifiesto. No lanza si la firma es inválida."""
    if exigir_binario:
        _exigir_cosign()
    ejecutar = ejecutar or ejecutar_real

    firma = Path(firma)
    payload = firma.with_suffix(firma.suffix + ".payload")
    payload.write_bytes(canonico(manifiesto))
    try:
        codigo, _salida, _error = ejecutar([
            "cosign", "verify-blob",
            "--key", str(clave_publica),
            "--signature", str(firma),
            str(payload),
        ])
    finally:
        payload.unlink(missing_ok=True)
    return codigo == 0
