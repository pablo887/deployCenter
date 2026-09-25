"""`dc-hub migrar --via-api`: las migraciones por la Management API de Supabase.

El transporte es falso: uno en memoria para el protocolo (URL, cabeceras,
errores) y otro que ejecuta el SQL en el Postgres efímero de los tests, para
probar que las migraciones reales pasan por este camino igual que por el otro.
"""

import re

import pytest

from deploycenter.errores import ErrorDeployCenter
from deploycenter.hub import cli, migraciones
from deploycenter.hub.migraciones import ApiSupabase

REF = "abcdefghijklmnopqrst"
ENTORNO = {"SUPABASE_URL": f"https://{REF}.supabase.co", "SUPABASE_ACCESS_TOKEN": "sbp_prueba"}


class ApiEnMemoria:
    """Hace de la API: lleva el registro de `dc_migraciones` y anota cada pedido."""

    def __init__(self, fallar_con=None):
        self.pedidos = []
        self.registro = set()
        self.fallar_con = fallar_con

    def __call__(self, url, cuerpo, cabeceras, timeout):
        self.pedidos.append((url, cuerpo["query"], cabeceras))
        sql = cuerpo["query"]
        if self.fallar_con and self.fallar_con in sql:
            return 400, {"message": "ERROR: 42P07: relation already exists"}
        if sql.rstrip().endswith(f"select nombre from {migraciones.TABLA_REGISTRO}"):
            return 201, [{"nombre": n} for n in sorted(self.registro)]
        self.registro.update(re.findall(r"values \('([^']+)'\)", sql))
        return 201, []


@pytest.fixture
def directorio(tmp_path):
    (tmp_path / "001_uno.sql").write_text("create table uno (x int);")
    (tmp_path / "002_dos.sql").write_text("create table dos (x int);")
    return tmp_path


class TestProtocolo:
    def test_ref_y_token_salen_del_entorno(self):
        api = ApiSupabase.desde_entorno(transporte=ApiEnMemoria(), entorno=ENTORNO)
        assert api.url == f"https://api.supabase.com/v1/projects/{REF}/database/query"

    def test_manda_el_token_como_bearer(self, directorio):
        falsa = ApiEnMemoria()
        ApiSupabase(REF, "sbp_prueba", transporte=falsa).aplicadas()
        _url, _sql, cabeceras = falsa.pedidos[0]
        assert cabeceras["Authorization"] == "Bearer sbp_prueba"

    @pytest.mark.parametrize("url", ["", "https://ejemplo.com", "http://localhost:54321"])
    def test_url_que_no_es_de_supabase(self, url):
        with pytest.raises(ErrorDeployCenter, match="SUPABASE_URL"):
            ApiSupabase.desde_entorno(entorno={**ENTORNO, "SUPABASE_URL": url})

    def test_sin_token(self):
        with pytest.raises(ErrorDeployCenter, match="SUPABASE_ACCESS_TOKEN"):
            ApiSupabase.desde_entorno(entorno={"SUPABASE_URL": ENTORNO["SUPABASE_URL"]})

    def test_el_error_de_la_api_llega_con_su_mensaje(self, directorio):
        api = ApiSupabase(REF, "t", transporte=ApiEnMemoria(fallar_con="dos"))
        with pytest.raises(ErrorDeployCenter, match="400.*already exists"):
            migraciones.aplicar(api, directorio)

    def test_nombre_con_comillas_no_entra_al_sql(self, tmp_path):
        (tmp_path / "001_o'brien.sql").write_text("select 1;")
        api = ApiSupabase(REF, "t", transporte=ApiEnMemoria())
        with pytest.raises(ErrorDeployCenter, match="inválido"):
            migraciones.aplicar(api, tmp_path)


class TestAplicar:
    def test_cada_archivo_va_con_su_registro_en_una_transaccion(self, directorio):
        falsa = ApiEnMemoria()
        api = ApiSupabase(REF, "t", transporte=falsa)
        assert migraciones.aplicar(api, directorio) == ["001_uno.sql", "002_dos.sql"]
        sql = falsa.pedidos[1][1]
        assert sql.startswith("begin;") and sql.rstrip().endswith("commit;")
        assert "create table uno" in sql and "values ('001_uno.sql')" in sql

    def test_es_idempotente(self, directorio):
        api = ApiSupabase(REF, "t", transporte=ApiEnMemoria())
        migraciones.aplicar(api, directorio)
        assert migraciones.aplicar(api, directorio) == []
        assert migraciones.pendientes(api, directorio) == []

    def test_si_falla_uno_quedan_los_anteriores(self, directorio):
        falsa = ApiEnMemoria(fallar_con="create table dos")
        api = ApiSupabase(REF, "t", transporte=falsa)
        with pytest.raises(ErrorDeployCenter):
            migraciones.aplicar(api, directorio)
        assert falsa.registro == {"001_uno.sql"}
        assert [r.name for r in migraciones.pendientes(api, directorio)] == ["002_dos.sql"]

    def test_la_cli_no_toca_la_base(self, directorio, monkeypatch, capsys):
        falsa = ApiEnMemoria()
        monkeypatch.setattr(migraciones, "transporte_urllib", lambda: falsa)
        for k, v in ENTORNO.items():
            monkeypatch.setenv(k, v)
        argv = ["--db", "postgresql+psycopg://nadie@no-existe/x", "--migraciones",
                str(directorio), "migrar", "--via-api"]
        assert cli.main(argv) == 0
        assert capsys.readouterr().out.split() == ["001_uno.sql", "002_dos.sql"]
        assert cli.main(argv) == 0
        assert capsys.readouterr().out.strip() == "todo al día"

    def test_la_cli_sin_entorno_explica_que_falta(self, monkeypatch, capsys):
        monkeypatch.delenv("SUPABASE_URL", raising=False)
        assert cli.main(["migrar", "--via-api"]) != 0
        assert "SUPABASE_URL" in capsys.readouterr().err


# --------------------------------------------------------------------------- #
# las migraciones reales, por la "API", contra el Postgres efímero
# --------------------------------------------------------------------------- #

@pytest.fixture
def base_vacia(postgres_servidor):
    import uuid

    from sqlalchemy import create_engine
    from sqlalchemy.engine import make_url

    nombre = "t_" + uuid.uuid4().hex[:12]
    admin = create_engine(postgres_servidor, isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        c.exec_driver_sql(f"create database {nombre}")
    url = make_url(postgres_servidor).set(database=nombre)
    try:
        yield url
    finally:
        with admin.connect() as c:
            c.exec_driver_sql(f"drop database if exists {nombre} with (force)")
        admin.dispose()


def api_sobre_postgres(url):
    """La API ejecuta el texto tal cual (varias sentencias) y devuelve el último
    resultado como lista de objetos; si algo falla, 400 con el mensaje."""
    import psycopg

    conninfo = url.set(drivername="postgresql").render_as_string(hide_password=False)

    def enviar(_url, cuerpo, _cabeceras, _timeout):
        with psycopg.connect(conninfo, autocommit=True) as conexion:
            cursor = conexion.cursor()
            try:
                cursor.execute(cuerpo["query"])
            except psycopg.Error as e:
                return 400, {"message": f"Failed to run sql query: {e}"}
            while cursor.nextset():  # psycopg deja parado en el primero
                pass
            if cursor.description is None:
                return 201, []
            columnas = [d.name for d in cursor.description]
            return 201, [dict(zip(columnas, fila, strict=True)) for fila in cursor.fetchall()]

    return enviar


class TestContraPostgres:
    def test_aplica_las_migraciones_del_repo_y_es_idempotente(self, base_vacia):
        from conftest import RAIZ
        from sqlalchemy import create_engine, text

        directorio = migraciones.directorio_por_defecto(RAIZ)
        api = ApiSupabase(REF, "t", transporte=api_sobre_postgres(base_vacia))
        aplicadas = migraciones.aplicar(api, directorio)
        assert aplicadas == sorted(r.name for r in directorio.glob("*.sql"))
        assert migraciones.aplicar(api, directorio) == []

        # el camino por engine ve el mismo registro: no reaplica nada
        engine = create_engine(base_vacia)
        assert migraciones.aplicar(engine, directorio) == []
        with engine.connect() as c:
            politicas = c.execute(text("select count(*) from pg_policies")).scalar()
            hook = c.execute(text(
                "select 1 from pg_proc where proname = 'custom_access_token_hook'")).scalar()
        engine.dispose()
        assert politicas > 11 and hook == 1

    def test_un_archivo_que_falla_no_deja_nada_a_medias(self, base_vacia, tmp_path):
        from sqlalchemy import create_engine, text

        (tmp_path / "001_bien.sql").write_text("create table bien (x int);")
        (tmp_path / "002_mal.sql").write_text("create table a_medias (x int);\nselect 1/0;")
        api = ApiSupabase(REF, "t", transporte=api_sobre_postgres(base_vacia))
        with pytest.raises(ErrorDeployCenter, match="division by zero"):
            migraciones.aplicar(api, tmp_path)

        engine = create_engine(base_vacia)
        with engine.connect() as c:
            assert c.execute(text("select to_regclass('bien')")).scalar() == "bien"
            assert c.execute(text("select to_regclass('a_medias')")).scalar() is None
        engine.dispose()
        assert [r.name for r in migraciones.pendientes(api, tmp_path)] == ["002_mal.sql"]
