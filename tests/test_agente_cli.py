import json

import pytest

from deploycenter.agente import cli
from deploycenter.salida import ERROR_USO, FALLA_VALIDACION, OK


@pytest.fixture
def docker_conectado(monkeypatch, docker_falso):
    """El CLI construye su propio Docker; acá le damos el doble."""
    monkeypatch.setattr(cli, "Docker", lambda **kw: docker_falso)
    return docker_falso


def correr(*argv):
    return cli.main(["--sin-color", *argv])


class TestEstado:
    def test_sin_desplegar(self, docker_conectado, instalacion_nueva, capsys):
        assert correr("estado", "--instalacion", str(instalacion_nueva.directorio)) == OK
        assert "sin estado registrado" in capsys.readouterr().out

    def test_muestra_version_y_retorno(self, docker_conectado, instalacion, capsys):
        instalacion.tomar_punto_retorno(manifiesto_actual={"release": "4.5.0"})
        assert correr("estado", "--instalacion", str(instalacion.directorio)) == OK
        salida = capsys.readouterr().out
        assert "mep 4.6.0" in salida
        assert "4.5.0" in salida

    def test_con_servicios_consulta_docker(self, docker_conectado, instalacion, capsys):
        correr("estado", "--instalacion", str(instalacion.directorio), "--servicios")
        assert "api" in capsys.readouterr().out
        assert docker_conectado.veces("ps") == 1


class TestPreflight:
    def test_ok(self, docker_conectado, instalacion, paquete, capsys):
        codigo = correr("preflight", "--instalacion", str(instalacion.directorio),
                        "--paquete", str(paquete.directorio))
        assert codigo == OK
        assert "listo para desplegar" in capsys.readouterr().out

    def test_con_bloqueo_devuelve_1(self, docker_conectado, instalacion, paquete, capsys):
        instalacion.guardar_estado(version="4.4.0")
        codigo = correr("preflight", "--instalacion", str(instalacion.directorio),
                        "--paquete", str(paquete.directorio))
        assert codigo == FALLA_VALIDACION
        assert "bloqueo" in capsys.readouterr().out

    def test_sin_descarga_no_hace_pull(self, docker_conectado, instalacion, paquete):
        correr("preflight", "--instalacion", str(instalacion.directorio),
               "--paquete", str(paquete.directorio), "--sin-descarga")
        assert docker_conectado.veces("pull") == 0

    def test_paquete_incompleto_es_error_de_uso(self, docker_conectado, instalacion,
                                                tmp_path, capsys):
        vacio = tmp_path / "vacio"
        vacio.mkdir()
        codigo = correr("preflight", "--instalacion", str(instalacion.directorio),
                        "--paquete", str(vacio))
        assert codigo == ERROR_USO
        assert "manifiesto.json" in capsys.readouterr().err


class TestDesplegar:
    def test_camino_feliz(self, docker_conectado, instalacion, paquete, capsys):
        codigo = correr("desplegar", "--instalacion", str(instalacion.directorio),
                        "--paquete", str(paquete.directorio), "--espera-cancelacion", "0")
        assert codigo == OK
        assert instalacion.version_instalada() == "4.7.0"
        assert "4.6.0 → 4.7.0" in capsys.readouterr().out

    def test_preflight_que_no_pasa_se_ve_en_pantalla(self, docker_conectado, instalacion,
                                                     paquete, capsys):
        instalacion.guardar_estado(version="4.4.0")
        codigo = correr("desplegar", "--instalacion", str(instalacion.directorio),
                        "--paquete", str(paquete.directorio), "--espera-cancelacion", "0")
        assert codigo == FALLA_VALIDACION
        salida = capsys.readouterr().out
        assert "ruta_upgrade" in salida

    def test_no_despliega_si_hay_otro_en_curso(self, docker_conectado, instalacion,
                                               paquete, capsys):
        instalacion.bloquear(dueno="otro")
        codigo = correr("desplegar", "--instalacion", str(instalacion.directorio),
                        "--paquete", str(paquete.directorio), "--espera-cancelacion", "0")
        assert codigo == FALLA_VALIDACION
        assert "despliegue en curso" in capsys.readouterr().err


class TestRollback:
    def test_pide_confirmacion(self, docker_conectado, instalacion, capsys):
        instalacion.tomar_punto_retorno(manifiesto_actual={"release": "4.5.0"})
        assert correr("rollback", "--instalacion", str(instalacion.directorio)) == OK
        assert "--si" in capsys.readouterr().out
        assert docker_conectado.veces("up") == 0

    def test_sin_punto_de_retorno(self, docker_conectado, instalacion, capsys):
        assert correr("rollback", "--instalacion",
                      str(instalacion.directorio)) == FALLA_VALIDACION
        assert "no hay punto de retorno" in capsys.readouterr().out

    def test_con_si_ejecuta(self, docker_conectado, instalacion, paquete, capsys):
        correr("desplegar", "--instalacion", str(instalacion.directorio),
               "--paquete", str(paquete.directorio), "--espera-cancelacion", "0")
        capsys.readouterr()

        assert correr("rollback", "--instalacion", str(instalacion.directorio), "--si") == OK
        assert instalacion.version_instalada() == "4.6.0"


class TestHistorial:
    def test_vacio(self, docker_conectado, instalacion_nueva, capsys):
        assert correr("historial", "--instalacion",
                      str(instalacion_nueva.directorio)) == OK
        assert "sin historial" in capsys.readouterr().out

    def test_en_json(self, docker_conectado, instalacion, paquete, capsys):
        correr("desplegar", "--instalacion", str(instalacion.directorio),
               "--paquete", str(paquete.directorio), "--espera-cancelacion", "0")
        capsys.readouterr()

        correr("historial", "--instalacion", str(instalacion.directorio), "--json")
        lineas = [json.loads(x) for x in capsys.readouterr().out.strip().splitlines()]
        assert any(e["operacion"] == "despliegue" and e["resultado"] == "ok" for e in lineas)

    def test_limite(self, docker_conectado, instalacion, capsys):
        for i in range(4):
            instalacion.registrar("op", "ok", n=i)
        correr("historial", "--instalacion", str(instalacion.directorio), "--limite", "2")
        assert len(capsys.readouterr().out.strip().splitlines()) == 2


class TestLogs:
    def test_imprime_lo_que_da_docker(self, docker_conectado, instalacion, capsys):
        assert correr("logs", "--instalacion", str(instalacion.directorio),
                      "--servicio", "api") == OK
        assert "logs de prueba" in capsys.readouterr().out
