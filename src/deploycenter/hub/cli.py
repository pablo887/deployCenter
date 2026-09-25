"""CLI del hub. Sirve la API y permite operar el hub sin la web.

    dc-hub migrar
    dc-hub migrar      --via-api      (Supabase por HTTPS, sin el puerto 5432)
    dc-hub servir      --puerto 8000      (API y web en el mismo origen)
    dc-hub usuario     <uuid> --rol soporte
    dc-hub usuario     <uuid> --rol aprobador --tenant andino
    dc-hub token-dev   <uuid>
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

La consola opera como el sistema, sin pasar por la RLS: es para Accusys, dentro
de su perímetro. La web opera siempre con la identidad de la persona.
"""

import argparse
import json
import os
import sys
from pathlib import Path

from ..errores import ErrorDeployCenter
from ..salida import ERROR_USO, FALLA_VALIDACION, OK, paleta, preparar_salida, simbolos, usar_color
from . import migraciones
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
    from .identidad import ValidadorJWT

    validador = ValidadorJWT.desde_entorno()
    hub = _hub(args)
    verde, _rojo, amarillo, gris, fin = paleta(usar_color(args))
    s = simbolos()
    if not hub.es_sqlite and (faltan := migraciones.pendientes(hub.engine, _dir_migraciones(args))):
        print(f"{amarillo}{s['aviso']}{fin} hay {len(faltan)} migración(es) sin aplicar: "
              f"corré 'dc-hub migrar'")
    web = None if args.sin_web else _dir_web(args)
    app = crear_app(hub, validador=validador, web=web,
                    config_web=config_web(validador) if web else None)
    print(f"{verde}{s['ok']}{fin} hub en http://{args.host}:{args.puerto}/")
    if web:
        print(f"  {gris}web desde {web}{fin}")
    from sqlalchemy.engine import make_url

    base = make_url(args.db).render_as_string(hide_password=True)
    print(f"  {gris}base {base} · catálogo {args.catalogo}{fin}")
    if validador is None:
        print(f"{amarillo}{s['aviso']}{fin} sin identidad configurada (SUPABASE_URL o "
              f"DC_JWT_*): la API de la web queda cerrada; el canal de los agentes funciona igual")
    elif validador.secreto:
        print(f"  {gris}identidad: secreto HS256 compartido{fin}")
    if hub.es_sqlite:
        print(f"{amarillo}{s['aviso']}{fin} SQLite: sin RLS; los permisos los aplica solo "
              f"el hub. Para producción, Postgres con las migraciones")
    uvicorn.run(app, host=args.host, port=args.puerto, log_level="info")
    return OK


def _dir_web(args):
    web = Path(args.web) if args.web else Path(args.catalogo) / "web"
    if not (web / "index.html").is_file():
        if args.web:
            raise ErrorDeployCenter(f"{web} no tiene index.html")
        return None
    return web


def config_web(validador, entorno=None):
    """Lo que la web necesita saber para ingresar. Todo es público: la
    publishable key de Supabase está hecha para viajar al navegador."""
    entorno = os.environ if entorno is None else entorno
    if validador is None:
        return {"identidad": None}
    if validador.secreto:
        return {"identidad": "token"}
    url, clave = entorno.get("SUPABASE_URL"), entorno.get("SUPABASE_PUBLISHABLE_KEY")
    if url and clave:
        return {"identidad": "supabase", "supabase_url": url.rstrip("/"),
                "publishable_key": clave}
    return {"identidad": None,
            "motivo": "falta SUPABASE_URL o SUPABASE_PUBLISHABLE_KEY en el hub"}


def _dir_migraciones(args):
    return args.migraciones or migraciones.directorio_por_defecto(args.catalogo)


def cmd_migrar(args):
    if args.via_api:
        api = migraciones.ApiSupabase.desde_entorno()
        aplicadas = migraciones.aplicar(api, _dir_migraciones(args))
        print("\n".join(aplicadas) if aplicadas else "todo al día")
        return OK
    hub = srv.Hub(srv.conectar(args.db), args.catalogo)
    if hub.es_sqlite:
        hub.crear_tablas()
        print("SQLite: tablas creadas desde el modelo (sin RLS)")
        return OK
    aplicadas = migraciones.aplicar(hub.engine, _dir_migraciones(args))
    print("\n".join(aplicadas) if aplicadas else "todo al día")
    return OK


def cmd_usuario(args):
    if args.quitar:
        _hub(args).quitar_usuario(args.id)
        print(f"{args.id}: dado de baja")
        return OK
    _imprimir(_hub(args).asignar_usuario(args.id, args.rol, tenant_id=args.tenant,
                                         nombre=args.nombre, email=args.email))
    return OK


def cmd_token_dev(args):
    from .identidad import token_de_desarrollo

    secreto = os.environ.get("DC_JWT_SECRET")
    if not secreto:
        print("hace falta DC_JWT_SECRET: el token se firma con el mismo secreto que valida "
              "el hub", file=sys.stderr)
        return ERROR_USO
    print(token_de_desarrollo(secreto, args.id, aal="aal1" if args.sin_mfa else "aal2",
                              horas=args.horas, emisor=os.environ.get("DC_JWT_EMISOR")))
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
    p.add_argument("--migraciones", help="por defecto, <catalogo>/supabase/migrations")
    sub = p.add_subparsers(dest="comando", required=True)

    mg = sub.add_parser("migrar", help="aplica las migraciones SQL pendientes")
    mg.add_argument("--via-api", action="store_true",
                    help="por la Management API de Supabase (SUPABASE_URL y "
                         "SUPABASE_ACCESS_TOKEN) en vez de --db")
    mg.set_defaults(func=cmd_migrar)

    us = sub.add_parser("usuario", help="da de alta, cambia el rol o da de baja a una persona")
    us.add_argument("id", help="el id del usuario en el proveedor de identidad (uuid)")
    us.add_argument("--rol", choices=["lector", "operador", "aprobador",
                                      "soporte", "publicador", "comercial"])
    us.add_argument("--tenant", help="cliente, para los roles de cliente")
    us.add_argument("--nombre")
    us.add_argument("--email")
    us.add_argument("--quitar", action="store_true")
    us.set_defaults(func=cmd_usuario)

    td = sub.add_parser("token-dev", help="emite un JWT de desarrollo firmado con DC_JWT_SECRET")
    td.add_argument("id")
    td.add_argument("--horas", type=float, default=8)
    td.add_argument("--sin-mfa", action="store_true")
    td.set_defaults(func=cmd_token_dev)

    sv = sub.add_parser("servir", help="levanta la API")
    sv.add_argument("--host", default="127.0.0.1")
    sv.add_argument("--puerto", type=int, default=8000)
    sv.add_argument("--web", default=os.environ.get("DC_HUB_WEB"),
                    help="carpeta del frontend (por defecto, <catalogo>/web)")
    sv.add_argument("--sin-web", action="store_true", help="solo la API")
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
