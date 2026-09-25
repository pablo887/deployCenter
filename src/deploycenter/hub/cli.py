"""CLI del hub. Sirve la API y permite operar el hub sin la web.

    dc-hub servir      --puerto 8000
    dc-hub tenant      andino "Banco Andino"
    dc-hub adquirir    andino mep --hasta 2026-12-31
    dc-hub codigo      andino --host srv-dock-01
    dc-hub ordenar     --agente ag-… --instalacion mep --tipo desplegar --release 4.7.0
    dc-hub parque
    dc-hub orden       ord-…
    dc-hub revocar     ag-…

La base sale de `--db` o de `DC_HUB_DB` (por defecto un SQLite local) y el
catálogo de `--catalogo` o `DC_HUB_CATALOGO` (la raíz del repo, la que tiene
`productos/`).
"""

import argparse
import json
import os
import sys
from pathlib import Path

from ..errores import ErrorDeployCenter
from ..salida import ERROR_USO, FALLA_VALIDACION, OK, paleta, preparar_salida, simbolos, usar_color
from . import servicio as srv

DB_POR_DEFECTO = "sqlite:///hub.db"


def _catalogo_por_defecto():
    aqui = Path.cwd()
    for candidata in [aqui, *aqui.parents]:
        if (candidata / "productos").is_dir():
            return candidata
    return aqui


def _hub(args):
    return srv.Hub.desde_url(args.db, args.catalogo)


def _imprimir(datos):
    print(json.dumps(datos, indent=2, ensure_ascii=False))


def cmd_servir(args):
    import uvicorn

    from .api import crear_app

    token = os.environ.get("DC_HUB_TOKEN_ADMIN")
    app = crear_app(_hub(args), token_admin=token)
    verde, _rojo, amarillo, gris, fin = paleta(usar_color(args))
    s = simbolos()
    print(f"{verde}{s['ok']}{fin} hub en http://{args.host}:{args.puerto}/")
    print(f"  {gris}base {args.db} · catálogo {args.catalogo}{fin}")
    if not token:
        print(f"{amarillo}{s['aviso']}{fin} sin DC_HUB_TOKEN_ADMIN: la API de la web queda "
              f"cerrada; el canal de los agentes funciona igual")
    uvicorn.run(app, host=args.host, port=args.puerto, log_level="info")
    return OK


def cmd_tenant(args):
    _imprimir(_hub(args).crear_tenant(args.id, args.nombre))
    return OK


def cmd_adquirir(args):
    _hub(args).adquirir(args.tenant, args.producto, args.hasta,
                        autoservicio=not args.sin_autoservicio, canal=args.canal)
    print(f"{args.tenant}: {args.producto} con mantenimiento hasta {args.hasta}")
    return OK


def cmd_codigo(args):
    datos = _hub(args).emitir_codigo(args.tenant, host=args.host)
    _imprimir(datos)
    print(f"\ndc-agent enrolar --hub <url del hub> --codigo {datos['codigo']}",
          file=sys.stderr)
    return OK


def cmd_ordenar(args):
    _imprimir(_hub(args).crear_orden(args.agente, args.instalacion, args.tipo,
                                     release=args.release, pedida_por=args.por))
    return OK


def cmd_parque(args):
    _imprimir(_hub(args).parque(tenant_id=args.tenant))
    return OK


def cmd_orden(args):
    _imprimir(_hub(args).orden(args.id))
    return OK


def cmd_cancelar_rollback(args):
    _hub(args).pedir_cancelacion_rollback(args.id)
    print(f"{args.id}: se pidió frenar la vuelta atrás")
    return OK


def cmd_revocar(args):
    _hub(args).revocar_agente(args.id)
    print(f"{args.id}: revocado")
    return OK


def construir_parser():
    p = argparse.ArgumentParser(prog="dc-hub", description="Hub de deployCenter.")
    p.add_argument("--sin-color", action="store_true")
    p.add_argument("--db", default=os.environ.get("DC_HUB_DB", DB_POR_DEFECTO))
    p.add_argument("--catalogo",
                   default=os.environ.get("DC_HUB_CATALOGO") or str(_catalogo_por_defecto()))
    sub = p.add_subparsers(dest="comando", required=True)

    sv = sub.add_parser("servir", help="levanta la API")
    sv.add_argument("--host", default="127.0.0.1")
    sv.add_argument("--puerto", type=int, default=8000)
    sv.set_defaults(func=cmd_servir)

    t = sub.add_parser("tenant", help="da de alta un cliente")
    t.add_argument("id")
    t.add_argument("nombre")
    t.set_defaults(func=cmd_tenant)

    a = sub.add_parser("adquirir", help="carga un producto adquirido y su mantenimiento")
    a.add_argument("tenant")
    a.add_argument("producto")
    a.add_argument("--hasta", required=True, help="fin del mantenimiento, AAAA-MM-DD")
    a.add_argument("--canal", default="estable", choices=["estable", "anticipado"])
    a.add_argument("--sin-autoservicio", action="store_true")
    a.set_defaults(func=cmd_adquirir)

    c = sub.add_parser("codigo", help="emite un código de enrolamiento de un solo uso")
    c.add_argument("tenant")
    c.add_argument("--host")
    c.set_defaults(func=cmd_codigo)

    o = sub.add_parser("ordenar", help="encola una orden para un agente")
    o.add_argument("--agente", required=True)
    o.add_argument("--instalacion", required=True)
    o.add_argument("--tipo", required=True, choices=["preflight", "desplegar", "rollback"])
    o.add_argument("--release")
    o.add_argument("--por", default="dc-hub (consola)")
    o.set_defaults(func=cmd_ordenar)

    pq = sub.add_parser("parque", help="agentes e instalaciones")
    pq.add_argument("--tenant")
    pq.set_defaults(func=cmd_parque)

    ve = sub.add_parser("orden", help="estado y eventos de una orden")
    ve.add_argument("id")
    ve.set_defaults(func=cmd_orden)

    cr = sub.add_parser("cancelar-rollback", help="frena la vuelta atrás de un despliegue")
    cr.add_argument("id")
    cr.set_defaults(func=cmd_cancelar_rollback)

    rv = sub.add_parser("revocar", help="revoca el token de un agente")
    rv.add_argument("id")
    rv.set_defaults(func=cmd_revocar)
    return p


def main(argv=None):
    preparar_salida()
    args = construir_parser().parse_args(argv)
    _verde, rojo, _amarillo, _gris, fin = paleta(usar_color(args))
    s = simbolos()
    try:
        return args.func(args)
    except srv.ErrorHub as e:
        print(f"{rojo}{s['mal']}{fin} {e.mensaje}", file=sys.stderr)
        return FALLA_VALIDACION
    except ErrorDeployCenter as e:
        print(f"{rojo}{s['mal']}{fin} {e}", file=sys.stderr)
        return ERROR_USO


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
