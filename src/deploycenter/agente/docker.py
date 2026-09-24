"""Catálogo cerrado de operaciones sobre Docker.

Esta es la pieza que hay que poder defender ante el área de seguridad de un
cliente. El agente tiene acceso al socket de Docker, que en la práctica equivale
a root en ese host, así que la respuesta a "¿pueden ejecutar cualquier cosa en mi
servidor?" tiene que ser un no verificable.

Por eso:

- Las únicas operaciones son las de esta clase: pull, up, down, ps, logs e
  inspect. No hay forma de pedirle al agente que corra un comando arbitrario.
- Todas se ejecutan sobre un directorio de stack concreto, validado contra la
  raíz configurada. Un manifiesto no puede hacer que el agente toque
  /etc o el stack de otro producto.
- Nunca se usa shell: los comandos se arman como lista de argumentos, así que no
  hay inyección posible desde un nombre de servicio o una ruta.
"""

import json
import shutil
import subprocess
from pathlib import Path

from ..errores import ErrorHerramienta

TIMEOUT_CORTO = 120
TIMEOUT_LARGO = 1800  # un pull de varios GB en un enlace lento


def ejecutar_real(comando, timeout=TIMEOUT_CORTO):
    try:
        p = subprocess.run(comando, capture_output=True, text=True,
                           timeout=timeout, check=False)
    except FileNotFoundError as e:
        raise ErrorHerramienta(f"no se encontró el ejecutable {comando[0]!r}") from e
    except subprocess.TimeoutExpired as e:
        raise ErrorHerramienta(
            f"{' '.join(comando[:3])} no respondió en {timeout}s") from e
    return p.returncode, p.stdout.strip(), p.stderr.strip()


class ErrorDocker(ErrorHerramienta):
    """Una operación de Docker devolvió error. Trae la salida para el historial."""

    def __init__(self, mensaje, codigo=None, salida="", error=""):
        self.codigo, self.salida, self.error = codigo, salida, error
        super().__init__(mensaje)


class Docker:
    """Envoltorio acotado sobre `docker` y `docker compose`."""

    def __init__(self, ejecutar=None, raiz_permitida=None):
        self.ejecutar = ejecutar or ejecutar_real
        self.raiz_permitida = Path(raiz_permitida).resolve() if raiz_permitida else None

    # -- seguridad ---------------------------------------------------------- #

    def _dentro_de_la_raiz(self, ruta):
        if self.raiz_permitida is None:
            return
        try:
            Path(ruta).resolve().relative_to(self.raiz_permitida)
        except ValueError:
            raise ErrorDocker(
                f"{ruta} está fuera de la raíz permitida {self.raiz_permitida}"
            ) from None

    def _validar(self, directorio, archivo=None):
        """El directorio del proyecto y el compose a aplicar.

        Van separados a propósito: el preflight descarga las imágenes de la
        versión nueva apuntando `-f` a un compose preparado aparte, mientras
        `--project-directory` sigue siendo el del stack para que el .env del
        cliente se resuelva igual. Así se baja lo nuevo sin tocar lo que corre.
        """
        d = Path(directorio).resolve()
        self._dentro_de_la_raiz(d)
        a = Path(archivo).resolve() if archivo else d / "docker-compose.yml"
        self._dentro_de_la_raiz(a)
        if not a.is_file():
            raise ErrorDocker(f"no existe el compose {a}")
        return d, a

    # -- primitivas --------------------------------------------------------- #

    def _compose(self, directorio, argumentos, archivo=None,
                 timeout=TIMEOUT_CORTO, tolerar_error=False):
        d, a = self._validar(directorio, archivo)
        comando = ["docker", "compose",
                   "--project-directory", str(d),
                   "-f", str(a),
                   *argumentos]
        codigo, salida, error = self.ejecutar(comando, timeout=timeout)
        if codigo != 0 and not tolerar_error:
            raise ErrorDocker(
                f"docker compose {argumentos[0]} falló en {d.name}",
                codigo=codigo, salida=salida, error=error,
            )
        return codigo, salida, error

    # -- operaciones permitidas --------------------------------------------- #

    def pull(self, directorio, archivo=None):
        """Descarga las imágenes del compose sin tocar lo que está corriendo."""
        _codigo, salida, error = self._compose(
            directorio, ["pull", "--quiet"], archivo=archivo, timeout=TIMEOUT_LARGO)
        return salida or error

    def up(self, directorio, archivo=None):
        """Aplica el compose. No reconstruye nada: solo imágenes publicadas."""
        _codigo, salida, error = self._compose(
            directorio, ["up", "-d", "--remove-orphans", "--no-build"],
            archivo=archivo, timeout=TIMEOUT_LARGO)
        return salida or error

    def down(self, directorio):
        _codigo, salida, _error = self._compose(directorio, ["down"], timeout=TIMEOUT_LARGO)
        return salida

    def ps(self, directorio):
        """Estado de los servicios. Devuelve [{servicio, estado, salud}]."""
        _codigo, salida, _error = self._compose(
            directorio, ["ps", "--all", "--format", "json"], tolerar_error=True)
        return self._parsear_ps(salida)

    @staticmethod
    def _parsear_ps(salida):
        """docker compose ps devuelve un objeto por línea en las versiones nuevas
        y un array en las viejas. Se soportan las dos."""
        texto = (salida or "").strip()
        if not texto:
            return []
        crudos = []
        if texto.startswith("["):
            try:
                crudos = json.loads(texto)
            except json.JSONDecodeError:
                return []
        else:
            for linea in texto.splitlines():
                linea = linea.strip()
                if not linea:
                    continue
                try:
                    crudos.append(json.loads(linea))
                except json.JSONDecodeError:
                    continue

        salida_normalizada = []
        for c in crudos:
            if not isinstance(c, dict):
                continue
            salida_normalizada.append({
                "servicio": c.get("Service") or c.get("Name") or "",
                "estado": (c.get("State") or "").lower(),
                "salud": (c.get("Health") or "").lower(),
            })
        return salida_normalizada

    def logs(self, directorio, servicio=None, lineas=200):
        argumentos = ["logs", "--no-color", "--tail", str(int(lineas))]
        if servicio:
            argumentos.append(servicio)
        _codigo, salida, error = self._compose(directorio, argumentos, tolerar_error=True)
        return salida or error

    def imagen_presente(self, referencia):
        """True si la imagen ya está en el host. Se consulta por la referencia
        completa con digest, así que responde por el binario exacto."""
        codigo, _salida, _error = self.ejecutar(
            ["docker", "image", "inspect", "--format", "{{.Id}}", referencia])
        return codigo == 0

    # -- utilidades sin docker ---------------------------------------------- #

    @staticmethod
    def espacio_libre_gb(directorio):
        uso = shutil.disk_usage(str(directorio))
        return uso.free / (1024 ** 3)

    @staticmethod
    def disponible():
        return shutil.which("docker") is not None
