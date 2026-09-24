import json

from deploycenter.agente.preflight import Preflight


def correr(docker, instalacion, paquete, **kw):
    return Preflight(docker, instalacion, paquete, **kw).correr(
        descargar=kw.pop("descargar", True))


def comprobacion(informe, nombre):
    return next(c for c in informe.comprobaciones if c.nombre == nombre)


class TestCaminoFeliz:
    def test_pasa_todo(self, docker_falso, instalacion, paquete):
        informe, preparado = Preflight(docker_falso, instalacion, paquete).correr()
        assert informe.ok, [c.detalle for c in informe.bloqueos]
        assert preparado.is_file()

    def test_no_toca_el_compose_que_esta_corriendo(self, docker_falso, instalacion, paquete):
        antes = instalacion.ruta_compose.read_text(encoding="utf-8")
        Preflight(docker_falso, instalacion, paquete).correr()
        assert instalacion.ruta_compose.read_text(encoding="utf-8") == antes

    def test_descarga_apuntando_al_compose_preparado(self, docker_falso, instalacion, paquete):
        _informe, preparado = Preflight(docker_falso, instalacion, paquete).correr()
        pulls = [c for c in docker_falso.llamadas if c[0] == "pull"]
        assert pulls and pulls[0][1] == str(preparado)

    def test_sin_descarga_no_llama_a_docker_pull(self, docker_falso, instalacion, paquete):
        Preflight(docker_falso, instalacion, paquete).correr(descargar=False)
        assert docker_falso.veces("pull") == 0


class TestManifiesto:
    def test_un_manifiesto_invalido_corta_temprano(self, docker_falso, instalacion,
                                                   paquete, base):
        base["imagenes"]["api"] = "registry.accusys.com.ar/mep/api:latest"
        paquete.ruta_manifiesto.write_text(json.dumps(base), encoding="utf-8")

        informe, preparado = Preflight(docker_falso, instalacion, paquete).correr()
        assert not informe.ok
        assert len(informe.comprobaciones) == 1  # no siguió
        assert preparado is None


class TestCircuito:
    def test_un_release_con_migraciones_no_es_autoservicio(self, docker_falso, instalacion,
                                                           paquete, base):
        base["db_migrations"] = True
        base["rollback_seguro"] = False
        paquete.ruta_manifiesto.write_text(json.dumps(base), encoding="utf-8")

        informe, _ = Preflight(docker_falso, instalacion, paquete).correr()
        c = comprobacion(informe, "circuito")
        assert not c.ok and c.bloqueante
        assert "Accusys" in c.detalle


class TestRutaDeUpgrade:
    def test_version_compatible(self, docker_falso, instalacion, paquete):
        informe, _ = Preflight(docker_falso, instalacion, paquete).correr()
        assert comprobacion(informe, "ruta_upgrade").ok

    def test_salto_no_soportado(self, docker_falso, instalacion, paquete):
        instalacion.guardar_estado(version="4.4.0")
        informe, _ = Preflight(docker_falso, instalacion, paquete).correr()
        c = comprobacion(informe, "ruta_upgrade")
        assert not c.ok
        assert "4.5.0" in c.detalle  # le dice por dónde pasar

    def test_ya_esta_instalada(self, docker_falso, instalacion, paquete):
        instalacion.guardar_estado(version="4.7.0")
        assert not comprobacion(
            Preflight(docker_falso, instalacion, paquete).correr()[0], "ruta_upgrade").ok

    def test_instalacion_inicial_es_solo_aviso(self, docker_falso, instalacion_nueva, paquete):
        informe, _ = Preflight(docker_falso, instalacion_nueva, paquete).correr()
        c = comprobacion(informe, "ruta_upgrade")
        assert c.ok and not c.bloqueante


class TestVariables:
    def test_el_default_no_bloquea(self, docker_falso, instalacion, paquete, base):
        base["variables_nuevas"] = [{
            "nombre": "MEP_FIRMA_TIMEOUT_S", "obligatoria": True, "default": "30",
            "descripcion": "Timeout de firma del conector, en segundos"}]
        paquete.ruta_manifiesto.write_text(json.dumps(base), encoding="utf-8")

        informe, _ = Preflight(docker_falso, instalacion, paquete).correr()
        assert comprobacion(informe, "variables").ok
        assert informe.ok

    def test_una_obligatoria_sin_default_bloquea(self, docker_falso, instalacion,
                                                 paquete, base):
        base["variables_nuevas"] = [{
            "nombre": "MEP_HSM_URL", "obligatoria": True, "default": None,
            "descripcion": "URL del HSM, que solo conoce el cliente"}]
        paquete.ruta_manifiesto.write_text(json.dumps(base), encoding="utf-8")

        informe, _ = Preflight(docker_falso, instalacion, paquete).correr()
        assert not informe.ok
        assert "MEP_HSM_URL" in comprobacion(informe, "variables").detalle

    def test_si_falta_una_variable_no_se_descarga(self, docker_falso, instalacion,
                                                  paquete, base):
        base["variables_nuevas"] = [{
            "nombre": "MEP_HSM_URL", "obligatoria": True, "default": None,
            "descripcion": "URL del HSM, que solo conoce el cliente"}]
        paquete.ruta_manifiesto.write_text(json.dumps(base), encoding="utf-8")

        Preflight(docker_falso, instalacion, paquete).correr()
        assert docker_falso.veces("pull") == 0


class TestEspacio:
    def test_suficiente(self, docker_falso, instalacion, paquete):
        assert comprobacion(
            Preflight(docker_falso, instalacion, paquete).correr()[0], "espacio").ok

    def test_insuficiente_bloquea(self, docker_falso, instalacion, paquete):
        docker_falso.espacio = 1.0
        informe, _ = Preflight(docker_falso, instalacion, paquete,
                               espacio_minimo_gb=5.0).correr()
        c = comprobacion(informe, "espacio")
        assert not c.ok
        assert "1.0 GB" in c.detalle


class TestDescarga:
    def test_el_pull_que_falla_bloquea(self, docker_falso, instalacion, paquete):
        docker_falso.fallar_pull = True
        informe, _ = Preflight(docker_falso, instalacion, paquete).correr()
        assert not informe.ok
        assert "manifest unknown" in comprobacion(informe, "descarga").detalle

    def test_verifica_que_la_imagen_quede_por_su_digest(self, docker_falso, instalacion,
                                                        paquete, base):
        docker_falso.imagenes_faltantes = {base["imagenes"]["api"]}
        informe, _ = Preflight(docker_falso, instalacion, paquete).correr()
        c = comprobacion(informe, "descarga")
        assert not c.ok
        assert "api" in c.detalle


class TestFirma:
    def test_sin_clave_publica_es_aviso_no_bloqueo(self, docker_falso, instalacion, paquete):
        informe, _ = Preflight(docker_falso, instalacion, paquete).correr()
        c = comprobacion(informe, "firma")
        assert not c.ok and not c.bloqueante
        assert informe.ok

    def test_con_clave_y_sin_firma_bloquea(self, docker_falso, instalacion, paquete, tmp_path):
        clave = tmp_path / "cosign.pub"
        clave.write_text("clave", encoding="utf-8")
        informe, _ = Preflight(docker_falso, instalacion, paquete,
                               clave_publica=clave).correr()
        c = comprobacion(informe, "firma")
        assert not c.ok and c.bloqueante


class TestEstadoActual:
    def test_servicios_caidos_avisan_pero_no_bloquean(self, docker_falso, instalacion,
                                                      paquete):
        docker_falso.sano = False
        informe, _ = Preflight(docker_falso, instalacion, paquete).correr()
        c = comprobacion(informe, "estado_actual")
        assert not c.ok and not c.bloqueante
        assert informe.ok  # se puede desplegar igual

    def test_instalacion_inicial(self, docker_falso, instalacion_nueva, paquete):
        c = comprobacion(
            Preflight(docker_falso, instalacion_nueva, paquete).correr()[0], "estado_actual")
        assert c.ok


class TestInforme:
    def test_resumen_es_serializable(self, docker_falso, instalacion, paquete):
        informe, _ = Preflight(docker_falso, instalacion, paquete).correr()
        assert json.loads(json.dumps(informe.resumen()))["ok"] is True
