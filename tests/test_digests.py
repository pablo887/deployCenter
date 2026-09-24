import pytest

from deploycenter import digests
from deploycenter.errores import ErrorHerramienta

DIGEST = "sha256:" + "c" * 64


class TestSeparar:
    def test_repo_con_tag(self):
        assert digests.separar("reg.acc.com.ar/mep/api:4.7.0") == (
            "reg.acc.com.ar/mep/api", "4.7.0", None)

    def test_repo_con_digest(self):
        assert digests.separar(f"reg.acc.com.ar/mep/api@{DIGEST}") == (
            "reg.acc.com.ar/mep/api", None, DIGEST)

    def test_repo_sin_tag(self):
        assert digests.separar("postgres") == ("postgres", None, None)

    def test_host_con_puerto_no_se_confunde_con_tag(self):
        """El caso que rompe un split ingenuo por ':'."""
        assert digests.separar("localhost:5000/mep/api") == ("localhost:5000/mep/api", None, None)

    def test_host_con_puerto_y_tag(self):
        assert digests.separar("localhost:5000/mep/api:4.7.0") == (
            "localhost:5000/mep/api", "4.7.0", None)

    def test_vacio(self):
        with pytest.raises(ValueError):
            digests.separar("")


class TestPinneado:
    def test_reconoce_un_digest_valido(self):
        assert digests.esta_pinneado(f"repo@{DIGEST}")

    def test_rechaza_un_digest_corto(self):
        assert not digests.esta_pinneado("repo@sha256:abc")

    def test_un_tag_no_es_pin(self):
        assert not digests.esta_pinneado("repo:4.7.0")

    def test_detecta_latest(self):
        assert digests.usa_latest("repo:latest")
        assert not digests.usa_latest("repo:4.7.0")


class TestResolver:
    def test_es_idempotente_sobre_algo_ya_pinneado(self, runner_fijo):
        ejecutar = runner_fijo()
        ref = f"repo@{DIGEST}"
        assert digests.resolver(ref, ejecutar=ejecutar) == ref
        assert ejecutar.llamadas == []  # ni consultó el registry

    def test_resuelve_un_tag_con_docker(self, runner_fijo):
        ejecutar = runner_fijo(salida=f'"{DIGEST}"')
        resuelta = digests.resolver("reg/mep/api:4.7.0", ejecutar=ejecutar, herramienta="docker")
        assert resuelta == f"reg/mep/api@{DIGEST}"
        assert ejecutar.llamadas[0][0] == "docker"

    def test_resuelve_un_tag_con_skopeo(self, runner_fijo):
        ejecutar = runner_fijo(salida=DIGEST)
        resuelta = digests.resolver("reg/mep/api:4.7.0", ejecutar=ejecutar, herramienta="skopeo")
        assert resuelta == f"reg/mep/api@{DIGEST}"
        assert ejecutar.llamadas[0][0] == "skopeo"

    def test_sin_tag_ni_digest_no_hay_nada_que_resolver(self, runner_fijo):
        with pytest.raises(ErrorHerramienta, match="no tiene tag ni digest"):
            digests.resolver("reg/mep/api", ejecutar=runner_fijo(), herramienta="docker")

    def test_el_registry_no_contesta(self, runner_fijo):
        ejecutar = runner_fijo(codigo=1, error="manifest unknown")
        with pytest.raises(ErrorHerramienta, match="manifest unknown"):
            digests.resolver("reg/mep/api:9.9.9", ejecutar=ejecutar, herramienta="docker")

    def test_respuesta_con_forma_inesperada(self, runner_fijo):
        ejecutar = runner_fijo(salida="no soy un digest")
        with pytest.raises(ErrorHerramienta, match="inesperada"):
            digests.resolver("reg/mep/api:4.7.0", ejecutar=ejecutar, herramienta="docker")

    def test_sin_docker_ni_skopeo(self, runner_fijo, monkeypatch):
        monkeypatch.setattr(digests, "herramienta_disponible", lambda: None)
        with pytest.raises(ErrorHerramienta, match="docker o skopeo"):
            digests.resolver("reg/mep/api:4.7.0", ejecutar=runner_fijo())


class TestPinearManifiesto:
    def test_reescribe_solo_lo_que_hace_falta(self, variante, runner_fijo):
        m = variante(imagenes={
            "api": "reg/mep/api:4.7.0",
            "web": f"reg/mep/web@{DIGEST}",
        })
        ejecutar = runner_fijo(salida=f'"{DIGEST}"')
        nuevo, cambios = digests.pinear_manifiesto(m, ejecutar=ejecutar, herramienta="docker")

        assert [c[0] for c in cambios] == ["api"]
        assert nuevo["imagenes"]["api"] == f"reg/mep/api@{DIGEST}"
        assert nuevo["imagenes"]["web"] == f"reg/mep/web@{DIGEST}"

    def test_no_muta_el_original(self, variante, runner_fijo):
        m = variante(imagenes={"api": "reg/mep/api:4.7.0", "web": f"reg/mep/web@{DIGEST}"})
        antes = m["imagenes"]["api"]
        digests.pinear_manifiesto(
            m, ejecutar=runner_fijo(salida=f'"{DIGEST}"'), herramienta="docker")
        assert m["imagenes"]["api"] == antes
