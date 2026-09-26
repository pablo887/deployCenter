"""Alta de cuentas en el proveedor de identidad.

Para dar de alta a una persona desde la web no hace falta conocer su id: el hub
crea la cuenta en el proveedor, que devuelve el id, y con ese id se carga el
rol. La persona recibe un mail de invitación para elegir su contraseña y
enrolar el segundo factor; nadie más conoce su contraseña.

- **Supabase** (`ProveedorSupabase`): `POST /auth/v1/invite` de la API de
  administración de Auth, con la clave secreta del proyecto. Esa clave saltea
  la RLS y administra cuentas, así que vive solo en el hub, en el perímetro de
  Accusys: nunca en la web ni en el agente.
- **Local** (`ProveedorLocal`): para desarrollo con `DC_JWT_SECRET`. Genera el
  id y no manda mails; el token de esa persona se emite con `dc-hub token-dev`.

Para mandar invitaciones a direcciones de clientes, el proyecto de Supabase
necesita un SMTP propio (Authentication → Emails → SMTP Settings): el servidor
de mails que trae por defecto solo entrega a los miembros del proyecto.
"""

import json
import os
import ssl
import urllib.error
import urllib.request
import uuid

from ..errores import ErrorDeployCenter


class ErrorProveedor(ErrorDeployCenter):
    """El proveedor rechazó el alta o no respondió."""

    def __init__(self, mensaje, estado=None):
        super().__init__(mensaje)
        self.estado = estado

    @property
    def es_del_pedido(self):
        """4xx (salvo 429): el problema es el dato, no el proveedor."""
        return self.estado is not None and 400 <= self.estado < 500 and self.estado != 429


class EmailYaRegistrado(ErrorProveedor):
    pass


def transporte_urllib():
    """(metodo, url, cuerpo, cabeceras, timeout) → (estado, cuerpo JSON o texto)."""
    opener = urllib.request.build_opener(
        urllib.request.HTTPSHandler(context=ssl.create_default_context()))

    def enviar(metodo, url, cuerpo, cabeceras, timeout):
        datos = json.dumps(cuerpo).encode("utf-8") if cuerpo is not None else None
        pedido = urllib.request.Request(url, data=datos, method=metodo, headers=cabeceras)
        try:
            with opener.open(pedido, timeout=timeout) as r:
                return r.status, _json_o_texto(r.read())
        except urllib.error.HTTPError as e:
            return e.code, _json_o_texto(e.read())
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            raise ErrorProveedor(f"no se pudo hablar con el proveedor de identidad: {e}") from e

    return enviar


def _json_o_texto(datos):
    try:
        return json.loads(datos) if datos else None
    except ValueError:
        return datos[:300].decode("utf-8", "replace")


def desde_entorno(entorno=None, transporte=None):
    """Supabase si están SUPABASE_URL y SUPABASE_SECRET_KEY; local si solo está
    DC_JWT_SECRET (desarrollo); None si no hay ninguno: el alta desde la web
    queda cerrada y se sigue pudiendo asignar un rol a un id conocido."""
    env = os.environ if entorno is None else entorno
    url = env.get("SUPABASE_URL")
    clave = env.get("SUPABASE_SECRET_KEY") or env.get("SUPABASE_SERVICE_ROLE_KEY")
    if url and clave:
        return ProveedorSupabase(url, clave, redirigir_a=env.get("DC_URL_WEB"),
                                 transporte=transporte)
    if env.get("DC_JWT_SECRET"):
        return ProveedorLocal()
    return None


class ProveedorSupabase:
    nombre = "supabase"

    def __init__(self, url, clave_secreta, redirigir_a=None, transporte=None, timeout=30):
        if not url or not clave_secreta:
            raise ErrorProveedor("falta SUPABASE_URL o SUPABASE_SECRET_KEY")
        self.base = f"{url.rstrip('/')}/auth/v1"
        self.clave = clave_secreta
        self.redirigir_a = redirigir_a
        self.transporte = transporte or transporte_urllib()
        self.timeout = timeout

    def _pedir(self, metodo, ruta, cuerpo=None):
        return self.transporte(
            metodo, f"{self.base}{ruta}", cuerpo,
            {"apikey": self.clave, "Authorization": f"Bearer {self.clave}",
             "Content-Type": "application/json", "Accept": "application/json"},
            self.timeout)

    def invitar(self, email, nombre=None):
        """Crea la cuenta y manda la invitación. Devuelve el id (uuid)."""
        cuerpo = {"email": email, "data": {"nombre": nombre} if nombre else {}}
        if self.redirigir_a:
            cuerpo["redirect_to"] = self.redirigir_a
        estado, datos = self._pedir("POST", "/invite", cuerpo)
        if estado in (409, 422) and _dice_registrado(datos):
            raise EmailYaRegistrado(email)
        if not 200 <= estado < 300:
            raise ErrorProveedor(f"el proveedor de identidad respondió {estado}: "
                                 f"{_mensaje(datos)}", estado)
        usuario_id = (datos or {}).get("id") if isinstance(datos, dict) else None
        try:
            return str(uuid.UUID(str(usuario_id)))
        except ValueError:
            raise ErrorProveedor("el proveedor de identidad no devolvió el id del usuario") \
                from None

    def borrar(self, usuario_id):
        estado, datos = self._pedir("DELETE", f"/admin/users/{usuario_id}")
        if not 200 <= estado < 300 and estado != 404:
            raise ErrorProveedor(f"el proveedor de identidad respondió {estado}: "
                                 f"{_mensaje(datos)}")


class ProveedorLocal:
    """Desarrollo: el id se genera acá y no se manda ningún mail."""

    nombre = "local"

    def __init__(self):
        self.emails = {}

    def invitar(self, email, nombre=None):
        if email in self.emails:
            raise EmailYaRegistrado(email)
        usuario_id = str(uuid.uuid4())
        self.emails[email] = usuario_id
        return usuario_id

    def borrar(self, usuario_id):
        self.emails = {e: u for e, u in self.emails.items() if u != usuario_id}


def _mensaje(datos):
    if isinstance(datos, dict):
        return datos.get("msg") or datos.get("message") or datos.get("error_description") \
            or datos.get("error") or datos
    return datos


def _dice_registrado(datos):
    codigos = ("email_exists", "user_already_exists")
    if isinstance(datos, dict) and datos.get("error_code") in codigos:
        return True
    return "already" in str(_mensaje(datos)).lower()
