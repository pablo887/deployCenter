# Demo del agente: un despliegue real en tu máquina

Un agente corriendo al lado del hub, con un producto de prueba (`demo`: un nginx
que muestra qué versión corre). Sirve para ver el circuito completo desde la web:
preflight, despliegue, verificación y vuelta atrás automática.

| Release | Qué pasa |
| --- | --- |
| `1.0.0` | Instalación inicial (nginx 1.26) |
| `2.0.0` | Upgrade (nginx 1.27); queda el punto de retorno a 1.0.0 |
| `3.0.0` | **Roto a propósito**: no levanta, la verificación falla y el agente vuelve solo a 2.0.0 (salvo que canceles la vuelta atrás desde la web) |

El stack queda publicado en http://localhost:18080 y la página dice la versión.

Qué tiene de distinto a un agente real, solo para la demo: no verifica la firma
del manifiesto (`--sin-firma`: la imagen no trae cosign y la demo no tiene clave)
y habla con el hub por HTTP dentro de la red de Docker. Usa el Docker de esta
máquina a través del socket.

## Pasos (PowerShell, en la carpeta del repo)

**1. Activar la demo en `.env`.** Agregá (o descomentá) estas líneas:

```
COMPOSE_PROFILES=db-local,agente-demo
DC_HUB_CATALOGO_EXTRA=/app/ejemplos/demo-agente/catalogo
```

y reconstruí el hub para que ofrezca el producto demo:

```powershell
git pull
docker compose up -d --build hub
```

**2. Darle el producto a un cliente.** En la web, como Comercial: Parametría →
elegí el cliente (o creá uno con *Nuevo cliente*) → *Agregar producto adquirido*
→ Demo.

**3. Emitir el código de enrolamiento** para ese cliente (acá `andino`):

```powershell
docker compose exec hub dc-hub codigo andino --host demo-01
```

Copiá el `codigo` (tipo `DC-XXXX-XXXX`) a `.env`:

```
DC_AGENTE_CODIGO=DC-XXXX-XXXX
```

**4. Levantar el agente:**

```powershell
docker compose up -d --build agente
docker compose logs -f agente
```

Tiene que decir `enrolado como ag-…` y `conectado a http://hub:8000`. El código
se usa una sola vez: las credenciales quedan en un volumen y en los próximos
arranques se conecta directo.

**5. Un Operador de ese cliente.** Solo el Operador despliega. Creá otro usuario
en Supabase (Authentication → Users → Add user, *Auto Confirm User*), ingresá
con él en una ventana de incógnito para activar el TOTP y ver su id, y dalo de
alta como Comercial desde Parametría → *Alta de usuario* con rol Operador.

**6. Desplegar.** Como el Operador: Mis instalaciones → Demo → *Ver versiones*.

1. `1.0.0` → *Correr preflight* → *Desplegar 1.0.0 ahora*. Abrí http://localhost:18080.
2. `2.0.0`, igual. La página pasa a decir 2.0.0.
3. `3.0.0`: la verificación falla y tenés 60 segundos para *Cancelar la vuelta
   atrás*. Si no la cancelás, el agente vuelve solo a 2.0.0 y la página lo muestra.

También podés volver a la versión anterior a mano desde la instalación.

## Limpiar

```powershell
docker compose stop agente
docker compose -p demo down          # el stack de nginx
docker volume rm deploycenter-hub_agente-stacks deploycenter-hub_agente-trabajo
```

Para enrolar de nuevo (por ejemplo, después de revocarlo desde la web), borrá
el volumen `agente-trabajo` y poné un código nuevo.
