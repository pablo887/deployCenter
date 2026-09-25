"""Aplica las migraciones SQL de `supabase/migrations/` a un Postgres.

En un proyecto de Supabase lo mismo se puede hacer con `supabase db push`, que
lleva su propio registro; esto es para cualquier otro Postgres (el local de los
tests, uno administrado) y lleva el registro en `dc_migraciones`.

Cada archivo corre en su propia transacción: si uno falla, quedan aplicados los
anteriores y ese no.
"""

from pathlib import Path

from ..errores import ErrorDeployCenter

TABLA_REGISTRO = "dc_migraciones"


def directorio_por_defecto(raiz):
    return Path(raiz) / "supabase" / "migrations"


def pendientes(engine, directorio):
    aplicadas = _aplicadas(engine)
    return [r for r in sorted(Path(directorio).glob("*.sql")) if r.name not in aplicadas]


def aplicar(engine, directorio):
    """Aplica lo que falta. Devuelve los nombres de los archivos aplicados."""
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
        cursor.execute(
            f"create table if not exists {TABLA_REGISTRO} ("
            "nombre varchar(200) primary key, "
            "aplicada timestamp not null default (now() at time zone 'utc'))")
        cursor.execute(f"select nombre from {TABLA_REGISTRO}")
        return {fila[0] for fila in cursor.fetchall()}
