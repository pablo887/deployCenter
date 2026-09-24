import datetime

import pytest

from deploycenter import manifiesto as mf
from deploycenter.errores import ErrorArchivo, ErrorValidacion


def problemas_con(manifiesto, **kw):
    return mf.validar(manifiesto, **kw)


class TestManifiestoValido:
    def test_el_base_pasa(self, base):
        assert problemas_con(base) == []

    def test_el_release_de_ejemplo_del_repo_pasa(self, raiz):
        ruta = raiz / "productos" / "mep" / "releases" / "4.7.0" / "manifiesto.json"
        m = mf.cargar(ruta)
        assert problemas_con(m, raiz=raiz) == []

    def test_json_roto_da_error_legible(self, tmp_path):
        ruta = tmp_path / "m.json"
        ruta.write_text("{ esto no es json", encoding="utf-8")
        with pytest.raises(ErrorArchivo, match="no es JSON válido"):
            mf.cargar(ruta)


class TestSchema:
    def test_falta_un_campo_obligatorio(self, base):
        del base["healthchecks"]
        assert any("healthchecks" in p for p in problemas_con(base))

    def test_campo_desconocido(self, base):
        base["inventado"] = 1
        assert any("inventado" in p for p in problemas_con(base))

    def test_canal_fuera_de_la_lista(self, variante):
        assert problemas_con(variante(canal="beta"))

    def test_version_no_semver(self, variante):
        assert problemas_con(variante(release="4.7"))

    def test_descripcion_de_variable_demasiado_corta(self, variante):
        m = variante(variables_nuevas=[
            {"nombre": "X_Y", "obligatoria": True, "descripcion": "corta"}
        ])
        assert problemas_con(m)


class TestPinneo:
    def test_exige_digest(self, variante):
        m = variante(imagenes={"api": "registry.accusys.com.ar/mep/api:4.7.0",
                               "web": "registry.accusys.com.ar/mep/web@sha256:" + "b" * 64})
        problemas = problemas_con(m)
        assert any("no está pinneada" in p for p in problemas)

    def test_se_puede_no_exigir_antes_de_publicar(self, variante):
        m = variante(imagenes={"api": "registry.accusys.com.ar/mep/api:4.7.0",
                               "web": "registry.accusys.com.ar/mep/web:4.7.0"})
        assert problemas_con(m, exigir_pin=False) == []

    def test_latest_siempre_falla(self, variante):
        m = variante(imagenes={"api": "registry.accusys.com.ar/mep/api:latest",
                               "web": "registry.accusys.com.ar/mep/web@sha256:" + "b" * 64})
        problemas = problemas_con(m, exigir_pin=False)
        assert any("latest" in p for p in problemas)


class TestHealthchecks:
    def test_servicio_sin_healthcheck(self, base):
        base["healthchecks"] = [base["healthchecks"][0]]  # saco el de web
        problemas = problemas_con(base)
        assert any("web" in p and "healthcheck" in p for p in problemas)

    def test_healthcheck_de_un_servicio_inexistente(self, base):
        base["healthchecks"].append(
            {"servicio": "fantasma", "url": "http://x:1/h", "espera": 200, "timeout_s": 10}
        )
        problemas = problemas_con(base)
        assert any("fantasma" in p for p in problemas)


class TestMigracionesYRollback:
    def test_migraciones_con_rollback_seguro_es_contradiccion(self, variante):
        m = variante(db_migrations=True, rollback_seguro=True)
        problemas = problemas_con(m)
        assert any("rollback_seguro" in p for p in problemas)

    def test_salvo_que_sean_compatibles_hacia_atras(self, variante):
        m = variante(db_migrations=True, rollback_seguro=True, migraciones_compatibles=True)
        assert problemas_con(m) == []

    def test_compatibles_sin_migraciones_no_tiene_sentido(self, variante):
        m = variante(db_migrations=False, migraciones_compatibles=True)
        assert any("migraciones_compatibles" in p for p in problemas_con(m))

    def test_migracion_sin_rollback_seguro_es_valido(self, variante):
        m = variante(db_migrations=True, rollback_seguro=False)
        assert problemas_con(m) == []


class TestVariables:
    def test_nombre_duplicado(self, variante):
        v = {"nombre": "MEP_X", "obligatoria": True, "default": "1",
             "descripcion": "una descripción suficientemente larga"}
        problemas = problemas_con(variante(variables_nuevas=[v, dict(v)]))
        assert any("más de una vez" in p for p in problemas)

    def test_secreta_no_puede_traer_default(self, variante):
        v = {"nombre": "MEP_CLAVE", "obligatoria": True, "default": "hola", "secreta": True,
             "descripcion": "una descripción suficientemente larga"}
        assert any("secreta" in p for p in problemas_con(variante(variables_nuevas=[v])))


class TestRangoDeUpgrade:
    def test_rango_invalido(self, variante):
        assert any("desde_version" in p for p in problemas_con(variante(desde_version=">=cuatro")))

    def test_no_se_puede_actualizar_hacia_atras(self, variante):
        m = variante(release="4.4.0", desde_version=">=4.5.0")
        assert any("posterior" in p for p in problemas_con(m))

    def test_permite_upgrade_desde(self, base):
        assert mf.permite_upgrade_desde(base, "4.6.2")
        assert not mf.permite_upgrade_desde(base, "4.4.9")


class TestFecha:
    def test_fecha_con_forma_pero_inexistente(self, variante):
        assert any("calendario" in p for p in problemas_con(variante(publicado="2026-02-30")))


class TestChangelog:
    def test_se_verifica_contra_el_disco(self, base, tmp_path):
        assert any("changelog" in p for p in problemas_con(base, raiz=tmp_path))


class TestCircuito:
    def test_sin_migraciones_es_autoservicio(self, base):
        assert mf.es_autoservicio(base)

    def test_con_migraciones_pasa_a_asistido(self, variante):
        assert not mf.es_autoservicio(variante(db_migrations=True, rollback_seguro=False))

    def test_migraciones_compatibles_vuelven_al_autoservicio(self, variante):
        m = variante(db_migrations=True, rollback_seguro=True, migraciones_compatibles=True)
        assert mf.es_autoservicio(m)


class TestReglaDeMantenimiento:
    """La regla comercial: se habilita lo publicado hasta el fin del mantenimiento."""

    def test_publicado_antes_del_vencimiento(self, base):
        assert mf.habilitado_para(base, "2026-12-31")

    def test_publicado_el_mismo_dia_entra(self, base):
        assert mf.habilitado_para(base, "2026-09-18")

    def test_publicado_despues_no_entra(self, base):
        assert not mf.habilitado_para(base, "2026-09-17")

    def test_acepta_date_ademas_de_texto(self, base):
        assert mf.habilitado_para(base, datetime.date(2026, 12, 31))

    def test_sin_mantenimiento_no_entra(self, base):
        assert not mf.habilitado_para(base, None)

    def test_parche_critico_entra_igual(self, variante):
        m = variante(critico_seguridad=True)
        assert mf.habilitado_para(m, "2020-01-01")
        assert mf.habilitado_para(m, None)


class TestExigirValido:
    def test_lanza_con_la_lista_completa(self, base):
        del base["healthchecks"]
        with pytest.raises(ErrorValidacion) as e:
            mf.exigir_valido(base)
        assert e.value.problemas
