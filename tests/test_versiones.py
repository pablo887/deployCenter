import pytest

from deploycenter import versiones as v


class TestParseo:
    def test_semver_simple(self):
        assert v.parsear("4.7.1") == (4, 7, 1, None)

    def test_con_prerelease(self):
        assert v.parsear("4.7.1-rc1") == (4, 7, 1, "rc1")

    @pytest.mark.parametrize("malo", ["4.7", "v4.7.1", "4.7.1.2", "", "cuatro.siete.uno"])
    def test_rechaza_lo_que_no_es_semver(self, malo):
        with pytest.raises(v.VersionInvalida):
            v.parsear(malo)


class TestComparacion:
    @pytest.mark.parametrize("a,b,esperado", [
        ("4.7.1", "4.7.0", 1),
        ("4.7.0", "4.7.1", -1),
        ("4.7.0", "4.7.0", 0),
        ("5.0.0", "4.99.99", 1),
        ("4.10.0", "4.9.0", 1),  # compara números, no texto
    ])
    def test_orden(self, a, b, esperado):
        assert v.comparar(a, b) == esperado

    def test_prerelease_va_antes_del_final(self):
        assert v.comparar("4.7.0-rc1", "4.7.0") == -1
        assert v.es_mayor("4.7.0", "4.7.0-rc1")


class TestRangos:
    def test_asterisco_acepta_todo(self):
        assert v.permite("*", "1.0.0")
        assert v.permite("*", "99.99.99")

    @pytest.mark.parametrize("version,esperado", [
        ("4.4.9", False),
        ("4.5.0", True),
        ("4.9.9", True),
    ])
    def test_mayor_o_igual(self, version, esperado):
        assert v.permite(">=4.5.0", version) is esperado

    def test_varias_clausulas_son_and(self):
        rango = ">=4.5.0, <5.0.0"
        assert v.permite(rango, "4.7.0")
        assert not v.permite(rango, "5.0.0")
        assert not v.permite(rango, "4.4.0")

    def test_sin_operador_es_exacta(self):
        assert v.permite("4.5.0", "4.5.0")
        assert not v.permite("4.5.0", "4.5.1")

    def test_rango_invalido(self):
        assert not v.rango_valido(">=cuatro")
        assert v.rango_valido(">=4.5.0")

    def test_cotas_inferiores(self):
        assert v.cotas_inferiores(">=4.5.0, <5.0.0") == ["4.5.0"]
        assert v.cotas_inferiores("*") == []
