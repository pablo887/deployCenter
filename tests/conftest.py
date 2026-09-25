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
