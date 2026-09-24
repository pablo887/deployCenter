import copy
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
