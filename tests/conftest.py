import copy
import json
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "src"))


@pytest.fixture
def raiz():
    return RAIZ


@pytest.fixture
def base():
    """Manifiesto mínimo y válido. Cada test lo rompe por un lado distinto."""
    return {
        "producto": "mep",
        "release": "4.7.0",
        "publicado": "2026-09-18",
        "canal": "estable",
        "imagenes": {
            "api": "registry.accusys.com.ar/mep/api@sha256:" + "a" * 64,
            "web": "registry.accusys.com.ar/mep/web@sha256:" + "b" * 64,
        },
        "desde_version": ">=4.5.0",
        "db_migrations": False,
        "rollback_seguro": True,
        "variables_nuevas": [],
        "healthchecks": [
            {"servicio": "api", "url": "http://api:8080/health", "espera": 200, "timeout_s": 120},
            {"servicio": "web", "url": "http://web:8080/healthz", "espera": 200, "timeout_s": 60},
        ],
        "changelog": "productos/mep/releases/4.7.0/changelog.md",
    }


@pytest.fixture
def variante(base):
    """Devuelve una copia de `base` con los campos pisados."""
    def _hacer(**campos):
        m = copy.deepcopy(base)
        m.update(campos)
        return m
    return _hacer


@pytest.fixture
def runner_fijo():
    """Ejecutor falso: devuelve siempre lo mismo y registra los comandos."""
    def _hacer(codigo=0, salida="", error=""):
        llamadas = []

        def ejecutar(comando, timeout=120):
            llamadas.append(comando)
            return codigo, salida, error

        ejecutar.llamadas = llamadas
        return ejecutar
    return _hacer


# --------------------------------------------------------------------------- #
# Fase 1: agente
# --------------------------------------------------------------------------- #

PLANTILLA_AGENTE = """\
name: {{ producto }}
services:
  api:
    image: {{ imagenes.api }}
  web:
    image: {{ imagenes.web }}
    ports:
      - "{{ env.get('MEP_EXPONER_EN', '127.0.0.1') }}:${MEP_PUERTO_WEB}:8080"
"""


class Reloj:
    """Reloj y sleep falsos: los tests no esperan de verdad, pero el tiempo
    avanza, así que los timeouts se alcanzan y los loops terminan."""

    def __init__(self, paso=5.0):
        self.t = 0.0
        self.paso = paso

    def __call__(self):
        self.t += self.paso
        return self.t

    def dormir(self, segundos):
        self.t += segundos


class DockerFalso:
    """Doble de la clase Docker. Registra qué se le pidió y en qué orden."""

    def __init__(self, servicios=("api", "web"), sano=True, espacio=100.0):
        self.servicios = list(servicios)
        self.sano = sano
        self.espacio = espacio
        self.llamadas = []
        self.fallar_up = False
        self.fallar_pull = False
        self.imagenes_faltantes = set()
        # salud que queda después de cada `up`, en orden. Sirve para el caso
        # "la versión nueva no levanta pero el rollback sí".
        self.salud_tras_up = None

    def _instantanea(self):
        return [{"servicio": s,
                 "estado": "running" if self.sano else "exited",
                 "salud": "healthy" if self.sano else "unhealthy"}
                for s in self.servicios]

    def ps(self, directorio):
        self.llamadas.append(("ps", str(directorio)))
        return self._instantanea()

    def pull(self, directorio, archivo=None):
        self.llamadas.append(("pull", str(archivo or directorio)))
        if self.fallar_pull:
            from deploycenter.agente.docker import ErrorDocker
            raise ErrorDocker("pull falló", codigo=1, error="manifest unknown")
        return ""

    def up(self, directorio, archivo=None):
        self.llamadas.append(("up", str(directorio)))
        if self.fallar_up:
            from deploycenter.agente.docker import ErrorDocker
            raise ErrorDocker("up falló", codigo=1, error="port is already allocated")
        if self.salud_tras_up:
            self.sano = self.salud_tras_up.pop(0)
        return ""

    def down(self, directorio):
        self.llamadas.append(("down", str(directorio)))
        return ""

    def logs(self, directorio, servicio=None, lineas=200):
        self.llamadas.append(("logs", str(directorio)))
        return "logs de prueba"

    def imagen_presente(self, referencia):
        return referencia not in self.imagenes_faltantes

    def espacio_libre_gb(self, directorio):
        return self.espacio

    # ayudas para los asserts
    def operaciones(self):
        return [c[0] for c in self.llamadas]

    def veces(self, operacion):
        return self.operaciones().count(operacion)


@pytest.fixture
def reloj():
    return Reloj()


@pytest.fixture
def docker_falso():
    return DockerFalso()


@pytest.fixture
def manifiesto_previo(variante):
    """La versión que ya está instalada."""
    return variante(release="4.6.0", publicado="2026-06-01", desde_version=">=4.5.0",
                    imagenes={
                        "api": "registry.accusys.com.ar/mep/api@sha256:" + "1" * 64,
                        "web": "registry.accusys.com.ar/mep/web@sha256:" + "2" * 64,
                    })


@pytest.fixture
def paquete(tmp_path, base):
    """Paquete de release 4.7.0, tal como lo entrega `dc empaquetar`."""
    from deploycenter.agente.instalacion import Paquete

    d = tmp_path / "paquete-4.7.0"
    d.mkdir()
    (d / "manifiesto.json").write_text(json.dumps(base), encoding="utf-8")
    (d / "compose.plantilla.yaml").write_text(PLANTILLA_AGENTE, encoding="utf-8")
    return Paquete(d)


@pytest.fixture
def instalacion(tmp_path, manifiesto_previo):
    """Instalación con 4.6.0 corriendo, su .env y su manifiesto aplicado."""
    from deploycenter import compose as compose_mod
    from deploycenter.agente.instalacion import Instalacion

    d = tmp_path / "stack" / "mep"
    d.mkdir(parents=True)
    inst = Instalacion(d).preparar()

    (d / ".env").write_text("MEP_PUERTO_WEB=8443\nMEP_EXPONER_EN=127.0.0.1\n",
                            encoding="utf-8")
    (d / "docker-compose.yml").write_text(
        compose_mod.render(PLANTILLA_AGENTE, manifiesto_previo, entorno={}),
        encoding="utf-8")

    inst.guardar_manifiesto(manifiesto_previo)
    inst.guardar_estado(producto="mep", version="4.6.0", estado="ok")
    return inst


@pytest.fixture
def instalacion_nueva(tmp_path):
    """Directorio sin nada desplegado todavía."""
    from deploycenter.agente.instalacion import Instalacion

    d = tmp_path / "stack" / "vacio"
    d.mkdir(parents=True)
    (d / ".env").write_text("MEP_PUERTO_WEB=8443\n", encoding="utf-8")
    return Instalacion(d).preparar()


# --------------------------------------------------------------------------- #
# Fase 2: hub y canal con el agente
# --------------------------------------------------------------------------- #

FIRMA_VALIDA = b"firma-de-prueba"


class RelojHub:
    """Reloj del hub que se puede adelantar a mano."""

    def __init__(self):
        import datetime
        self.t = datetime.datetime(2026, 9, 25, 15, 0, 0)

    def __call__(self):
        return self.t

    def avanzar(self, segundos):
        import datetime
        self.t += datetime.timedelta(seconds=segundos)


@pytest.fixture
def catalogo(tmp_path, base):
    """Árbol productos/ con MEP 4.7.0 firmado y sellado, como lo publica Accusys."""
    from deploycenter import compose as compose_mod

    raiz = tmp_path / "catalogo"
    prod = raiz / "productos" / "mep"
    rel = prod / "releases" / "4.7.0"
    rel.mkdir(parents=True)
    (prod / "producto.yaml").write_text(
        "codigo: mep\nnombre: MEP\nregistry: registry.accusys.com.ar/mep\n"
        "plantilla: compose.plantilla.yaml\n", encoding="utf-8")
    (prod / "compose.plantilla.yaml").write_text(PLANTILLA_AGENTE, encoding="utf-8")
    m = dict(base, plantilla_sha256=compose_mod.huella(PLANTILLA_AGENTE))
    (rel / "manifiesto.json").write_text(json.dumps(m), encoding="utf-8")
    (rel / "manifiesto.json.sig").write_bytes(FIRMA_VALIDA)
    (rel / "changelog.md").write_text("# MEP 4.7.0\n", encoding="utf-8")
    return raiz


@pytest.fixture
def agregar_release(catalogo, base):
    """Publica otro release de MEP en el catálogo de prueba."""
    from deploycenter import compose as compose_mod

    def _hacer(version, **campos):
        rel = catalogo / "productos" / "mep" / "releases" / version
        rel.mkdir(parents=True)
        m = dict(base, release=version, plantilla_sha256=compose_mod.huella(PLANTILLA_AGENTE),
                 changelog=f"productos/mep/releases/{version}/changelog.md")
        m.update(campos)
        (rel / "manifiesto.json").write_text(json.dumps(m), encoding="utf-8")
        (rel / "manifiesto.json.sig").write_bytes(FIRMA_VALIDA)
        (rel / "changelog.md").write_text(f"# MEP {version}\n", encoding="utf-8")
        return m
    return _hacer


@pytest.fixture
def reloj_hub():
    return RelojHub()


@pytest.fixture
def hub(tmp_path, catalogo, reloj_hub):
    """Hub con Banco Andino dado de alta y MEP adquirido hasta fin de año."""
    from deploycenter.hub.servicio import Hub

    h = Hub.desde_url(f"sqlite:///{tmp_path / 'hub.db'}", catalogo, reloj=reloj_hub)
    h.crear_tenant("andino", "Banco Andino")
    h.adquirir("andino", "mep", "2026-12-31")
    return h


@pytest.fixture
def firma_falsa(monkeypatch):
    """cosign no está en el entorno de tests: la firma es válida si es FIRMA_VALIDA."""
    from pathlib import Path as _Path

    from deploycenter import firma as firma_mod

    def verificar(_manifiesto, _clave, ruta_firma, **_kw):
        return _Path(ruta_firma).read_bytes() == FIRMA_VALIDA

    monkeypatch.setattr(firma_mod, "verificar", verificar)
    return verificar


# --------------------------------------------------------------------------- #
# Postgres real, para RLS y migraciones
# --------------------------------------------------------------------------- #

def _binarios_postgres():
    import glob
    import shutil

    if shutil.which("initdb") and shutil.which("pg_ctl"):
        return Path(shutil.which("initdb")).parent
    candidatos = sorted(glob.glob("/usr/lib/postgresql/*/bin/initdb"))
    return Path(candidatos[-1]).parent if candidatos else None


@pytest.fixture(scope="session")
def postgres_servidor():
    """URL de un servidor Postgres para los tests.

    Usa DC_TEST_PG_URL si está definida (una base cualquiera del servidor: los
    tests crean las suyas). Si no, levanta un cluster efímero con initdb. Si no
    hay Postgres, los tests se saltean, salvo con DC_EXIGIR_PG=1, que es lo que
    usa el pipeline para que la RLS no quede sin probar sin que nadie se entere.
    """
    import os
    import shutil
    import socket
    import subprocess
    import tempfile

    url = os.environ.get("DC_TEST_PG_URL")
    if url:
        yield url
        return

    binarios = _binarios_postgres()
    if binarios is None:
        if os.environ.get("DC_EXIGIR_PG"):
            pytest.fail("DC_EXIGIR_PG está definido y no hay Postgres para los tests")
        pytest.skip("no hay Postgres: definí DC_TEST_PG_URL o instalá postgresql")

    como = []
    base = Path(tempfile.mkdtemp(prefix="dc-pg-"))
    base.chmod(0o755)
    datos, socket_dir = base / "datos", base / "socket"
    datos.mkdir()
    socket_dir.mkdir()
    if os.geteuid() == 0:  # initdb no corre como root
        como = ["runuser", "-u", "postgres", "--"]
        shutil.chown(datos, "postgres")
        shutil.chown(socket_dir, "postgres")

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        puerto = s.getsockname()[1]

    def correr(*argv):
        r = subprocess.run([*como, *argv], capture_output=True, text=True)
        if r.returncode != 0:
            log = datos / "pg.log"
            raise RuntimeError(f"{argv[0]} falló: {r.stderr or r.stdout}"
                               + (log.read_text() if log.is_file() else ""))

    correr(str(binarios / "initdb"), "-D", str(datos), "-U", "postgres",
           "--auth=trust", "-E", "UTF8", "--locale=C")
    correr(str(binarios / "pg_ctl"), "-D", str(datos), "-w", "-l", str(datos / "pg.log"),
           "-o", f"-k {socket_dir} -p {puerto} -c listen_addresses='' -c fsync=off", "start")
    try:
        yield f"postgresql+psycopg://postgres@/postgres?host={socket_dir}&port={puerto}"
    finally:
        subprocess.run([*como, str(binarios / "pg_ctl"), "-D", str(datos), "-m", "immediate",
                        "stop"], capture_output=True)
        shutil.rmtree(base, ignore_errors=True)


@pytest.fixture
def postgres_url(postgres_servidor):
    """Una base nueva por test, con las migraciones aplicadas."""
    import uuid

    from sqlalchemy import create_engine
    from sqlalchemy.engine import make_url

    from deploycenter.hub import migraciones

    nombre = "t_" + uuid.uuid4().hex[:12]
    admin = create_engine(postgres_servidor, isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        c.exec_driver_sql(f"create database {nombre}")
    url = make_url(postgres_servidor).set(database=nombre).render_as_string(hide_password=False)
    engine = create_engine(url)
    migraciones.aplicar(engine, migraciones.directorio_por_defecto(RAIZ))
    engine.dispose()
    try:
        yield url
    finally:
        with admin.connect() as c:
            c.exec_driver_sql(f"drop database if exists {nombre} with (force)")
        admin.dispose()
