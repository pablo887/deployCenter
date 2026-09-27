/* deployCenter · datos de ejemplo del mock.
   Todo es ficticio: clientes, hosts, digests y usuarios. */

window.DC_SEED = {
  hoy: '2026-09-25',

  productos: [
    { id: 'mep',     codigo: 'MEP',     nombre: 'MEP',           desc: 'Medios electrónicos de pago' },
    { id: 'factnova',codigo: 'FNV',     nombre: 'Factnova',      desc: 'Factoring y descuento de documentos' },
    { id: 'pases',   codigo: 'PCR',     nombre: 'Pases & CRyL',  desc: 'Pases y cuentas de registro y liquidación' },
    { id: 'repi',    codigo: 'REPI',    nombre: 'Repi',          desc: 'Reportes regulatorios' },
    { id: 'sml',     codigo: 'SML',     nombre: 'SML',           desc: 'Sistema de mensajería y liquidaciones' },
    { id: 'cedin',   codigo: 'CEDIN',   nombre: 'CEDIN',         desc: 'Certificados de depósito' }
  ],

  // Releases por producto, del más viejo al más nuevo.
  releases: {
    mep: [
      { version: '4.4.2', publicado: '2026-03-02', canal: 'estable', desde: '>=4.3.0', db: false, rollbackSeguro: true, critico: false,
        imagenes: { api: 'sha256:1c07e4…', web: 'sha256:88d1a0…' }, variables: [],
        changelog: ['Corrige redondeo en comisiones de transferencias diferidas.', 'Mejora el tiempo de respuesta del listado de lotes.'] },
      { version: '4.5.0', publicado: '2026-04-20', canal: 'estable', desde: '>=4.4.0', db: true, rollbackSeguro: false, critico: false,
        imagenes: { api: 'sha256:5e9b31…', web: 'sha256:c2f84d…' }, variables: [],
        changelog: ['Nuevo esquema de conciliación por lote (migra la tabla de movimientos).', 'Filtros por estado en la bandeja de rechazos.'] },
      { version: '4.5.1', publicado: '2026-05-28', canal: 'estable', desde: '>=4.5.0', db: false, rollbackSeguro: true, critico: false,
        imagenes: { api: 'sha256:73a0fe…', web: 'sha256:0b4c19…' }, variables: [],
        changelog: ['Corrige la exportación de conciliación con más de 10.000 filas.'] },
      { version: '4.6.0', publicado: '2026-07-15', canal: 'estable', desde: '>=4.5.0', db: false, rollbackSeguro: true, critico: false,
        imagenes: { api: 'sha256:a61d02…', web: 'sha256:e7730c…' }, variables: [],
        changelog: ['Reintentos configurables en el conector del BCRA.', 'Panel de estado de colas en la consola de operación.', 'Actualiza la librería de firma digital.'] },
      { version: '4.6.1', publicado: '2026-08-12', canal: 'estable', desde: '>=4.5.0', db: false, rollbackSeguro: true, critico: true,
        imagenes: { api: 'sha256:d40b87…', web: 'sha256:e7730c…' }, variables: [],
        changelog: ['Parche de seguridad: valida el emisor del certificado en el conector de firma (CVE-2026-31844).'] },
      { version: '4.7.0', publicado: '2026-09-18', canal: 'estable', desde: '>=4.5.0', db: false, rollbackSeguro: true, critico: false,
        imagenes: { api: 'sha256:9f2c6b…', web: 'sha256:41ab93…' },
        variables: [ { nombre: 'MEP_FIRMA_TIMEOUT_S', obligatoria: true, def: '30', descripcion: 'Timeout de firma del conector, en segundos' } ],
        changelog: ['Timeout de firma configurable por instalación.', 'Nueva vista de auditoría de operaciones rechazadas.', 'Corrige la paginación del historial de lotes.'] },
      { version: '4.8.0-rc1', publicado: '2026-09-23', canal: 'anticipado', desde: '>=4.7.0', db: false, rollbackSeguro: true, critico: false,
        imagenes: { api: 'sha256:2e5fa8…', web: 'sha256:7d0c44…' }, variables: [],
        changelog: ['Adelanto: API de consulta de lotes en tiempo real.', 'Adelanto: nuevo tablero de operación diaria.'] }
    ],
    factnova: [
      { version: '2.2.0', publicado: '2026-05-10', canal: 'estable', desde: '>=2.0.0', db: false, rollbackSeguro: true, critico: false,
        imagenes: { api: 'sha256:44e0a1…', worker: 'sha256:b1c93e…' }, variables: [],
        changelog: ['Cesión parcial de facturas de crédito electrónicas.'] },
      { version: '2.3.0', publicado: '2026-08-01', canal: 'estable', desde: '>=2.2.0', db: false, rollbackSeguro: true, critico: false,
        imagenes: { api: 'sha256:9a07d5…', worker: 'sha256:31f6c2…' }, variables: [],
        changelog: ['Aforo por pagador configurable.', 'Reporte de vencimientos por semana.'] },
      { version: '2.3.1', publicado: '2026-09-10', canal: 'estable', desde: '>=2.3.0', db: false, rollbackSeguro: true, critico: false,
        imagenes: { api: 'sha256:c85e10…', worker: 'sha256:31f6c2…' }, variables: [],
        changelog: ['Corrige el cálculo de intereses en años bisiestos.'] }
    ],
    pases: [
      { version: '2.0.0', publicado: '2026-02-14', canal: 'estable', desde: '>=1.8.0', db: false, rollbackSeguro: true, critico: false,
        imagenes: { api: 'sha256:0fe2b7…', web: 'sha256:6c1d8a…' }, variables: [],
        changelog: ['Nueva liquidación de pases pasivos.'] },
      { version: '2.1.0', publicado: '2026-08-25', canal: 'estable', desde: '>=2.0.0', db: false, rollbackSeguro: true, critico: false,
        imagenes: { api: 'sha256:e210c4…', web: 'sha256:6c1d8a…' }, variables: [],
        changelog: ['Conciliación automática con CRyL.', 'Alertas por diferencia de aforos.'] }
    ],
    repi: [
      { version: '3.0.4', publicado: '2026-04-03', canal: 'estable', desde: '>=3.0.0', db: false, rollbackSeguro: true, critico: false,
        imagenes: { api: 'sha256:5b88c2…' }, variables: [],
        changelog: ['Régimen informativo de efectivo mínimo, versión abril.'] },
      { version: '3.1.0', publicado: '2026-09-02', canal: 'estable', desde: '>=3.0.0', db: false, rollbackSeguro: true, critico: false,
        imagenes: { api: 'sha256:fe0917…' },
        variables: [ { nombre: 'REPI_COLA_MAX', obligatoria: true, def: '500', descripcion: 'Cantidad máxima de reportes en cola de envío' } ],
        changelog: ['Cola de envío con reintentos al organismo de control.', 'Nuevo validador de diseño de registro.'] },
      { version: '3.1.1', publicado: '2026-09-21', canal: 'estable', desde: '>=3.1.0', db: false, rollbackSeguro: true, critico: false,
        imagenes: { api: 'sha256:83b4de…' }, variables: [],
        changelog: ['Corrige el encabezado del archivo de posición diaria.'] }
    ],
    sml: [
      { version: '1.8.0', publicado: '2026-03-30', canal: 'estable', desde: '>=1.6.0', db: false, rollbackSeguro: true, critico: false,
        imagenes: { core: 'sha256:772e1a…' }, variables: [], changelog: ['Soporte para mensajes MT 202 COV.'] },
      { version: '1.9.0', publicado: '2026-09-05', canal: 'estable', desde: '>=1.8.0', db: true, rollbackSeguro: false, critico: false,
        imagenes: { core: 'sha256:1d93fa…' }, variables: [], changelog: ['Particiona el histórico de mensajes por mes (migra el esquema).'] }
    ],
    cedin: [
      { version: '5.0.0', publicado: '2026-01-19', canal: 'estable', desde: '>=4.8.0', db: false, rollbackSeguro: true, critico: false,
        imagenes: { api: 'sha256:aa41c0…', web: 'sha256:5f02e9…' }, variables: [], changelog: ['Emisión de certificados en lote.'] },
      { version: '5.1.0', publicado: '2026-07-28', canal: 'estable', desde: '>=5.0.0', db: false, rollbackSeguro: true, critico: false,
        imagenes: { api: 'sha256:0c7be3…', web: 'sha256:5f02e9…' }, variables: [], changelog: ['Consulta pública de certificados por código QR.'] }
    ]
  },

  tenants: [
    { id: 'andino', nombre: 'Banco Andino', estado: 'activo', dobleAprobacion: false,
      avisos: ['operaciones@bancoandino.example', 'infra@bancoandino.example'],
      productos: [
        { producto: 'mep',  mantenimientoHasta: '2026-12-31', autoservicio: true, canal: 'estable', ambientes: ['produccion', 'homologacion'] },
        { producto: 'repi', mantenimientoHasta: '2026-10-20', autoservicio: true, canal: 'estable', ambientes: ['produccion'] }
      ] },
    { id: 'litoral', nombre: 'Banco Litoral Unido', estado: 'activo', dobleAprobacion: true,
      avisos: ['mesa.sistemas@litoralunido.example'],
      productos: [
        { producto: 'mep',      mantenimientoHasta: '2026-06-30', autoservicio: true, canal: 'estable', ambientes: ['produccion'] },
        { producto: 'factnova', mantenimientoHasta: '2027-03-31', autoservicio: true, canal: 'estable', ambientes: ['produccion'] }
      ] },
    { id: 'mercantil', nombre: 'Caja Mercantil', estado: 'activo', dobleAprobacion: false,
      avisos: ['it@cajamercantil.example'],
      productos: [
        { producto: 'mep', mantenimientoHasta: '2027-01-31', autoservicio: true,  canal: 'estable', ambientes: ['produccion'] },
        { producto: 'sml', mantenimientoHasta: '2026-11-10', autoservicio: false, canal: 'estable', ambientes: ['produccion'] }
      ] },
    { id: 'horizonte', nombre: 'Financiera Horizonte', estado: 'activo', dobleAprobacion: false,
      avisos: ['devops@fhorizonte.example'],
      productos: [
        { producto: 'mep',   mantenimientoHasta: '2027-06-30', autoservicio: true, canal: 'anticipado', ambientes: ['produccion', 'homologacion'] },
        { producto: 'cedin', mantenimientoHasta: '2027-06-30', autoservicio: true, canal: 'estable',    ambientes: ['produccion'] }
      ] },
    { id: 'vallesur', nombre: 'Banco del Valle Sur', estado: 'suspendido', dobleAprobacion: false,
      avisos: ['sistemas@vallesur.example'],
      productos: [
        { producto: 'mep',   mantenimientoHasta: '2026-08-31', autoservicio: true, canal: 'estable', ambientes: ['produccion'] },
        { producto: 'pases', mantenimientoHasta: '2026-08-31', autoservicio: true, canal: 'estable', ambientes: ['produccion'] }
      ] },
    { id: 'trescerros', nombre: 'Cooperativa Tres Cerros', estado: 'activo', dobleAprobacion: true,
      avisos: ['tecnologia@trescerros.example'],
      productos: [
        { producto: 'mep',      mantenimientoHasta: '2026-11-15', autoservicio: true, canal: 'estable', ambientes: ['produccion'] },
        { producto: 'factnova', mantenimientoHasta: '2026-11-15', autoservicio: true, canal: 'estable', ambientes: ['produccion'] }
      ] }
  ],

  agentes: [
    { id: 'ag-andino-01',    tenant: 'andino',    host: 'srv-dock-01.andino.local',    version: '1.3.0', estado: 'online',  visto: 'hace 4 s',   disco: 58 },
    { id: 'ag-andino-02',    tenant: 'andino',    host: 'srv-dock-homo.andino.local',  version: '1.3.0', estado: 'online',  visto: 'hace 7 s',   disco: 71 },
    { id: 'ag-litoral-01',   tenant: 'litoral',   host: 'dckprd01.litoral.lan',        version: '1.3.0', estado: 'online',  visto: 'hace 3 s',   disco: 40 },
    { id: 'ag-mercantil-01', tenant: 'mercantil', host: 'docker-prod.cmercantil.int',  version: '1.2.1', estado: 'offline', visto: 'hace 3 h',   disco: 22 },
    { id: 'ag-horizonte-01', tenant: 'horizonte', host: 'fh-app-01',                   version: '1.3.0', estado: 'online',  visto: 'hace 5 s',   disco: 64 },
    { id: 'ag-horizonte-02', tenant: 'horizonte', host: 'fh-homo-01',                  version: '1.3.0', estado: 'online',  visto: 'hace 9 s',   disco: 80 },
    { id: 'ag-vallesur-01',  tenant: 'vallesur',  host: 'vs-docker-01',                version: '1.2.1', estado: 'online',  visto: 'hace 6 s',   disco: 47 },
    { id: 'ag-trescerros-01',tenant: 'trescerros',host: 'tc-srv-prod',                 version: '1.3.0', estado: 'online',  visto: 'hace 2 s',   disco: 35 }
  ],

  instalaciones: [
    { id: 'i-andino-mep-prd',  tenant: 'andino', producto: 'mep',  ambiente: 'produccion',   agente: 'ag-andino-01', version: '4.6.1',
      ultimoDeploy: { fecha: '2026-08-14 10:12', por: 'Martín Ríos' }, variables: {} },
    { id: 'i-andino-mep-hom',  tenant: 'andino', producto: 'mep',  ambiente: 'homologacion', agente: 'ag-andino-02', version: '4.7.0',
      ultimoDeploy: { fecha: '2026-09-22 15:40', por: 'Martín Ríos' }, variables: { MEP_FIRMA_TIMEOUT_S: '30' } },
    { id: 'i-andino-repi-prd', tenant: 'andino', producto: 'repi', ambiente: 'produccion',   agente: 'ag-andino-01', version: '3.0.4',
      ultimoDeploy: { fecha: '2026-04-09 09:05', por: 'Soporte Accusys' }, variables: {} },
    { id: 'i-litoral-mep-prd', tenant: 'litoral', producto: 'mep', ambiente: 'produccion',   agente: 'ag-litoral-01', version: '4.5.1',
      ultimoDeploy: { fecha: '2026-06-02 22:30', por: 'Soporte Accusys' }, variables: {} },
    { id: 'i-litoral-fnv-prd', tenant: 'litoral', producto: 'factnova', ambiente: 'produccion', agente: 'ag-litoral-01', version: '2.3.0',
      ultimoDeploy: { fecha: '2026-08-06 11:20', por: 'Paula Ferreyra' }, variables: {} },
    { id: 'i-mercantil-mep-prd', tenant: 'mercantil', producto: 'mep', ambiente: 'produccion', agente: 'ag-mercantil-01', version: '4.6.0',
      ultimoDeploy: { fecha: '2026-07-21 23:10', por: 'Soporte Accusys' }, variables: {} },
    { id: 'i-mercantil-sml-prd', tenant: 'mercantil', producto: 'sml', ambiente: 'produccion', agente: 'ag-mercantil-01', version: '1.8.0',
      ultimoDeploy: { fecha: '2026-04-12 22:00', por: 'Soporte Accusys' }, variables: {} },
    { id: 'i-horizonte-mep-prd', tenant: 'horizonte', producto: 'mep', ambiente: 'produccion', agente: 'ag-horizonte-01', version: '4.7.0',
      ultimoDeploy: { fecha: '2026-09-19 12:02', por: 'Diego Suárez' }, variables: { MEP_FIRMA_TIMEOUT_S: '45' } },
    { id: 'i-horizonte-mep-hom', tenant: 'horizonte', producto: 'mep', ambiente: 'homologacion', agente: 'ag-horizonte-02', version: '4.8.0-rc1',
      ultimoDeploy: { fecha: '2026-09-24 10:30', por: 'Diego Suárez' }, variables: { MEP_FIRMA_TIMEOUT_S: '45' } },
    { id: 'i-horizonte-cedin-prd', tenant: 'horizonte', producto: 'cedin', ambiente: 'produccion', agente: 'ag-horizonte-01', version: '5.1.0',
      ultimoDeploy: { fecha: '2026-08-03 14:15', por: 'Diego Suárez' }, variables: {} },
    { id: 'i-vallesur-mep-prd', tenant: 'vallesur', producto: 'mep', ambiente: 'produccion', agente: 'ag-vallesur-01', version: '4.4.2',
      ultimoDeploy: { fecha: '2026-03-10 22:45', por: 'Soporte Accusys' }, variables: {} },
    { id: 'i-vallesur-pases-prd', tenant: 'vallesur', producto: 'pases', ambiente: 'produccion', agente: 'ag-vallesur-01', version: '2.0.0',
      ultimoDeploy: { fecha: '2026-02-20 22:00', por: 'Soporte Accusys' }, variables: {} },
    { id: 'i-trescerros-mep-prd', tenant: 'trescerros', producto: 'mep', ambiente: 'produccion', agente: 'ag-trescerros-01', version: '4.6.0',
      ultimoDeploy: { fecha: '2026-07-30 09:40', por: 'Lucía Paz' }, variables: {} },
    { id: 'i-trescerros-fnv-prd', tenant: 'trescerros', producto: 'factnova', ambiente: 'produccion', agente: 'ag-trescerros-01', version: '2.2.0',
      ultimoDeploy: { fecha: '2026-05-18 09:10', por: 'Lucía Paz' }, variables: {} }
  ],

  usuarios: [
    { id: 'u1', nombre: 'Martín Ríos',      email: 'mrios@bancoandino.example',     tenant: 'andino',     rol: 'operador' },
    { id: 'u2', nombre: 'Carla Benítez',    email: 'cbenitez@bancoandino.example',  tenant: 'andino',     rol: 'aprobador' },
    { id: 'u3', nombre: 'Tomás Aguirre',    email: 'taguirre@bancoandino.example',  tenant: 'andino',     rol: 'lector' },
    { id: 'u4', nombre: 'Paula Ferreyra',   email: 'pferreyra@litoralunido.example',tenant: 'litoral',    rol: 'operador' },
    { id: 'u5', nombre: 'Julián Molina',    email: 'jmolina@litoralunido.example',  tenant: 'litoral',    rol: 'aprobador' },
    { id: 'u6', nombre: 'Diego Suárez',     email: 'dsuarez@fhorizonte.example',    tenant: 'horizonte',  rol: 'operador' },
    { id: 'u7', nombre: 'Lucía Paz',        email: 'lpaz@trescerros.example',       tenant: 'trescerros', rol: 'operador' },
    { id: 'a1', nombre: 'Sofía Herrera',    email: 'sherrera@accusys.example',      tenant: null,         rol: 'soporte' },
    { id: 'a2', nombre: 'Nicolás Vidal',    email: 'nvidal@accusys.example',        tenant: null,         rol: 'publicador' },
    { id: 'a3', nombre: 'Valeria Quiroga',  email: 'vquiroga@accusys.example',      tenant: null,         rol: 'comercial' }
  ],

  historial: [
    { id: 'h1',  fecha: '2026-09-24 10:30', tenant: 'horizonte', producto: 'mep',      ambiente: 'homologacion', tipo: 'Despliegue', desde: '4.7.0', hacia: '4.8.0-rc1', resultado: 'ok',       por: 'Diego Suárez',   duracion: '3 min 12 s' },
    { id: 'h2',  fecha: '2026-09-22 15:40', tenant: 'andino',    producto: 'mep',      ambiente: 'homologacion', tipo: 'Despliegue', desde: '4.6.1', hacia: '4.7.0',     resultado: 'ok',       por: 'Martín Ríos',    duracion: '4 min 02 s' },
    { id: 'h3',  fecha: '2026-09-19 12:02', tenant: 'horizonte', producto: 'mep',      ambiente: 'produccion',   tipo: 'Despliegue', desde: '4.6.1', hacia: '4.7.0',     resultado: 'ok',       por: 'Diego Suárez',   duracion: '3 min 48 s' },
    { id: 'h4',  fecha: '2026-09-11 16:25', tenant: 'trescerros',producto: 'mep',      ambiente: 'produccion',   tipo: 'Despliegue', desde: '4.6.0', hacia: '4.6.1',     resultado: 'rollback', por: 'Lucía Paz',      duracion: '6 min 30 s' },
    { id: 'h5',  fecha: '2026-08-14 10:12', tenant: 'andino',    producto: 'mep',      ambiente: 'produccion',   tipo: 'Despliegue', desde: '4.6.0', hacia: '4.6.1',     resultado: 'ok',       por: 'Martín Ríos',    duracion: '3 min 20 s' },
    { id: 'h6',  fecha: '2026-08-06 11:20', tenant: 'litoral',   producto: 'factnova', ambiente: 'produccion',   tipo: 'Despliegue', desde: '2.2.0', hacia: '2.3.0',     resultado: 'ok',       por: 'Paula Ferreyra', duracion: '2 min 41 s' },
    { id: 'h7',  fecha: '2026-08-03 14:15', tenant: 'horizonte', producto: 'cedin',    ambiente: 'produccion',   tipo: 'Despliegue', desde: '5.0.0', hacia: '5.1.0',     resultado: 'ok',       por: 'Diego Suárez',   duracion: '2 min 05 s' },
    { id: 'h8',  fecha: '2026-07-21 23:10', tenant: 'mercantil', producto: 'mep',      ambiente: 'produccion',   tipo: 'Asistido',   desde: '4.5.1', hacia: '4.6.0',     resultado: 'ok',       por: 'Soporte Accusys',duracion: '38 min' },
    { id: 'h9',  fecha: '2026-06-02 22:30', tenant: 'litoral',   producto: 'mep',      ambiente: 'produccion',   tipo: 'Asistido',   desde: '4.5.0', hacia: '4.5.1',     resultado: 'ok',       por: 'Soporte Accusys',duracion: '52 min' }
  ]
};
