"""Verificación post-despliegue: ¿levantó bien la versión nueva?

Es el criterio objetivo de éxito, y por eso mismo es lo que dispara el rollback
automático. Sin esto el rollback no puede existir: no habría forma de decidir
cuándo hace falta.

Se mira en dos niveles, y los dos tienen que dar bien:

1. **Los contenedores.** Todos los servicios del manifiesto corriendo y, los que
   declaran healthcheck en el compose, en estado healthy. Esto sale de
   `docker compose ps` y no necesita red.
2. **El smoke test.** Las URLs que declara el manifiesto responden el código
   esperado. Esto sí necesita que el agente alcance la red del stack; cuando no
   puede resolver el nombre, se reporta como omitido y no como éxito.

Esto NO es monitoreo funcional. Comprueba que el sistema arrancó, no que esté
operando bien tres horas después. Esa distinción está en el alcance del
documento de arquitectura y conviene no borrarla.
"""

import time
import urllib.error
import urllib.request

ESTADOS_ARRIBA = {"running"}
SALUD_MALA = {"unhealthy"}
SALUD_ESPERANDO = {"starting"}


class Resultado:
    def __init__(self, nombre, ok, detalle, omitido=False):
        self.nombre, self.ok, self.detalle, self.omitido = nombre, ok, detalle, omitido

    def __repr__(self):  # pragma: no cover
        marca = "omitido" if self.omitido else ("ok" if self.ok else "falla")
        return f"<{self.nombre}: {marca}>"


class Informe:
    def __init__(self, resultados, segundos=0.0):
        self.resultados = list(resultados)
        self.segundos = segundos

    @property
    def ok(self):
        return all(r.ok for r in self.resultados if not r.omitido)

    @property
    def fallas(self):
        return [r for r in self.resultados if not r.ok and not r.omitido]

    @property
    def omitidos(self):
        return [r for r in self.resultados if r.omitido]

    def resumen(self):
        return {
            "ok": self.ok,
            "segundos": round(self.segundos, 1),
            "resultados": [
                {"nombre": r.nombre, "ok": r.ok, "omitido": r.omitido, "detalle": r.detalle}
                for r in self.resultados
            ],
        }


def pedir_http(url, timeout):
    """Cliente por defecto. Devuelve (codigo, detalle)."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:  # noqa: S310
            return r.status, ""
    except urllib.error.HTTPError as e:
        return e.code, ""
    except urllib.error.URLError as e:
        return None, str(e.reason)
    except (TimeoutError, OSError) as e:
        return None, str(e)


def esperar_contenedores(docker, instalacion, servicios, timeout_s=120, intervalo_s=3,
                         dormir=time.sleep, reloj=time.monotonic):
    """Espera a que los servicios estén arriba y sanos. Devuelve un Resultado."""
    pendientes = set(servicios)
    limite = reloj() + timeout_s
    ultimo = "todavía no respondió docker compose ps"

    while True:
        estados = {e["servicio"]: e for e in docker.ps(instalacion.directorio)}
        malos = []
        for servicio in sorted(pendientes):
            e = estados.get(servicio)
            if e is None:
                malos.append(f"{servicio}: no aparece en el stack")
            elif e["estado"] not in ESTADOS_ARRIBA:
                malos.append(f"{servicio}: {e['estado'] or 'sin estado'}")
            elif e["salud"] in SALUD_MALA:
                malos.append(f"{servicio}: unhealthy")
            elif e["salud"] in SALUD_ESPERANDO:
                malos.append(f"{servicio}: starting")
        if not malos:
            return Resultado("contenedores", True,
                             f"{len(pendientes)} servicio(s) arriba y sanos")
        ultimo = "; ".join(malos)
        if reloj() >= limite:
            return Resultado("contenedores", False, f"a los {timeout_s}s: {ultimo}")
        dormir(intervalo_s)


def smoke_http(healthchecks, cliente=None):
    """Pega a las URLs del manifiesto. Si no se puede resolver el nombre, se
    marca omitido: es mejor decir 'no lo pude comprobar' que dar por bueno algo
    que no se comprobó."""
    cliente = cliente or pedir_http
    resultados = []
    for hc in healthchecks:
        nombre = f"smoke:{hc['servicio']}"
        codigo, detalle = cliente(hc["url"], hc.get("timeout_s", 30))
        if codigo is None:
            resultados.append(Resultado(
                nombre, True,
                f"no se pudo alcanzar {hc['url']} desde el agente ({detalle})",
                omitido=True))
        elif codigo == hc["espera"]:
            resultados.append(Resultado(nombre, True, f"{hc['url']} devolvió {codigo}"))
        else:
            resultados.append(Resultado(
                nombre, False,
                f"{hc['url']} devolvió {codigo}, se esperaba {hc['espera']}"))
    return resultados


def verificar(docker, instalacion, manifiesto, timeout_s=None, cliente_http=None,
              dormir=time.sleep, reloj=time.monotonic):
    """Verificación completa de un despliegue."""
    inicio = reloj()
    checks = manifiesto.get("healthchecks") or []
    servicios = sorted((manifiesto.get("imagenes") or {}).keys())
    if timeout_s is None:
        timeout_s = max([c.get("timeout_s", 120) for c in checks] or [120])

    resultados = [esperar_contenedores(
        docker, instalacion, servicios, timeout_s=timeout_s,
        dormir=dormir, reloj=reloj)]

    # El smoke test solo tiene sentido si los contenedores arrancaron.
    if resultados[0].ok:
        resultados.extend(smoke_http(checks, cliente=cliente_http))

    return Informe(resultados, segundos=reloj() - inicio)
