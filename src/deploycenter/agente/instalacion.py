"""Una instalación: el directorio de stack de un producto en el host del cliente.

    /opt/accusys/mep/
    ├── docker-compose.yml        generado, no se edita a mano
    ├── .env                      del cliente, nunca sale de acá
    └── .deploycenter/
        ├── estado.json           qué versión y qué digests corren hoy
        ├── historial.jsonl       append-only, quién hizo qué y cuándo
        ├── despliegue.lock       mientras hay una operación en curso
        ├── manifiestos/4.7.0.json
        └── retorno/              el punto al que se vuelve si algo falla
            ├── docker-compose.yml
            └── manifiesto.json

Un host con dos productos tiene dos directorios como este. El agente es uno solo
y los atiende a los dos: cada uno con su estado, su historial y su punto de
retorno independientes.
"""

import datetime
import json
import os
from pathlib import Path

from ..errores import ErrorArchivo, ErrorDeployCenter

INTERNO = ".deploycenter"
COMPOSE = "docker-compose.yml"
ENTORNO = ".env"

ESTADO_OK = "ok"
ESTADO_DESPLEGANDO = "desplegando"
ESTADO_DEGRADADO = "degradado"


class ErrorBloqueo(ErrorDeployCenter):
    """Ya hay un despliegue en curso sobre esta instalación."""


def ahora():
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat()


class Paquete:
    """Lo que hace falta para desplegar una versión: manifiesto + plantilla.

    En Fase 1 lo baja una persona; en Fase 2 lo entrega el hub. La forma es la
    misma en los dos casos, así que el agente no cambia cuando aparezca el hub.
    """

    def __init__(self, directorio):
        self.directorio = Path(directorio)
        self.ruta_manifiesto = self.directorio / "manifiesto.json"
        self.ruta_plantilla = self.directorio / "compose.plantilla.yaml"
        self.ruta_firma = self.directorio / "manifiesto.json.sig"

    def existe(self):
        return self.ruta_manifiesto.is_file() and self.ruta_plantilla.is_file()

    def exigir(self):
        if not self.ruta_manifiesto.is_file():
            raise ErrorArchivo(f"el paquete no tiene manifiesto.json: {self.directorio}")
        if not self.ruta_plantilla.is_file():
            raise ErrorArchivo(
                f"el paquete no tiene compose.plantilla.yaml: {self.directorio}")
        return self

    def manifiesto(self):
        from .. import manifiesto as mf
        return mf.cargar(self.ruta_manifiesto)

    def tiene_firma(self):
        return self.ruta_firma.is_file()


class Instalacion:
    def __init__(self, directorio):
        self.directorio = Path(directorio)
        self.interno = self.directorio / INTERNO
        self.ruta_compose = self.directorio / COMPOSE
        self.ruta_entorno = self.directorio / ENTORNO
        self.ruta_estado = self.interno / "estado.json"
        self.ruta_historial = self.interno / "historial.jsonl"
        self.ruta_lock = self.interno / "despliegue.lock"
        self.dir_retorno = self.interno / "retorno"
        self.dir_manifiestos = self.interno / "manifiestos"

    # -- ciclo de vida ------------------------------------------------------ #

    def preparar(self):
        for d in (self.interno, self.dir_retorno, self.dir_manifiestos):
            d.mkdir(parents=True, exist_ok=True)
        return self

    def inicializada(self):
        return self.ruta_estado.is_file()

    # -- estado ------------------------------------------------------------- #

    def estado(self):
        if not self.ruta_estado.is_file():
            return {}
        try:
            return json.loads(self.ruta_estado.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise ErrorArchivo(f"{self.ruta_estado} está corrupto: {e}") from e

    def guardar_estado(self, **campos):
        self.preparar()
        actual = self.estado()
        actual.update(campos)
        actual["actualizado"] = ahora()
        self.ruta_estado.write_text(
            json.dumps(actual, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return actual

    def version_instalada(self):
        return self.estado().get("version")

    def producto(self):
        return self.estado().get("producto")

    # -- entorno ------------------------------------------------------------ #

    def entorno(self):
        from .. import variables as vars_
        if not self.ruta_entorno.is_file():
            return {}
        return vars_.leer_env(self.ruta_entorno)

    def escribir_entorno(self, entorno):
        from .. import variables as vars_
        return vars_.escribir_env(entorno, self.ruta_entorno)

    # -- historial ---------------------------------------------------------- #

    def registrar(self, operacion, resultado, **detalle):
        """Append-only: el historial no se edita, solo crece."""
        self.preparar()
        evento = {"ts": ahora(), "operacion": operacion, "resultado": resultado}
        evento.update(detalle)
        with self.ruta_historial.open("a", encoding="utf-8") as f:
            f.write(json.dumps(evento, ensure_ascii=False) + "\n")
        return evento

    def historial(self, limite=None):
        if not self.ruta_historial.is_file():
            return []
        eventos = []
        for linea in self.ruta_historial.read_text(encoding="utf-8").splitlines():
            linea = linea.strip()
            if not linea:
                continue
            try:
                eventos.append(json.loads(linea))
            except json.JSONDecodeError:
                continue
        return eventos[-limite:] if limite else eventos

    # -- punto de retorno --------------------------------------------------- #

    def tomar_punto_retorno(self, manifiesto_actual=None):
        """Copia el compose vigente y el manifiesto que lo generó.

        Se toma ANTES de tocar nada. Es lo que hace que el rollback sea exacto:
        el compose guardado tiene los digests que están corriendo en este
        momento, no un tag que mañana puede apuntar a otra cosa.
        """
        self.preparar()
        if not self.ruta_compose.is_file():
            raise ErrorArchivo(
                f"no hay {COMPOSE} en {self.directorio}: no se puede tomar punto de retorno")
        destino = self.dir_retorno / COMPOSE
        destino.write_bytes(self.ruta_compose.read_bytes())

        if manifiesto_actual is not None:
            (self.dir_retorno / "manifiesto.json").write_text(
                json.dumps(manifiesto_actual, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8")

        self.guardar_estado(retorno_tomado=ahora())
        return destino

    def hay_punto_retorno(self):
        return (self.dir_retorno / COMPOSE).is_file()

    def manifiesto_de_retorno(self):
        ruta = self.dir_retorno / "manifiesto.json"
        if not ruta.is_file():
            return None
        return json.loads(ruta.read_text(encoding="utf-8"))

    def restaurar_punto_retorno(self):
        """Devuelve el compose anterior a su lugar. No levanta nada: eso lo hace
        quien orquesta, que es el único que sabe si además hay que avisar."""
        origen = self.dir_retorno / COMPOSE
        if not origen.is_file():
            raise ErrorArchivo(f"no hay punto de retorno en {self.dir_retorno}")
        self.ruta_compose.write_bytes(origen.read_bytes())
        return self.ruta_compose

    # -- manifiestos aplicados ---------------------------------------------- #

    def guardar_manifiesto(self, manifiesto):
        self.preparar()
        ruta = self.dir_manifiestos / f"{manifiesto['release']}.json"
        ruta.write_text(json.dumps(manifiesto, indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8")
        return ruta

    def manifiesto_aplicado(self, version=None):
        version = version or self.version_instalada()
        if not version:
            return None
        ruta = self.dir_manifiestos / f"{version}.json"
        if not ruta.is_file():
            return None
        return json.loads(ruta.read_text(encoding="utf-8"))

    # -- bloqueo ------------------------------------------------------------ #

    def bloquear(self, dueno="cli"):
        """Lock por instalación: dos despliegues en paralelo sobre el mismo
        producto es la forma más rápida de dejarlo en un estado incoherente."""
        self.preparar()
        try:
            fd = os.open(str(self.ruta_lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            detalle = ""
            try:
                detalle = self.ruta_lock.read_text(encoding="utf-8").strip()
            except OSError:
                pass
            raise ErrorBloqueo(
                f"ya hay un despliegue en curso sobre {self.directorio.name}"
                + (f" ({detalle})" if detalle else "")
            ) from None
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(f"{dueno} {ahora()}")
        return self.ruta_lock

    def desbloquear(self):
        try:
            self.ruta_lock.unlink()
        except FileNotFoundError:
            pass

    def bloqueada(self):
        return self.ruta_lock.exists()


def descubrir_instalaciones(raiz):
    """Cada subdirectorio con un compose o con estado previo es una instalación."""
    raiz = Path(raiz)
    if not raiz.is_dir():
        return []
    salida = []
    for d in sorted(raiz.iterdir()):
        if not d.is_dir():
            continue
        if (d / "docker-compose.yml").is_file() or (d / ".deploycenter").is_dir():
            salida.append(Instalacion(d))
    return salida
