from deploycenter.agente import verificacion as v


def cliente(respuestas):
    """Cliente HTTP falso: url -> (codigo, detalle)."""
    def _pedir(url, timeout):
        return respuestas.get(url, (None, "sin ruta"))
    return _pedir


class TestEsperarContenedores:
    def test_todos_arriba_y_sanos(self, docker_falso, instalacion, reloj):
        r = v.esperar_contenedores(docker_falso, instalacion, ["api", "web"],
                                   dormir=reloj.dormir, reloj=reloj)
        assert r.ok

    def test_un_servicio_caido(self, docker_falso, instalacion, reloj):
        docker_falso.sano = False
        r = v.esperar_contenedores(docker_falso, instalacion, ["api", "web"],
                                   timeout_s=20, dormir=reloj.dormir, reloj=reloj)
        assert not r.ok
        assert "api" in r.detalle

    def test_servicio_que_no_aparece(self, docker_falso, instalacion, reloj):
        r = v.esperar_contenedores(docker_falso, instalacion, ["api", "fantasma"],
                                   timeout_s=20, dormir=reloj.dormir, reloj=reloj)
        assert not r.ok
        assert "fantasma" in r.detalle

    def test_reintenta_hasta_el_timeout(self, docker_falso, instalacion, reloj):
        docker_falso.sano = False
        v.esperar_contenedores(docker_falso, instalacion, ["api"], timeout_s=60,
                               dormir=reloj.dormir, reloj=reloj)
        assert docker_falso.veces("ps") > 1

    def test_starting_todavia_no_es_sano(self, docker_falso, instalacion, reloj):
        docker_falso.ps = lambda d: [{"servicio": "api", "estado": "running",
                                      "salud": "starting"}]
        r = v.esperar_contenedores(docker_falso, instalacion, ["api"], timeout_s=10,
                                   dormir=reloj.dormir, reloj=reloj)
        assert not r.ok
        assert "starting" in r.detalle

    def test_sin_healthcheck_declarado_alcanza_con_running(self, docker_falso,
                                                           instalacion, reloj):
        docker_falso.ps = lambda d: [{"servicio": "api", "estado": "running", "salud": ""}]
        r = v.esperar_contenedores(docker_falso, instalacion, ["api"],
                                   dormir=reloj.dormir, reloj=reloj)
        assert r.ok


class TestSmokeHttp:
    def test_codigo_esperado(self):
        checks = [{"servicio": "api", "url": "http://api:8080/health",
                   "espera": 200, "timeout_s": 5}]
        r = v.smoke_http(checks, cliente=cliente({"http://api:8080/health": (200, "")}))
        assert r[0].ok and not r[0].omitido

    def test_codigo_distinto_es_falla(self):
        checks = [{"servicio": "api", "url": "http://api:8080/health",
                   "espera": 200, "timeout_s": 5}]
        r = v.smoke_http(checks, cliente=cliente({"http://api:8080/health": (503, "")}))
        assert not r[0].ok
        assert "503" in r[0].detalle

    def test_inalcanzable_se_marca_omitido_no_exitoso(self):
        """Si el agente no llega a la red del stack, decimos 'no lo pude
        comprobar'. Dar por bueno algo que no se comprobó sería peor."""
        checks = [{"servicio": "api", "url": "http://api:8080/health",
                   "espera": 200, "timeout_s": 5}]
        r = v.smoke_http(checks, cliente=cliente({}))
        assert r[0].omitido
        assert "no se pudo alcanzar" in r[0].detalle


class TestInforme:
    def test_los_omitidos_no_hacen_fallar(self):
        inf = v.Informe([v.Resultado("a", True, ""), v.Resultado("b", True, "", omitido=True)])
        assert inf.ok
        assert len(inf.omitidos) == 1

    def test_una_falla_alcanza(self):
        inf = v.Informe([v.Resultado("a", True, ""), v.Resultado("b", False, "roto")])
        assert not inf.ok
        assert inf.fallas[0].nombre == "b"

    def test_resumen_es_serializable(self):
        import json
        inf = v.Informe([v.Resultado("a", True, "bien")], segundos=1.23)
        assert json.loads(json.dumps(inf.resumen()))["ok"] is True


class TestVerificarCompleto:
    def test_ok_de_punta_a_punta(self, docker_falso, instalacion, base, reloj):
        respuestas = {hc["url"]: (hc["espera"], "") for hc in base["healthchecks"]}
        inf = v.verificar(docker_falso, instalacion, base,
                          cliente_http=cliente(respuestas),
                          dormir=reloj.dormir, reloj=reloj)
        assert inf.ok
        assert len(inf.resultados) == 3  # contenedores + 2 smoke

    def test_si_los_contenedores_no_levantan_no_hace_smoke(self, docker_falso,
                                                           instalacion, base, reloj):
        docker_falso.sano = False
        inf = v.verificar(docker_falso, instalacion, base, timeout_s=10,
                          cliente_http=cliente({}), dormir=reloj.dormir, reloj=reloj)
        assert not inf.ok
        assert len(inf.resultados) == 1

    def test_el_smoke_fallado_hace_fallar_todo(self, docker_falso, instalacion, base, reloj):
        respuestas = {hc["url"]: (500, "") for hc in base["healthchecks"]}
        inf = v.verificar(docker_falso, instalacion, base,
                          cliente_http=cliente(respuestas),
                          dormir=reloj.dormir, reloj=reloj)
        assert not inf.ok

    def test_smoke_inalcanzable_no_hace_fallar(self, docker_falso, instalacion, base, reloj):
        inf = v.verificar(docker_falso, instalacion, base,
                          cliente_http=cliente({}), dormir=reloj.dormir, reloj=reloj)
        assert inf.ok
        assert len(inf.omitidos) == 2
