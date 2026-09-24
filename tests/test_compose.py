import pytest
import yaml

from deploycenter import compose
from deploycenter.errores import ErrorArchivo, ErrorValidacion

PLANTILLA_MINIMA = """
name: {{ producto }}
services:
  api:
    image: {{ imagenes.api }}
  web:
    image: {{ imagenes.web }}
    ports:
      - "{{ env.get('MEP_EXPONER_EN', '127.0.0.1') }}:${MEP_PUERTO_WEB}:8080"
"""


class TestRender:
    def test_interpola_las_imagenes_del_manifiesto(self, base):
        texto = compose.render(PLANTILLA_MINIMA, base)
        datos = yaml.safe_load(texto)
        assert datos["services"]["api"]["image"] == base["imagenes"]["api"]

    def test_deja_pasar_la_interpolacion_de_compose(self, base):
        """${VARIABLE} no lo toca Jinja: lo resuelve docker compose con el .env."""
        texto = compose.render(PLANTILLA_MINIMA, base)
        assert "${MEP_PUERTO_WEB}" in texto

    def test_usa_el_entorno_para_la_estructura(self, base):
        texto = compose.render(PLANTILLA_MINIMA, base, entorno={"MEP_EXPONER_EN": "0.0.0.0"})
        assert '"0.0.0.0:${MEP_PUERTO_WEB}:8080"' in texto

    def test_default_cuando_la_variable_no_esta(self, base):
        texto = compose.render(PLANTILLA_MINIMA, base, entorno={})
        assert '"127.0.0.1:${MEP_PUERTO_WEB}:8080"' in texto

    def test_falla_si_la_plantilla_pide_algo_que_no_existe(self, base):
        with pytest.raises(ErrorArchivo, match="no se pudo renderizar"):
            compose.render("image: {{ imagenes.fantasma }}", base)

    def test_plantilla_que_no_existe(self, base, tmp_path):
        with pytest.raises(ErrorArchivo, match="no se pudo leer"):
            compose.render_desde_archivos(tmp_path / "no-existe.yaml", base)


class TestVerificacion:
    def test_un_compose_sano_no_tiene_problemas(self, base):
        texto = compose.render(PLANTILLA_MINIMA, base)
        assert compose.problemas_del_compose(texto, manifiesto=base) == []

    def test_detecta_latest(self, base):
        texto = "services:\n  api:\n    image: registry.accusys.com.ar/mep/api:latest\n"
        assert any("latest" in p for p in compose.problemas_del_compose(texto))

    def test_detecta_imagen_propia_sin_pinnear(self, base):
        texto = "services:\n  api:\n    image: registry.accusys.com.ar/mep/api:4.7.0\n"
        assert any("no está pinneada" in p for p in compose.problemas_del_compose(texto))

    def test_una_imagen_de_terceros_por_tag_es_valida(self):
        texto = "services:\n  db:\n    image: postgres:16.4-alpine\n"
        assert compose.problemas_del_compose(texto) == []

    def test_rechaza_build_local(self):
        texto = "services:\n  api:\n    build: .\n"
        assert any("build" in p for p in compose.problemas_del_compose(texto))

    def test_servicio_sin_imagen(self):
        texto = "services:\n  api:\n    restart: always\n"
        assert any("no declara image" in p for p in compose.problemas_del_compose(texto))

    def test_yaml_roto(self):
        assert any("YAML" in p for p in compose.problemas_del_compose("services:\n  - [a\n"))

    def test_sin_services(self):
        assert compose.problemas_del_compose("name: mep\n")

    def test_falta_un_servicio_del_manifiesto(self, base):
        texto = "services:\n  api:\n    image: " + base["imagenes"]["api"] + "\n"
        problemas = compose.problemas_del_compose(texto, manifiesto=base)
        assert any("web" in p for p in problemas)


class TestGenerar:
    def test_escribe_el_archivo(self, base, tmp_path):
        plantilla = tmp_path / "p.yaml"
        plantilla.write_text(PLANTILLA_MINIMA, encoding="utf-8")
        salida = tmp_path / "docker-compose.yml"

        texto = compose.generar(plantilla, base, entorno={}, salida=salida)
        assert salida.read_text(encoding="utf-8") == texto
        assert yaml.safe_load(texto)["services"]["api"]["image"] == base["imagenes"]["api"]

    def test_no_escribe_si_el_resultado_es_invalido(self, base, tmp_path):
        plantilla = tmp_path / "p.yaml"
        plantilla.write_text("services:\n  api:\n    image: registry.accusys.com.ar/x:latest\n",
                             encoding="utf-8")
        salida = tmp_path / "docker-compose.yml"
        with pytest.raises(ErrorValidacion):
            compose.generar(plantilla, base, salida=salida)
        assert not salida.exists()


class TestPlantillaRealDeMep:
    """La plantilla que se versiona en el repo tiene que renderizar y pasar la
    verificación con el release de ejemplo."""

    def test_renderiza_y_verifica(self, raiz):
        from deploycenter import manifiesto as mf

        m = mf.cargar(raiz / "productos" / "mep" / "releases" / "4.7.0" / "manifiesto.json")
        plantilla = raiz / "productos" / "mep" / "compose.plantilla.yaml"
        texto = compose.render_desde_archivos(plantilla, m, entorno={"MEP_EXPONER_EN": "127.0.0.1"})

        assert compose.problemas_del_compose(texto, manifiesto=m) == []

        datos = yaml.safe_load(texto)
        assert set(datos["services"]) == {"api", "web", "db"}
        # la base es de terceros: no está en el manifiesto y no se pinnea por digest
        assert datos["services"]["db"]["image"] == "postgres:16.4-alpine"
        # los secretos no quedan en el compose, van por interpolación de compose
        assert "${MEP_DB_CLAVE}" in texto
        assert "reemplazar-en-el-servidor-del-cliente" not in texto
