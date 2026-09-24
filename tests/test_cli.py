"""Tests del CLI por su contrato público: código de salida y salida de texto.

El código de salida importa porque es lo que corta el pipeline: 0 pasa,
1 la validación encontró problemas, 2 error de uso o falta una herramienta.
"""

import json
import shutil

import pytest

from deploycenter.cli import ERROR_USO, FALLA_VALIDACION, OK, main


@pytest.fixture
def repo(tmp_path, raiz):
    """Copia mínima del repo real, para poder romperla sin consecuencias."""
    destino = tmp_path / "repo"
    (destino / "productos").mkdir(parents=True)
    shutil.copytree(raiz / "productos" / "mep", destino / "productos" / "mep")
    return destino


def correr(*argv):
    return main(["--sin-color", *argv])


class TestValidar:
    def test_release_sano(self, repo, capsys):
        ruta = repo / "productos/mep/releases/4.7.0/manifiesto.json"
        assert correr("--raiz", str(repo), "validar", str(ruta)) == OK
        assert "autoservicio" in capsys.readouterr().out

    def test_release_roto_devuelve_1_y_lista_todo(self, repo, capsys):
        ruta = repo / "productos/mep/releases/4.7.0/manifiesto.json"
        m = json.loads(ruta.read_text(encoding="utf-8"))
        m["imagenes"]["api"] = "registry.accusys.com.ar/mep/api:latest"
        m["healthchecks"] = [h for h in m["healthchecks"] if h["servicio"] != "web"]
        ruta.write_text(json.dumps(m), encoding="utf-8")

        assert correr("--raiz", str(repo), "validar", str(ruta)) == FALLA_VALIDACION
        salida = capsys.readouterr().out
        assert "latest" in salida
        assert "healthcheck" in salida

    def test_archivo_inexistente_es_error_de_uso(self, repo, capsys):
        assert correr("validar", str(repo / "no-existe.json")) == ERROR_USO
        assert "no se pudo leer" in capsys.readouterr().err

    def test_marca_el_circuito_asistido(self, repo, capsys):
        ruta = repo / "productos/mep/releases/4.7.0/manifiesto.json"
        m = json.loads(ruta.read_text(encoding="utf-8"))
        m["db_migrations"] = True
        m["rollback_seguro"] = False
        ruta.write_text(json.dumps(m), encoding="utf-8")

        assert correr("--raiz", str(repo), "validar", str(ruta)) == OK
        assert "asistido" in capsys.readouterr().out


class TestVariables:
    def test_el_default_no_bloquea(self, repo, tmp_path, capsys):
        env = tmp_path / ".env"
        env.write_text("MEP_PUERTO_WEB=8443\n", encoding="utf-8")
        ruta = repo / "productos/mep/releases/4.7.0/manifiesto.json"
        assert correr("variables", str(ruta), "--entorno", str(env)) == OK
        assert "se completa con el default" in capsys.readouterr().out

    def test_una_obligatoria_sin_default_corta(self, repo, tmp_path, capsys):
        ruta = repo / "productos/mep/releases/4.7.0/manifiesto.json"
        m = json.loads(ruta.read_text(encoding="utf-8"))
        m["variables_nuevas"].append({
            "nombre": "MEP_HSM_URL", "obligatoria": True, "default": None,
            "descripcion": "URL del HSM, que solo conoce el cliente",
        })
        ruta.write_text(json.dumps(m), encoding="utf-8")
        env = tmp_path / ".env"
        env.write_text("MEP_PUERTO_WEB=8443\n", encoding="utf-8")

        assert correr("variables", str(ruta), "--entorno", str(env)) == FALLA_VALIDACION
        assert "MEP_HSM_URL" in capsys.readouterr().out


class TestRender:
    def test_genera_el_compose(self, repo, tmp_path, capsys):
        env = tmp_path / ".env"
        env.write_text("MEP_PUERTO_WEB=8443\nMEP_EXPONER_EN=127.0.0.1\n", encoding="utf-8")
        salida = tmp_path / "docker-compose.yml"

        codigo = correr("--raiz", str(repo), "render", "--producto", "mep",
                        "--release", "4.7.0", "--entorno", str(env), "--salida", str(salida))
        assert codigo == OK
        texto = salida.read_text(encoding="utf-8")
        assert "@sha256:" in texto
        assert "${MEP_DB_CLAVE}" in texto  # el secreto no se interpola

    def test_a_stdout_si_no_hay_salida(self, repo, capsys):
        codigo = correr("--raiz", str(repo), "render", "--producto", "mep", "--release", "4.7.0")
        assert codigo == OK
        assert "services:" in capsys.readouterr().out

    def test_corta_si_falta_una_variable_obligatoria(self, repo, tmp_path, capsys):
        ruta = repo / "productos/mep/releases/4.7.0/manifiesto.json"
        m = json.loads(ruta.read_text(encoding="utf-8"))
        m["variables_nuevas"] = [{
            "nombre": "MEP_HSM_URL", "obligatoria": True, "default": None,
            "descripcion": "URL del HSM, que solo conoce el cliente",
        }]
        ruta.write_text(json.dumps(m), encoding="utf-8")

        codigo = correr("--raiz", str(repo), "render", "--producto", "mep", "--release", "4.7.0")
        assert codigo == FALLA_VALIDACION
        assert "MEP_HSM_URL" in capsys.readouterr().err

    def test_sin_producto_ni_manifiesto_es_error_de_uso(self, repo, capsys):
        assert correr("--raiz", str(repo), "render") == ERROR_USO


class TestNuevoRelease:
    def test_crea_manifiesto_y_changelog(self, repo, capsys):
        assert correr("--raiz", str(repo), "nuevo-release", "--producto", "mep",
                      "--version", "4.8.0", "--desde", ">=4.7.0") == OK
        carpeta = repo / "productos/mep/releases/4.8.0"
        assert (carpeta / "manifiesto.json").is_file()
        assert (carpeta / "changelog.md").is_file()

    def test_sale_sin_pinnear_y_valida_con_sin_pin(self, repo):
        correr("--raiz", str(repo), "nuevo-release", "--producto", "mep", "--version", "4.8.0")
        ruta = repo / "productos/mep/releases/4.8.0/manifiesto.json"
        assert "@sha256:" not in ruta.read_text(encoding="utf-8")
        assert correr("--raiz", str(repo), "validar", str(ruta), "--sin-pin") == OK
        assert correr("--raiz", str(repo), "validar", str(ruta)) == FALLA_VALIDACION

    def test_con_migraciones_deja_el_rollback_en_falso(self, repo):
        correr("--raiz", str(repo), "nuevo-release", "--producto", "mep",
               "--version", "5.0.0", "--con-migraciones")
        m = json.loads((repo / "productos/mep/releases/5.0.0/manifiesto.json").read_text("utf-8"))
        assert m["db_migrations"] is True
        assert m["rollback_seguro"] is False

    def test_no_pisa_un_release_existente(self, repo, capsys):
        assert correr("--raiz", str(repo), "nuevo-release", "--producto", "mep",
                      "--version", "4.7.0") == ERROR_USO
        assert "ya existe" in capsys.readouterr().err


class TestSchema:
    def test_imprime_json_valido(self, capsys):
        assert correr("schema") == OK
        datos = json.loads(capsys.readouterr().out)
        assert datos["title"].startswith("Manifiesto")
