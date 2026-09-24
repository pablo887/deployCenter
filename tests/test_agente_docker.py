import pytest

from deploycenter.agente.docker import Docker, ErrorDocker


@pytest.fixture
def stack(tmp_path):
    d = tmp_path / "raiz" / "mep"
    d.mkdir(parents=True)
    (d / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    return d


class TestLimitesDeSeguridad:
    """El agente no debe poder operar fuera de la raíz configurada. Es la
    respuesta a '¿pueden tocar cualquier cosa de mi servidor?'."""

    def test_rechaza_un_directorio_fuera_de_la_raiz(self, tmp_path, stack, runner_fijo):
        afuera = tmp_path / "otro"
        afuera.mkdir()
        (afuera / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
        d = Docker(ejecutar=runner_fijo(), raiz_permitida=stack.parent)
        with pytest.raises(ErrorDocker, match="fuera de la raíz permitida"):
            d.up(afuera)

    def test_rechaza_un_compose_fuera_de_la_raiz(self, tmp_path, stack, runner_fijo):
        afuera = tmp_path / "otro" / "docker-compose.yml"
        afuera.parent.mkdir()
        afuera.write_text("services: {}\n", encoding="utf-8")
        d = Docker(ejecutar=runner_fijo(), raiz_permitida=stack.parent)
        with pytest.raises(ErrorDocker, match="fuera de la raíz permitida"):
            d.pull(stack, archivo=afuera)

    def test_rechaza_escapar_con_dos_puntos(self, stack, runner_fijo):
        d = Docker(ejecutar=runner_fijo(), raiz_permitida=stack)
        with pytest.raises(ErrorDocker, match="fuera de la raíz permitida"):
            d.up(stack / ".." / "..")

    def test_sin_raiz_configurada_no_restringe(self, stack, runner_fijo):
        d = Docker(ejecutar=runner_fijo(), raiz_permitida=None)
        d.up(stack)  # no lanza

    def test_exige_que_el_compose_exista(self, tmp_path, runner_fijo):
        vacio = tmp_path / "vacio"
        vacio.mkdir()
        with pytest.raises(ErrorDocker, match="no existe el compose"):
            Docker(ejecutar=runner_fijo()).up(vacio)


class TestComandos:
    def test_up_no_construye_imagenes(self, stack, runner_fijo):
        ejecutar = runner_fijo()
        Docker(ejecutar=ejecutar).up(stack)
        comando = ejecutar.llamadas[0]
        assert "--no-build" in comando
        assert "-d" in comando

    def test_separa_project_directory_del_archivo(self, stack, tmp_path, runner_fijo):
        """Así el preflight baja el compose nuevo sin tocar el que corre, pero
        el .env del cliente se sigue resolviendo desde el stack."""
        preparado = stack / "preparado.yml"
        preparado.write_text("services: {}\n", encoding="utf-8")
        ejecutar = runner_fijo()
        Docker(ejecutar=ejecutar).pull(stack, archivo=preparado)

        comando = ejecutar.llamadas[0]
        i_dir = comando.index("--project-directory")
        i_arch = comando.index("-f")
        assert comando[i_dir + 1] == str(stack.resolve())
        assert comando[i_arch + 1] == str(preparado.resolve())

    def test_nunca_usa_shell(self, stack, runner_fijo):
        ejecutar = runner_fijo()
        Docker(ejecutar=ejecutar).up(stack)
        assert isinstance(ejecutar.llamadas[0], list)

    def test_propaga_el_error_con_la_salida(self, stack, runner_fijo):
        ejecutar = runner_fijo(codigo=1, error="port is already allocated")
        with pytest.raises(ErrorDocker) as e:
            Docker(ejecutar=ejecutar).up(stack)
        assert e.value.error == "port is already allocated"
        assert e.value.codigo == 1

    def test_ps_tolera_error_y_devuelve_vacio(self, stack, runner_fijo):
        d = Docker(ejecutar=runner_fijo(codigo=1, error="no such project"))
        assert d.ps(stack) == []

    def test_logs_acota_las_lineas(self, stack, runner_fijo):
        ejecutar = runner_fijo(salida="texto")
        Docker(ejecutar=ejecutar).logs(stack, servicio="api", lineas=50)
        comando = ejecutar.llamadas[0]
        assert "--tail" in comando and "50" in comando and "api" in comando

    def test_imagen_presente_consulta_por_la_referencia_completa(self, runner_fijo):
        ejecutar = runner_fijo(codigo=0, salida="sha256:abc")
        assert Docker(ejecutar=ejecutar).imagen_presente("reg/api@sha256:x")
        assert "reg/api@sha256:x" in ejecutar.llamadas[0]

    def test_imagen_ausente(self, runner_fijo):
        assert not Docker(ejecutar=runner_fijo(codigo=1)).imagen_presente("reg/api@sha256:x")


class TestParseoDePs:
    def test_formato_una_linea_por_servicio(self):
        salida = ('{"Service":"api","State":"running","Health":"healthy"}\n'
                  '{"Service":"web","State":"exited","Health":""}')
        assert Docker._parsear_ps(salida) == [
            {"servicio": "api", "estado": "running", "salud": "healthy"},
            {"servicio": "web", "estado": "exited", "salud": ""},
        ]

    def test_formato_array(self):
        salida = '[{"Service":"api","State":"running","Health":"HEALTHY"}]'
        assert Docker._parsear_ps(salida) == [
            {"servicio": "api", "estado": "running", "salud": "healthy"}]

    def test_vacio(self):
        assert Docker._parsear_ps("") == []
        assert Docker._parsear_ps(None) == []

    def test_ignora_lineas_que_no_son_json(self):
        salida = 'ruido\n{"Service":"api","State":"running"}'
        assert Docker._parsear_ps(salida) == [
            {"servicio": "api", "estado": "running", "salud": ""}]

    def test_array_roto(self):
        assert Docker._parsear_ps("[{roto") == []

    def test_cae_a_name_si_no_hay_service(self):
        assert Docker._parsear_ps('{"Name":"mep-api-1","State":"running"}')[0]["servicio"] == \
            "mep-api-1"
