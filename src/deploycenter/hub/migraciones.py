"""Aplica las migraciones SQL de `supabase/migrations/` a un Postgres.

En un proyecto de Supabase lo mismo se puede hacer con `supabase db push`, que
lleva su propio registro; esto es para cualquier otro Postgres (el local de los
tests, uno administrado) y lleva el registro en `dc_migraciones`.

Cuando no hay salida al puerto de Postgres (solo HTTPS), `ApiSupabase` aplica
las mismas migraciones por la Management API de Supabase, con el mismo
registro. Así un proyecto puede empezar por un camino y seguir por el otro.

Cada archivo corre en su propia transacción: si uno falla, quedan aplicados los
anteriores y ese no.
"""

import json
import os
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from ..errores import ErrorDeployCenter

TABLA_REGISTRO = "dc_migraciones"
API_SUPABASE = "https://api.supabase.com"

_CREAR_REGISTRO = (
    f"create table if not exists {TABLA_REGISTRO} ("
    "nombre varchar(200) primary key, "
    "aplicada timestamp not null default (now() at time zone 'utc'))")
# el nombre va dentro del SQL (la API no recibe parámetros): solo nombres sin comillas
_NOMBRE_VALIDO = re.compile(r"^[0-9A-Za-z_.-]+\.sql$")


def directorio_por_defecto(raiz):
    return Path(raiz) / "supabase" / "migrations"


def pendientes(destino, directorio):
    """`destino` es un engine de SQLAlchemy o una `ApiSupabase`."""
    aplicadas = destino.aplicadas() if isinstance(destino, ApiSupabase) else _aplicadas(destino)
    return [r for r in sorted(Path(directorio).glob("*.sql")) if r.name not in aplicadas]


def aplicar(engine, directorio):
    """Aplica lo que falta. Devuelve los nombres de los archivos aplicados."""
    if isinstance(engine, ApiSupabase):
        return _aplicar_por_api(engine, directorio)
    if engine.dialect.name != "postgresql":
        raise ErrorDeployCenter(
            "las migraciones son SQL de Postgres; con SQLite el hub crea las tablas solo")
    directorio = Path(directorio)
    if not directorio.is_dir():
        raise ErrorDeployCenter(f"no existe el directorio de migraciones {directorio}")

    aplicadas = []
    for ruta in pendientes(engine, directorio):
        sql = ruta.read_text(encoding="utf-8")
        with engine.begin() as conexion:
            # conexión cruda: el SQL trae varias sentencias y usa % en format(),
            # así que no pasa por la interpolación de parámetros
            cursor = conexion.connection.driver_connection.cursor()
            cursor.execute(sql)
            cursor.execute(f"insert into {TABLA_REGISTRO} (nombre) values (%s)", (ruta.name,))
        aplicadas.append(ruta.name)
    return aplicadas


def _aplicadas(engine):
    with engine.begin() as conexion:
        cursor = conexion.connection.driver_connection.cursor()
        cursor.execute(_CREAR_REGISTRO)
        cursor.execute(f"select nombre from {TABLA_REGISTRO}")
        return {fila[0] for fila in cursor.fetchall()}


def _aplicar_por_api(api, directorio):
    directorio = Path(directorio)
    if not directorio.is_dir():
        raise ErrorDeployCenter(f"no existe el directorio de migraciones {directorio}")
    aplicadas = []
    for ruta in pendientes(api, directorio):
        if not _NOMBRE_VALIDO.match(ruta.name):
            raise ErrorDeployCenter(f"nombre de migración inválido: {ruta.name!r}")
        sql = ruta.read_text(encoding="utf-8")
        # la API corre el texto entero en una sola llamada: el begin/commit explícito
        # deja el archivo y su registro en la misma transacción
        api.consultar(f"begin;\n{sql}\n;\n"
                      f"insert into {TABLA_REGISTRO} (nombre) values ('{ruta.name}');\n"
                      "commit;")
        aplicadas.append(ruta.name)
    return aplicadas


# --------------------------------------------------------------------------- #
# Management API de Supabase
# --------------------------------------------------------------------------- #

def transporte_urllib():
    """POST con JSON; `build_opener` toma el proxy de las variables de entorno."""
    opener = urllib.request.build_opener(
        urllib.request.HTTPSHandler(context=ssl.create_default_context()))

    def enviar(url, cuerpo, cabeceras, timeout):
        pedido = urllib.request.Request(url, data=json.dumps(cuerpo).encode("utf-8"),
                                        method="POST", headers=cabeceras)
        try:
            with opener.open(pedido, timeout=timeout) as r:
                return r.status, _json_o_texto(r.read())
        except urllib.error.HTTPError as e:
            return e.code, _json_o_texto(e.read())
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            raise ErrorDeployCenter(f"no se pudo hablar con la API de Supabase: {e}") from e

    return enviar


def _json_o_texto(datos):
    try:
        return json.loads(datos) if datos else None
    except ValueError:
        return datos[:300].decode("utf-8", "replace")


def ref_de_url(url):
    """`https://<ref>.supabase.co` → `<ref>`."""
    host = urllib.parse.urlsplit(url or "").hostname or ""
    ref, _, dominio = host.partition(".")
    if not ref or dominio != "supabase.co":
        raise ErrorDeployCenter(
            f"SUPABASE_URL tiene que ser https://<ref>.supabase.co (vino {host or url!r})")
    return ref


class ApiSupabase:
    """SQL contra un proyecto de Supabase por `POST /v1/projects/{ref}/database/query`.

    Corre como `postgres` (el dueño de las tablas), igual que `dc-hub migrar` con
    la cadena de conexión. El token es un personal access token (`sbp_…`).
    """

    def __init__(self, ref, token, transporte=None, base=API_SUPABASE, timeout=60):
        if not token:
            raise ErrorDeployCenter("falta el token de la Management API (SUPABASE_ACCESS_TOKEN)")
        self.ref = ref
        self.token = token
        self.transporte = transporte or transporte_urllib()
        self.url = f"{base.rstrip('/')}/v1/projects/{ref}/database/query"
        self.timeout = timeout

    @classmethod
    def desde_entorno(cls, transporte=None, entorno=None):
        entorno = os.environ if entorno is None else entorno
        if not entorno.get("SUPABASE_URL"):
            raise ErrorDeployCenter("--via-api necesita SUPABASE_URL (https://<ref>.supabase.co)")
        return cls(ref_de_url(entorno["SUPABASE_URL"]), entorno.get("SUPABASE_ACCESS_TOKEN"),
                   transporte=transporte)

    def consultar(self, sql):
        """Devuelve las filas del último resultado, como diccionarios."""
        estado, cuerpo = self.transporte(
            self.url, {"query": sql},
            {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json",
             "Accept": "application/json"},
            self.timeout)
        if not 200 <= estado < 300:
            detalle = cuerpo.get("message", cuerpo) if isinstance(cuerpo, dict) else cuerpo
            raise ErrorDeployCenter(f"la API de Supabase respondió {estado}: {detalle}")
        return cuerpo if isinstance(cuerpo, list) else []

    def aplicadas(self):
        filas = self.consultar(f"{_CREAR_REGISTRO};\nselect nombre from {TABLA_REGISTRO}")
        return {f["nombre"] for f in filas}
