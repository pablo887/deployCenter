import json

from deploycenter import firma


class TestCanonico:
    def test_no_depende_del_orden_de_las_claves(self, base):
        otro = dict(reversed(list(base.items())))
        assert firma.canonico(base) == firma.canonico(otro)

    def test_no_depende_de_la_indentacion(self, base, tmp_path):
        ruta = tmp_path / "m.json"
        ruta.write_text(json.dumps(base, indent=8), encoding="utf-8")
        recargado = json.loads(ruta.read_text(encoding="utf-8"))
        assert firma.canonico(base) == firma.canonico(recargado)

    def test_ignora_el_campo_firma(self, base):
        con_firma = dict(base, firma="cosign:loquesea")
        assert firma.canonico(base) == firma.canonico(con_firma)

    def test_cambiar_un_digest_cambia_la_firma(self, base):
        otro = json.loads(json.dumps(base))
        otro["imagenes"]["api"] = "registry.accusys.com.ar/mep/api@sha256:" + "f" * 64
        assert firma.canonico(base) != firma.canonico(otro)


class TestFirmar:
    def test_arma_bien_el_comando(self, base, tmp_path, runner_fijo):
        ejecutar = runner_fijo()
        destino = tmp_path / "m.json.sig"
        firma.firmar(base, tmp_path / "cosign.key", destino,
                     ejecutar=ejecutar, exigir_binario=False)

        comando = ejecutar.llamadas[0]
        assert comando[:3] == ["cosign", "sign-blob", "--yes"]
        assert "--output-signature" in comando

    def test_limpia_el_payload_temporal(self, base, tmp_path, runner_fijo):
        destino = tmp_path / "m.json.sig"
        firma.firmar(base, tmp_path / "k", destino,
                     ejecutar=runner_fijo(), exigir_binario=False)
        assert not list(tmp_path.glob("*.payload"))

    def test_limpia_el_payload_aunque_cosign_falle(self, base, tmp_path, runner_fijo):
        import pytest

        from deploycenter.errores import ErrorHerramienta

        destino = tmp_path / "m.json.sig"
        with pytest.raises(ErrorHerramienta):
            firma.firmar(base, tmp_path / "k", destino,
                         ejecutar=runner_fijo(codigo=1, error="sin clave"),
                         exigir_binario=False)
        assert not list(tmp_path.glob("*.payload"))


class TestVerificar:
    def test_firma_valida(self, base, tmp_path, runner_fijo):
        assert firma.verificar(base, tmp_path / "k.pub", tmp_path / "m.sig",
                               ejecutar=runner_fijo(codigo=0), exigir_binario=False)

    def test_firma_invalida_no_lanza_devuelve_false(self, base, tmp_path, runner_fijo):
        assert not firma.verificar(base, tmp_path / "k.pub", tmp_path / "m.sig",
                                   ejecutar=runner_fijo(codigo=1), exigir_binario=False)
