# Registry privado

La segunda mitad de la Fase 0. Reemplaza el repositorio público de Docker Hub por
un registry en un dominio de Accusys.

## Por qué no es opcional

Tres razones, en orden de urgencia:

1. **La allowlist.** Los servidores de los clientes solo salen a destinos
   habilitados. Docker Hub no es un dominio: es el registry, más el servicio de
   autenticación, más el almacenamiento detrás de una CDN cuyos nombres e IPs
   rotan. Cada rotación es un despliegue que falla por red. Un dominio propio
   reduce eso a un FQDN fijo.
2. **La parametría.** La credencial de cada cliente se alcanza a los repos de los
   productos que compró. Es el segundo punto de control, el que no depende de que
   la web muestre o esconda un botón.
3. **La exposición actual.** Hoy cualquiera puede descargar las imágenes de los
   productos y no hay registro de quién lo hace. Eso ya es un problema, con o sin
   este proyecto.

## Qué hay que decidir

> **Pendiente.** La elección del producto de registry está abierta. Lo que sigue
> es la recomendación, no una decisión tomada.

**Harbor** es el que mejor encaja, porque tiene exactamente el modelo que necesita
la parametría: un proyecto por producto, *robot accounts* con permiso de solo
lectura y alcance por proyecto, y expiración por cuenta. Dar de baja a un cliente
es revocar su robot account. Además hace escaneo de vulnerabilidades, que en
clientes bancarios se va a pedir.

Las alternativas: **zot** es mucho más liviano pero la gestión de permisos por
cliente hay que armarla; **Docker Registry** a secas no tiene autorización por
repo y necesita un proxy adelante.

## Estructura de repos

Un repo por servicio, colgando del producto:

```
registry.accusys.com.ar/
├── mep/api            mep/web
├── factnova/...
├── pases-cryl/...
├── repi/...
├── sml/...
└── cedin/...
```

El campo `registry` de `productos/<codigo>/producto.yaml` define la base, y el
manifiesto referencia `<base>/<servicio>@sha256:...`. El test
`test_repo.py::TestRelease::test_las_imagenes_apuntan_al_registry_del_producto`
verifica que se respete.

## Credenciales

| Quién | Permiso | Alcance |
|---|---|---|
| CI de Accusys | lectura y escritura | todos los productos |
| Cliente | solo lectura | los productos que tiene adquiridos |
| Agente | usa la credencial del cliente | ídem |

La credencial del cliente se configura una vez en el host con `docker login` y
queda en el `config.json` de Docker. El agente no la maneja: el `docker pull` lo
hace el daemon con lo que ya tiene configurado.

## Registry local para desarrollo

Para probar el circuito de `dc pinear` sin tocar el registry real:

```bash
cd registry
docker compose up -d

# publicar una imagen de prueba
docker pull alpine:3.20
docker tag alpine:3.20 localhost:5000/mep/api:4.8.0
docker push localhost:5000/mep/api:4.8.0

# resolver el digest
cd ..
python -m deploycenter.cli pinear /ruta/al/manifiesto.json --escribir
```

Ojo: el registry local es HTTP sin autenticación. Sirve para probar el toolchain
y nada más. No se parece al de producción en lo que importa —permisos y TLS—, así
que no lo uses para validar el modelo de credenciales.

## Qué falta para cerrar la Fase 0

- [ ] Elegir el producto de registry y levantarlo
- [ ] Definir los dominios definitivos (ver pendiente del documento de arquitectura)
- [ ] Certificado TLS y su renovación
- [ ] Crear los repos de MEP y cargar las primeras imágenes
- [ ] Una credencial de prueba con alcance a un solo producto, para verificar que
      el corte funciona antes de dársela a un cliente
- [ ] Pedirle a un cliente piloto que habilite el dominio en su allowlist
