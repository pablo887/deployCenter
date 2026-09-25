"""Validación del JWT de las personas: el camino de producción (JWKS, claves
asimétricas) y la configuración desde el entorno."""

import base64
import hashlib
import hmac
import json
import time
import uuid

import pytest

jwt = pytest.importorskip("jwt")
pytest.importorskip("cryptography")

from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec, rsa  # noqa: E402

from deploycenter.hub import servicio as srv  # noqa: E402
from deploycenter.hub.identidad import ValidadorJWT  # noqa: E402

EMISOR = "https://abcd.supabase.co/auth/v1"


class ClaveFija:
    def __init__(self, clave_publica):
        self.key = clave_publica


class JWKSFalso:
    """Hace de PyJWKClient: devuelve siempre la misma clave pública."""

    def __init__(self, clave_publica):
        self.clave = clave_publica

    def get_signing_key_from_jwt(self, _token):
        return ClaveFija(self.clave)


def claims(**kw):
    base = {"sub": str(uuid.uuid4()), "aud": "authenticated", "iss": EMISOR, "aal": "aal2",
            "exp": int(time.time()) + 600}
    base.update(kw)
    return base


@pytest.fixture(params=["RS256", "ES256"])
def par(request):
    if request.param == "RS256":
        privada = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    else:
        privada = ec.generate_private_key(ec.SECP256R1())
    return request.param, privada, privada.public_key()


def validador(publica, **kw):
    return ValidadorJWT(cliente_jwks=JWKSFalso(publica), emisor=EMISOR, **kw)


class TestJWKS:
    def test_token_valido(self, par):
        alg, privada, publica = par
        c = claims()
        assert validador(publica).validar(jwt.encode(c, privada, algorithm=alg))["sub"] == c["sub"]

    def test_firmado_con_otra_clave(self, par):
        alg, _privada, publica = par
        otra = (rsa.generate_private_key(public_exponent=65537, key_size=2048) if alg == "RS256"
                else ec.generate_private_key(ec.SECP256R1()))
        with pytest.raises(srv.NoAutorizado):
            validador(publica).validar(jwt.encode(claims(), otra, algorithm=alg))

    def test_confusion_de_algoritmos(self, par):
        """HS256 firmado con la clave pública como secreto: el agujero clásico."""
        _alg, _privada, publica = par
        pem = publica.public_bytes(serialization.Encoding.PEM,
                                   serialization.PublicFormat.SubjectPublicKeyInfo)
        # PyJWT se niega a firmar HS256 con un PEM, así que el token se arma a mano
        def b64(d):
            return base64.urlsafe_b64encode(d).rstrip(b"=")

        cabecera = b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
        cuerpo = b64(json.dumps(claims()).encode())
        firma = b64(hmac.new(pem, cabecera + b"." + cuerpo, hashlib.sha256).digest())
        falso = (cabecera + b"." + cuerpo + b"." + firma).decode()
        with pytest.raises(srv.NoAutorizado):
            validador(publica).validar(falso)

    def test_alg_none(self, par):
        _alg, _privada, publica = par
        sin_firma = jwt.encode(claims(), None, algorithm="none")
        with pytest.raises(srv.NoAutorizado):
            validador(publica).validar(sin_firma)

    @pytest.mark.parametrize("campo,valor", [
        ("aud", "otra-audiencia"), ("iss", "https://otro.supabase.co/auth/v1"),
        ("exp", 1000)])
    def test_audiencia_emisor_y_vencimiento(self, par, campo, valor):
        alg, privada, publica = par
        with pytest.raises(srv.NoAutorizado):
            validador(publica).validar(jwt.encode(claims(**{campo: valor}), privada,
                                                  algorithm=alg))

    def test_sin_sub(self, par):
        alg, privada, publica = par
        c = claims()
        del c["sub"]
        with pytest.raises(srv.NoAutorizado):
            validador(publica).validar(jwt.encode(c, privada, algorithm=alg))

    def test_segundo_factor(self, par):
        alg, privada, publica = par
        t = jwt.encode(claims(aal="aal1"), privada, algorithm=alg)
        with pytest.raises(srv.Prohibido):
            validador(publica).validar(t)
        assert validador(publica, exigir_mfa=False).validar(t)


class TestConfiguracion:
    def test_supabase_url_alcanza(self):
        v = ValidadorJWT.desde_entorno({"SUPABASE_URL": "https://abcd.supabase.co/"})
        assert v.emisor == EMISOR
        assert v.secreto is None and v._jwks is not None
        assert v.exigir_mfa is True

    def test_secreto_para_desarrollo(self):
        v = ValidadorJWT.desde_entorno({"DC_JWT_SECRET": "x" * 32, "DC_EXIGIR_MFA": "0"})
        assert v.secreto and v._jwks is None and v.exigir_mfa is False

    def test_con_secreto_no_se_usa_jwks(self):
        """Uno u otro, nunca los dos: aceptar ambos abre la confusión de algoritmos."""
        v = ValidadorJWT.desde_entorno({"SUPABASE_URL": "https://abcd.supabase.co",
                                        "DC_JWT_SECRET": "x" * 32})
        assert v._jwks is None

    def test_sin_configuracion(self):
        assert ValidadorJWT.desde_entorno({}) is None

    def test_no_se_configuran_los_dos_a_mano(self):
        with pytest.raises(ValueError):
            ValidadorJWT(jwks_url="https://x/jwks.json", secreto="y" * 32)
