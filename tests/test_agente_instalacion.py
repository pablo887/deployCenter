import json

import pytest

from deploycenter.agente.instalacion import ErrorBloqueo, Instalacion, Paquete
from deploycenter.errores import ErrorArchivo


class TestEstado:
    def test_arranca_vacia(self, instalacion_nueva):
        assert not instalacion_nueva.inicializada()
        assert instalacion_nueva.estado() == {}
        assert instalacion_nueva.version_instalada() is None

    def test_guarda_y_lee(self, instalacion):
        assert instalacion.version_instalada() == "4.6.0"
        assert instalacion.producto() == "mep"

    def test_actualiza_sin_borrar_lo_otro(self, instalacion):
        instalacion.guardar_estado(estado="degradado")
        e = instalacion.estado()
        assert e["estado"] == "degradado"
        assert e["version"] == "4.6.0"

    def test_deja_marca_de_tiempo(self, instalacion):
        assert instalacion.estado()["actualizado"].endswith("+00:00")

    def test_estado_corrupto_da_error_legible(self, instalacion):
        instalacion.ruta_estado.write_text("{ roto", encoding="utf-8")
        with pytest.raises(ErrorArchivo, match="corrupto"):
            instalacion.estado()


class TestHistorial:
    def test_es_append_only(self, instalacion):
        instalacion.registrar("despliegue", "ok", desde="4.5.0", hacia="4.6.0")
        instalacion.registrar("despliegue", "revertido", desde="4.6.0", hacia="4.7.0")
        eventos = instalacion.historial()
        assert [e["resultado"] for e in eventos] == ["ok", "revertido"]

    def test_limite_devuelve_los_ultimos(self, instalacion):
        for i in range(5):
            instalacion.registrar("op", "ok", n=i)
        assert [e["n"] for e in instalacion.historial(limite=2)] == [3, 4]

    def test_ignora_lineas_rotas(self, instalacion):
        instalacion.registrar("op", "ok")
        with instalacion.ruta_historial.open("a", encoding="utf-8") as f:
            f.write("esto no es json\n")
        assert len(instalacion.historial()) == 1

    def test_sin_historial_devuelve_vacio(self, instalacion_nueva):
        assert instalacion_nueva.historial() == []


class TestPuntoDeRetorno:
    def test_guarda_el_compose_vigente(self, instalacion, manifiesto_previo):
        original = instalacion.ruta_compose.read_text(encoding="utf-8")
        instalacion.tomar_punto_retorno(manifiesto_actual=manifiesto_previo)

        assert instalacion.hay_punto_retorno()
        assert (instalacion.dir_retorno / "docker-compose.yml").read_text(
            encoding="utf-8") == original
        assert instalacion.manifiesto_de_retorno()["release"] == "4.6.0"

    def test_restaura_exactamente_lo_guardado(self, instalacion, manifiesto_previo):
        original = instalacion.ruta_compose.read_text(encoding="utf-8")
        instalacion.tomar_punto_retorno(manifiesto_actual=manifiesto_previo)
        instalacion.ruta_compose.write_text("otra cosa", encoding="utf-8")

        instalacion.restaurar_punto_retorno()
        assert instalacion.ruta_compose.read_text(encoding="utf-8") == original

    def test_sin_compose_no_se_puede_tomar(self, instalacion_nueva):
        with pytest.raises(ErrorArchivo, match="punto de retorno"):
            instalacion_nueva.tomar_punto_retorno()

    def test_sin_punto_no_se_puede_restaurar(self, instalacion):
        with pytest.raises(ErrorArchivo, match="no hay punto de retorno"):
            instalacion.restaurar_punto_retorno()

    def test_el_compose_guardado_tiene_digests(self, instalacion, manifiesto_previo):
        instalacion.tomar_punto_retorno(manifiesto_actual=manifiesto_previo)
        guardado = (instalacion.dir_retorno / "docker-compose.yml").read_text(encoding="utf-8")
        assert "@sha256:" + "1" * 64 in guardado


class TestManifiestosAplicados:
    def test_guarda_por_version(self, instalacion, base):
        instalacion.guardar_manifiesto(base)
        assert instalacion.manifiesto_aplicado("4.7.0")["release"] == "4.7.0"

    def test_el_actual_sale_del_estado(self, instalacion):
        assert instalacion.manifiesto_aplicado()["release"] == "4.6.0"

    def test_version_desconocida(self, instalacion):
        assert instalacion.manifiesto_aplicado("9.9.9") is None


class TestBloqueo:
    def test_dos_despliegues_en_paralelo_no(self, instalacion):
        instalacion.bloquear(dueno="uno")
        with pytest.raises(ErrorBloqueo, match="ya hay un despliegue en curso"):
            instalacion.bloquear(dueno="dos")

    def test_el_mensaje_dice_quien_lo_tiene(self, instalacion):
        instalacion.bloquear(dueno="despliegue:abc123")
        with pytest.raises(ErrorBloqueo, match="abc123"):
            instalacion.bloquear()

    def test_se_libera(self, instalacion):
        instalacion.bloquear()
        instalacion.desbloquear()
        assert not instalacion.bloqueada()
        instalacion.bloquear()  # no lanza

    def test_desbloquear_dos_veces_no_falla(self, instalacion):
        instalacion.desbloquear()
        instalacion.desbloquear()


class TestEntorno:
    def test_lee_el_env_del_cliente(self, instalacion):
        assert instalacion.entorno()["MEP_PUERTO_WEB"] == "8443"

    def test_sin_env_devuelve_vacio(self, tmp_path):
        d = tmp_path / "pelado"
        d.mkdir()
        assert Instalacion(d).entorno() == {}


class TestPaquete:
    def test_completo(self, paquete):
        assert paquete.existe()
        assert paquete.manifiesto()["release"] == "4.7.0"

    def test_sin_manifiesto(self, tmp_path):
        d = tmp_path / "incompleto"
        d.mkdir()
        (d / "compose.plantilla.yaml").write_text("x", encoding="utf-8")
        with pytest.raises(ErrorArchivo, match="manifiesto.json"):
            Paquete(d).exigir()

    def test_sin_plantilla(self, tmp_path, base):
        d = tmp_path / "incompleto2"
        d.mkdir()
        (d / "manifiesto.json").write_text(json.dumps(base), encoding="utf-8")
        with pytest.raises(ErrorArchivo, match="compose.plantilla.yaml"):
            Paquete(d).exigir()

    def test_firma_opcional(self, paquete):
        assert not paquete.tiene_firma()
        paquete.ruta_firma.write_text("firma", encoding="utf-8")
        assert paquete.tiene_firma()
