/* deployCenter · conexión con el hub.

   Si la página la sirve un hub (`dc-hub servir`), /config.json dice a qué
   proveedor de identidad hablarle y la web trabaja con datos reales. Abierta
   como archivo suelto, o desde un servidor que no es el hub, no hay config y
   queda la maqueta con datos de ejemplo.

   Identidad: Supabase Auth por su API REST, sin librerías. Contraseña, después
   TOTP (enrolamiento con QR la primera vez) y un token aal2, que es lo único
   que acepta el hub. El token vive en sessionStorage: se pierde al cerrar la
   pestaña. La CSP que manda el hub no deja cargar scripts de otro origen. */
(function () {
  'use strict';

  const CLAVE_SESION = 'deploycenter-sesion';
  let CFG = null;
  let sesion = leerSesion();
  let temporizador = null;

  // ------------------------------------------------------------------ utilidades

  function leerSesion() {
    try { return JSON.parse(sessionStorage.getItem(CLAVE_SESION)) || null; } catch (e) { return null; }
  }
  function guardarSesion(s) {
    sesion = s;
    try { s ? sessionStorage.setItem(CLAVE_SESION, JSON.stringify(s)) : sessionStorage.removeItem(CLAVE_SESION); } catch (e) { /* sin storage: queda en memoria */ }
    programarRenovacion();
  }
  function claims(token) {
    try {
      const b = token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/');
      return JSON.parse(decodeURIComponent(escape(atob(b + '='.repeat((4 - b.length % 4) % 4)))));
    } catch (e) { return {}; }
  }
  // los errores de Supabase Auth que una persona puede ver al ingresar
  const TRADUCCIONES = {
    invalid_credentials: 'Email o contraseña incorrectos.',
    email_not_confirmed: 'El email todavía no está confirmado.',
    user_banned: 'El usuario está bloqueado.',
    over_request_rate_limit: 'Demasiados intentos: esperá un momento y probá de nuevo.',
    mfa_verification_failed: 'El código no es válido o ya venció: probá con el que muestra ahora la app.',
    mfa_challenge_expired: 'El desafío venció: volvé a ingresar el código.',
    mfa_ip_address_mismatch: 'Cambió la red entre el desafío y la verificación: volvé a ingresar el código.',
    mfa_factor_not_found: 'El segundo factor ya no existe: volvé a ingresar.',
    session_not_found: 'La sesión venció: volvé a ingresar.',
    refresh_token_not_found: 'La sesión venció: volvé a ingresar.'
  };

  class ErrorHub extends Error {
    constructor(mensaje, estado, datos) { super(mensaje); this.estado = estado; this.datos = datos; }
  }
  async function pedir(url, opciones) {
    let r;
    try { r = await fetch(url, opciones); } catch (e) { throw new ErrorHub('No hay conexión con ' + new URL(url, location.href).host, 0); }
    const texto = await r.text();
    let datos = null;
    try { datos = texto ? JSON.parse(texto) : null; } catch (e) { datos = { detalle: texto.slice(0, 300) }; }
    if (!r.ok) {
      const codigo = datos && (datos.error_code || datos.code);
      const msg = TRADUCCIONES[codigo] || (datos && (datos.detalle || datos.msg || datos.message || datos.error_description || datos.error));
      throw new ErrorHub(msg || `Error ${r.status}`, r.status, datos);
    }
    return datos;
  }

  // ------------------------------------------------------------------ configuración

  async function iniciar() {
    try {
      const r = await fetch('config.json', { cache: 'no-store' });
      if (!r.ok) return null;
      CFG = await r.json();
    } catch (e) { return null; }
    programarRenovacion();
    return CFG;
  }

  // ------------------------------------------------------------------ Supabase Auth

  function auth(metodo, ruta, cuerpo, token) {
    const cab = { apikey: CFG.publishable_key, 'Content-Type': 'application/json' };
    if (token) cab.Authorization = 'Bearer ' + token;
    return pedir(`${CFG.supabase_url}/auth/v1${ruta}`, { method: metodo, headers: cab, body: cuerpo ? JSON.stringify(cuerpo) : undefined });
  }
  function aSesion(d) {
    return { access_token: d.access_token, refresh_token: d.refresh_token, expires_at: d.expires_at || Math.floor(Date.now() / 1000) + (d.expires_in || 3600) };
  }

  async function ingresar(email, clave) {
    const d = await auth('POST', '/token?grant_type=password', { email, password: clave });
    guardarSesion(aSesion(d));
    return estadoMfa();
  }

  // Qué falta para el aal2: nada, verificar un factor que ya existe o enrolar uno.
  async function estadoMfa() {
    if (aal() === 'aal2') return { paso: 'listo' };
    const u = await auth('GET', '/user', null, sesion.access_token);
    const factores = (u.factors || []).filter(f => f.factor_type === 'totp');
    const verificado = factores.find(f => f.status === 'verified');
    if (verificado) return { paso: 'totp', factorId: verificado.id };
    // un enrolamiento que quedó a medias estorba (el nombre del factor es único)
    for (const f of factores) { try { await auth('DELETE', `/factors/${f.id}`, null, sesion.access_token); } catch (e) { /* sigue */ } }
    const f = await auth('POST', '/factors', { factor_type: 'totp', friendly_name: 'deployHub ' + new Date().toISOString().slice(0, 16) }, sesion.access_token);
    return { paso: 'enrolar', factorId: f.id, qr: qrComoDataUri(f.totp && f.totp.qr_code), secreto: f.totp && f.totp.secret };
  }

  // Según la versión, Supabase manda el SVG crudo o un data URI sin codificar
  // (donde un '#' lo cortaría). Para <img> tiene que ser un data URI codificado.
  function qrComoDataUri(qr) {
    if (!qr) return null;
    const m = /^data:image\/svg\+xml;(?:utf-8|charset=utf-8),(.*)$/s.exec(qr);
    const svg = m ? m[1] : qr.trimStart().startsWith('<') ? qr : null;
    return svg ? 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(svg) : qr;
  }

  async function verificar(factorId, codigo) {
    const d = await auth('POST', `/factors/${factorId}/challenge`, {}, sesion.access_token);
    const v = await auth('POST', `/factors/${factorId}/verify`, { challenge_id: d.id, code: codigo }, sesion.access_token);
    guardarSesion(aSesion(v));
  }

  function usarToken(token) {
    // desarrollo: el hub valida con un secreto compartido (dc-hub token-dev)
    const c = claims(token);
    guardarSesion({ access_token: token, refresh_token: null, expires_at: c.exp || 0 });
  }

  async function renovar() {
    if (!sesion || !sesion.refresh_token || !CFG || CFG.identidad !== 'supabase') return false;
    try {
      const d = await auth('POST', '/token?grant_type=refresh_token', { refresh_token: sesion.refresh_token });
      guardarSesion(aSesion(d));
      return true;
    } catch (e) { return false; }
  }
  function programarRenovacion() {
    clearTimeout(temporizador);
    if (!sesion || !sesion.refresh_token) return;
    const ms = Math.max(5000, (sesion.expires_at - 90) * 1000 - Date.now());
    temporizador = setTimeout(async () => { if (!(await renovar())) window.dispatchEvent(new Event('dc-sesion-vencida')); }, ms);
  }

  async function salir() {
    const s = sesion;
    guardarSesion(null);
    if (s && s.refresh_token && CFG && CFG.identidad === 'supabase') {
      try { await auth('POST', '/logout', {}, s.access_token); } catch (e) { /* ya no importa */ }
    }
  }

  function aal() { return sesion ? claims(sesion.access_token).aal : null; }
  function usuario() {
    if (!sesion) return null;
    const c = claims(sesion.access_token);
    return { id: c.sub, email: c.email, aal: c.aal, vence: c.exp };
  }
  function haySesion() {
    return !!sesion && aal() === 'aal2' && (sesion.refresh_token || sesion.expires_at * 1000 > Date.now());
  }

  // ------------------------------------------------------------------ API del hub

  async function api(metodo, ruta, cuerpo, reintento) {
    try {
      return await pedir('api/v1' + ruta, {
        method: metodo,
        headers: Object.assign({ 'Content-Type': 'application/json' }, sesion ? { Authorization: 'Bearer ' + sesion.access_token } : {}),
        body: cuerpo ? JSON.stringify(cuerpo) : undefined
      });
    } catch (e) {
      if (e.estado === 401 && !reintento && await renovar()) return api(metodo, ruta, cuerpo, true);
      if (e.estado === 401) window.dispatchEvent(new Event('dc-sesion-vencida'));
      throw e;
    }
  }

  // ------------------------------------------------------------------ estado para las vistas

  const dos = n => String(n).padStart(2, '0');
  // el hub guarda UTC; si la fecha no trae zona, es UTC
  const instante = iso => iso ? new Date(/Z|[+-]\d\d:?\d\d$/.test(iso) ? iso : iso + 'Z') : new Date(NaN);
  function fechaLocal(iso, conSegundos) {
    const d = instante(iso);
    if (isNaN(d)) return '';
    return `${d.getFullYear()}-${dos(d.getMonth() + 1)}-${dos(d.getDate())} ${dos(d.getHours())}:${dos(d.getMinutes())}` + (conSegundos ? ':' + dos(d.getSeconds()) : '');
  }
  function haceCuanto(iso) {
    if (!iso) return 'nunca';
    const s = Math.max(0, Math.round((Date.now() - instante(iso)) / 1000));
    if (s < 60) return `hace ${s} s`;
    if (s < 3600) return `hace ${Math.round(s / 60)} min`;
    if (s < 86400) return `hace ${Math.round(s / 3600)} h`;
    return `hace ${Math.round(s / 86400)} días`;
  }
  function duracion(desde, hasta) {
    if (!desde || !hasta) return '';
    const s = Math.max(1, Math.round((instante(hasta) - instante(desde)) / 1000));
    return s < 3600 ? `${Math.floor(s / 60)} min ${dos(s % 60)} s` : `${Math.floor(s / 3600)} h ${dos(Math.floor(s / 60) % 60)} min`;
  }
  // Lo que reporta un agente (host, versión, nombre de la instalación) llega a
  // vistas que lo interpolan tal cual: se reduce a caracteres que no son HTML.
  const limpio = v => v == null ? v : String(v).replace(/[^\w.:@+\/ -]/g, '·').slice(0, 200);
  const sinMarkdown = t => String(t).replace(/\*\*(.+?)\*\*/g, '$1').replace(/`([^`]+)`/g, '$1');

  function resultadoHistorial(o) {
    if (o.estado === 'cancelada') return 'cancelada';
    if (o.estado !== 'terminada') return 'en_curso';
    if (o.tipo === 'rollback') return o.resultado === 'ok' ? 'manual' : 'error';
    return { ok: 'ok', revertido: 'rollback', degradado: 'retenido' }[o.resultado] || 'error';
  }

  const ACCIONES_AUDITORIA = {
    cliente_alta: d => `Cliente dado de alta${d.nombre ? ': ' + d.nombre : ''}`,
    cliente_estado: d => `Cliente ${d.estado}`,
    parametria: d => `${d.producto}: mantenimiento hasta ${d.mantenimiento_hasta || '—'} · canal ${d.canal} · autoservicio ${d.autoservicio ? 'sí' : 'no'}`,
    codigo_enrolamiento: d => `Código de enrolamiento emitido${d.host ? ' para ' + d.host : ''}`,
    agente_revocado: d => `Agente ${d.agente || ''} revocado`,
    orden: d => `Orden de ${d.tipo}${d.release ? ' ' + d.release : ''} sobre ${d.instalacion || ''}`,
    orden_retirada: d => `Orden ${d.orden} retirada`,
    rollback_cancelado: d => `Vuelta atrás cancelada en ${d.orden}`,
    habilitacion_otorgada: d => `Habilitación a Accusys por ${d.horas} h${d.motivo ? ': ' + d.motivo : ''}`,
    habilitacion_revocada: () => 'Habilitación a Accusys revocada',
    usuario_rol: d => `Rol ${d.rol} para ${d.usuario}`,
    usuario_baja: d => `Baja de ${d.usuario}`
  };

  async function cargar() {
    const [yo, catalogo, tenants, parque, ordenes, usuarios, auditoria, habilitaciones] = await Promise.all([
      api('GET', '/yo'), api('GET', '/catalogo'), api('GET', '/tenants'), api('GET', '/parque'),
      api('GET', '/ordenes?limite=300'), api('GET', '/usuarios'), api('GET', '/auditoria?limite=300'),
      api('GET', '/habilitaciones')
    ]);
    const S = { hoy: new Date().toISOString().slice(0, 10), mails: [], manifiestos: {}, ordenes, habilitaciones, yo };

    S.productos = catalogo.map(p => ({ id: p.id, codigo: p.id.toUpperCase(), nombre: p.nombre, desc: p.descripcion || '' }));
    S.releases = {};
    catalogo.forEach(p => {
      S.releases[p.id] = p.releases.map(({ manifiesto: m, changelog }) => {
        S.manifiestos[`${p.id}@${m.release}`] = m;
        return {
          version: m.release, publicado: m.publicado, canal: m.canal || 'estable', desde: m.desde_version,
          db: !!m.db_migrations, rollbackSeguro: !!m.rollback_seguro, critico: !!m.critico_seguridad,
          imagenes: Object.fromEntries(Object.entries(m.imagenes || {}).map(([k, v]) => [k, String(v).split('@').pop()])),
          variables: (m.variables_nuevas || []).map(v => ({ nombre: v.nombre, obligatoria: !!v.obligatoria, def: v.default || '', descripcion: v.descripcion || '' })),
          changelog: (changelog || []).map(sinMarkdown)
        };
      });
    });
    const conocido = id => !!S.releases[id];

    S.tenants = tenants.map(t => ({
      id: t.id, nombre: t.nombre, estado: t.estado, dobleAprobacion: false, avisos: [],
      productos: t.productos.filter(p => conocido(p.producto)).map(p => ({
        producto: p.producto, mantenimientoHasta: p.mantenimiento_hasta, autoservicio: p.autoservicio, canal: p.canal, ambientes: []
      }))
    }));

    const ultimo = {};
    ordenes.slice().reverse().forEach(o => {
      if (['desplegar', 'rollback'].includes(o.tipo) && o.estado === 'terminada' && ['ok', 'revertido'].includes(o.resultado))
        ultimo[`${o.agente}:${o.instalacion}`] = { fecha: fechaLocal(o.terminada), por: o.pedida_por };
    });
    S.agentes = []; S.instalaciones = [];
    parque.forEach(a => {
      S.agentes.push({
        id: limpio(a.id), tenant: a.tenant, host: limpio(a.host), version: limpio(a.version) || '—', disco: null,
        estado: a.estado === 'revocado' ? 'revocado' : a.en_linea ? 'online' : 'offline',
        visto: haceCuanto(a.ultimo_contacto)
      });
      a.instalaciones.filter(i => conocido(i.producto)).forEach(i => S.instalaciones.push({
        id: limpio(`${a.id}:${i.nombre}`), nombre: i.nombre, tenant: a.tenant, producto: i.producto, ambiente: limpio(i.nombre), agente: limpio(a.id),
        version: limpio(i.version) || '—', ultimoDeploy: ultimo[`${a.id}:${i.nombre}`] || { fecha: '', por: '' }, variables: {},
        puntoRetorno: i.punto_retorno ? { version: limpio(i.punto_retorno), fecha: '' } : null,
        estado: i.estado === 'degradado' ? 'retenido' : 'ok', bloqueada: i.bloqueada
      }));
    });

    // una instalación de un producto sin parametría se muestra igual, sin habilitar nada
    S.instalaciones.forEach(i => {
      const t = S.tenants.find(x => x.id === i.tenant);
      if (t && !t.productos.find(x => x.producto === i.producto))
        t.productos.push({ producto: i.producto, mantenimientoHasta: null, autoservicio: false, canal: 'estable', ambientes: [] });
    });

    const instDe = o => S.instalaciones.find(i => i.agente === limpio(o.agente) && i.nombre === o.instalacion);
    S.historial = ordenes.filter(o => ['desplegar', 'rollback'].includes(o.tipo)).map(o => {
      const r = o.resumen || {}, i = instDe(o);
      return {
        id: o.id, orden: o.id, fecha: fechaLocal(o.creada), tenant: o.tenant, producto: o.producto || (i && i.producto),
        ambiente: limpio(o.instalacion), tipo: o.tipo === 'rollback' ? 'Rollback' : 'Despliegue',
        desde: limpio(r.desde) || '—', hacia: limpio(r.hacia || o.release) || '—', resultado: resultadoHistorial(o),
        por: o.pedida_por, duracion: duracion(o.entregada || o.creada, o.terminada)
      };
    }).filter(h => conocido(h.producto));

    S.auditoria = auditoria.map(a => ({
      fecha: fechaLocal(a.fecha), por: a.usuario || '—', tenant: a.tenant,
      accion: (ACCIONES_AUDITORIA[a.accion] || (d => `${a.accion} ${JSON.stringify(d)}`))(a.detalle || {})
    }));

    S.usuarios = usuarios.map(u => ({ id: u.usuario_id, nombre: u.nombre || u.email || u.usuario_id.slice(0, 8), email: u.email || '', tenant: u.tenant, rol: u.rol }));
    if (!S.usuarios.find(u => u.id === yo.usuario_id))
      S.usuarios.push({ id: yo.usuario_id, nombre: yo.nombre || yo.email || 'Sin nombre', email: yo.email || '', tenant: yo.tenant, rol: yo.rol });
    S.sesion = yo.usuario_id;
    return S;
  }

  window.DC_HUB = {
    iniciar, config: () => CFG, ingresar, estadoMfa, verificar, usarToken, salir, renovar,
    haySesion, usuario, api, cargar, fechaLocal, haceCuanto, ErrorHub
  };
})();
