"""Modo conectado: el agente sale a buscar trabajo al hub.

    enrolar ─▶ credenciales propias (token, nunca la clave de la base)
    ciclo:  vaciar el buzón → latido → long-poll de órdenes → ejecutar → resultado

Todo el tráfico lo inicia el agente, por 443 y a un solo dominio. Respeta el
proxy del sistema (`HTTPS_PROXY`) y acepta una CA propia para los clientes cuyo
proxy inspecciona TLS: son las dos causas más probables de que el primer
arranque falle en un banco.

Tres decisiones que conviene no perder:

**El hub propone, el agente verifica.** La orden trae el paquete, pero el agente
no confía en el hub: exige la firma del manifiesto con una clave pública que se
configura en el servidor —nunca la manda el hub— y comprueba que la plantilla
recibida sea la que declara el manifiesto firmado. Un hub comprometido puede
encolar órdenes, no hacer desplegar algo que Accusys no firmó.

**Nada se pierde si se corta la red.** Eventos y resultados pasan por un buzón en
disco antes de salir. Si el hub no responde, el despliegue sigue —ya tiene la
orden y las imágenes—, termina, verifica o revierte, y el buzón se vacía cuando
vuelve la conexión.

**Un hub que no responde no demora el rollback.** Los envíos durante el
despliegue usan timeouts cortos y, después de una falla, esperan antes de
reintentar. La cuenta regresiva corre con el reloj del agente, no con el de la
red.
"""

import base64
import json
import os
import re
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from .. import __version__
from .. import compose as compose_mod
from ..errores import ErrorDeployCenter
from . import despliegue as desp_mod
from .docker import Docker
from .instalacion import ErrorBloqueo, Instalacion, Paquete, ahora, descubrir_instalaciones
from .preflight import Preflight

TIMEOUT_NORMAL_S = 15
TIMEOUT_CORTO_S = 5
ESPERA_POLL_S = 25
INTERVALO_LATIDO_S = 30
INTERVALO_CONTROL_S = 3
PAUSA_TRAS_FALLA_S = 30
PAQUETES_CONSERVADOS = 10
BACKOFF_MAXIMO_S = 60

# el hub acepta hasta 4000; lo que sobra suele ser ruido de docker y se ve en los eventos
MAX_DETALLE = 3000

NOMBRE_INSTALACION = re.compile(r"^[a-z0-9][a-z0-9._-]{0,99}$")
ID_ORDEN = re.compile(r"^ord-[a-z0-9]{1,36}$")
HOSTS_LOCALES = {"localhost", "127.0.0.1", "::1"}


class ErrorConexion(ErrorDeployCenter):
    """El hub no respondió o respondió 5xx. Se reintenta."""


class ErrorRespuesta(ErrorDeployCenter):
    """El hub entendió y dijo que no. Reintentar lo mismo no sirve."""

    def __init__(self, status, mensaje):
        super().__init__(f"el hub respondió {status}: {mensaje}")
        self.status = status
        self.mensaje = mensaje


class CredencialesInvalidas(ErrorRespuesta):
    """Token revocado o inexistente. El agente deja de pedir órdenes."""


# --------------------------------------------------------------------------- #
# transporte
# --------------------------------------------------------------------------- #

def transporte_urllib(ca=None):
    """HTTP con la librería estándar: nada más que instalar en el servidor del
    cliente. `build_opener` incluye el proxy de las variables de entorno."""
    contexto = ssl.create_default_context(cafile=str(ca) if ca else None)
    opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=contexto))

    def enviar(metodo, url, cuerpo, cabeceras, timeout):
        datos = json.dumps(cuerpo).encode("utf-8") if cuerpo is not None else None
        pedido = urllib.request.Request(url, data=datos, method=metodo, headers=cabeceras)
        try:
            with opener.open(pedido, timeout=timeout) as r:
                return r.status, _json_o_nada(r.read())
        except urllib.error.HTTPError as e:
            return e.code, _json_o_nada(e.read())
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            raise ErrorConexion(f"no se pudo hablar con el hub: {e}") from e

    return enviar


def _json_o_nada(texto):
    if not texto:
        return None
    try:
        return json.loads(texto)
    except ValueError:
        return {"detalle": texto[:300].decode("utf-8", "replace")}


class ClienteHub:
    def __init__(self, url, token=None, transporte=None, ca=None, permitir_http=False):
        partes = urllib.parse.urlsplit(url)
        if partes.scheme not in ("https", "http"):
            raise ErrorDeployCenter(f"URL del hub inválida: {url!r}")
        if partes.scheme == "http" and not permitir_http \
                and partes.hostname not in HOSTS_LOCALES:
            raise ErrorDeployCenter(
                "el hub tiene que ser https; http solo se admite contra localhost "
                "o con --permitir-http en un entorno de prueba")
        self.url = url.rstrip("/")
        self.token = token
        self.transporte = transporte or transporte_urllib(ca)

    def _pedir(self, metodo, ruta, cuerpo=None, timeout=TIMEOUT_NORMAL_S):
        cabeceras = {"Accept": "application/json",
                     "User-Agent": f"deploycenter-agente/{__version__}"}
        if cuerpo is not None:
            cabeceras["Content-Type"] = "application/json"
        if self.token:
            cabeceras["Authorization"] = f"Bearer {self.token}"
        status, datos = self.transporte(metodo, self.url + ruta, cuerpo, cabeceras, timeout)
        detalle = (datos or {}).get("detalle", "") if isinstance(datos, dict) else ""
        if status == 401:
            raise CredencialesInvalidas(status, detalle or "no autorizado")
        if status >= 500:
            raise ErrorConexion(f"el hub respondió {status}: {detalle}")
        if status >= 400:
            raise ErrorRespuesta(status, detalle or "pedido rechazado")
        return status, datos

    def enrolar(self, codigo, host, version=__version__):
        _, datos = self._pedir("POST", "/api/agente/v1/enrolar",
                               {"codigo": codigo, "host": host, "version": version})
        return datos

    def latido(self, instalaciones, version=__version__):
        _, datos = self._pedir("POST", "/api/agente/v1/latido",
                               {"version": version, "instalaciones": instalaciones})
        return datos

    def siguiente_orden(self, espera=ESPERA_POLL_S):
        status, datos = self._pedir("GET", f"/api/agente/v1/ordenes/siguiente?espera={espera:g}",
                                    timeout=espera + TIMEOUT_NORMAL_S)
        return None if status == 204 else datos

    def enviar_eventos(self, orden_id, eventos, timeout=TIMEOUT_CORTO_S):
        _, datos = self._pedir("POST", f"/api/agente/v1/ordenes/{orden_id}/eventos",
                               {"eventos": eventos}, timeout=timeout)
        return datos or {}

    def control(self, orden_id, timeout=TIMEOUT_CORTO_S):
        _, datos = self._pedir("GET", f"/api/agente/v1/ordenes/{orden_id}/control",
                               timeout=timeout)
        return datos or {}

    def enviar_resultado(self, orden_id, resultado, detalle=None, resumen=None,
                         timeout=TIMEOUT_CORTO_S):
        _, datos = self._pedir("POST", f"/api/agente/v1/ordenes/{orden_id}/resultado",
                               {"resultado": resultado, "detalle": detalle,
                                "resumen": resumen}, timeout=timeout)
        return datos or {}


# --------------------------------------------------------------------------- #
# credenciales
# --------------------------------------------------------------------------- #

class Credenciales:
    """Lo que devuelve el enrolamiento. Solo lo lee el usuario del agente."""

    def __init__(self, ruta):
        self.ruta = Path(ruta)

    def existe(self):
        return self.ruta.is_file()

    def guardar(self, hub, agente_id, token, tenant):
        self.ruta.parent.mkdir(parents=True, exist_ok=True)
        contenido = json.dumps({"hub": hub, "agente_id": agente_id, "token": token,
                                "tenant": tenant, "enrolado": ahora()}, indent=2)
        temporal = self.ruta.with_suffix(".tmp")
        fd = os.open(str(temporal), os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(contenido + "\n")
        os.replace(temporal, self.ruta)
        return self.ruta

    def cargar(self):
        if not self.existe():
            raise ErrorDeployCenter(
                f"no hay credenciales en {self.ruta}: primero 'dc-agent enrolar'")
        return json.loads(self.ruta.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# buzón de salida
# --------------------------------------------------------------------------- #

class Buzon:
    """Eventos y resultados esperando salir hacia el hub, en orden y en disco.

    Si el hub rechaza un mensaje con un 4xx (por ejemplo, una orden que ya no
    existe), el mensaje pasa a `rechazados/` para que no trabe a los demás.
    """

    def __init__(self, directorio, reloj=time.monotonic):
        self.dir = Path(directorio)
        self.dir_rechazados = self.dir / "rechazados"
        self.reloj = reloj
        self._secuencia = 0
        self._no_antes_de = 0.0
        self.cancelaciones = set()

    def depositar(self, tipo, orden_id, cuerpo):
        self.dir.mkdir(parents=True, exist_ok=True)
        self._secuencia += 1
        nombre = f"{time.time_ns():020d}-{self._secuencia:06d}-{tipo}.json"
        temporal = self.dir / (nombre + ".tmp")
        temporal.write_text(json.dumps({"tipo": tipo, "orden": orden_id, "cuerpo": cuerpo},
                                       ensure_ascii=False), encoding="utf-8")
        os.replace(temporal, self.dir / nombre)

    def pendientes(self):
        if not self.dir.is_dir():
            return []
        return sorted(p for p in self.dir.glob("*.json") if p.is_file())

    def vaciar(self, cliente, forzar=False):
        """Manda lo que pueda. Devuelve cuántos mensajes salieron."""
        if not forzar and self.reloj() < self._no_antes_de:
            return 0
        enviados = 0
        for ruta in self.pendientes():
            try:
                mensaje = json.loads(ruta.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                self._apartar(ruta)
                continue
            try:
                self._enviar(cliente, mensaje)
            except CredencialesInvalidas:
                raise
            except ErrorRespuesta:
                self._apartar(ruta)
                continue
            except ErrorConexion:
                self._no_antes_de = self.reloj() + PAUSA_TRAS_FALLA_S
                break
            ruta.unlink(missing_ok=True)
            enviados += 1
        return enviados

    def _apartar(self, ruta):
        self.dir_rechazados.mkdir(parents=True, exist_ok=True)
        os.replace(ruta, self.dir_rechazados / ruta.name)

    def _enviar(self, cliente, mensaje):
        orden, cuerpo = mensaje["orden"], mensaje["cuerpo"]
        if mensaje["tipo"] == "eventos":
            respuesta = cliente.enviar_eventos(orden, cuerpo["eventos"])
            if respuesta.get("cancelar_rollback"):
                self.cancelaciones.add(orden)
        elif mensaje["tipo"] == "resultado":
            cliente.enviar_resultado(orden, cuerpo["resultado"], cuerpo.get("detalle"),
                                     cuerpo.get("resumen"))
        else:  # pragma: no cover - el buzón solo lo llena este módulo
            raise ErrorRespuesta(400, f"tipo de mensaje desconocido: {mensaje['tipo']}")


# --------------------------------------------------------------------------- #
# el conector
# --------------------------------------------------------------------------- #

class OrdenRechazada(ErrorDeployCenter):
    """El agente no ejecuta la orden: algo no cierra con lo que le mandaron."""


class Conector:
    def __init__(self, cliente, raiz, dir_trabajo, docker=None, clave_publica=None,
                 exigir_firma=True, espera_cancelacion_s=desp_mod.ESPERA_CANCELACION_S,
                 espera_poll_s=ESPERA_POLL_S, intervalo_latido_s=INTERVALO_LATIDO_S,
                 intervalo_control_s=INTERVALO_CONTROL_S, dormir=time.sleep,
                 reloj=time.monotonic, cliente_http=None, avisar_local=None):
        self.cliente = cliente
        self.raiz = Path(raiz)
        self.dir_trabajo = Path(dir_trabajo)
        self.docker = docker or Docker(raiz_permitida=self.raiz)
        self.clave_publica = clave_publica
        self.exigir_firma = exigir_firma
        self.espera_cancelacion_s = espera_cancelacion_s
        self.espera_poll_s = espera_poll_s
        self.intervalo_latido_s = intervalo_latido_s
        self.intervalo_control_s = intervalo_control_s
        self.dormir = dormir
        self.reloj = reloj
        self.cliente_http = cliente_http
        self.avisar_local = avisar_local or (lambda _texto: None)
        self.buzon = Buzon(self.dir_trabajo / "buzon", reloj=reloj)
        self._ultimo_latido = None

    # -- inventario ---------------------------------------------------------- #

    def inventario(self):
        salida = []
        for inst in descubrir_instalaciones(self.raiz):
            e = inst.estado()
            retorno = None
            if inst.hay_punto_retorno():
                retorno = (inst.manifiesto_de_retorno() or {}).get("release")
            salida.append({
                "nombre": inst.directorio.name, "producto": e.get("producto"),
                "version": e.get("version"), "estado": e.get("estado"),
                "bloqueada": inst.bloqueada(), "punto_retorno": retorno,
            })
        return salida

    def latido(self):
        self.cliente.latido(self.inventario())
        self._ultimo_latido = self.reloj()

    def _toca_latido(self):
        return (self._ultimo_latido is None
                or self.reloj() - self._ultimo_latido >= self.intervalo_latido_s)

    # -- ciclo ---------------------------------------------------------------- #

    def ciclo(self, espera=None):
        """Una vuelta: buzón, latido, una orden. Devuelve la orden ejecutada o None."""
        self.buzon.vaciar(self.cliente, forzar=True)
        if self._toca_latido():
            self.latido()
        orden = self.cliente.siguiente_orden(
            self.espera_poll_s if espera is None else espera)
        if orden is None:
            return None
        self.ejecutar(orden)
        self.latido()
        self.buzon.vaciar(self.cliente, forzar=True)
        return orden

    def correr(self, parar=lambda: False):
        """Loop principal. Con la red caída reintenta con espera creciente; con el
        token revocado se detiene, porque insistir no lo va a arreglar."""
        pausa = 1
        while not parar():
            try:
                self.ciclo()
                pausa = 1
            except CredencialesInvalidas:
                raise
            except ErrorConexion as e:
                self.avisar_local(f"sin conexión con el hub: {e}; reintento en {pausa}s")
                self.dormir(pausa)
                pausa = min(pausa * 2, BACKOFF_MAXIMO_S)
            except ErrorRespuesta as e:
                self.avisar_local(str(e))
                self.dormir(pausa)
                pausa = min(pausa * 2, BACKOFF_MAXIMO_S)

    # -- ejecución de una orden ----------------------------------------------- #

    def ejecutar(self, orden):
        orden_id = orden.get("id", "")
        if not ID_ORDEN.match(orden_id):
            # sin un id válido no hay a quién responderle
            self.avisar_local(f"orden con id inválido: {orden_id!r}")
            return {"resultado": "rechazada", "detalle": "id de orden inválido"}

        self._evento(orden_id, "orden_recibida", tipo=orden.get("tipo"),
                     instalacion=orden.get("instalacion"), release=orden.get("release"))
        try:
            resultado = self._ejecutar(orden)
        except OrdenRechazada as e:
            resultado = {"resultado": "rechazada", "detalle": str(e)}
        except ErrorBloqueo as e:
            resultado = {"resultado": "bloqueada", "detalle": str(e)}
        except ErrorDeployCenter as e:
            resultado = {"resultado": "error", "detalle": str(e)}
        except Exception as e:  # noqa: BLE001 - lo que sea, el hub se tiene que enterar
            resultado = {"resultado": "error", "detalle": f"{type(e).__name__}: {e}"}

        detalle = resultado.get("detalle") or ""
        if len(detalle) > MAX_DETALLE:
            resultado["detalle"] = detalle[:MAX_DETALLE] + " […]"
        self.avisar_local(f"{orden_id}: {resultado['resultado']} — {resultado.get('detalle')}")
        self.buzon.depositar("resultado", orden_id, resultado)
        self.buzon.vaciar(self.cliente, forzar=True)
        self._limpiar_paquetes()
        return resultado

    def _limpiar_paquetes(self):
        """Conserva los últimos paquetes recibidos, para diagnóstico; el resto se borra."""
        import shutil

        carpeta = self.dir_trabajo / "paquetes"
        if not carpeta.is_dir():
            return
        viejos = sorted((d for d in carpeta.iterdir() if d.is_dir()),
                        key=lambda d: d.stat().st_mtime)[:-PAQUETES_CONSERVADOS]
        for d in viejos:
            shutil.rmtree(d, ignore_errors=True)

    def _ejecutar(self, orden):
        tipo = orden.get("tipo")
        instalacion = self._instalacion(orden.get("instalacion"))

        if tipo == "rollback":
            r = desp_mod.rollback_manual(self.docker, instalacion,
                                         cliente_http=self.cliente_http,
                                         dormir=self.dormir, reloj=self.reloj)
            return {"resultado": r.estado, "detalle": r.detalle, "resumen": r.resumen()}

        if tipo not in ("preflight", "desplegar"):
            raise OrdenRechazada(f"tipo de orden fuera del catálogo: {tipo!r}")

        paquete = self._materializar(orden, instalacion)

        if tipo == "preflight":
            instalacion.bloquear(dueno=f"preflight:{orden['id']}")
            try:
                informe, _ = Preflight(self.docker, instalacion, paquete,
                                       clave_publica=self.clave_publica).correr()
            finally:
                instalacion.desbloquear()
            return {"resultado": "ok" if informe.ok else "abortado",
                    "detalle": "listo para desplegar" if informe.ok else
                    "; ".join(c.detalle for c in informe.bloqueos),
                    "resumen": {"preflight": informe.resumen()}}

        r = desp_mod.Despliegue(
            self.docker, instalacion, paquete,
            espera_cancelacion_s=self.espera_cancelacion_s,
            clave_publica=self.clave_publica,
            avisar=lambda evento, datos: self._evento(orden["id"], evento, **datos),
            dormir=self.dormir, reloj=self.reloj, cliente_http=self.cliente_http,
        ).ejecutar(cancelado=self._cancelador(orden["id"]))
        return {"resultado": r.estado, "detalle": r.detalle, "resumen": r.resumen()}

    def _instalacion(self, nombre):
        if not isinstance(nombre, str) or not NOMBRE_INSTALACION.match(nombre):
            raise OrdenRechazada(f"nombre de instalación inválido: {nombre!r}")
        directorio = (self.raiz / nombre).resolve()
        try:
            directorio.relative_to(self.raiz.resolve())
        except ValueError:
            raise OrdenRechazada(f"{nombre!r} queda fuera de {self.raiz}") from None
        if not directorio.is_dir():
            raise OrdenRechazada(
                f"no hay una instalación {nombre!r} en este host; la instalación inicial "
                f"la hace Accusys")
        return Instalacion(directorio)

    def _materializar(self, orden, instalacion):
        """Baja el paquete de la orden a disco, después de comprobar que es lo que
        dice ser. Lo que no cierra se rechaza antes de tocar la instalación."""
        paquete = orden.get("paquete") or {}
        manifiesto = paquete.get("manifiesto")
        plantilla = paquete.get("plantilla")
        if not isinstance(manifiesto, dict) or not isinstance(plantilla, str):
            raise OrdenRechazada("la orden no trae un paquete con manifiesto y plantilla")

        if manifiesto.get("producto") != orden.get("producto") \
                or manifiesto.get("release") != orden.get("release"):
            raise OrdenRechazada(
                f"el paquete es {manifiesto.get('producto')} {manifiesto.get('release')} "
                f"y la orden pide {orden.get('producto')} {orden.get('release')}")
        producto_local = instalacion.producto()
        if producto_local and producto_local != manifiesto["producto"]:
            raise OrdenRechazada(
                f"{instalacion.directorio.name} corre {producto_local}, no "
                f"{manifiesto['producto']}")

        huella = manifiesto.get("plantilla_sha256")
        if self.exigir_firma:
            if not self.clave_publica:
                raise OrdenRechazada(
                    "el agente no tiene clave pública configurada: sin verificar la firma "
                    "no despliega órdenes del hub")
            if not paquete.get("firma"):
                raise OrdenRechazada("el paquete no trae la firma del manifiesto")
            if not huella:
                raise OrdenRechazada(
                    "el manifiesto no declara plantilla_sha256: no hay cómo comprobar la "
                    "plantilla recibida")
        if huella and huella != compose_mod.huella(plantilla):
            raise OrdenRechazada(
                "la plantilla recibida no coincide con la huella del manifiesto firmado")

        destino = self.dir_trabajo / "paquetes" / orden["id"]
        destino.mkdir(parents=True, exist_ok=True)
        (destino / "manifiesto.json").write_text(
            json.dumps(manifiesto, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        (destino / "compose.plantilla.yaml").write_text(plantilla, encoding="utf-8")
        if paquete.get("firma"):
            (destino / "manifiesto.json.sig").write_bytes(base64.b64decode(paquete["firma"]))
        if paquete.get("changelog"):
            (destino / "changelog.md").write_text(paquete["changelog"], encoding="utf-8")
        return Paquete(destino).exigir()

    # -- eventos y cancelación ------------------------------------------------ #

    def _evento(self, orden_id, evento, **datos):
        self.buzon.depositar("eventos", orden_id, {"eventos": [
            {"ts": ahora(), "evento": evento, "datos": _serializable(datos)}]})
        try:
            self.buzon.vaciar(self.cliente)
        except CredencialesInvalidas:
            pass  # el despliegue sigue; el loop principal se entera después

    def _cancelador(self, orden_id):
        """La web frena el rollback marcando la orden en el hub. El agente se entera
        por la respuesta a sus eventos o preguntando, sin demorar la cuenta."""
        estado = {"proxima": 0.0}

        def cancelado():
            if orden_id in self.buzon.cancelaciones:
                return True
            if self.reloj() < estado["proxima"]:
                return False
            estado["proxima"] = self.reloj() + self.intervalo_control_s
            try:
                if self.cliente.control(orden_id).get("cancelar_rollback"):
                    self.buzon.cancelaciones.add(orden_id)
            except (ErrorConexion, ErrorRespuesta):
                estado["proxima"] = self.reloj() + PAUSA_TRAS_FALLA_S
            return orden_id in self.buzon.cancelaciones

        return cancelado


def _serializable(datos):
    try:
        json.dumps(datos)
        return datos
    except (TypeError, ValueError):
        return {k: str(v) for k, v in datos.items()}


def host_local():
    return socket.getfqdn() or socket.gethostname()
