"""UI local del agente.

Corre en el servidor del cliente y da la misma operación que el CLI, pero con
pantalla: qué versión corre, qué paquetes hay, preflight, despliegue con la
cuenta regresiva visible, vuelta atrás e historial.

En Fase 1 es la única interfaz gráfica que existe. Cuando aparezca el hub sigue
siendo útil como respaldo: si el hub no está disponible, o si un cliente prefiere
no depender de él, desde acá se despliega igual.

**Dónde escucha.** Por defecto en 127.0.0.1. Esto no tiene login: quien llega a
la página puede desplegar. Exponerla en la red de un banco sería abrir un panel
de despliegue sin autenticación, así que el acceso normal es por consola del
servidor o por túnel SSH. Si alguien la abre igual con `--host 0.0.0.0`, el token
pasa a ser obligatorio y se avisa por pantalla.

**Contra qué protege el token.** Contra que otra cosa del mismo host —o una
página que el operador tenga abierta en otra pestaña— dispare un despliegue por
CSRF. Va en un header, no en una cookie, así que un formulario cruzado no lo
puede mandar. Se valida también el header Host, que es lo que corta el DNS
rebinding.
"""

import secrets
import time
from pathlib import Path

from ... import manifiesto as mf
from ...errores import ErrorDeployCenter
from .. import despliegue as desp_mod
from ..docker import Docker
from ..instalacion import ErrorBloqueo, Paquete, descubrir_instalaciones  # noqa: F401
from ..preflight import Preflight
from .operaciones import Registro

PUERTO = 9000
HOST = "127.0.0.1"
HOSTS_LOCALES = {"127.0.0.1", "localhost", "::1", "[::1]"}

CABECERA_TOKEN = "X-DC-Token"


def es_local(host):
    return host in HOSTS_LOCALES


# --------------------------------------------------------------------------- #
# descubrimiento
# --------------------------------------------------------------------------- #

def paquetes_disponibles(dir_paquetes, producto=None):
    """Los paquetes que alguien dejó en el directorio. En Fase 2 los trae el hub."""
    dir_paquetes = Path(dir_paquetes)
    if not dir_paquetes.is_dir():
        return []
    salida = []
    for d in sorted(dir_paquetes.iterdir()):
        if not d.is_dir():
            continue
        paquete = Paquete(d)
        if not paquete.existe():
            continue
        try:
            m = paquete.manifiesto()
        except ErrorDeployCenter:
            continue
        if producto and m.get("producto") != producto:
            continue
        salida.append({
            "nombre": d.name,
            "producto": m.get("producto"),
            "release": m.get("release"),
            "publicado": m.get("publicado"),
            "canal": m.get("canal"),
            "autoservicio": mf.es_autoservicio(m),
            "firmado": paquete.tiene_firma(),
        })
    return salida


def _resumen(instalacion, docker=None, con_servicios=False):
    estado = instalacion.estado()
    datos = {
        "nombre": instalacion.directorio.name,
        "directorio": str(instalacion.directorio),
        "producto": estado.get("producto"),
        "version": estado.get("version"),
        "estado": estado.get("estado", "sin desplegar"),
        "actualizado": estado.get("actualizado"),
        "bloqueada": instalacion.bloqueada(),
        "hay_retorno": instalacion.hay_punto_retorno(),
        "retorno_a": (instalacion.manifiesto_de_retorno() or {}).get("release"),
        "servicios": [],
    }
    if con_servicios and docker is not None:
        try:
            datos["servicios"] = docker.ps(instalacion.directorio)
        except ErrorDeployCenter as e:
            datos["servicios_error"] = str(e)
    return datos


# --------------------------------------------------------------------------- #
# app
# --------------------------------------------------------------------------- #

def crear_app(raiz, dir_paquetes, token=None, host=HOST,
              espera_cancelacion_s=desp_mod.ESPERA_CANCELACION_S,
              docker=None, registro=None, clave_publica=None):
    try:
        from flask import Flask, abort, jsonify, render_template, request
    except ImportError as e:  # pragma: no cover
        raise ErrorDeployCenter(
            "la UI local necesita Flask: instalá el paquete con el extra 'ui'") from e

    raiz = Path(raiz)
    dir_paquetes = Path(dir_paquetes)
    token = token or secrets.token_urlsafe(24)
    docker = docker or Docker(raiz_permitida=raiz)
    registro = registro if registro is not None else Registro()

    app = Flask(__name__, template_folder="plantillas")
    app.config.update(DC_TOKEN=token, DC_RAIZ=raiz, DC_PAQUETES=dir_paquetes,
                      DC_HOST=host, DC_ESPERA=espera_cancelacion_s)

    hosts_validos = HOSTS_LOCALES | {host}

    # -- guardas ------------------------------------------------------------ #

    @app.before_request
    def _guardas():
        # DNS rebinding: el navegador puede resolver un dominio ajeno a 127.0.0.1
        # y hablarle a esta app. Si el Host no es uno de los nuestros, no es para
        # nosotros.
        nombre = (request.host or "").rsplit(":", 1)[0]
        if nombre not in hosts_validos:
            abort(403, description="Host no permitido")
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            if request.headers.get(CABECERA_TOKEN) != token:
                abort(403, description="token inválido o ausente")
        return None

    def _instalacion(nombre):
        for inst in descubrir_instalaciones(raiz):
            if inst.directorio.name == nombre:
                return inst
        abort(404, description=f"no hay una instalación llamada {nombre!r}")

    def _paquete(nombre):
        destino = (dir_paquetes / nombre).resolve()
        try:
            destino.relative_to(dir_paquetes.resolve())
        except ValueError:
            abort(400, description="nombre de paquete inválido")
        paquete = Paquete(destino)
        if not paquete.existe():
            abort(404, description=f"no hay un paquete llamado {nombre!r}")
        return paquete

    # -- páginas ------------------------------------------------------------ #

    @app.get("/")
    def index():
        return render_template("index.html", token=token,
                               expuesta=not es_local(host), host=host)

    # -- API ---------------------------------------------------------------- #

    @app.get("/api/salud")
    def salud():
        return jsonify({"ok": True, "raiz": str(raiz), "paquetes": str(dir_paquetes)})

    @app.get("/api/instalaciones")
    def listar():
        instalaciones = []
        for inst in descubrir_instalaciones(raiz):
            datos = _resumen(inst, docker, con_servicios=False)
            activa = registro.activa_de(datos["nombre"])
            datos["operacion"] = activa.a_dict() if activa else None
            instalaciones.append(datos)
        return jsonify({"instalaciones": instalaciones})

    @app.get("/api/instalaciones/<nombre>")
    def detalle(nombre):
        inst = _instalacion(nombre)
        datos = _resumen(inst, docker, con_servicios=True)
        datos["historial"] = list(reversed(inst.historial(limite=30)))
        datos["paquetes"] = paquetes_disponibles(dir_paquetes, producto=datos["producto"])
        activa = registro.activa_de(nombre)
        datos["operacion"] = activa.a_dict() if activa else None
        datos["operaciones"] = [o.a_dict() for o in registro.ultimas_de(nombre)]
        return jsonify(datos)

    @app.get("/api/instalaciones/<nombre>/logs")
    def logs(nombre):
        inst = _instalacion(nombre)
        servicio = request.args.get("servicio") or None
        lineas = min(int(request.args.get("lineas", 200)), 2000)
        try:
            return jsonify({"logs": docker.logs(inst.directorio, servicio=servicio,
                                                lineas=lineas)})
        except ErrorDeployCenter as e:
            return jsonify({"logs": "", "error": str(e)}), 200

    def _lanzar(nombre, tipo, paquete_nombre, funcion):
        if registro.activa_de(nombre) is not None:
            return jsonify({"error": "ya hay una operación en curso"}), 409
        op = registro.crear(tipo, nombre, paquete_nombre)
        registro.ejecutar(op, funcion)
        return jsonify(op.a_dict()), 202

    @app.post("/api/instalaciones/<nombre>/preflight")
    def preflight(nombre):
        inst = _instalacion(nombre)
        paquete = _paquete((request.get_json(silent=True) or {}).get("paquete", ""))

        def correr(op):
            op.fase = "preflight"
            informe, _preparado = Preflight(
                docker, inst, paquete, clave_publica=clave_publica).correr()
            return informe.resumen()

        return _lanzar(nombre, "preflight", paquete.directorio.name, correr)

    @app.post("/api/instalaciones/<nombre>/desplegar")
    def desplegar(nombre):
        inst = _instalacion(nombre)
        paquete = _paquete((request.get_json(silent=True) or {}).get("paquete", ""))

        def correr(op):
            def avisar(evento, datos):
                op.registrar(evento, datos)
                if evento == "despliegue_iniciado":
                    op.fase = "desplegando"
                elif evento == "despliegue_falla":
                    # acá arranca lo que el operador ve como cuenta regresiva
                    op.fase = "cuenta_regresiva"
                    op.cancelable_hasta = time.time() + espera_cancelacion_s
                elif evento in ("rollback_ok", "rollback_falla"):
                    op.fase = "revirtiendo"
                    op.cancelable_hasta = None

            op.fase = "preflight"
            resultado = desp_mod.Despliegue(
                docker, inst, paquete,
                espera_cancelacion_s=espera_cancelacion_s,
                clave_publica=clave_publica, avisar=avisar,
            ).ejecutar(cancelado=op.cancelado)
            return resultado.resumen()

        return _lanzar(nombre, "despliegue", paquete.directorio.name, correr)

    @app.post("/api/instalaciones/<nombre>/rollback")
    def rollback(nombre):
        inst = _instalacion(nombre)
        if not inst.hay_punto_retorno():
            return jsonify({"error": "no hay punto de retorno"}), 400

        def correr(op):
            op.fase = "revirtiendo"
            return desp_mod.rollback_manual(docker, inst).resumen()

        return _lanzar(nombre, "rollback", None, correr)

    @app.get("/api/operaciones/<id_operacion>")
    def operacion(id_operacion):
        op = registro.obtener(id_operacion)
        if op is None:
            abort(404, description="operación desconocida")
        return jsonify(op.a_dict())

    @app.post("/api/operaciones/<id_operacion>/cancelar")
    def cancelar(id_operacion):
        op = registro.obtener(id_operacion)
        if op is None:
            abort(404, description="operación desconocida")
        if not op.cancelable:
            return jsonify({"error": "esta operación ya no se puede cancelar"}), 409
        op.cancelar()
        return jsonify(op.a_dict())

    @app.errorhandler(403)
    @app.errorhandler(404)
    @app.errorhandler(400)
    def _error(e):
        return jsonify({"error": getattr(e, "description", str(e))}), e.code

    @app.errorhandler(ErrorBloqueo)
    def _bloqueo(e):
        return jsonify({"error": str(e)}), 409

    return app


def servidor_disponible():
    """waitress si está, si no el servidor de desarrollo de Flask.

    El de Flask avisa en cada arranque que no es para producción, y tiene razón:
    esto corre en el servidor de un cliente y ese warning va a aparecer en una
    auditoría. waitress es Python puro, anda igual en Linux y en Windows, y
    resuelve la objeción sin sumar nada pesado.
    """
    try:
        import waitress  # noqa: F401
        return "waitress"
    except ImportError:
        return "flask"


def servir(raiz, dir_paquetes, host=HOST, puerto=PUERTO, token=None,
           espera_cancelacion_s=desp_mod.ESPERA_CANCELACION_S, clave_publica=None):
    token = token or secrets.token_urlsafe(24)
    app = crear_app(raiz, dir_paquetes, token=token, host=host,
                    espera_cancelacion_s=espera_cancelacion_s,
                    clave_publica=clave_publica)

    if servidor_disponible() == "waitress":
        from waitress import serve
        serve(app, host=host, port=puerto, threads=8, ident="deployCenter")
    else:
        app.run(host=host, port=puerto, threaded=True, debug=False, use_reloader=False)
