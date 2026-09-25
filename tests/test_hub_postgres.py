"""RLS contra un Postgres real.

Estos tests no pasan por el hub: hablan SQL directo, como lo haría cualquiera
que consiguiera un JWT válido. Lo que prueban es que el aislamiento vive en la
base: una consulta sin filtro igual no devuelve filas de otro cliente, y una
escritura que la política no admite se rechaza aunque la aplicación se
equivoque.
"""

import datetime
import json
import uuid
from contextlib import contextmanager

import pytest

sqlalchemy = pytest.importorskip("sqlalchemy")
pytest.importorskip("psycopg")

from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.exc import DBAPIError, ProgrammingError  # noqa: E402

AHORA = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None, microsecond=0)

U = {nombre: str(uuid.uuid4()) for nombre in (
    "operador_andino", "aprobador_andino", "lector_andino", "operador_litoral",
    "soporte", "comercial", "sin_alta")}


@pytest.fixture
def db(postgres_url):
    engine = create_engine(postgres_url)
    with engine.begin() as c:
        for t, nombre in (("andino", "Banco Andino"), ("litoral", "Banco Litoral Unido")):
            c.execute(text("insert into tenants (id, nombre) values (:t, :n)"),
                      {"t": t, "n": nombre})
            c.execute(text("insert into tenant_productos (tenant_id, producto, mantenimiento_hasta)"
                           " values (:t, 'mep', '2026-12-31')"), {"t": t})
            c.execute(text(
                "insert into agentes (id, tenant_id, host, token_hash, enrolado)"
                " values (:a, :t, :h, :th, :ahora)"),
                {"a": f"ag-{t}", "t": t, "h": f"srv-{t}", "th": "h" * 60 + t[:4],
                 "ahora": AHORA})
            c.execute(text(
                "insert into instalaciones (agente_id, nombre, producto, version, actualizado)"
                " values (:a, 'mep', 'mep', '4.6.0', :ahora)"), {"a": f"ag-{t}", "ahora": AHORA})
            c.execute(text(
                "insert into ordenes (id, tenant_id, agente_id, instalacion, tipo, producto,"
                " release, estado, pedida_por, creada) values (:o, :t, :a, 'mep', 'preflight',"
                " 'mep', '4.7.0', 'terminada', 'semilla', :ahora)"),
                {"o": f"ord-{t}", "t": t, "a": f"ag-{t}", "ahora": AHORA})
            c.execute(text("insert into eventos_orden (orden_id, recibido, evento)"
                           " values (:o, :ahora, 'orden_recibida')"),
                      {"o": f"ord-{t}", "ahora": AHORA})
        for clave, (t, rol) in {"operador_andino": ("andino", "operador"),
                                "aprobador_andino": ("andino", "aprobador"),
                                "lector_andino": ("andino", "lector"),
                                "operador_litoral": ("litoral", "operador")}.items():
            c.execute(text("insert into usuarios_tenant (usuario_id, tenant_id, rol)"
                           " values (:u, :t, :r)"), {"u": U[clave], "t": t, "r": rol})
        for clave in ("soporte", "comercial"):
            c.execute(text("insert into usuarios_accusys (usuario_id, rol) values (:u, :r)"),
                      {"u": U[clave], "r": clave})
    yield engine
    engine.dispose()


@contextmanager
def como(engine, quien, aal="aal2", rol="authenticated"):
    """Una transacción con la identidad de alguien, como la arma el hub o PostgREST."""
    claims = {"sub": U[quien], "aal": aal, "role": "authenticated"} if quien else {}
    with engine.connect() as c:
        tx = c.begin()
        c.execute(text("select set_config('request.jwt.claims', :c, true)"),
                  {"c": json.dumps(claims)})
        c.execute(text(f"set local role {rol}"))
        try:
            yield c
        finally:
            tx.rollback()


def ids(c, tabla, columna="id"):
    return sorted(r[0] for r in c.execute(text(f"select {columna} from {tabla}")))


def falla(c, sql, params=None):
    """La sentencia la rechaza la base por RLS o por permisos, y no por otra cosa:
    un error de sintaxis en el test no puede pasar por un rechazo."""
    with pytest.raises((ProgrammingError, DBAPIError)) as e:
        with c.begin_nested():
            c.execute(text(sql), params or {})
    mensaje = str(e.value.orig)
    assert "row-level security" in mensaje or "permission denied" in mensaje, mensaje


class TestAislamiento:
    @pytest.mark.parametrize("tabla,columna,esperado", [
        ("tenants", "id", ["andino"]),
        ("tenant_productos", "tenant_id", ["andino"]),
        ("agentes", "id", ["ag-andino"]),
        ("instalaciones", "agente_id", ["ag-andino"]),
        ("ordenes", "id", ["ord-andino"]),
        ("eventos_orden", "orden_id", ["ord-andino"]),
    ])
    def test_sin_filtro_solo_ve_lo_suyo(self, db, tabla, columna, esperado):
        with como(db, "operador_andino") as c:
            assert ids(c, tabla, columna) == esperado

    def test_el_otro_cliente_tampoco_ve_lo_ajeno(self, db):
        with como(db, "operador_litoral") as c:
            assert ids(c, "ordenes") == ["ord-litoral"]

    def test_accusys_ve_todo_el_parque(self, db):
        with como(db, "soporte") as c:
            assert ids(c, "ordenes") == ["ord-andino", "ord-litoral"]
            assert ids(c, "tenants") == ["andino", "litoral"]

    def test_un_usuario_sin_alta_no_ve_nada(self, db):
        with como(db, "sin_alta") as c:
            for tabla in ("tenants", "ordenes", "agentes", "instalaciones"):
                assert ids(c, tabla, "1") == [], tabla

    def test_sin_segundo_factor_no_ve_nada(self, db):
        with como(db, "soporte", aal="aal1") as c:
            assert ids(c, "tenants") == []
            assert ids(c, "ordenes") == []

    def test_sin_claims_no_ve_nada(self, db):
        with como(db, None) as c:
            assert ids(c, "tenants") == []

    def test_anon_no_tiene_permisos(self, db):
        with como(db, None, rol="anon") as c:
            falla(c, "select id from tenants")

    def test_quitar_el_rol_corta_el_acceso_en_el_acto(self, db):
        """El rol se lee de la tabla, no del token: no hay que esperar que venza."""
        with db.begin() as c:
            c.execute(text("delete from usuarios_tenant where usuario_id = :u"),
                      {"u": U["operador_andino"]})
        with como(db, "operador_andino") as c:
            assert ids(c, "ordenes") == []


class TestColumnasOcultas:
    def test_el_hash_del_token_del_agente_no_se_lee(self, db):
        with como(db, "soporte") as c:
            falla(c, "select token_hash from agentes")
            assert ids(c, "agentes", "host") == ["srv-andino", "srv-litoral"]

    def test_el_hash_del_codigo_no_se_lee(self, db):
        with como(db, "soporte") as c:
            falla(c, "select hash from codigos_enrolamiento")


class TestOrdenes:
    INSERTAR = ("insert into ordenes (id, tenant_id, agente_id, instalacion, tipo, producto,"
                " release, pedida_por, pedida_por_id, creada) values (:id, :t, :a, 'mep',"
                " 'desplegar', 'mep', '4.7.0', 'x', :por, now())")

    def _orden(self, t, por):
        return {"id": "ord-" + uuid.uuid4().hex[:8], "t": t, "a": f"ag-{t}", "por": U[por]}

    def test_el_operador_ordena_en_su_cliente(self, db):
        with como(db, "operador_andino") as c:
            c.execute(text(self.INSERTAR), self._orden("andino", "operador_andino"))

    def test_el_operador_no_ordena_en_otro_cliente(self, db):
        with como(db, "operador_andino") as c:
            falla(c, self.INSERTAR, self._orden("litoral", "operador_andino"))

    def test_no_se_firma_una_orden_a_nombre_de_otro(self, db):
        with como(db, "operador_andino") as c:
            falla(c, self.INSERTAR, self._orden("andino", "aprobador_andino"))

    @pytest.mark.parametrize("quien", ["lector_andino", "aprobador_andino", "comercial"])
    def test_otros_roles_no_ordenan(self, db, quien):
        with como(db, quien) as c:
            falla(c, self.INSERTAR, self._orden("andino", quien))

    def test_soporte_sin_habilitacion_no_ordena(self, db):
        with como(db, "soporte") as c:
            falla(c, self.INSERTAR, self._orden("andino", "soporte"))

    def _habilitar(self, db, horas=2, revocada=False):
        """Como dueño: la política del Aprobador para otorgarla se prueba aparte."""
        with db.begin() as c:
            c.execute(text(
                "insert into habilitaciones (tenant_id, otorgada_por, otorgada, vence, revocada)"
                " values ('andino', :u, now() at time zone 'utc',"
                " now() at time zone 'utc' + make_interval(hours => :h), :rev)"),
                {"u": U["aprobador_andino"], "h": horas, "rev": AHORA if revocada else None})

    def test_soporte_con_habilitacion_ordena_solo_en_ese_cliente(self, db):
        self._habilitar(db)
        with como(db, "soporte") as c:
            c.execute(text(self.INSERTAR), self._orden("andino", "soporte"))
            falla(c, self.INSERTAR, self._orden("litoral", "soporte"))

    def test_una_habilitacion_vencida_no_sirve(self, db):
        with db.begin() as c:
            c.execute(text(
                "insert into habilitaciones (tenant_id, otorgada_por, otorgada, vence) values"
                " ('andino', :u, now() at time zone 'utc' - interval '3 hours',"
                " now() at time zone 'utc' - interval '1 hour')"), {"u": U["aprobador_andino"]})
        with como(db, "soporte") as c:
            falla(c, self.INSERTAR, self._orden("andino", "soporte"))

    def test_una_habilitacion_revocada_no_sirve(self, db):
        self._habilitar(db, revocada=True)
        with como(db, "soporte") as c:
            falla(c, self.INSERTAR, self._orden("andino", "soporte"))


class TestHabilitaciones:
    INSERTAR = ("insert into habilitaciones (tenant_id, otorgada_por, otorgada, vence)"
                " values (:t, :u, now() at time zone 'utc',"
                " now() at time zone 'utc' + interval '1 hour')")

    def test_la_otorga_el_aprobador_de_su_cliente(self, db):
        with como(db, "aprobador_andino") as c:
            c.execute(text(self.INSERTAR), {"t": "andino", "u": U["aprobador_andino"]})

    def test_no_para_otro_cliente(self, db):
        with como(db, "aprobador_andino") as c:
            falla(c, self.INSERTAR, {"t": "litoral", "u": U["aprobador_andino"]})

    @pytest.mark.parametrize("quien", ["operador_andino", "soporte"])
    def test_ni_el_operador_ni_accusys_se_la_dan_solos(self, db, quien):
        with como(db, quien) as c:
            falla(c, self.INSERTAR, {"t": "andino", "u": U[quien]})


class TestAuditoria:
    def test_se_agrega_pero_no_se_edita_ni_se_borra(self, db):
        with como(db, "operador_andino") as c:
            c.execute(text("insert into auditoria (fecha, usuario_id, tenant_id, accion)"
                           " values (now(), :u, 'andino', 'prueba')"),
                      {"u": U["operador_andino"]})
            falla(c, "update auditoria set accion = 'otra'")
            falla(c, "delete from auditoria")

    def test_no_se_registra_a_nombre_de_otro(self, db):
        with como(db, "operador_andino") as c:
            falla(c, "insert into auditoria (fecha, usuario_id, tenant_id, accion)"
                     " values (now(), :u, 'andino', 'prueba')", {"u": U["aprobador_andino"]})


class TestUsuariosYParametria:
    def test_el_aprobador_cambia_roles_de_su_cliente(self, db):
        with como(db, "aprobador_andino") as c:
            r = c.execute(text("update usuarios_tenant set rol = 'operador'"
                               " where usuario_id = :u"), {"u": U["lector_andino"]})
            assert r.rowcount == 1

    def test_el_aprobador_no_se_cambia_su_propio_rol(self, db):
        with como(db, "aprobador_andino") as c:
            r = c.execute(text("update usuarios_tenant set rol = 'operador'"
                               " where usuario_id = :u"), {"u": U["aprobador_andino"]})
            assert r.rowcount == 0

    def test_el_aprobador_no_da_de_alta_en_otro_cliente(self, db):
        with como(db, "aprobador_andino") as c:
            falla(c, "insert into usuarios_tenant (usuario_id, tenant_id, rol)"
                     " values (:u, 'litoral', 'operador')", {"u": str(uuid.uuid4())})

    def test_el_operador_no_administra_usuarios(self, db):
        with como(db, "operador_andino") as c:
            falla(c, "insert into usuarios_tenant (usuario_id, tenant_id, rol)"
                     " values (:u, 'andino', 'operador')", {"u": str(uuid.uuid4())})

    def test_la_parametria_la_edita_comercial(self, db):
        sql = "update tenant_productos set mantenimiento_hasta = '2027-12-31'"
        with como(db, "operador_andino") as c:
            assert c.execute(text(sql)).rowcount == 0
        with como(db, "comercial") as c:
            assert c.execute(text(sql)).rowcount == 2


class TestHook:
    def test_agrega_cliente_y_rol_al_token(self, db):
        evento = {"user_id": U["operador_andino"],
                  "claims": {"sub": U["operador_andino"], "app_metadata": {"provider": "email"}}}
        with db.connect() as c:
            r = c.execute(text("select custom_access_token_hook(cast(:e as jsonb))"),
                          {"e": json.dumps(evento)}).scalar()
        meta = r["claims"]["app_metadata"]
        assert meta == {"provider": "email", "tenant_id": "andino", "dc_rol": "operador"}

    def test_usuario_de_accusys(self, db):
        evento = {"user_id": U["soporte"], "claims": {}}
        with db.connect() as c:
            r = c.execute(text("select custom_access_token_hook(cast(:e as jsonb))"),
                          {"e": json.dumps(evento)}).scalar()
        assert r["claims"]["app_metadata"] == {"tenant_id": None, "dc_rol": "soporte"}


class TestMigraciones:
    def test_son_idempotentes(self, postgres_url):
        from conftest import RAIZ

        from deploycenter.hub import migraciones

        engine = create_engine(postgres_url)
        assert migraciones.aplicar(engine, migraciones.directorio_por_defecto(RAIZ)) == []
        engine.dispose()

    def test_el_mapeo_coincide_con_las_migraciones(self, postgres_url):
        """Si alguien agrega una columna al modelo y no a la migración (o al revés),
        falla acá y no en producción."""
        from deploycenter.hub import modelos as m

        engine = create_engine(postgres_url)
        with engine.connect() as c:
            filas = c.execute(text(
                "select table_name, column_name from information_schema.columns"
                " where table_schema = 'public'")).all()
        engine.dispose()
        en_la_base = {}
        for tabla, columna in filas:
            en_la_base.setdefault(tabla, set()).add(columna)

        for tabla in m.Base.metadata.sorted_tables:
            assert tabla.name in en_la_base, f"falta la tabla {tabla.name} en las migraciones"
            assert {col.name for col in tabla.columns} == en_la_base[tabla.name], tabla.name
        sobran = set(en_la_base) - {t.name for t in m.Base.metadata.sorted_tables} \
            - {"dc_migraciones"}
        assert not sobran, f"tablas en las migraciones sin mapeo: {sobran}"
