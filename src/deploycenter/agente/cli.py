"""CLI del agente. Se usa en el servidor del cliente.

    dc-agent estado     --instalacion /opt/accusys/mep
    dc-agent preflight  --instalacion /opt/accusys/mep --paquete /tmp/mep-4.7.0
    dc-agent desplegar  --instalacion /opt/accusys/mep --paquete /tmp/mep-4.7.0
    dc-agent rollback   --instalacion /opt/accusys/mep
    dc-agent historial  --instalacion /opt/accusys/mep
    dc-agent logs       --instalacion /opt/accusys/mep --servicio api

En Fase 1 el paquete lo baja una persona y el despliegue lo dispara este CLI.
En Fase 2 el mismo motor lo va a disparar una orden que llega del hub: el
agente no cambia, cambia quién aprieta el botón.
"""

import argparse
import json
import signal
import sys

from ..errores import ErrorArchivo, ErrorDeployCenter, ErrorHerramienta
from ..salida import (
    ERROR_USO,
    FALLA_VALIDACION,
    OK,
    paleta,
    preparar_salida,
    simbolos,
    usar_color,
)
from . import despliegue as desp_mod
from .docker import Docker
from .instalacion import ErrorBloqueo, Instalacion, Paquete
from .preflight import Preflight


def _contexto(args):
    instalacion = Instalacion(args.instalacion)
    docker = Docker(raiz_permitida=args.raiz_permitida)
    return instalacion, docker


def cancelador_interactivo():
    """Ctrl-C durante la cuenta regresiva cancela el rollback en vez de matar
    el proceso: frenar la vuelta atrás a mano es una decisión legítima."""
    estado = {"cancelado": False}

    def manejar(_signum, _frame):
        estado["cancelado"] = True

    try:
        anterior = signal.signal(signal.SIGINT, manejar)
    except (ValueError, OSError):  # pragma: no cover - fuera del hilo principal
        return (lambda: False), (lambda: None)

    def restaurar():
        try:
            signal.signal(signal.SIGINT, anterior)
        except (ValueError, OSError):  # pragma: no cover
            pass

    return (lambda: estado["cancelado"]), restaurar


# --------------------------------------------------------------------------- #
# comandos
# --------------------------------------------------------------------------- #

def cmd_estado(args):
    verde, rojo, amarillo, gris, fin = paleta(usar_color(args))
    s = simbolos()
    instalacion, docker = _contexto(args)

    if not instalacion.inicializada():
        print(f"{amarillo}{s['aviso']}{fin} {instalacion.directorio}: "
              f"sin estado registrado (nunca se desplegó desde acá)")
        return OK

    e = instalacion.estado()
    marca = {"ok": (verde, s["ok"]), "degradado": (rojo, s["mal"]),
             "desplegando": (amarillo, s["aviso"])}.get(
                 e.get("estado"), (gris, s["punto"]))
    color, simbolo = marca

    print(f"{color}{simbolo}{fin} {e.get('producto', '?')} {e.get('version', '?')}")
    print(f"  estado       {color}{e.get('estado', '?')}{fin}")
    print(f"  directorio   {instalacion.directorio}")
    print(f"  actualizado  {e.get('actualizado', '?')}")
    if instalacion.hay_punto_retorno():
        previo = instalacion.manifiesto_de_retorno() or {}
        print(f"  retorno a    {previo.get('release', 'compose anterior')}")
    else:
        print(f"  retorno      {gris}sin punto de retorno{fin}")

    if args.servicios:
        try:
            for servicio in docker.ps(instalacion.directorio):
                salud = f" ({servicio['salud']})" if servicio["salud"] else ""
                print(f"    {servicio['servicio']:<12} {servicio['estado']}{salud}")
        except ErrorHerramienta as err:
            print(f"    {gris}no se pudo consultar docker: {err}{fin}")
    return OK


def _imprimir_preflight(informe, args):
    verde, rojo, amarillo, gris, fin = paleta(usar_color(args))
    s = simbolos()
    for c in informe.comprobaciones:
        if c.ok:
            print(f"  {verde}{s['ok']}{fin} {c.nombre:<14} {gris}{c.detalle}{fin}")
        elif c.bloqueante:
            print(f"  {rojo}{s['mal']}{fin} {c.nombre:<14} {c.detalle}")
        else:
            print(f"  {amarillo}{s['aviso']}{fin} {c.nombre:<14} {c.detalle}")


def cmd_preflight(args):
    verde, rojo, _amarillo, gris, fin = paleta(usar_color(args))
    s = simbolos()
    instalacion, docker = _contexto(args)
    paquete = Paquete(args.paquete).exigir()

    informe, _preparado = Preflight(
        docker, instalacion, paquete, clave_publica=args.clave_publica,
        espacio_minimo_gb=args.espacio_minimo,
    ).correr(descargar=not args.sin_descarga)

    _imprimir_preflight(informe, args)
    print()
    if informe.ok:
        print(f"{verde}{s['ok']}{fin} listo para desplegar")
        if informe.avisos:
            print(f"  {gris}{len(informe.avisos)} aviso(s) que no bloquean{fin}")
        return OK
    print(f"{rojo}{s['mal']}{fin} {len(informe.bloqueos)} bloqueo(s): "
          f"el despliegue no puede arrancar")
    return FALLA_VALIDACION


def cmd_desplegar(args):
    verde, rojo, amarillo, gris, fin = paleta(usar_color(args))
    s = simbolos()
    instalacion, docker = _contexto(args)
    paquete = Paquete(args.paquete).exigir()
    manifiesto = paquete.manifiesto()

    print(f"{gris}{manifiesto['producto']} {instalacion.version_instalada() or '(nueva)'} "
          f"→ {manifiesto['release']}{fin}")
    print()

    def avisar(evento, datos):
        if evento == "despliegue_falla":
            print()
            print(f"{rojo}{s['mal']}{fin} la verificación falló: {datos.get('motivo')}")
            if manifiesto.get("rollback_seguro"):
                print(f"  {amarillo}vuelta atrás automática en "
                      f"{args.espera_cancelacion}s — Ctrl-C para cancelarla{fin}")
        elif evento == "requiere_intervencion":
            print(f"  {rojo}no se revierte solo: {datos.get('razon')}{fin}")

    cancelado, restaurar = cancelador_interactivo()
    try:
        resultado = desp_mod.Despliegue(
            docker, instalacion, paquete,
            espera_cancelacion_s=args.espera_cancelacion,
            clave_publica=args.clave_publica,
            avisar=avisar,
        ).ejecutar(cancelado=cancelado, descargar=not args.sin_descarga)
    finally:
        restaurar()

    if resultado.preflight is not None and not resultado.preflight.ok:
        _imprimir_preflight(resultado.preflight, args)
        print()

    print()
    if resultado.estado == desp_mod.OK:
        print(f"{verde}{s['ok']}{fin} {resultado.detalle}")
        return OK
    if resultado.estado == desp_mod.REVERTIDO:
        print(f"{amarillo}{s['aviso']}{fin} {resultado.detalle}")
        return FALLA_VALIDACION
    print(f"{rojo}{s['mal']}{fin} {resultado.detalle}")
    if resultado.estado == desp_mod.DEGRADADO:
        print(f"  {gris}la instalación quedó marcada como degradada; "
              f"mirá 'dc-agent logs'{fin}")
    return FALLA_VALIDACION


def cmd_rollback(args):
    verde, rojo, amarillo, _gris, fin = paleta(usar_color(args))
    s = simbolos()
    instalacion, docker = _contexto(args)

    if not instalacion.hay_punto_retorno():
        print(f"{rojo}{s['mal']}{fin} no hay punto de retorno en {instalacion.directorio}")
        return FALLA_VALIDACION

    previo = instalacion.manifiesto_de_retorno() or {}
    destino = previo.get("release", "el compose anterior")
    if not args.si:
        print(f"{amarillo}{s['aviso']}{fin} se va a volver a {destino}. "
              f"Repetí con --si para confirmarlo.")
        return OK

    resultado = desp_mod.rollback_manual(docker, instalacion)
    if resultado.estado == desp_mod.REVERTIDO:
        print(f"{verde}{s['ok']}{fin} {resultado.detalle}")
        return OK
    print(f"{rojo}{s['mal']}{fin} {resultado.detalle}")
    return FALLA_VALIDACION


def cmd_historial(args):
    _verde, _rojo, _amarillo, gris, fin = paleta(usar_color(args))
    instalacion, _docker = _contexto(args)
    eventos = instalacion.historial(limite=args.limite)
    if not eventos:
        print(f"{gris}sin historial en {instalacion.directorio}{fin}")
        return OK
    if args.json:
        for e in eventos:
            print(json.dumps(e, ensure_ascii=False))
        return OK
    for e in eventos:
        desde, hacia = e.get("desde"), e.get("hacia")
        salto = f" {desde or '?'} → {hacia}" if hacia else ""
        print(f"{e['ts']}  {e['operacion']:<16} {e['resultado']:<12}{salto}")
        if e.get("detalle"):
            print(f"  {gris}{e['detalle']}{fin}")
    return OK


def cmd_logs(args):
    instalacion, docker = _contexto(args)
    print(docker.logs(instalacion.directorio, servicio=args.servicio, lineas=args.lineas))
    return OK


# --------------------------------------------------------------------------- #

def construir_parser():
    p = argparse.ArgumentParser(
        prog="dc-agent",
        description="Agente de deployCenter: despliega, verifica y vuelve atrás.")
    p.add_argument("--sin-color", action="store_true")
    p.add_argument("--raiz-permitida",
                   help="directorio raíz fuera del cual el agente no opera")
    sub = p.add_subparsers(dest="comando", required=True)

    def con_instalacion(sp):
        sp.add_argument("--instalacion", required=True,
                        help="directorio del stack del producto")
        return sp

    e = con_instalacion(sub.add_parser("estado", help="qué versión corre y cómo está"))
    e.add_argument("--servicios", action="store_true", help="consultar docker compose ps")
    e.set_defaults(func=cmd_estado)

    pf = con_instalacion(sub.add_parser("preflight", help="comprobaciones previas, sin impacto"))
    pf.add_argument("--paquete", required=True, help="directorio con manifiesto y plantilla")
    pf.add_argument("--clave-publica", help="clave para verificar la firma del manifiesto")
    pf.add_argument("--espacio-minimo", type=float, default=5.0, help="en GB")
    pf.add_argument("--sin-descarga", action="store_true",
                    help="no hacer el pull; solo las comprobaciones baratas")
    pf.set_defaults(func=cmd_preflight)

    d = con_instalacion(sub.add_parser("desplegar", help="preflight, despliegue y verificación"))
    d.add_argument("--paquete", required=True)
    d.add_argument("--clave-publica")
    d.add_argument("--espera-cancelacion", type=int, default=desp_mod.ESPERA_CANCELACION_S,
                   help="segundos para frenar la vuelta atrás con Ctrl-C")
    d.add_argument("--sin-descarga", action="store_true")
    d.set_defaults(func=cmd_desplegar)

    r = con_instalacion(sub.add_parser("rollback", help="volver a la versión anterior"))
    r.add_argument("--si", action="store_true", help="confirmar")
    r.set_defaults(func=cmd_rollback)

    h = con_instalacion(sub.add_parser("historial", help="qué pasó en esta instalación"))
    h.add_argument("--limite", type=int)
    h.add_argument("--json", action="store_true")
    h.set_defaults(func=cmd_historial)

    lg = con_instalacion(sub.add_parser("logs", help="logs del stack"))
    lg.add_argument("--servicio")
    lg.add_argument("--lineas", type=int, default=200)
    lg.set_defaults(func=cmd_logs)

    return p


def main(argv=None):
    preparar_salida()
    args = construir_parser().parse_args(argv)
    _verde, rojo, _amarillo, _gris, fin = paleta(usar_color(args))
    s = simbolos()
    try:
        return args.func(args)
    except ErrorBloqueo as e:
        print(f"{rojo}{s['mal']}{fin} {e}", file=sys.stderr)
        return FALLA_VALIDACION
    except (ErrorArchivo, ErrorHerramienta) as e:
        print(f"{rojo}{s['mal']}{fin} {e}", file=sys.stderr)
        return ERROR_USO
    except ErrorDeployCenter as e:  # pragma: no cover - red de seguridad
        print(f"{rojo}{s['mal']}{fin} {e}", file=sys.stderr)
        return ERROR_USO


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
