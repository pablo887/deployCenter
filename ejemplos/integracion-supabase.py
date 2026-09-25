"""Prueba de integración contra un proyecto real de Supabase.

Recorre lo que hace una persona de verdad y comprueba que la base aplica los
permisos con los claims que emite Supabase, no con los que arman los tests:

  1. crea usuarios de prueba (confirmados) con la secret key y les da rol en
     clientes de prueba (usuarios_tenant / usuarios_accusys) por la Management API;
  2. cada uno ingresa con contraseña (publishable key), enrola TOTP, desafía y
     verifica con el código calculado del secreto, y queda con un token aal2;
  3. `ValidadorJWT.desde_entorno()` valida ese token contra el JWKS real, y el
     hook tiene que haber agregado app_metadata.tenant_id y dc_rol;
  4. con esos claims, SQL por la Management API reproduce los chequeos de RLS de
     tests/test_hub_postgres.py: aislamiento entre clientes sin filtro, sin
     segundo factor no se ve nada, las columnas de hash no se leen;
  5. borra usuarios y datos de prueba, salga bien o mal.

    python ejemplos/integracion-supabase.py

Necesita SUPABASE_URL, SUPABASE_PUBLISHABLE_KEY, SUPABASE_SECRET_KEY y
SUPABASE_ACCESS_TOKEN; si falta alguna, se saltea. Antes: `dc-hub migrar
--via-api` y el hook activo (Authentication → Hooks → Custom Access Token →
public.custom_access_token_hook). Los datos de prueba llevan el prefijo
`zz-prueba-`, así se reconocen si algo quedara a medias.
"""

import base64
import hashlib
import hmac
import json
import os
import secrets
import struct
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from deploycenter.errores import ErrorDeployCenter
from deploycenter.hub import migraciones
from deploycenter.hub.identidad import ValidadorJWT
from deploycenter.hub.servicio import Prohibido

VARIABLES = ("SUPABASE_URL", "SUPABASE_PUBLISHABLE_KEY", "SUPABASE_SECRET_KEY",
             "SUPABASE_ACCESS_TOKEN")
RAIZ = Path(__file__).resolve().parents[1]


# --------------------------------------------------------------------------- #
# HTTP y TOTP
# --------------------------------------------------------------------------- #

def http(metodo, url, cuerpo=None, cabeceras=None):
    datos = json.dumps(cuerpo).encode() if cuerpo is not None else None
    pedido = urllib.request.Request(url, data=datos, method=metodo, headers={
        "Content-Type": "application/json", **(cabeceras or {})})
    try:
        with urllib.request.urlopen(pedido, timeout=30) as r:
            texto = r.read()
            return r.status, json.loads(texto) if texto else None
    except urllib.error.HTTPError as e:
        texto = e.read()
        try:
            return e.code, json.loads(texto)
        except ValueError:
            return e.code, texto[:300].decode("utf-8", "replace")


def totp(secreto, momento=None, digitos=6, paso=30):
    """RFC 6238 con SHA-1, que es lo que usa Supabase."""
    clave = base64.b32decode(secreto.upper() + "=" * (-len(secreto) % 8))
    contador = int((time.time() if momento is None else momento) // paso)
    resumen = hmac.new(clave, struct.pack(">Q", contador), hashlib.sha1).digest()
    corte = resumen[-1] & 0x0F
    numero = struct.unpack(">I", resumen[corte:corte + 4])[0] & 0x7FFFFFFF
    return str(numero % 10 ** digitos).zfill(digitos)


def literal(texto):
    """Literal SQL con dollar quoting (la API no recibe parámetros)."""
    marca = "$dc" + secrets.token_hex(4) + "$"
    assert marca not in texto
    return f"{marca}{texto}{marca}"


# --------------------------------------------------------------------------- #
# la prueba
# --------------------------------------------------------------------------- #

class Integracion:
    def __init__(self):
        self.url = os.environ["SUPABASE_URL"].rstrip("/")
        self.publicable = os.environ["SUPABASE_PUBLISHABLE_KEY"]
        self.secreta = os.environ["SUPABASE_SECRET_KEY"]
        self.api = migraciones.ApiSupabase.desde_entorno()
        sufijo = secrets.token_hex(3)
        self.tenants = [f"zz-prueba-{sufijo}-a", f"zz-prueba-{sufijo}-b"]
        self.sufijo = sufijo
        self.usuarios = {}   # clave → id
        self.fallas = []

    # --- utilidades -------------------------------------------------------

    def sql(self, texto):
        return self.api.consultar(texto)

    def comprobar(self, condicion, que):
        print(f"  {'✓' if condicion else '✗'} {que}")
        if not condicion:
            self.fallas.append(que)

    def admin(self, metodo, ruta, cuerpo=None):
        return http(metodo, f"{self.url}/auth/v1{ruta}", cuerpo,
                    {"apikey": self.secreta, "Authorization": f"Bearer {self.secreta}"})

    def auth(self, metodo, ruta, cuerpo=None, token=None):
        cab = {"apikey": self.publicable}
        if token:
            cab["Authorization"] = f"Bearer {token}"
        return http(metodo, f"{self.url}/auth/v1{ruta}", cuerpo, cab)

    def como(self, claims, consulta):
        """Filas de `consulta` con la identidad de esos claims, como la arma el hub
        (set_config local + set local role authenticated). Todo en una sola
        llamada: la API corre el texto como una transacción implícita."""
        return self.sql(
            f"select set_config('request.jwt.claims', {literal(json.dumps(claims))}, true);\n"
            "set local role authenticated;\n"
            f"with t as ({consulta}) select coalesce(json_agg(t), '[]'::json) as filas from t"
        )[0]["filas"]

    def rechaza(self, claims, consulta):
        try:
            self.como(claims, consulta)
        except ErrorDeployCenter as e:
            return "permission denied" in str(e) or "row-level security" in str(e)
        return False

    # --- precondiciones ---------------------------------------------------

    def precondiciones(self):
        print("precondiciones")
        filas = self.sql("select 1 as a; select 2 as b")
        if filas != [{"b": 2}]:
            raise SystemExit(f"la API no devuelve el último resultado ({filas!r}): "
                             "los chequeos de abajo no valdrían")
        faltan = migraciones.pendientes(self.api, migraciones.directorio_por_defecto(RAIZ))
        if faltan:
            raise SystemExit("faltan migraciones: corré 'dc-hub migrar --via-api' "
                             f"({', '.join(r.name for r in faltan)})")
        estado, config = http(
            "GET", f"{migraciones.API_SUPABASE}/v1/projects/{self.api.ref}/config/auth",
            cabeceras={"Authorization": f"Bearer {self.api.token}"})
        if estado != 200:
            raise SystemExit(f"no se pudo leer la configuración de Auth: {estado}")
        if not config.get("hook_custom_access_token_enabled"):
            raise SystemExit("el hook no está activo: Authentication → Hooks → Custom Access "
                             "Token → Postgres → public.custom_access_token_hook")
        if not config.get("mfa_totp_enroll_enabled") or not config.get("mfa_totp_verify_enabled"):
            raise SystemExit("MFA TOTP no está habilitado en Authentication → Multi-Factor")
        print("  ✓ API, migraciones, hook y TOTP")

    # --- datos ------------------------------------------------------------

    def sembrar(self):
        print("datos de prueba")
        roles = {"operador_a": ("tenant", self.tenants[0], "operador"),
                 "operador_b": ("tenant", self.tenants[1], "operador"),
                 "soporte": ("accusys", None, "soporte")}
        for clave in roles:
            email = f"dc-prueba+{self.sufijo}-{clave.replace('_', '-')}@example.com"
            clave_acceso = secrets.token_urlsafe(18) + "Aa1!"
            estado, u = self.admin("POST", "/admin/users", {
                "email": email, "password": clave_acceso, "email_confirm": True})
            if estado not in (200, 201):
                raise SystemExit(f"no se pudo crear {email}: {estado} {u}")
            self.usuarios[clave] = {"id": u["id"], "email": email, "clave": clave_acceso}
        print(f"  ✓ {len(self.usuarios)} usuarios confirmados")

        sentencias = []
        for t in self.tenants:
            sentencias += [
                f"insert into tenants (id, nombre) values ('{t}', 'Prueba {t}')",
                f"insert into tenant_productos (tenant_id, producto, mantenimiento_hasta)"
                f" values ('{t}', 'mep', '2099-12-31')",
                f"insert into agentes (id, tenant_id, host, token_hash, enrolado) values"
                f" ('ag-{t}', '{t}', 'srv-{t}', md5('{t}') || md5('{t}x'), now())",
                f"insert into instalaciones (agente_id, nombre, producto, version, actualizado)"
                f" values ('ag-{t}', 'mep', 'mep', '4.6.0', now())",
                f"insert into ordenes (id, tenant_id, agente_id, instalacion, tipo, producto,"
                f" release, estado, pedida_por, creada) values ('ord-{t}', '{t}', 'ag-{t}',"
                f" 'mep', 'preflight', 'mep', '4.7.0', 'terminada', 'integracion', now())",
                f"insert into codigos_enrolamiento (hash, tenant_id, creado, expira) values"
                f" (md5('{t}') || md5('{t}c'), '{t}', now(), now() + interval '1 hour')",
            ]
        for clave, (tipo, tenant, rol) in roles.items():
            uid = self.usuarios[clave]["id"]
            sentencias.append(
                f"insert into usuarios_tenant (usuario_id, tenant_id, rol) values"
                f" ('{uid}', '{tenant}', '{rol}')" if tipo == "tenant" else
                f"insert into usuarios_accusys (usuario_id, rol) values ('{uid}', '{rol}')")
        self.sql("begin;\n" + ";\n".join(sentencias) + ";\ncommit;")
        print(f"  ✓ clientes {', '.join(self.tenants)} con agente, instalación y orden")

    # --- identidad --------------------------------------------------------

    def ingresar(self, clave):
        """Contraseña → TOTP enrolado y verificado. Devuelve (token aal1, token aal2)."""
        u = self.usuarios[clave]
        estado, s = self.auth("POST", "/token?grant_type=password",
                              {"email": u["email"], "password": u["clave"]})
        if estado != 200:
            raise SystemExit(f"{clave}: login {estado} {s}")
        aal1 = s["access_token"]
        estado, f = self.auth("POST", "/factors", {"factor_type": "totp",
                                                   "friendly_name": f"prueba-{self.sufijo}"},
                              token=aal1)
        if estado != 200:
            raise SystemExit(f"{clave}: enrolar TOTP {estado} {f}")
        # Supabase exige que el desafío y la verificación vengan de la misma IP;
        # detrás de un proxy con varias salidas eso no siempre pasa: otro desafío
        for _intento in range(6):
            estado, d = self.auth("POST", f"/factors/{f['id']}/challenge", {}, token=aal1)
            if estado != 200:
                raise SystemExit(f"{clave}: desafío {estado} {d}")
            estado, v = self.auth("POST", f"/factors/{f['id']}/verify",
                                  {"challenge_id": d["id"], "code": totp(f["totp"]["secret"])},
                                  token=aal1)
            if estado == 200:
                return aal1, v["access_token"]
            if not (isinstance(v, dict) and v.get("error_code") == "mfa_ip_address_mismatch"):
                break
        raise SystemExit(f"{clave}: verificar TOTP {estado} {v}")

    def identidad(self):
        print("identidad (JWKS real + hook)")
        validador = ValidadorJWT.desde_entorno()
        self.comprobar(validador is not None and validador.secreto is None,
                       "ValidadorJWT.desde_entorno() usa el JWKS del proyecto")
        self.claims, self.claims_aal1 = {}, {}
        esperado = {"operador_a": (self.tenants[0], "operador"),
                    "operador_b": (self.tenants[1], "operador"),
                    "soporte": (None, "soporte")}
        for clave, (tenant, rol) in esperado.items():
            aal1, aal2 = self.ingresar(clave)
            claims = validador.validar(aal2)
            meta = claims.get("app_metadata", {})
            self.comprobar(claims["aal"] == "aal2" and claims["sub"] == self.usuarios[clave]["id"],
                           f"{clave}: token aal2 válido (firma, emisor, audiencia)")
            self.comprobar(meta.get("tenant_id") == tenant and meta.get("dc_rol") == rol,
                           f"{clave}: el hook agregó tenant_id={tenant} y dc_rol={rol}")
            try:
                validador.validar(aal1)
                self.comprobar(False, f"{clave}: el token sin TOTP no pasa el validador")
            except Prohibido:
                self.comprobar(True, f"{clave}: el token sin TOTP no pasa el validador")
            # la firma ya la verificó el validador (falla antes que el aal)
            self.claims_aal1[clave] = validador._jwt.decode(
                aal1, options={"verify_signature": False})
            self.claims[clave] = claims

    # --- RLS con los claims reales -----------------------------------------

    def rls(self):
        print("RLS con los claims reales")
        a, b = self.tenants
        for tabla, columna, de_a in (("tenants", "id", a), ("tenant_productos", "tenant_id", a),
                                     ("agentes", "id", f"ag-{a}"),
                                     ("instalaciones", "agente_id", f"ag-{a}"),
                                     ("ordenes", "id", f"ord-{a}")):
            filas = self.como(self.claims["operador_a"], f"select {columna} as v from {tabla}")
            self.comprobar([f["v"] for f in filas] == [de_a],
                           f"operador de {a}, sin filtro, en {tabla}: solo lo suyo")
        filas = self.como(self.claims["operador_b"], "select id as v from ordenes")
        self.comprobar([f["v"] for f in filas] == [f"ord-{b}"],
                       f"operador de {b}: tampoco ve lo ajeno")
        filas = self.como(self.claims["soporte"], "select id as v from tenants")
        self.comprobar({a, b} <= {f["v"] for f in filas}, "Soporte de Accusys ve los dos")

        for clave in ("operador_a", "soporte"):
            filas = self.como(self.claims_aal1[clave],
                              "select id from tenants union all select id from ordenes")
            self.comprobar(filas == [], f"{clave} sin segundo factor (aal1): no ve nada")

        self.comprobar(self.rechaza(self.claims["soporte"], "select token_hash from agentes"),
                       "agentes.token_hash no se lee")
        self.comprobar(self.rechaza(self.claims["soporte"],
                                    "select hash from codigos_enrolamiento"),
                       "codigos_enrolamiento.hash no se lee")
        hosts = self.como(self.claims["soporte"], "select host from agentes")
        self.comprobar({f"srv-{a}", f"srv-{b}"} <= {f["host"] for f in hosts},
                       "el resto de agentes sí se lee")
        uid = self.usuarios["operador_a"]["id"]
        self.comprobar(self.rechaza(self.claims["operador_a"], (
            "insert into ordenes (id, tenant_id, agente_id, instalacion, tipo, producto, release,"
            f" pedida_por, pedida_por_id, creada) values ('ord-{b}-x', '{b}', 'ag-{b}', 'mep',"
            f" 'desplegar', 'mep', '4.7.0', 'x', '{uid}', now()) returning id")),
            f"el operador de {a} no ordena en {b}")

    # --- limpieza ---------------------------------------------------------

    def limpiar(self):
        print("limpieza")
        lista = ", ".join(f"'{t}'" for t in self.tenants)
        ids = ", ".join(f"'{u['id']}'" for u in self.usuarios.values()) or "null"
        try:
            self.sql(
                "begin;\n"
                f"delete from eventos_orden where orden_id in"
                f" (select id from ordenes where tenant_id in ({lista}));\n"
                f"delete from ordenes where tenant_id in ({lista});\n"
                f"delete from instalaciones where agente_id in"
                f" (select id from agentes where tenant_id in ({lista}));\n"
                f"delete from agentes where tenant_id in ({lista});\n"
                f"delete from codigos_enrolamiento where tenant_id in ({lista});\n"
                f"delete from tenant_productos where tenant_id in ({lista});\n"
                f"delete from usuarios_tenant where usuario_id in ({ids})"
                f" or tenant_id in ({lista});\n"
                f"delete from usuarios_accusys where usuario_id in ({ids});\n"
                f"delete from tenants where id in ({lista});\n"
                "commit;")
            print("  ✓ datos borrados")
        except ErrorDeployCenter as e:
            print(f"  ✗ no se pudieron borrar los datos ({e}); quedan con prefijo zz-prueba-")
        for clave, u in self.usuarios.items():
            estado, _ = self.admin("DELETE", f"/admin/users/{u['id']}")
            print(f"  {'✓' if estado == 200 else '✗'} usuario {clave} borrado ({estado})")

    def correr(self):
        self.precondiciones()
        try:
            self.sembrar()
            self.identidad()
            self.rls()
        finally:
            self.limpiar()
        if self.fallas:
            print(f"\n{len(self.fallas)} chequeo(s) fallaron")
            return 1
        print("\ntodo en orden")
        return 0


def main():
    faltan = [v for v in VARIABLES if not os.environ.get(v)]
    if faltan:
        print(f"salteado: faltan {', '.join(faltan)}")
        return 0
    return Integracion().correr()


if __name__ == "__main__":
    sys.exit(main())
