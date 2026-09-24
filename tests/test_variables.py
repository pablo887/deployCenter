import pytest

from deploycenter import variables as v
from deploycenter.errores import ErrorArchivo

OBLIGATORIA_SIN_DEFAULT = {
    "nombre": "MEP_HSM_URL",
    "obligatoria": True,
    "default": None,
    "descripcion": "URL del HSM del cliente, que solo conoce el cliente",
}
OBLIGATORIA_CON_DEFAULT = {
    "nombre": "MEP_FIRMA_TIMEOUT_S",
    "obligatoria": True,
    "default": "30",
    "descripcion": "Timeout de firma del conector, en segundos",
}
OPCIONAL = {
    "nombre": "MEP_LOG_NIVEL",
    "obligatoria": False,
    "default": "info",
    "descripcion": "Nivel de log de la aplicación",
}


class TestParseoDeEnv:
    def test_lo_basico(self):
        assert v.parsear_env("A=1\nB=dos\n") == {"A": "1", "B": "dos"}

    def test_ignora_comentarios_y_vacias(self):
        texto = "# comentario\n\nA=1\n   \n# otro\nB=2\n"
        assert v.parsear_env(texto) == {"A": "1", "B": "2"}

    def test_saca_comillas(self):
        assert v.parsear_env('A="con espacios"\nB=\'simple\'\n') == {
            "A": "con espacios", "B": "simple"}

    def test_acepta_export(self):
        assert v.parsear_env("export A=1\n") == {"A": "1"}

    def test_comentario_al_final_de_linea(self):
        assert v.parsear_env("A=1 # esto es un comentario\n") == {"A": "1"}

    def test_valor_con_numeral_pegado_no_es_comentario(self):
        assert v.parsear_env("A=clave#rara\n") == {"A": "clave#rara"}

    def test_valor_vacio(self):
        assert v.parsear_env("A=\n") == {"A": ""}

    def test_linea_invalida_dice_cual(self):
        with pytest.raises(ErrorArchivo, match="línea 2"):
            v.parsear_env("A=1\nesto no es una asignación\n")

    def test_lee_y_escribe(self, tmp_path):
        ruta = tmp_path / ".env"
        v.escribir_env({"B": "2", "A": "1"}, ruta)
        assert ruta.read_text(encoding="utf-8") == "A=1\nB=2\n"
        assert v.leer_env(ruta) == {"A": "1", "B": "2"}

    def test_archivo_que_no_existe(self, tmp_path):
        with pytest.raises(ErrorArchivo, match="no se pudo leer"):
            v.leer_env(tmp_path / "no-existe")


class TestClasificacion:
    def test_falta_una_obligatoria_sin_default(self, variante):
        m = variante(variables_nuevas=[OBLIGATORIA_SIN_DEFAULT])
        inf = v.informe(m, {})
        assert [x["nombre"] for x in inf["faltantes"]] == ["MEP_HSM_URL"]
        assert inf["bloquea"] is True

    def test_la_que_tiene_default_no_bloquea(self, variante):
        m = variante(variables_nuevas=[OBLIGATORIA_CON_DEFAULT])
        inf = v.informe(m, {})
        assert inf["faltantes"] == []
        assert [x["nombre"] for x in inf["completables"]] == ["MEP_FIRMA_TIMEOUT_S"]
        assert inf["bloquea"] is False

    def test_lo_que_ya_esta_no_se_toca(self, variante):
        m = variante(variables_nuevas=[OBLIGATORIA_CON_DEFAULT])
        inf = v.informe(m, {"MEP_FIRMA_TIMEOUT_S": "90"})
        assert inf["completables"] == []
        assert [x["nombre"] for x in inf["presentes"]] == ["MEP_FIRMA_TIMEOUT_S"]

    def test_release_sin_variables_nuevas(self, base):
        inf = v.informe(base, {})
        assert inf["bloquea"] is False
        assert inf["faltantes"] == []


class TestDefaults:
    def test_aplica_los_que_faltan(self, variante):
        m = variante(variables_nuevas=[OBLIGATORIA_CON_DEFAULT, OPCIONAL])
        salida = v.aplicar_defaults(m, {})
        assert salida == {"MEP_FIRMA_TIMEOUT_S": "30", "MEP_LOG_NIVEL": "info"}

    def test_no_pisa_lo_que_el_cliente_ya_puso(self, variante):
        m = variante(variables_nuevas=[OBLIGATORIA_CON_DEFAULT])
        salida = v.aplicar_defaults(m, {"MEP_FIRMA_TIMEOUT_S": "90"})
        assert salida["MEP_FIRMA_TIMEOUT_S"] == "90"

    def test_no_muta_el_entorno_original(self, variante):
        m = variante(variables_nuevas=[OBLIGATORIA_CON_DEFAULT])
        entorno = {}
        v.aplicar_defaults(m, entorno)
        assert entorno == {}

    def test_no_inventa_valor_para_una_sin_default(self, variante):
        m = variante(variables_nuevas=[OBLIGATORIA_SIN_DEFAULT])
        assert v.aplicar_defaults(m, {}) == {}
