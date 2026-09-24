"""CLI del toolchain de Fase 0.

    dc validar    productos/mep/releases/4.7.0/manifiesto.json
    dc pinear     productos/mep/releases/4.7.0/manifiesto.json --escribir
    dc variables  productos/mep/releases/4.7.0/manifiesto.json --entorno .env
    dc render     --producto mep --release 4.7.0 --entorno .env --salida docker-compose.yml
    dc firmar     productos/mep/releases/4.7.0/manifiesto.json --clave cosign.key
    dc verificar  productos/mep/releases/4.7.0/manifiesto.json --clave-publica cosign.pub
    dc nuevo-release --producto mep --version 4.7.1 --desde '>=4.5.0'
    dc schema

Códigos de salida: 0 todo bien, 1 la validación encontró problemas,
2 error de uso o falta una herramienta externa.
"""

import argparse
import datetime
import json
import sys
from pathlib import Path

import yaml

from . import compose, digests, firma
from . import manifiesto as mf
from . import variables as vars_
from .errores import ErrorArchivo, ErrorDeployCenter, ErrorHerramienta, ErrorValidacion
from .salida import (  # noqa: F401  (re-export)
    ERROR_USO,
    FALLA_VALIDACION,
    OK,
    preparar_salida,
    simbolos,
)
from .salida import paleta as _color
from .salida import usar_color as _usar_color


def raiz_por_defecto():
    """Raíz del repo de productos: la que contiene la carpeta productos/."""
    aqui = Path.cwd()
    for candidata in [aqui, *aqui.parents]:
        if (candidata / "productos").is_dir():
            return candidata
    return aqui


def ruta_release(raiz, producto, version):
    return Path(raiz) / "productos" / producto / "releases" / version / "manifiesto.json"


def cargar_producto(raiz, producto):
    ruta = Path(raiz) / "productos" / producto / "producto.yaml"
    try:
        datos = yaml.safe_load(ruta.read_text(encoding="utf-8"))
    except OSError as e:
        raise ErrorArchivo(f"no se pudo leer {ruta}: {e}") from e
    except yaml.YAMLError as e:
        raise ErrorArchivo(f"{ruta} no es YAML válido: {e}") from e
    if not isinstance(datos, dict):
        raise ErrorArchivo(f"{ruta} debería ser un mapeo YAML")
    return datos


# --------------------------------------------------------------------------- #
# comandos
# --------------------------------------------------------------------------- #

def cmd_validar(args):
    verde, rojo, amarillo, gris, fin = _color(_usar_color(args))
    s = simbolos()
    raiz = Path(args.raiz) if args.raiz else raiz_por_defecto()
    m = mf.cargar(args.manifiesto)
    problemas = mf.validar(m, raiz=raiz, exigir_pin=not args.sin_pin)

    if problemas:
        print(f"{rojo}{s['mal']}{fin} {args.manifiesto}: {len(problemas)} problema(s)")
        for p in problemas:
            print(f"  {rojo}{s['punto']}{fin} {p}")
        return FALLA_VALIDACION

    modo = "autoservicio" if mf.es_autoservicio(m) else "asistido"
    color_modo = verde if modo == "autoservicio" else amarillo
    print(f"{verde}{s['ok']}{fin} {args.manifiesto}")
    print(f"  producto   {m['producto']} {m['release']} ({m['canal']})")
    print(f"  publicado  {m['publicado']}")
    print(f"  circuito   {color_modo}{modo}{fin}")
    print(f"  servicios  {', '.join(mf.servicios(m))}")
    print(f"  desde      {m['desde_version']}")
    if args.sin_pin:
        print(f"  {gris}(no se exigió pinneo por digest){fin}")
    return OK


def cmd_pinear(args):
    verde, rojo, _amarillo, gris, fin = _color(_usar_color(args))
    s = simbolos()
    ruta = Path(args.manifiesto)
    m = mf.cargar(ruta)

    nuevo, cambios = digests.pinear_manifiesto(m)
    if not cambios:
        print(f"{verde}{s['ok']}{fin} ya estaba todo pinneado por digest")
        return OK

    for servicio, antes, despues in cambios:
        print(f"  {servicio}")
        print(f"    {gris}antes {fin}{antes}")
        print(f"    {verde}ahora {fin}{despues}")

    if args.escribir:
        mf.guardar(nuevo, ruta)
        print(f"{verde}{s['ok']}{fin} {ruta} actualizado ({len(cambios)} imagen/es)")
    else:
        print(f"{rojo}{s['punto']}{fin} no se escribió nada; agregá --escribir para aplicarlo")
    return OK


def cmd_variables(args):
    verde, rojo, amarillo, gris, fin = _color(_usar_color(args))
    s = simbolos()
    m = mf.cargar(args.manifiesto)
    entorno = vars_.leer_env(args.entorno) if args.entorno else {}
    inf = vars_.informe(m, entorno)

    if not vars_.declaradas(m):
        print(f"{verde}{s['ok']}{fin} el release no declara variables nuevas")
        return OK

    for v in inf["presentes"]:
        print(f"  {verde}{s['ok']}{fin} {v['nombre']} {gris}ya está en el entorno{fin}")
    for v in inf["completables"]:
        print(f"  {amarillo}{s['mas']}{fin} {v['nombre']} "
              f"{gris}se completa con el default {v['default']!r}{fin}")
    for v in inf["faltantes"]:
        print(f"  {rojo}{s['mal']}{fin} {v['nombre']} — {v['descripcion']}")

    if inf["bloquea"]:
        print()
        print(f"{rojo}{s['mal']}{fin} faltan {len(inf['faltantes'])} variable(s) "
              f"obligatoria(s) sin default")
        print(f"  {gris}el preflight se detiene acá: hay que cargarlas antes de desplegar{fin}")
        return FALLA_VALIDACION

    print()
    print(f"{verde}{s['ok']}{fin} el entorno alcanza para desplegar {m['producto']} {m['release']}")
    return OK


def cmd_render(args):
    verde, _rojo, _amarillo, gris, fin = _color(_usar_color(args))
    s = simbolos()
    raiz = Path(args.raiz) if args.raiz else raiz_por_defecto()

    if args.manifiesto:
        ruta_m = Path(args.manifiesto)
    else:
        if not (args.producto and args.release):
            raise ErrorHerramienta("indicá --manifiesto, o bien --producto y --release")
        ruta_m = ruta_release(raiz, args.producto, args.release)
    m = mf.cargar(ruta_m)

    if args.plantilla:
        ruta_p = Path(args.plantilla)
    else:
        producto = cargar_producto(raiz, m["producto"])
        ruta_p = raiz / "productos" / m["producto"] / producto.get(
            "plantilla", "compose.plantilla.yaml")

    entorno = vars_.leer_env(args.entorno) if args.entorno else {}
    entorno = vars_.aplicar_defaults(m, entorno)

    faltan = vars_.faltantes(m, entorno)
    if faltan and not args.forzar:
        nombres = ", ".join(v["nombre"] for v in faltan)
        raise ErrorValidacion([f"faltan variables obligatorias sin default: {nombres}"])

    texto = compose.generar(ruta_p, m, entorno=entorno, salida=args.salida)
    if args.salida:
        print(f"{verde}{s['ok']}{fin} {args.salida}")
        print(f"  {gris}desde {ruta_p.name} + {m['producto']} {m['release']}{fin}")
    else:
        sys.stdout.write(texto)
    return OK


def cmd_firmar(args):
    verde, _rojo, _amarillo, gris, fin = _color(_usar_color(args))
    s = simbolos()
    ruta = Path(args.manifiesto)
    m = mf.cargar(ruta)
    mf.exigir_valido(m, raiz=Path(args.raiz) if args.raiz else raiz_por_defecto())
    destino = Path(args.salida) if args.salida else ruta.with_suffix(
        ruta.suffix + firma.SUFIJO_FIRMA)
    firma.firmar(m, args.clave, destino)
    print(f"{verde}{s['ok']}{fin} {destino}")
    print(f"  {gris}firmado sobre la forma canónica del manifiesto{fin}")
    return OK


def cmd_verificar(args):
    verde, rojo, _amarillo, _gris, fin = _color(_usar_color(args))
    s = simbolos()
    ruta = Path(args.manifiesto)
    m = mf.cargar(ruta)
    firma_ruta = Path(args.firma) if args.firma else ruta.with_suffix(
        ruta.suffix + firma.SUFIJO_FIRMA)
    if firma.verificar(m, args.clave_publica, firma_ruta):
        print(f"{verde}{s['ok']}{fin} firma válida para {ruta}")
        return OK
    print(f"{rojo}{s['mal']}{fin} firma inválida para {ruta}")
    return FALLA_VALIDACION


def cmd_schema(_args):
    print(json.dumps(mf.cargar_schema(), indent=2, ensure_ascii=False))
    return OK


def cmd_empaquetar(args):
    """Arma el paquete que consume el agente: manifiesto + plantilla + changelog.

    Es la misma forma que va a entregar el hub en Fase 2, así que el agente no
    tiene que cambiar cuando el hub aparezca.
    """
    verde, _rojo, _amarillo, gris, fin = _color(_usar_color(args))
    s = simbolos()
    raiz = Path(args.raiz) if args.raiz else raiz_por_defecto()

    ruta_m = ruta_release(raiz, args.producto, args.release)
    m = mf.cargar(ruta_m)
    mf.exigir_valido(m, raiz=raiz)

    producto = cargar_producto(raiz, args.producto)
    ruta_p = raiz / "productos" / args.producto / producto.get(
        "plantilla", "compose.plantilla.yaml")

    destino = Path(args.salida)
    destino.mkdir(parents=True, exist_ok=True)
    copiados = []

    (destino / "manifiesto.json").write_bytes(ruta_m.read_bytes())
    copiados.append("manifiesto.json")
    (destino / "compose.plantilla.yaml").write_bytes(ruta_p.read_bytes())
    copiados.append("compose.plantilla.yaml")

    changelog = raiz / m["changelog"]
    if changelog.is_file():
        (destino / "changelog.md").write_bytes(changelog.read_bytes())
        copiados.append("changelog.md")

    firma_origen = ruta_m.with_suffix(ruta_m.suffix + ".sig")
    if firma_origen.is_file():
        (destino / "manifiesto.json.sig").write_bytes(firma_origen.read_bytes())
        copiados.append("manifiesto.json.sig")
    else:
        print(f"  {gris}sin firma: el agente no va a poder verificarla{fin}")

    print(f"{verde}{s['ok']}{fin} {destino}")
    print(f"  {gris}{', '.join(copiados)}{fin}")
    return OK


def cmd_nuevo_release(args):
    verde, _rojo, _amarillo, gris, fin = _color(_usar_color(args))
    s = simbolos()
    raiz = Path(args.raiz) if args.raiz else raiz_por_defecto()
    producto = cargar_producto(raiz, args.producto)
    base_registry = producto.get("registry", f"registry.accusys.com.ar/{args.producto}")

    imagenes, checks = {}, []
    for servicio, cfg in (producto.get("servicios") or {}).items():
        imagenes[servicio] = f"{base_registry}/{servicio}:{args.version}"
        hc = (cfg or {}).get("healthcheck") or {}
        checks.append({
            "servicio": servicio,
            "url": hc.get("url", f"http://{servicio}:8080/health"),
            "espera": hc.get("espera", 200),
            "timeout_s": hc.get("timeout_s", 120),
        })

    nuevo = {
        "producto": args.producto,
        "release": args.version,
        "publicado": args.fecha or datetime.date.today().isoformat(),
        "canal": args.canal,
        "imagenes": imagenes,
        "desde_version": args.desde,
        "db_migrations": args.con_migraciones,
        "rollback_seguro": not args.con_migraciones,
        "critico_seguridad": False,
        "variables_nuevas": [],
        "healthchecks": checks,
        "changelog": f"productos/{args.producto}/releases/{args.version}/changelog.md",
    }

    carpeta = raiz / "productos" / args.producto / "releases" / args.version
    if carpeta.exists() and not args.sobrescribir:
        raise ErrorHerramienta(f"{carpeta} ya existe; usá --sobrescribir si es a propósito")
    carpeta.mkdir(parents=True, exist_ok=True)

    mf.guardar(nuevo, carpeta / "manifiesto.json")
    changelog = carpeta / "changelog.md"
    if not changelog.exists():
        migra = ("sí, el release va por circuito asistido"
                 if args.con_migraciones else "no")
        changelog.write_text(
            f"# {producto.get('nombre', args.producto)} {args.version}\n\n"
            f"_Publicado {nuevo['publicado']} · canal {args.canal}_\n\n"
            "## Qué cambia\n\n- \n\n"
            "## Impacto en el despliegue\n\n"
            "- Variables nuevas: ninguna\n"
            f"- Migraciones de base: {migra}\n"
            f"- Ruta de upgrade: {args.desde}\n",
            encoding="utf-8",
        )

    print(f"{verde}{s['ok']}{fin} {carpeta}")
    print(f"  {gris}las imágenes quedaron por tag; corré 'dc pinear ... --escribir' "
          f"cuando estén publicadas{fin}")
    return OK


# --------------------------------------------------------------------------- #
# armado del parser
# --------------------------------------------------------------------------- #

def construir_parser():
    p = argparse.ArgumentParser(
        prog="dc",
        description="Toolchain de releases de deployCenter (Fase 0).",
    )
    p.add_argument("--sin-color", action="store_true", help="salida sin códigos ANSI")
    p.add_argument("--raiz", help="raíz del repo de productos (por defecto se busca productos/)")
    sub = p.add_subparsers(dest="comando", required=True)

    v = sub.add_parser("validar", help="valida un manifiesto contra el schema y las reglas")
    v.add_argument("manifiesto")
    v.add_argument("--sin-pin", action="store_true",
                   help="no exigir digests; sirve antes de publicar las imágenes")
    v.set_defaults(func=cmd_validar)

    pin = sub.add_parser("pinear", help="resuelve los tags a digests contra el registry")
    pin.add_argument("manifiesto")
    pin.add_argument("--escribir", action="store_true", help="aplica los cambios al archivo")
    pin.set_defaults(func=cmd_pinear)

    va = sub.add_parser("variables",
                        help="compara las variables del release con el entorno del cliente")
    va.add_argument("manifiesto")
    va.add_argument("--entorno", help="archivo .env del cliente")
    va.set_defaults(func=cmd_variables)

    r = sub.add_parser("render", help="genera el docker-compose.yml del cliente")
    r.add_argument("--manifiesto")
    r.add_argument("--producto")
    r.add_argument("--release")
    r.add_argument("--plantilla")
    r.add_argument("--entorno", help="archivo .env del cliente")
    r.add_argument("--salida", help="si no se indica, va a stdout")
    r.add_argument("--forzar", action="store_true", help="renderizar aunque falten variables")
    r.set_defaults(func=cmd_render)

    f = sub.add_parser("firmar", help="firma el manifiesto con cosign")
    f.add_argument("manifiesto")
    f.add_argument("--clave", required=True)
    f.add_argument("--salida")
    f.set_defaults(func=cmd_firmar)

    ver = sub.add_parser("verificar", help="verifica la firma de un manifiesto")
    ver.add_argument("manifiesto")
    ver.add_argument("--clave-publica", required=True)
    ver.add_argument("--firma")
    ver.set_defaults(func=cmd_verificar)

    n = sub.add_parser("nuevo-release", help="crea el esqueleto de un release")
    n.add_argument("--producto", required=True)
    n.add_argument("--version", required=True)
    n.add_argument("--desde", default="*", help="rango de versiones de origen, ej '>=4.5.0'")
    n.add_argument("--canal", default="estable", choices=["estable", "anticipado"])
    n.add_argument("--fecha", help="fecha de publicación (por defecto, hoy)")
    n.add_argument("--con-migraciones", action="store_true")
    n.add_argument("--sobrescribir", action="store_true")
    n.set_defaults(func=cmd_nuevo_release)

    emp = sub.add_parser("empaquetar",
                         help="arma el paquete de release que consume el agente")
    emp.add_argument("--producto", required=True)
    emp.add_argument("--release", required=True)
    emp.add_argument("--salida", required=True, help="directorio destino del paquete")
    emp.set_defaults(func=cmd_empaquetar)

    s = sub.add_parser("schema", help="imprime el JSON Schema del manifiesto")
    s.set_defaults(func=cmd_schema)

    return p


def main(argv=None):
    preparar_salida()
    args = construir_parser().parse_args(argv)
    _verde, rojo, _amarillo, _gris, fin = _color(_usar_color(args))
    s = simbolos()
    try:
        return args.func(args)
    except ErrorValidacion as e:
        print(f"{rojo}{s['mal']}{fin} {len(e.problemas)} problema(s)", file=sys.stderr)
        for p in e.problemas:
            print(f"  {rojo}{s['punto']}{fin} {p}", file=sys.stderr)
        return FALLA_VALIDACION
    except (ErrorArchivo, ErrorHerramienta) as e:
        print(f"{rojo}{s['mal']}{fin} {e}", file=sys.stderr)
        return ERROR_USO
    except ErrorDeployCenter as e:  # pragma: no cover - red de seguridad
        print(f"{rojo}{s['mal']}{fin} {e}", file=sys.stderr)
        return ERROR_USO


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
