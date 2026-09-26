"""Invitaciones: el hub crea la persona en Supabase Auth y devuelve el link para
que elija su contraseña.

Usa la API de administración de Supabase con la secret key, que vive solo en el
hub (nunca viaja al navegador). El link no depende del mail: Supabase sin SMTP
propio manda muy pocos y solo a los miembros del proyecto, así que quien invita
recibe el link y lo hace llegar por el canal que quiera. Cuando haya SMTP, el
mismo link puede salir por mail.

El link lleva a Supabase, que lo valida y redirige al hub con la sesión en el
fragmento de la URL (#access_token=…&type=invite). La web la toma, pide la
contraseña y después el segundo factor, como en cualquier primer ingreso.
"""

import json
import os
import re
import urllib.error
import urllib.request

from ..errores import ErrorDeployCenter

EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class ErrorIdentidad(ErrorDeployCenter):
    """El proveedor de identidad rechazó el pedido o no respondió."""


def transporte_urllib():
    opener = urllib.request.build_opener()

    def enviar(metodo, url, cuerpo, cabeceras, timeout):
        datos = json.dumps(cuerpo).encode("utf-8") if cuerpo is not None else None
        pedido = urllib.request.Request(url, data=datos, method=metodo, headers=cabeceras)
        try:
            with opener.open(pedido, timeout=timeout) as r:
                texto = r.read()
                return r.status, json.loads(texto) if texto else None
        except urllib.error.HTTPError as e:
            texto = e.read()
            try:
                return e.code, json.loads(texto) if texto else None
            except ValueError:
                return e.code, {"msg": texto[:300].decode("utf-8", "replace")}
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            raise ErrorIdentidad(f"no se pudo hablar con el proveedor de identidad: {e}") from e

    return enviar


class AdminSupabase:
    def __init__(self, url, clave_secreta, transporte=None, timeout=20):
        if not url or not clave_secreta:
            raise ErrorDeployCenter("faltan SUPABASE_URL o SUPABASE_SECRET_KEY")
        self.url = url.rstrip("/") + "/auth/v1"
        self.clave = clave_secreta
        self.transporte = transporte or transporte_urllib()
        self.timeout = timeout

    @classmethod
    def desde_entorno(cls, entorno=None, transporte=None):
        """None si el hub no tiene la secret key: las invitaciones quedan apagadas."""
        entorno = os.environ if entorno is None else entorno
        if not entorno.get("SUPABASE_URL") or not entorno.get("SUPABASE_SECRET_KEY"):
            return None
        return cls(entorno["SUPABASE_URL"], entorno["SUPABASE_SECRET_KEY"], transporte)

    def _pedir(self, metodo, ruta, cuerpo=None):
        estado, datos = self.transporte(
            metodo, self.url + ruta, cuerpo,
            {"apikey": self.clave, "Authorization": f"Bearer {self.clave}",
             "Content-Type": "application/json"}, self.timeout)
        return estado, datos or {}

    def _link(self, tipo, email, redirigir_a, datos=None):
        cuerpo = {"type": tipo, "email": email, "redirect_to": redirigir_a}
        if datos:
            cuerpo["data"] = datos
        return self._pedir("POST", "/admin/generate_link", cuerpo)

    @staticmethod
    def _usuario_y_link(datos):
        # según la versión, el usuario viene en la raíz o en "user"
        usuario = datos.get("user") or datos
        link = datos.get("action_link") or (datos.get("properties") or {}).get("action_link")
        if not usuario.get("id") or not link:
            raise ErrorIdentidad("el proveedor de identidad no devolvió el usuario y el link")
        return usuario["id"], link

    def invitar(self, email, redirigir_a, nombre=None):
        """Crea la persona si no existe. Devuelve (id, link, nueva).

        Si ya tenía usuario, el link es de recuperación: sirve igual para
        entrar y definir una contraseña nueva."""
        email = _email_valido(email)
        estado, datos = self._link("invite", email, redirigir_a,
                                   {"nombre": nombre} if nombre else None)
        if 200 <= estado < 300:
            return (*self._usuario_y_link(datos), True)
        if _ya_existe(estado, datos):
            return (*self.link_de_acceso(email, redirigir_a), False)
        raise ErrorIdentidad(f"no se pudo invitar a {email}: {_mensaje(estado, datos)}")

    def link_de_acceso(self, email, redirigir_a):
        """Link para que alguien que ya existe entre y elija una contraseña nueva."""
        estado, datos = self._link("recovery", _email_valido(email), redirigir_a)
        if 200 <= estado < 300:
            return self._usuario_y_link(datos)
        raise ErrorIdentidad(f"no se pudo generar el link para {email}: "
                             f"{_mensaje(estado, datos)}")


def _email_valido(email):
    email = (email or "").strip().lower()
    if not EMAIL.match(email) or len(email) > 320:
        raise ErrorDeployCenter(f"{email!r} no es un email válido")
    return email


def _ya_existe(estado, datos):
    codigo = str(datos.get("error_code") or datos.get("code") or "")
    texto = str(datos.get("msg") or datos.get("message") or "").lower()
    return estado in (400, 409, 422) and (
        codigo in ("email_exists", "user_already_exists") or "already" in texto)


def _mensaje(estado, datos):
    texto = datos.get("msg") or datos.get("message") or datos.get("error") or ""
    return f"{estado} {texto}".strip()
