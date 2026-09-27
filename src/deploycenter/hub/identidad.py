"""Quién es el usuario de la web: validación del JWT.

El hub no emite tokens de personas: los emite el proveedor de identidad
(Supabase Auth, u otro que publique sus claves). Acá solo se valida la firma, el
emisor, la audiencia, el vencimiento y el segundo factor. Qué puede hacer cada
uno no sale del token: sale de las tablas de usuarios, en cada pedido.

Dos formas de validar la firma:

- **JWKS** (recomendada): claves públicas del proveedor, rotables. En Supabase,
  `<SUPABASE_URL>/auth/v1/.well-known/jwks.json`.
- **Secreto compartido HS256**: proyectos de Supabase con la clave JWT legada, y
  desarrollo local con `dc-hub token-dev`.

Nunca las dos a la vez: aceptar HS256 con una clave pública es el agujero
clásico de confusión de algoritmos.
"""

import os
import time

from .servicio import NoAutorizado, Prohibido

ALGORITMOS_ASIMETRICOS = ["RS256", "ES256"]


class ValidadorJWT:
    def __init__(self, jwks_url=None, secreto=None, emisor=None, audiencia="authenticated",
                 exigir_mfa=True, cliente_jwks=None, margen_s=30):
        try:
            import jwt
        except ImportError as e:  # pragma: no cover
            raise NoAutorizado("el hub necesita PyJWT: instalá el extra 'hub'") from e
        if bool(jwks_url or cliente_jwks) == bool(secreto):
            raise ValueError("configurá JWKS o secreto HS256, uno de los dos")
        self._jwt = jwt
        self.secreto = secreto
        self.emisor = emisor
        self.audiencia = audiencia
        self.exigir_mfa = exigir_mfa
        self.margen_s = margen_s
        self._jwks = cliente_jwks or (jwt.PyJWKClient(jwks_url, cache_keys=True)
                                      if jwks_url else None)

    @classmethod
    def desde_entorno(cls, entorno=None):
        """Lee la configuración del entorno. Devuelve None si no hay ninguna.

        SUPABASE_URL alcanza para un proyecto de Supabase: de ahí salen las
        claves (JWKS) y el emisor. DC_JWT_JWKS_URL / DC_JWT_SECRET / DC_JWT_EMISOR
        sirven para otro proveedor o para desarrollo.
        """
        env = os.environ if entorno is None else entorno
        supabase = (env.get("SUPABASE_URL") or "").rstrip("/")
        jwks_url = env.get("DC_JWT_JWKS_URL") or (
            f"{supabase}/auth/v1/.well-known/jwks.json" if supabase else None)
        secreto = env.get("DC_JWT_SECRET")
        if secreto:
            jwks_url = None
        if not jwks_url and not secreto:
            return None
        emisor = env.get("DC_JWT_EMISOR") or (f"{supabase}/auth/v1" if supabase else None)
        return cls(jwks_url=jwks_url, secreto=secreto, emisor=emisor,
                   audiencia=env.get("DC_JWT_AUDIENCIA", "authenticated"),
                   exigir_mfa=env.get("DC_EXIGIR_MFA", "1") != "0")

    def validar(self, token):
        """Devuelve los claims si el token es válido. Si no, NoAutorizado o Prohibido."""
        if not token:
            raise NoAutorizado("falta el token de sesión")
        jwt = self._jwt
        opciones = {"require": ["exp", "sub"], "verify_aud": self.audiencia is not None,
                    "verify_iss": self.emisor is not None}
        try:
            if self.secreto:
                clave, algoritmos = self.secreto, ["HS256"]
            else:
                clave, algoritmos = self._jwks.get_signing_key_from_jwt(token).key, \
                    ALGORITMOS_ASIMETRICOS
            claims = jwt.decode(token, clave, algorithms=algoritmos, audience=self.audiencia,
                                issuer=self.emisor, leeway=self.margen_s, options=opciones)
        except jwt.ExpiredSignatureError:
            raise NoAutorizado("la sesión venció: volvé a ingresar") from None
        except (jwt.PyJWTError, ValueError) as e:
            raise NoAutorizado(f"token de sesión inválido: {e}") from None

        if self.exigir_mfa and claims.get("aal") != "aal2":
            raise Prohibido("hace falta el segundo factor (TOTP) para usar deployCenter")
        return claims


def token_de_desarrollo(secreto, usuario_id, aal="aal2", horas=8, emisor=None,
                        audiencia="authenticated", email=None):
    """Un JWT como los de Supabase, firmado con un secreto local. Solo para desarrollo."""
    import jwt

    ahora = int(time.time())
    claims = {"sub": usuario_id, "aal": aal, "role": "authenticated", "iat": ahora,
              "exp": ahora + int(horas * 3600)}
    if audiencia:
        claims["aud"] = audiencia
    if emisor:
        claims["iss"] = emisor
    if email:
        claims["email"] = email
    return jwt.encode(claims, secreto, algorithm="HS256")
