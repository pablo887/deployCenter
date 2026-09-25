/* deployCenter · web (vanilla JS, sin build).
   Dos modos. Servida por un hub (dc-hub servir): datos reales, identidad con
   segundo factor y órdenes que ejecuta el agente (ver hub.js). Abierta sola:
   maqueta con datos de ejemplo, estado en memoria + localStorage. */
(function () {
  'use strict';

  const SEED = window.DC_SEED;
  const KEY = 'deploycenter-mock-v1';
  const AGENTE_ULTIMA = '1.3.0';
  const HUB = window.DC_HUB;
  let MODO = 'demo';       // 'hub' cuando la página la sirve un hub

  /* ============================================================
     Estado
     ============================================================ */
  let S = cargar() || nuevo();
  let DEP = null;          // despliegue en curso (runtime, no se persiste)
  let DEPH = null;         // modo hub: la orden real que se está siguiendo
  let UI = { vista: '', inst: null, prod: null, tenantParam: null, prodRel: null, login: { user: 'u1', paso: 1 }, drawer: null, modal: null, filtroAud: { tenant: '', tipo: '' } };

  function nuevo() {
    const s = JSON.parse(JSON.stringify(SEED));
    s.sesion = null;
    s.auditoria = [
      { fecha: '2026-09-20 11:05', por: 'Valeria Quiroga', tenant: 'andino', accion: 'Mantenimiento de Repi cargado hasta 2026-10-20' },
      { fecha: '2026-09-18 09:30', por: 'Nicolás Vidal', tenant: null, accion: 'Release MEP 4.7.0 firmado y publicado (canal estable)' },
      { fecha: '2026-09-01 17:42', por: 'Valeria Quiroga', tenant: 'vallesur', accion: 'Cliente suspendido: autoservicio deshabilitado' }
    ];
    s.mails = [
      { fecha: '2026-09-23 09:00', tenant: 'horizonte', para: 'devops@fhorizonte.example', asunto: 'MEP 4.8.0-rc1 disponible en canal anticipado', cuerpo: 'La versión ya está en tu catálogo con su changelog.' },
      { fecha: '2026-09-21 09:00', tenant: 'andino', para: 'operaciones@bancoandino.example', asunto: 'Repi: tu mantenimiento vence en 29 días', cuerpo: 'Vence el 20/10/2026. Copia a comercial de Accusys.' },
      { fecha: '2026-09-18 09:02', tenant: 'andino', para: 'operaciones@bancoandino.example', asunto: 'MEP 4.7.0 disponible', cuerpo: 'Nueva versión con 1 variable nueva (MEP_FIRMA_TIMEOUT_S). Corré el preflight cuando quieras.' },
      { fecha: '2026-09-11 16:31', tenant: 'trescerros', para: 'tecnologia@trescerros.example; guardia@accusys.example', asunto: 'Rollback automático · MEP 4.6.1 en producción', cuerpo: 'La verificación falló (healthcheck api). Se restauró 4.6.0 con los digests guardados.' }
    ];
    s.ordenes = [];
    s.instalaciones.forEach(i => { i.puntoRetorno = retornoInicial(s, i); i.estado = 'ok'; });
    return s;
  }
  function retornoInicial(s, inst) {
    const rels = s.releases[inst.producto];
    const idx = rels.findIndex(r => r.version === inst.version);
    if (idx <= 0) return null;
    if (rels[idx].db && !rels[idx].rollbackSeguro) return null;
    return { version: rels[idx - 1].version, fecha: inst.ultimoDeploy.fecha };
  }
  function cargar() { try { const t = localStorage.getItem(KEY); return t ? JSON.parse(t) : null; } catch (e) { return null; } }
  function guardar() { if (MODO === 'hub') return; try { localStorage.setItem(KEY, JSON.stringify(S)); } catch (e) { /* sin storage: sigue en memoria */ } }

  /* ============================================================
     Utilidades
     ============================================================ */
  const $ = (sel, el = document) => el.querySelector(sel);
  const esc = v => String(v == null ? '' : v).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const MESES = ['ene', 'feb', 'mar', 'abr', 'may', 'jun', 'jul', 'ago', 'sep', 'oct', 'nov', 'dic'];
  const fecha = d => { if (!d) return '—'; const [y, m, dd] = d.slice(0, 10).split('-'); return `${+dd} ${MESES[+m - 1]} ${y}`; };
  const dias = (a, b) => Math.round((Date.parse(b) - Date.parse(a)) / 864e5);
  const hora = () => { const d = new Date(); return String(d.getHours()).padStart(2, '0') + ':' + String(d.getMinutes()).padStart(2, '0') + ':' + String(d.getSeconds()).padStart(2, '0'); };
  const ahora = () => `${S.hoy} ${hora().slice(0, 5)}`;
  const iniciales = n => n.split(' ').map(p => p[0]).slice(0, 2).join('').toUpperCase();
  const AMB = { produccion: 'Producción', homologacion: 'Homologación' };
  const amb = a => AMB[a] || esc(a);   // en el hub es el nombre que reporta el agente
  const ROLES = {
    lector: { nombre: 'Lector', lado: 'Cliente' }, operador: { nombre: 'Operador', lado: 'Cliente' }, aprobador: { nombre: 'Aprobador', lado: 'Cliente' },
    soporte: { nombre: 'Soporte', lado: 'Accusys' }, publicador: { nombre: 'Publicador', lado: 'Accusys' }, comercial: { nombre: 'Comercial', lado: 'Accusys' }
  };

  function parseV(v) { const [core, pre] = v.split('-'); return { n: core.split('.').map(Number), pre: pre || null }; }
  function cmpV(a, b) {
    const A = parseV(a), B = parseV(b);
    for (let i = 0; i < 3; i++) if (A.n[i] !== B.n[i]) return A.n[i] - B.n[i];
    if (A.pre === B.pre) return 0; if (!A.pre) return 1; if (!B.pre) return -1; return A.pre < B.pre ? -1 : 1;
  }
  const satisface = (v, rango) => { const m = /^>=\s*(.+)$/.exec(rango || ''); return m ? cmpV(v, m[1]) >= 0 : true; };

  const prod = id => S.productos.find(p => p.id === id);
  const tenant = id => S.tenants.find(t => t.id === id);
  const agente = id => S.agentes.find(a => a.id === id);
  const inst = id => S.instalaciones.find(i => i.id === id);
  const usuarioActual = () => S.usuarios.find(u => u.id === S.sesion);
  const esAccusys = () => { const u = usuarioActual(); return u && !u.tenant; };
  const rels = p => S.releases[p] || [];
  const rel = (p, v) => rels(p).find(r => r.version === v);
  const tp = (t, p) => (tenant(t) || { productos: [] }).productos.find(x => x.producto === p);
  const ultimaEstable = p => rels(p).filter(r => r.canal === 'estable').slice(-1)[0];
  const instsDe = t => S.instalaciones.filter(i => i.tenant === t);

  function mant(tprod) {
    if (!tprod || !tprod.mantenimientoHasta) return { estado: 'sin-dato', dias: 0, pill: 'p-neutro', txt: 'Sin parametría' };
    const d = dias(S.hoy, tprod.mantenimientoHasta);
    if (d < 0) return { estado: 'vencido', dias: d, pill: 'p-crit', txt: `Vencido hace ${-d} días` };
    if (d <= 60) return { estado: 'por-vencer', dias: d, pill: 'p-warn', txt: `Vence en ${d} días` };
    return { estado: 'vigente', dias: d, pill: 'p-ok', txt: 'Vigente' };
  }

  // Regla de habilitación del documento: adquirido + publicado antes del vencimiento (o crítico de seguridad),
  // sin migración, con autoservicio, ruta de upgrade soportada y agente en línea.
  function evaluar(i, r) {
    const t = tenant(i.tenant), tpr = tp(i.tenant, i.producto), ag = agente(i.agente);
    const c = cmpV(r.version, i.version);
    if (c === 0) return { estado: 'instalada', motivo: 'Es la versión que corre hoy.' };
    if (c < 0) {
      if (i.puntoRetorno && i.puntoRetorno.version === r.version) return { estado: 'retorno', motivo: 'Punto de retorno registrado. Volver atrás nunca se bloquea.' };
      return { estado: 'anterior', motivo: 'Versión anterior a la instalada.' };
    }
    if (t.estado === 'suspendido') return { estado: 'bloqueado', motivo: 'Cliente suspendido: el autoservicio está cortado. El historial se conserva.' };
    if (r.db) return { estado: 'asistido', motivo: 'Trae migración de base: se despliega con Accusys y backup obligatorio.' };
    if (!tpr.autoservicio) return { estado: 'asistido', motivo: 'El autoservicio está deshabilitado para este producto: lo despliega Accusys.' };
    if (r.publicado > tpr.mantenimientoHasta && !r.critico) return { estado: 'bloqueado', motivo: `Publicado el ${fecha(r.publicado)}, después del fin del mantenimiento (${fecha(tpr.mantenimientoHasta)}).` };
    if (!satisface(i.version, r.desde)) {
      const inter = rels(i.producto).filter(x => cmpV(x.version, i.version) > 0 && cmpV(x.version, r.version) < 0 && satisface(i.version, x.desde) && satisface(x.version, r.desde)).slice(-1)[0];
      return { estado: 'bloqueado', motivo: `La ruta de upgrade pide ${r.desde}.` + (inter ? ` Instalá primero la ${inter.version}.` : '') };
    }
    if (!ag || ag.estado !== 'online') return { estado: 'bloqueado', motivo: 'El agente de este servidor está fuera de línea. El botón vuelve cuando reconecte.' };
    const extra = r.publicado > tpr.mantenimientoHasta && r.critico ? ' Parche crítico: se habilita aunque el mantenimiento esté vencido.' : '';
    return { estado: 'habilitado', motivo: (r.variables.length ? `Pide ${r.variables.length} variable nueva.` : 'Sin variables nuevas.') + extra };
  }
  function relsVisibles(i) {
    const tpr = tp(i.tenant, i.producto);
    return rels(i.producto).filter(r => r.canal === 'estable' || tpr.canal === 'anticipado' || r.version === i.version);
  }
  function mejorDisponible(i) {
    return relsVisibles(i).filter(r => cmpV(r.version, i.version) > 0 && evaluar(i, r).estado === 'habilitado').slice(-1)[0];
  }
  function atraso(i) {
    const lista = rels(i.producto).filter(r => r.canal === 'estable');
    const u = lista.length - 1;
    const idx = lista.findIndex(r => r.version === i.version);
    if (idx === -1) return 0; // canal anticipado, por delante
    return u - idx;
  }
  function puedeEjecutar() {
    const u = usuarioActual();
    if (!u) return { ok: false };
    if (u.rol === 'operador') return { ok: true };
    if (u.rol === 'lector') return { ok: false, motivo: 'Tu rol (Lector) puede ver pero no ejecutar.' };
    if (u.rol === 'aprobador') return { ok: false, motivo: 'El rol Aprobador habilita órdenes, no las ejecuta.' };
    return { ok: false, motivo: 'Accusys no despliega sin habilitación explícita del cliente.' };
  }

  function mail(t, asunto, cuerpo, extra) {
    const te = tenant(t);
    const para = (te ? te.avisos.join('; ') : '') + (extra ? '; ' + extra : '');
    S.mails.unshift({ fecha: ahora(), tenant: t, para, asunto, cuerpo });
  }
  function auditar(accion, t) { S.auditoria.unshift({ fecha: ahora(), por: usuarioActual().nombre, tenant: t || null, accion }); }

  function toast(txt, err) {
    const el = document.createElement('div');
    el.className = 'toast' + (err ? ' err' : '');
    el.innerHTML = `<i></i><span>${esc(txt)}</span>`;
    $('#toasts').appendChild(el);
    setTimeout(() => el.remove(), 3600);
  }

  /* ============================================================
     Íconos (trazo)
     ============================================================ */
  const ic = p => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${p}</svg>`;
  const I = {
    server: ic('<rect x="3" y="4" width="18" height="7" rx="2"/><rect x="3" y="13" width="18" height="7" rx="2"/><path d="M7 7.5h.01M7 16.5h.01"/>'),
    box: ic('<path d="M21 8 12 3 3 8v8l9 5 9-5z"/><path d="m3 8 9 5 9-5M12 13v8"/>'),
    clock: ic('<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>'),
    users: ic('<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20a6.5 6.5 0 0 1 13 0"/><path d="M16 4.5a3.5 3.5 0 0 1 0 7M18 14a6 6 0 0 1 3.5 6"/>'),
    grid: ic('<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>'),
    sliders: ic('<path d="M4 6h10M18 6h2M4 12h4M12 12h8M4 18h12M20 18h0"/><circle cx="16" cy="6" r="2"/><circle cx="10" cy="12" r="2"/><circle cx="18" cy="18" r="2"/>'),
    tag: ic('<path d="M3 12V4a1 1 0 0 1 1-1h8l9 9-9 9z"/><circle cx="7.5" cy="7.5" r="1.5"/>'),
    cpu: ic('<rect x="6" y="6" width="12" height="12" rx="2"/><path d="M9 2v4M15 2v4M9 18v4M15 18v4M2 9h4M2 15h4M18 9h4M18 15h4"/>'),
    list: ic('<path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01"/>'),
    mail: ic('<rect x="3" y="5" width="18" height="14" rx="2"/><path d="m3 7 9 6 9-6"/>'),
    menu: ic('<path d="M4 6h16M4 12h16M4 18h16"/>'),
    alert: ic('<path d="M12 3 2 20h20z"/><path d="M12 10v4M12 17h.01"/>'),
    info: ic('<circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8h.01"/>'),
    check: ic('<circle cx="12" cy="12" r="9"/><path d="m8 12 3 3 5-6"/>'),
    x: ic('<path d="M6 6l12 12M18 6 6 18"/>'),
    rocket: ic('<path d="M5 15c-1.5 1.5-2 5-2 5s3.5-.5 5-2"/><path d="M9 15 6 12c1-4 5-9 12-9 0 7-5 11-9 12z"/><circle cx="14.5" cy="9.5" r="1.5"/>'),
    undo: ic('<path d="M9 14 4 9l5-5"/><path d="M4 9h10a6 6 0 0 1 0 12h-3"/>'),
    shield: ic('<path d="M12 3 4 6v6c0 5 3.5 8 8 9 4.5-1 8-4 8-9V6z"/><path d="m9 12 2 2 4-4"/>'),
    plus: ic('<path d="M12 5v14M5 12h14"/>'),
    logout: ic('<path d="M15 4h4a1 1 0 0 1 1 1v14a1 1 0 0 1-1 1h-4M10 17l5-5-5-5M15 12H3"/>')
  };

  /* ============================================================
     Render principal
     ============================================================ */
  function render() {
    const app = $('#app');
    if (!S.sesion) { app.innerHTML = MODO === 'hub' ? vistaLoginHub() : vistaLogin(); return; }
    const u = usuarioActual();
    const vistas = esAccusys() ? VISTAS_ACCUSYS : VISTAS_CLIENTE;
    let v = UI.vista;
    if (!vistas[v] && !(v === 'instalacion' && UI.inst) && !(v === 'desplegar' && (DEP || DEPH))) v = Object.keys(vistas)[0];
    if (v === 'instalacion' && esAccusys()) v = 'parque';
    UI.vista = v;
    const cuerpo = (VISTAS_EXTRA[v] || vistas[v]).render();
    app.innerHTML = `
      <div class="app" id="shell">
        ${side(vistas, v)}
        <div style="min-width:0">
          ${top(u)}
          <div id="banner-dep">${bannerDep()}</div>
          <main class="contenido" id="main">${cuerpo}</main>
        </div>
      </div>
      ${UI.drawer ? drawerHtml() : ''}
      ${UI.modal ? modalHtml() : ''}`;
    const c = $('.consola'); if (c) c.scrollTop = c.scrollHeight;
  }

  function side(vistas, v) {
    const mails = misMails().length;
    const items = Object.entries(vistas).map(([k, x]) =>
      `<a href="#${k}" class="${k === v || (v === 'instalacion' && k === 'instalaciones') || (v === 'desplegar' && k === 'catalogo') ? 'activo' : ''}">${x.icono}<span>${x.titulo}</span>${x.cuenta ? `<span class="cuenta">${x.cuenta()}</span>` : ''}</a>`).join('');
    return `
      <aside class="side">
        <div class="marca"><span class="m">D</span><span class="n">deploy<i>Hub</i></span></div>
        <nav class="nav" aria-label="Principal">
          <div class="nav-titulo">${esAccusys() ? 'Accusys · interno' : 'Mi organización'}</div>
          ${items}
        </nav>
        <div class="side-pie">
          ${MODO === 'hub' ? `<span>Conectado al hub</span><span><code>${esc(location.host)}</code></span>` : `
          <span>Mock navegable · datos ficticios</span>
          <span>hub <code>deploy.accusys.com.ar</code></span>
          <span>${mails} mails en la bandeja de avisos</span>`}
        </div>
      </aside>`;
  }

  function top(u) {
    const t = u.tenant ? tenant(u.tenant) : null;
    const n = misMails().filter(m => m.fecha.startsWith(S.hoy)).length;
    return `
      <header class="top">
        <button class="icono-btn hamburguesa" data-a="menu" aria-label="Abrir menú">${I.menu}</button>
        <div class="tenant">
          <b>${t ? esc(t.nombre) : 'Accusys'}</b>
          <small>${t ? `Tenant <code>${t.id}</code> · ${t.estado === 'activo' ? 'activo' : 'suspendido'}` : 'Vista interna · todo el parque'}</small>
        </div>
        <div class="espacio"></div>
        ${MODO === 'hub' ? '' : `<button class="icono-btn" data-a="abrir-mails" aria-label="Avisos por mail">${I.mail}${n ? `<span class="punto">${n}</span>` : ''}</button>`}
        <button class="usuario" data-a="abrir-usuarios" aria-label="${MODO === 'hub' ? 'Tu sesión' : 'Cambiar de usuario'}">
          <span class="avatar">${iniciales(u.nombre)}</span>
          <span class="quien"><b>${esc(u.nombre)}</b><small>${ROLES[u.rol].nombre} · ${ROLES[u.rol].lado}</small></span>
        </button>
      </header>`;
  }

  function bannerDep() {
    if (MODO === 'hub') return bannerHub();
    if (!DEP || UI.vista === 'desplegar') return '';
    const u = usuarioActual();
    if (!u || !u.tenant || u.tenant !== inst(DEP.instId).tenant) return '';
    const i = inst(DEP.instId);
    const txt = {
      preflight: 'Preflight en curso', variables: 'Preflight esperando variables', confirmar: 'Despliegue listo para confirmar', aprobacion: 'Orden pendiente de aprobación',
      ejecutando: 'Despliegue en curso', fallo: `Verificación fallida · rollback en ${DEP.cuenta} s`, rollback: 'Rollback en curso', ok: 'Despliegue terminado', revertido: 'Rollback completado', retenido: 'Rollback cancelado: instalación en diagnóstico'
    }[DEP.fase] || 'Despliegue';
    const cls = DEP.fase === 'fallo' || DEP.fase === 'retenido' ? 'a-crit' : DEP.fase === 'ok' ? 'a-ok' : 'a-info';
    return `<div style="padding:14px 28px 0"><div class="aviso ${cls}" style="align-items:center">${I.rocket}<div style="flex:1"><b>${txt}</b> · ${prod(i.producto).nombre} ${amb(i.ambiente)} → ${DEP.version}. La ejecución es del agente: podés navegar tranquilo.</div><a class="btn btn-sec btn-chico" href="#desplegar">Ver</a></div></div>`;
  }

  const misMails = () => { const u = usuarioActual(); return u ? S.mails.filter(m => !u.tenant || m.tenant === u.tenant) : []; };

  /* ============================================================
     Login
     ============================================================ */
  function vistaLogin() {
    const L = UI.login;
    const u = S.usuarios.find(x => x.id === L.user);
    const demo = S.usuarios.map(x => `
      <button class="demo-user ${x.id === L.user ? 'sel' : ''}" data-a="login-user" data-id="${x.id}">
        <b>${esc(x.nombre)}</b>
        <small>${ROLES[x.rol].nombre} · ${x.tenant ? esc(tenant(x.tenant).nombre) : 'Accusys'}</small>
      </button>`).join('');
    const form = L.paso === 1 ? `
      <div class="pila">
        <div class="campo"><label for="login-email">Email</label><input id="login-email" type="email" value="${esc(u.email)}" autocomplete="off"></div>
        <div class="campo"><label for="login-pass">Contraseña</label><input id="login-pass" type="password" value="demo-demo-demo"></div>
        <button class="btn btn-pri" data-a="login-1">Ingresar</button>
      </div>` : `
      <div class="pila">
        <div class="campo"><span class="lbl">Código de verificación (TOTP)</span>
          <div class="totp">${[0, 1, 2, 3, 4, 5].map(n => `<input type="text" inputmode="numeric" maxlength="1" id="totp-${n}" data-totp="${n}" aria-label="Dígito ${n + 1}">`).join('')}</div>
          <span class="ayuda">Abrí tu app de autenticación. En este mock sirve cualquier número de 6 dígitos.</span>
        </div>
        <div class="fila"><button class="btn btn-pri" data-a="login-2">Verificar y entrar</button><button class="btn btn-fantasma" data-a="login-autocompletar">Completar código de demo</button></div>
      </div>`;
    return `
      <div class="login">
        ${heroLogin()}
        <section class="login-form">
          <img class="logo-accusys" src="accusys.png" alt="Accusys">
          <div class="pila" style="gap:6px">
            <h2 style="font-size:1.4rem">Ingresar</h2>
            <p class="suave">Login multitenant en <code>auth.accusys.com.ar</code>, con segundo factor.</p>
          </div>
          ${form}
          <div class="pila" style="gap:10px">
            <span class="eyebrow">Usuarios de demo</span>
            <div class="demo-users">${demo}</div>
          </div>
        </section>
      </div>`;
  }

  function heroLogin() {
    return `
        <section class="login-hero">
          <div class="marca"><span class="m">D</span><span class="n">deploy<i>Hub</i></span><span class="sub">CENTRO DE<br>RELEASES</span></div>
          <h1>Releases y despliegue <span>autoservicio</span></h1>
        </section>`;
  }

  /* ============================================================
     Vistas · cliente
     ============================================================ */
  const VISTAS_CLIENTE = {
    instalaciones: { titulo: 'Mis instalaciones', icono: I.server, render: vInstalaciones },
    catalogo: { titulo: 'Catálogo', icono: I.box, render: vCatalogo },
    historial: { titulo: 'Historial', icono: I.clock, render: vHistorial },
    usuarios: { titulo: 'Usuarios y roles', icono: I.users, render: vUsuarios }
  };
  const VISTAS_EXTRA = {
    instalacion: { render: vInstalacion },
    desplegar: { render: vDesplegar }
  };

  function vInstalaciones() {
    const u = usuarioActual(), t = tenant(u.tenant);
    const insts = instsDe(t.id);
    const avisos = [];
    if (t.estado === 'suspendido') avisos.push(`<div class="aviso a-crit">${I.alert}<div><b>Autoservicio suspendido.</b> Tu organización está suspendida: podés ver el catálogo y el historial, pero no desplegar. Volver atrás sigue disponible desde cada instalación.</div></div>`);
    t.productos.forEach(p => {
      const m = mant(p);
      if (m.estado === 'vencido') avisos.push(`<div class="aviso a-warn">${I.alert}<div><b>${prod(p.producto).nombre}: mantenimiento vencido el ${fecha(p.mantenimientoHasta)}.</b> Seguís teniendo derecho a todo lo publicado hasta esa fecha y a los parches críticos de seguridad. Para versiones nuevas, hablá con comercial de Accusys.</div></div>`);
      else if (m.estado === 'por-vencer') avisos.push(`<div class="aviso a-warn">${I.clock}<div><b>${prod(p.producto).nombre}: el mantenimiento vence en ${m.dias} días</b> (${fecha(p.mantenimientoHasta)}). Lo publicado hasta ese día queda habilitado para siempre.</div></div>`);
    });
    const pendientes = insts.filter(i => mejorDisponible(i)).length;
    const cards = insts.map(i => {
      const p = prod(i.producto), ag = agente(i.agente), tpr = tp(i.tenant, i.producto), m = mant(tpr);
      const mejor = mejorDisponible(i);
      let estado;
      if (i.estado === 'retenido') estado = `<span class="pill p-crit">Falló · en diagnóstico</span>`;
      else if (ag.estado !== 'online') estado = `<span class="pill p-crit">Agente fuera de línea</span>`;
      else if (mejor) estado = `<span class="pill p-info">Disponible ${esc(mejor.version)}</span>`;
      else estado = `<span class="pill p-ok">Al día</span>`;
      return `
        <article class="panel inst" data-a="ver-inst" data-id="${i.id}" tabindex="0">
          <div class="inst-cab">
            <span class="tag-prod">${p.codigo}</span>
            <div class="t"><h3>${p.nombre}</h3><div class="amb">${amb(i.ambiente)} · <code>${esc(ag.host)}</code></div></div>
          </div>
          <div class="version-grande"><b>${esc(i.version)}</b>${estado}</div>
          <dl class="datos">
            <dt>Último despliegue</dt><dd class="num">${i.ultimoDeploy.fecha ? `${esc(i.ultimoDeploy.fecha)} · ${esc(i.ultimoDeploy.por)}` : '<span class="suave">sin registro</span>'}</dd>
            <dt>Agente</dt><dd>${ag.estado === 'online' ? `En línea · ${ag.visto}` : `Sin contacto · ${ag.visto}`}</dd>
            <dt>Mantenimiento</dt><dd><span class="pill ${m.pill}">${fecha(tpr.mantenimientoHasta)}</span></dd>
          </dl>
          <div class="inst-pie">
            <span class="suave chico">${i.puntoRetorno ? `Punto de retorno: ${esc(i.puntoRetorno.version)}` : 'Sin punto de retorno'}</span>
            <button class="btn btn-sec btn-chico" data-a="ir-catalogo" data-id="${i.id}">Ver versiones</button>
          </div>
        </article>`;
    }).join('');
    const ok = insts.filter(i => !mejorDisponible(i)).length;
    return `
      <div class="cabecera"><div class="t">
        <span class="eyebrow">Mis instalaciones</span>
        <h1>${esc(t.nombre)}</h1>
        <p>${insts.length} instalaciones de ${t.productos.length} productos. ${pendientes ? `${pendientes} con versión nueva habilitada.` : 'Todo al día.'}</p>
      </div></div>
      ${avisos.join('')}
      <div class="grid g4">
        <div class="panel kpi"><span class="l">Instalaciones</span><span class="v">${insts.length}</span><span class="d">${new Set(insts.map(i => i.agente)).size} servidores con agente</span></div>
        <div class="panel kpi"><span class="l">Al día</span><span class="v">${ok}</span><span class="d">sin versión nueva habilitada</span></div>
        <div class="panel kpi"><span class="l">Para actualizar</span><span class="v">${pendientes}</span><span class="d">con preflight disponible</span></div>
        <div class="panel kpi"><span class="l">Último despliegue</span><span class="v" style="font-size:1.15rem;padding-top:6px">${fecha(insts.map(i => i.ultimoDeploy.fecha).sort().slice(-1)[0])}</span><span class="d">según el historial</span></div>
      </div>
      <div class="grid g3">${cards}</div>`;
  }

  function vInstalacion() {
    const i = inst(UI.inst);
    const p = prod(i.producto), ag = agente(i.agente), r = rel(i.producto, i.version), tpr = tp(i.tenant, i.producto), m = mant(tpr);
    const vecinos = S.instalaciones.filter(x => x.agente === i.agente && x.id !== i.id);
    const servicios = !r ? '<tr><td colspan="3" class="suave">La versión instalada no está en el catálogo del hub.</td></tr>' : Object.entries(r.imagenes).map(([k, d]) => {
      const falla = i.estado === 'retenido' && k === Object.keys(r.imagenes).slice(-1)[0];
      return `<tr><td><b>${p.id}-${k}</b></td><td><code>registry.accusys.com.ar/${p.id}/${k}@${esc(d)}</code></td><td>${falla ? '<span class="pill p-crit">unhealthy</span>' : '<span class="pill p-ok">healthy</span>'}</td></tr>`;
    }).join('');
    const hist = S.historial.filter(h => h.tenant === i.tenant && h.producto === i.producto && h.ambiente === i.ambiente);
    const vars = Object.keys(i.variables);
    const pe = puedeEjecutar();
    return `
      <div class="migas"><a href="#instalaciones">Mis instalaciones</a><span>/</span><span>${p.nombre} · ${amb(i.ambiente)}</span></div>
      <div class="cabecera">
        <div class="t"><span class="eyebrow">${p.desc}</span><h1>${p.nombre} ${esc(i.version)} · ${amb(i.ambiente)}</h1>
        <p>Stack en <code>/opt/accusys/${p.id}</code> sobre <code>${esc(ag.host)}</code>. ${vecinos.length ? `El mismo agente atiende también ${vecinos.map(v => prod(v.producto).nombre).join(', ')}; cada stack tiene su propio punto de retorno.` : ''}</p></div>
        <div class="fila">
          ${i.puntoRetorno ? `<button class="btn btn-sec" data-a="pedir-rollback" data-id="${i.id}" ${pe.ok ? '' : 'disabled title="' + esc(pe.motivo) + '"'}>${I.undo} Volver a ${esc(i.puntoRetorno.version)}</button>` : ''}
          <button class="btn btn-pri" data-a="ir-catalogo" data-id="${i.id}">Ver versiones</button>
        </div>
      </div>
      ${i.estado === 'retenido' ? `<div class="aviso a-crit">${I.alert}<div><b>El último despliegue falló y el rollback se canceló para diagnosticar.</b> La instalación quedó como estaba. Podés volver a ${esc(i.puntoRetorno ? i.puntoRetorno.version : 'la versión anterior')} cuando quieras.</div></div>` : ''}
      <div class="grid g3">
        <div class="panel kpi"><span class="l">Agente</span><span class="v" style="font-size:1.15rem;padding-top:6px">${ag.estado === 'online' ? '<span class="pill p-ok">En línea</span>' : '<span class="pill p-crit">Fuera de línea</span>'}</span><span class="d">deploy-agent ${ag.version} · último contacto ${ag.visto}</span></div>
        <div class="panel kpi"><span class="l">Mantenimiento</span><span class="v" style="font-size:1.15rem;padding-top:6px"><span class="pill ${m.pill}">${m.txt}</span></span><span class="d">hasta el ${fecha(tpr.mantenimientoHasta)} · canal ${tpr.canal}</span></div>
        <div class="panel kpi"><span class="l">Punto de retorno</span><span class="v" style="font-size:1.15rem;padding-top:6px">${i.puntoRetorno ? esc(i.puntoRetorno.version) : '—'}</span><span class="d">${i.puntoRetorno ? 'compose + digests guardados' + (i.puntoRetorno.fecha ? ' el ' + esc(i.puntoRetorno.fecha) : '') : 'la versión instalada trajo migración'}</span></div>
      </div>
      <div class="grid g2">
        <section class="panel">
          <div class="panel-cab"><h2>Servicios</h2><span class="suave chico">healthchecks declarados en el manifiesto</span></div>
          <div class="tabla-wrap"><table><thead><tr><th>Servicio</th><th>Imagen (digest)</th><th>Estado</th></tr></thead><tbody>${servicios}</tbody></table></div>
        </section>
        <section class="panel">
          <div class="panel-cab"><h2>Variables de entorno</h2><span class="suave chico"><code>/opt/accusys/${p.id}/.env</code></span></div>
          <div class="panel-cuerpo pila">
            <p class="suave chico">Los valores viven en el servidor. El hub solo conoce los nombres que declara cada manifiesto.</p>
            ${vars.length ? `<dl class="datos">${vars.map(v => `<dt><code>${esc(v)}</code></dt><dd>definida en el servidor · <code>••••</code></dd>`).join('')}</dl>` : '<p class="suave chico">Ninguna variable declarada por releases recientes.</p>'}
          </div>
        </section>
      </div>
      <section class="panel">
        <div class="panel-cab"><h2>Historial de esta instalación</h2></div>
        ${tablaHistorial(hist, false)}
      </section>`;
  }

  function vCatalogo() {
    const u = usuarioActual(), t = tenant(u.tenant);
    if (!t.productos.length) return `<div class="cabecera"><div class="t"><span class="eyebrow">Catálogo</span><h1>Versiones de tus productos</h1></div></div><div class="panel vacio">Tu organización todavía no tiene productos cargados. Los carga Comercial de Accusys.</div>`;
    if (!UI.prod || !tp(t.id, UI.prod)) UI.prod = t.productos[0].producto;
    const insts = instsDe(t.id).filter(i => i.producto === UI.prod);
    if (!UI.inst || !insts.find(i => i.id === UI.inst)) UI.inst = insts[0] && insts[0].id;
    const i = inst(UI.inst), tpr = tp(t.id, UI.prod), m = mant(tpr), p = prod(UI.prod);
    const tabs = t.productos.map(x => `<button class="tab ${x.producto === UI.prod ? 'activo' : ''}" data-a="cat-prod" data-id="${x.producto}">${prod(x.producto).nombre}</button>`).join('');
    if (!i) return `<div class="cabecera"><div class="t"><span class="eyebrow">Catálogo</span><h1>Versiones de tus productos</h1></div></div><div class="tabs">${tabs}</div><div class="panel vacio">${p.nombre} está adquirido pero todavía no tiene una instalación con agente. El alta inicial la hace Accusys.</div>`;
    const pe = puedeEjecutar();
    const lista = relsVisibles(i).slice().reverse().map(r => {
      const ev = evaluar(i, r);
      const badges = [];
      if (ev.estado === 'instalada') badges.push('<span class="pill p-ok">Instalada</span>');
      if (r.canal === 'anticipado') badges.push('<span class="pill p-vio">Canal anticipado</span>');
      if (r.critico) badges.push('<span class="pill p-crit">Crítico de seguridad</span>');
      if (r.db) badges.push('<span class="pill p-warn">Migración de base</span>');
      if (r.variables.length) badges.push(`<span class="pill p-info">${r.variables.length} variable nueva</span>`);
      let accion = '';
      if (ev.estado === 'habilitado') accion = `<button class="btn btn-pri" data-a="iniciar-deploy" data-v="${r.version}" ${pe.ok ? '' : 'disabled title="' + esc(pe.motivo) + '"'}>${I.rocket} Correr preflight</button>`;
      else if (ev.estado === 'asistido') accion = `<button class="btn btn-sec" data-a="coordinar" data-v="${r.version}">Coordinar con Accusys</button>`;
      else if (ev.estado === 'retorno') accion = `<button class="btn btn-sec" data-a="pedir-rollback" data-id="${i.id}" ${pe.ok ? '' : 'disabled title="' + esc(pe.motivo) + '"'}>${I.undo} Volver a esta versión</button>`;
      else if (ev.estado === 'bloqueado') accion = `<span class="pill p-neutro">No habilitada</span>`;
      return `
        <div class="release ${ev.estado === 'instalada' ? 'instalada' : ''}">
          <div class="ver"><b>${esc(r.version)}</b><small class="num">${fecha(r.publicado)}</small></div>
          <div>
            <div class="fila" style="gap:6px">${badges.join('')}</div>
            <ul>${r.changelog.map(c => `<li>${esc(c)}</li>`).join('')}</ul>
            <div class="fila chico suave" style="margin-top:8px;gap:14px"><span>desde <code>${esc(r.desde)}</code></span><span>rollback ${r.rollbackSeguro ? 'seguro' : 'no automático'}</span><button class="btn btn-fantasma btn-chico" data-a="ver-manifiesto" data-p="${r.producto || UI.prod}" data-v="${r.version}">Ver manifiesto</button></div>
          </div>
          <div class="acciones">${accion}<span class="motivo">${esc(ev.motivo)}</span></div>
        </div>`;
    }).join('');
    const selInst = insts.length > 1 ? `
      <div class="campo" style="min-width:240px"><label for="cat-inst">Instalación</label>
        <select id="cat-inst" data-f="cat-inst">${insts.map(x => `<option value="${x.id}" ${x.id === UI.inst ? 'selected' : ''}>${amb(x.ambiente)} · ${esc(x.version)} · ${esc(agente(x.agente).host)}</option>`).join('')}</select>
      </div>` : '';
    return `
      <div class="cabecera"><div class="t">
        <span class="eyebrow">Catálogo</span><h1>Versiones de tus productos</h1>
        <p>Ves solo los productos que tu organización adquirió. Se habilita todo lo publicado hasta el fin de tu mantenimiento, comparando por fecha de publicación.</p>
      </div></div>
      <div class="tabs" role="tablist">${tabs}</div>
      <div class="grid g3" style="align-items:end">
        <div class="panel kpi"><span class="l">${p.nombre} instalada</span><span class="v">${esc(i.version)}</span><span class="d">${amb(i.ambiente)} · ${esc(agente(i.agente).host)}</span></div>
        <div class="panel kpi"><span class="l">Mantenimiento</span><span class="v" style="font-size:1.15rem;padding-top:6px"><span class="pill ${m.pill}">${m.txt}</span></span><span class="d">hasta el ${fecha(tpr.mantenimientoHasta)} · autoservicio ${tpr.autoservicio ? 'habilitado' : 'deshabilitado'}</span></div>
        ${selInst || `<div class="panel kpi"><span class="l">Canal</span><span class="v" style="font-size:1.15rem;padding-top:6px">${tpr.canal === 'anticipado' ? 'Anticipado' : 'Estable'}</span><span class="d">${tpr.canal === 'anticipado' ? 'recibís release candidates' : 'solo versiones estables'}</span></div>`}
      </div>
      <section class="panel">${lista}</section>`;
  }

  function vHistorial() {
    const u = usuarioActual();
    const hist = S.historial.filter(h => h.tenant === u.tenant);
    return `
      <div class="cabecera"><div class="t"><span class="eyebrow">Historial y auditoría</span><h1>Quién desplegó qué, y cómo terminó</h1>
      <p>Registro no editable. Cada fila guarda versión de origen y destino, resultado, duración y los logs que subió el agente.</p></div></div>
      <section class="panel">${tablaHistorial(hist, true)}</section>`;
  }

  function tablaHistorial(hist, conProducto) {
    if (!hist.length) return '<div class="vacio">Todavía no hay operaciones registradas.</div>';
    const res = { ok: '<span class="pill p-ok">Verificado</span>', rollback: '<span class="pill p-warn">Rollback automático</span>', manual: '<span class="pill p-info">Rollback manual</span>', retenido: '<span class="pill p-crit">Falló · retenido</span>', error: '<span class="pill p-crit">Falló</span>', en_curso: '<span class="pill p-info">En curso</span>', cancelada: '<span class="pill p-neutro">Retirada</span>' };
    return `<div class="tabla-wrap"><table><thead><tr><th>Fecha</th>${conProducto ? '<th>Producto</th>' : ''}<th>Operación</th><th>Versión</th><th>Resultado</th><th>Por</th><th>Duración</th></tr></thead><tbody>
      ${hist.map(h => `<tr class="click" data-a="ver-log" data-id="${h.id}">
        <td class="num">${esc(h.fecha)}</td>
        ${conProducto ? `<td><span class="tag-prod">${prod(h.producto).codigo}</span> <span class="suave chico">${amb(h.ambiente)}</span></td>` : ''}
        <td>${esc(h.tipo)}</td><td class="num"><code>${esc(h.desde)} → ${esc(h.hacia)}</code></td>
        <td>${res[h.resultado] || esc(h.resultado)}</td><td>${esc(h.por)}</td><td class="num">${esc(h.duracion)}</td></tr>`).join('')}
    </tbody></table></div>`;
  }

  function vUsuarios() {
    const u = usuarioActual(), t = tenant(u.tenant);
    const us = S.usuarios.filter(x => x.tenant === t.id);
    const admin = u.rol === 'aprobador';
    return `
      <div class="cabecera"><div class="t"><span class="eyebrow">Usuarios y roles</span><h1>Quién puede hacer qué en ${esc(t.nombre)}</h1>
      <p>El despliegue lo opera tu organización. Los roles aplican solo dentro de tu tenant.</p></div>
      <button class="btn btn-pri" data-a="invitar" ${admin ? '' : 'disabled title="Solo un Aprobador administra usuarios"'}>${I.plus} ${MODO === 'hub' ? 'Dar de alta' : 'Invitar usuario'}</button></div>
      <section class="panel"><div class="tabla-wrap"><table><thead><tr><th>Usuario</th><th>Rol</th><th>Segundo factor</th><th></th></tr></thead><tbody>
        ${us.map(x => `<tr><td><b>${esc(x.nombre)}</b><span class="sub">${esc(x.email)}</span></td>
          <td>${admin && x.id !== u.id ? `<select data-f="rol-usuario" data-id="${x.id}" aria-label="Rol de ${esc(x.nombre)}">${['lector', 'operador', 'aprobador'].map(r => `<option value="${r}" ${x.rol === r ? 'selected' : ''}>${ROLES[r].nombre}</option>`).join('')}</select>` : `<span class="pill p-info sin-punto">${ROLES[x.rol].nombre}</span>`}</td>
          <td><span class="pill p-ok">${MODO === 'hub' ? 'TOTP obligatorio' : 'TOTP activo'}</span></td><td class="suave chico">${x.id === u.id ? 'Sos vos' : ''}</td></tr>`).join('')}
      </tbody></table></div></section>
      <div class="grid g3">
        <div class="panel panel-cuerpo pila"><h3>Lector</h3><p class="suave chico">Ve catálogo, estado, historial y changelog. No ejecuta nada.</p></div>
        <div class="panel panel-cuerpo pila"><h3>Operador</h3><p class="suave chico">Corre el preflight, carga variables, despliega y puede cancelar un rollback en curso.</p></div>
        <div class="panel panel-cuerpo pila"><h3>Aprobador</h3><p class="suave chico">Habilita órdenes cuando hay doble aprobación y administra usuarios. No ejecuta despliegues.</p></div>
      </div>
      ${MODO === 'hub' ? panelHabilitaciones(t, admin) : `<div class="aviso a-info">${I.shield}<div><b>Doble aprobación: ${t.dobleAprobacion ? 'activada' : 'desactivada'}.</b> ${t.dobleAprobacion ? 'Cada orden queda pendiente hasta que la autorice un segundo usuario con rol Aprobador.' : 'Un Operador puede ejecutar sin una segunda firma. Lo configura Accusys a pedido de tu organización.'}</div></div>`}`;
  }

  /* ============================================================
     Flujo de despliegue
     ============================================================ */
  const PASOS = ['Preflight', 'Variables', 'Confirmación', 'Despliegue', 'Verificación', 'Resultado'];
  function pasoIdx(f) { return { preflight: 0, variables: 1, confirmar: 2, aprobacion: 2, ejecutando: 3, fallo: 4, rollback: 4, ok: 5, revertido: 5, retenido: 5 }[f]; }

  function iniciarDeploy(instId, version) {
    if (DEP && !['ok', 'revertido', 'retenido'].includes(DEP.fase)) { toast('Ya hay una operación en curso sobre una instalación. El agente toma un lock por instalación.', true); UI.vista = 'desplegar'; location.hash = 'desplegar'; render(); return; }
    const i = inst(instId), r = rel(i.producto, version);
    DEP = { instId, version, desde: i.version, fase: 'preflight', checks: [], vars: {}, simularFallo: false, logs: [], cuenta: 60, pasoEj: [], inicio: Date.now(), orden: 'ord-' + Math.random().toString(36).slice(2, 7) };
    location.hash = 'desplegar'; UI.vista = 'desplegar';
    correrPreflight(i, r);
  }

  function correrPreflight(i, r) {
    const ag = agente(i.agente);
    const faltan = r.variables.filter(v => v.obligatoria && !(v.nombre in i.variables) && !(v.nombre in DEP.vars));
    const libre = Math.round(ag.disco * 0.6 * 10) / 10;
    DEP.fase = 'preflight';
    DEP.checks = [
      { t: 'Firma del manifiesto', d: 'cosign · clave de publicación de Accusys', res: 'ok', det: 'Firma válida' },
      { t: 'Ruta de upgrade', d: `desde ${i.version} hacia ${r.version} (${r.desde})`, res: satisface(i.version, r.desde) ? 'ok' : 'fallo', det: 'Soportada' },
      { t: 'Variables de entorno', d: r.variables.length ? r.variables.map(v => v.nombre).join(', ') : 'el manifiesto no declara variables nuevas', res: faltan.length ? 'fallo' : 'ok', det: faltan.length ? `Falta ${faltan.length}` : 'Completas' },
      { t: 'Espacio en disco', d: `necesita 1,8 GB · libres ${libre} GB en /var/lib/docker`, res: 'ok', det: 'Suficiente' },
      { t: 'Versión del agente', d: `deploy-agent ${ag.version} compatible con el manifiesto`, res: 'ok', det: 'Compatible' },
      { t: 'Descarga anticipada de imágenes', d: Object.keys(r.imagenes).map(k => `${k}@${r.imagenes[k]}`).join(' · '), res: 'ok', det: 'Digests verificados', barra: true }
    ].map(c => Object.assign(c, { estado: 'esperando', prog: 0 }));
    render();
    let k = 0;
    const siguiente = () => {
      if (!DEP) return;
      if (k >= DEP.checks.length) {
        DEP.fase = DEP.checks.some(c => c.estado === 'fallo') ? 'variables' : 'confirmar';
        return refrescarDep();
      }
      const c = DEP.checks[k];
      // si una variable falta, no descargamos: el preflight corta ahí sin tocar nada
      if (c.barra && DEP.checks.some(x => x.estado === 'fallo')) { c.estado = 'esperando'; c.det = 'Pendiente'; k = DEP.checks.length; return siguiente(); }
      c.estado = 'corriendo'; refrescarDep();
      if (c.barra) {
        const tick = setInterval(() => {
          if (!DEP) return clearInterval(tick);
          c.prog = Math.min(100, c.prog + 9 + Math.random() * 12);
          if (c.prog >= 100) { clearInterval(tick); c.estado = c.res; k++; setTimeout(siguiente, 250); }
          refrescarDep();
        }, 180);
      } else {
        setTimeout(() => { if (!DEP) return; c.estado = c.res; k++; refrescarDep(); setTimeout(siguiente, 120); }, 420 + Math.random() * 300);
      }
    };
    setTimeout(siguiente, 300);
  }

  function refrescarDep() {
    if (UI.vista === 'desplegar' && !UI.modal && !UI.drawer) render();
    else { const b = $('#banner-dep'); if (b) b.innerHTML = bannerDep(); }
  }

  function vDesplegar() {
    if (MODO === 'hub') return vOrdenHub();
    if (!DEP) return `<div class="vacio">No hay ningún despliegue en curso. Elegí una versión desde el <a href="#catalogo">catálogo</a>.</div>`;
    const i = inst(DEP.instId), p = prod(i.producto), r = rel(i.producto, DEP.version), t = tenant(i.tenant), ag = agente(i.agente);
    const pi = pasoIdx(DEP.fase);
    const falloFase = ['fallo', 'rollback', 'revertido', 'retenido'].includes(DEP.fase);
    const stepper = PASOS.map((n, k) => {
      let c = k < pi ? 'hecho' : k === pi ? 'actual' : '';
      if (falloFase && k === 4) c = 'fallo';
      if (k === 1 && !r.variables.length && pi > 1) c = 'hecho';
      if (DEP.fase === 'ok' && k === 5) c = 'hecho';
      return `<div class="paso ${c}"><span class="n">${c === 'hecho' ? '✓' : c === 'fallo' ? '!' : k + 1}</span>${n}</div>`;
    }).join('');
    const checks = DEP.checks.map(c => `
      <div class="chk">
        <span class="ic ${c.estado}">${c.estado === 'ok' ? '✓' : c.estado === 'fallo' ? '✕' : c.estado === 'corriendo' ? '' : '·'}</span>
        <div><b>${esc(c.t)}</b><small>${esc(c.d)}</small></div>
        <div>${c.barra && c.estado === 'corriendo' ? `<div class="barra"><i style="width:${c.prog}%"></i></div>` : c.estado === 'ok' ? `<span class="pill p-ok">${esc(c.det)}</span>` : c.estado === 'fallo' ? `<span class="pill p-crit">${esc(c.det)}</span>` : c.estado === 'corriendo' ? '<span class="suave chico">verificando…</span>' : `<span class="suave chico">${c.det === 'Pendiente' ? 'no corre hasta completar variables' : 'en espera'}</span>`}</div>
      </div>`).join('');

    let accion = '';
    if (DEP.fase === 'variables') {
      const faltan = r.variables.filter(v => !(v.nombre in i.variables));
      accion = `
        <section class="panel">
          <div class="panel-cab"><div><h2>Completá las variables nuevas</h2><p>El preflight cortó antes de tocar nada. Los valores se escriben en <code>/opt/accusys/${p.id}/.env</code> del servidor y nunca se envían al hub.</p></div></div>
          <div class="panel-cuerpo pila">
            ${faltan.map(v => `<div class="campo"><label for="var-${v.nombre}"><code>${esc(v.nombre)}</code>${v.obligatoria ? ' · obligatoria' : ''}</label>
              <input id="var-${v.nombre}" type="text" data-var="${v.nombre}" value="${esc(DEP.vars[v.nombre] != null ? DEP.vars[v.nombre] : '')}" placeholder="${esc(v.def)}">
              <span class="ayuda">${esc(v.descripcion)}. Valor por defecto: <code>${esc(v.def)}</code>.</span></div>`).join('')}
            <div class="fila"><button class="btn btn-pri" data-a="guardar-vars">Guardar en el servidor y repetir preflight</button><button class="btn btn-fantasma" data-a="usar-defaults">Usar valores por defecto</button></div>
          </div>
        </section>`;
    } else if (DEP.fase === 'confirmar' || DEP.fase === 'aprobacion') {
      const aprobador = S.usuarios.find(x => x.tenant === t.id && x.rol === 'aprobador');
      accion = `
        <section class="panel">
          <div class="panel-cab"><div><h2>Confirmá el despliegue</h2><p>Todo lo lento ya pasó: las imágenes están descargadas y verificadas contra su digest.</p></div></div>
          <div class="panel-cuerpo pila">
            <dl class="datos">
              <dt>Instalación</dt><dd>${p.nombre} · ${amb(i.ambiente)} · <code>${esc(ag.host)}</code></dd>
              <dt>Versión</dt><dd class="num"><code>${esc(i.version)} → ${esc(r.version)}</code></dd>
              <dt>Punto de retorno</dt><dd>compose vigente + digests actuales, antes de tocar nada</dd>
              <dt>Verificación</dt><dd>healthcheck HTTP de ${Object.keys(r.imagenes).join(', ')} · timeout 120 s · smoke tests</dd>
              <dt>Si falla</dt><dd>${r.rollbackSeguro ? 'aviso + cuenta regresiva de 60 s + rollback automático' : 'corta y escala a la guardia de Accusys (rollback no seguro)'}</dd>
            </dl>
            <div class="campo"><span class="lbl">Ventana</span>
              <div class="fila"><label class="check"><input type="radio" name="ventana" checked> Ahora, en horario laboral</label><label class="check suave"><input type="radio" name="ventana" disabled> Programar fecha y hora <span class="pill p-neutro sin-punto">v2</span></label></div>
            </div>
            <label class="check"><input type="checkbox" id="simular-fallo" data-f="simular" ${DEP.simularFallo ? 'checked' : ''}> Simular que falla la verificación (para ver el rollback)</label>
            ${DEP.fase === 'aprobacion' ? `
              <div class="aviso a-warn">${I.users}<div><b>Orden pendiente de aprobación.</b> ${esc(t.nombre)} exige doble aprobación. Queda en espera hasta que la autorice ${aprobador ? esc(aprobador.nombre) : 'un Aprobador'}; no puede aprobarla quien la pidió.</div></div>
              <div class="fila"><button class="btn btn-pri" data-a="aprobar">${I.check} Aprobar como ${aprobador ? esc(aprobador.nombre) : 'Aprobador'} (demo)</button><button class="btn btn-sec" data-a="cancelar-dep">Retirar la orden</button></div>` : `
              <div class="fila">
                ${t.dobleAprobacion ? `<button class="btn btn-pri" data-a="pedir-aprobacion">Enviar a aprobación</button>` : `<button class="btn btn-pri" data-a="ejecutar">${I.rocket} Desplegar ${esc(r.version)} ahora</button>`}
                <button class="btn btn-sec" data-a="cancelar-dep">Cancelar</button>
              </div>`}
          </div>
        </section>`;
    }

    let ejec = '';
    if (pi >= 3) {
      const pasos = DEP.pasoEj.map(x => `<div class="chk"><span class="ic ${x.estado}">${x.estado === 'ok' ? '✓' : x.estado === 'fallo' ? '✕' : x.estado === 'corriendo' ? '' : '·'}</span><div><b>${esc(x.t)}</b><small>${esc(x.d)}</small></div><span></span></div>`).join('');
      let banner = '';
      if (DEP.fase === 'fallo') {
        const circ = 2 * Math.PI * 32, off = circ * (1 - DEP.cuenta / 60);
        banner = `
          <div class="cuenta-regresiva">
            <div class="reloj"><svg viewBox="0 0 74 74"><circle cx="37" cy="37" r="32" fill="none" stroke="var(--linea)" stroke-width="6"/><circle cx="37" cy="37" r="32" fill="none" stroke="var(--rojo)" stroke-width="6" stroke-linecap="round" stroke-dasharray="${circ}" stroke-dashoffset="${off}"/></svg><span>${DEP.cuenta}</span></div>
            <div class="pila" style="gap:8px;flex:1">
              <div><b>La verificación falló.</b> El healthcheck de <code>${esc(DEP.servicioFallo)}</code> no respondió 200 dentro del timeout. Ya salió el mail a tu organización y a la guardia de Accusys. Si nadie lo cancela, el agente restaura ${esc(DEP.desde)} con los digests guardados.</div>
              <div class="fila"><button class="btn btn-peligro" data-a="revertir-ya">${I.undo} Revertir ahora</button><button class="btn btn-sec" data-a="cancelar-rollback">Cancelar rollback y dejar para diagnóstico</button></div>
            </div>
          </div>`;
      } else if (DEP.fase === 'ok') {
        banner = `<div class="resultado a-ok aviso" style="padding:18px"><span class="big" style="background:var(--verde)">✓</span><div class="pila" style="gap:6px"><b style="font-size:1.05rem">${p.nombre} ${esc(r.version)} desplegada y verificada.</b><span class="suave chico">Resultado y logs subidos al hub. Salió el mail de cierre a tu organización y a Accusys. El punto de retorno a ${esc(DEP.desde)} queda disponible.</span><div class="fila"><button class="btn btn-sec btn-chico" data-a="ver-inst" data-id="${i.id}">Ver la instalación</button><button class="btn btn-fantasma btn-chico" data-a="cerrar-dep">Cerrar</button></div></div></div>`;
      } else if (DEP.fase === 'revertido') {
        banner = `<div class="resultado aviso a-warn" style="padding:18px"><span class="big" style="background:var(--naranja)">↩</span><div class="pila" style="gap:6px"><b style="font-size:1.05rem">Rollback completado: ${p.nombre} volvió a ${esc(DEP.desde)}.</b><span class="suave chico">Mismo binario que antes, restaurado por digest. Los logs del intento quedan en el historial para que Accusys los revise.</span><div class="fila"><button class="btn btn-sec btn-chico" data-a="ver-inst" data-id="${i.id}">Ver la instalación</button><button class="btn btn-fantasma btn-chico" data-a="cerrar-dep">Cerrar</button></div></div></div>`;
      } else if (DEP.fase === 'retenido') {
        banner = `<div class="resultado aviso a-crit" style="padding:18px"><span class="big" style="background:var(--rojo)">!</span><div class="pila" style="gap:6px"><b style="font-size:1.05rem">Rollback cancelado. La instalación quedó con ${esc(r.version)} y el fallo a la vista.</b><span class="suave chico">Diagnosticá con los logs. Volver a ${esc(DEP.desde)} sigue disponible en cualquier momento.</span><div class="fila"><button class="btn btn-peligro btn-chico" data-a="revertir-ya">${I.undo} Revertir ahora</button><button class="btn btn-fantasma btn-chico" data-a="cerrar-dep">Cerrar</button></div></div></div>`;
      }
      ejec = `
        ${banner}
        <div class="grid g2">
          <section class="panel"><div class="panel-cab"><h2>Ejecución en el agente</h2><span class="suave chico">orden <code>${DEP.orden}</code></span></div><div class="panel-cuerpo checks">${pasos}</div></section>
          <section class="panel"><div class="panel-cab"><h2>Logs en vivo</h2><span class="suave chico">deploy-agent · ${esc(ag.host)}</span></div><div class="panel-cuerpo"><div class="consola" role="log">${DEP.logs.map(l => `<span class="ts">[${l.h}]</span> <span class="${l.c || ''}">${esc(l.t)}</span>`).join('\n')}</div></div></section>
        </div>`;
    }

    if (DEP.manual) return `
      <div class="migas"><a href="#instalaciones">Mis instalaciones</a><span>/</span><span>${p.nombre} · ${amb(i.ambiente)}</span></div>
      <div class="cabecera"><div class="t"><span class="eyebrow">Rollback manual</span><h1>${p.nombre} ${esc(DEP.version)} → ${esc(DEP.desde)}</h1>
        <p>${amb(i.ambiente)} en <code>${esc(ag.host)}</code>. Se restaura el punto de retorno registrado, identificado por digest.</p></div></div>
      ${ejec}`;
    return `
      <div class="migas"><a href="#catalogo">Catálogo</a><span>/</span><span>${p.nombre} ${esc(r.version)}</span></div>
      <div class="cabecera"><div class="t"><span class="eyebrow">Despliegue autoservicio</span><h1>${p.nombre} ${esc(DEP.desde)} → ${esc(r.version)}</h1>
        <p>${amb(i.ambiente)} en <code>${esc(ag.host)}</code>. La ejecución es del agente, no del navegador: si cerrás esta pestaña, sigue igual.</p></div></div>
      <div class="panel panel-cuerpo"><div class="stepper">${stepper}</div></div>
      ${pi <= 2 ? `<div class="grid g2" style="align-items:start">
        <section class="panel"><div class="panel-cab"><div><h2>Preflight</h2><p>Sin tocar el sistema en funcionamiento.</p></div>${DEP.fase === 'preflight' ? '<span class="pill p-info">Corriendo</span>' : DEP.fase === 'variables' ? '<span class="pill p-crit">Falló</span>' : '<span class="pill p-ok">Aprobado</span>'}</div><div class="panel-cuerpo checks">${checks}</div></section>
        <div class="pila">${accion || `<div class="panel panel-cuerpo suave">Esperando el resultado del preflight…</div>`}
          <section class="panel"><div class="panel-cab"><h2>Qué cambia en ${esc(r.version)}</h2></div><div class="panel-cuerpo"><ul style="margin:0;padding-left:18px">${r.changelog.map(c => `<li>${esc(c)}</li>`).join('')}</ul></div></section>
        </div>
      </div>` : ejec}`;
  }

  function log(t, c) { DEP.logs.push({ h: hora(), t, c }); }
  function correrScript(pasos, fin) {
    let k = 0;
    const dueño = DEP;
    const sig = () => {
      if (!DEP || DEP !== dueño) return;
      if (k >= pasos.length) return fin && fin();
      const s = pasos[k++];
      setTimeout(() => { if (DEP !== dueño) return; s.fn && s.fn(); if (s.t) log(s.t, s.c); refrescarDep(); sig(); }, s.ms || 450);
    };
    sig();
  }

  function ejecutar() {
    const i = inst(DEP.instId), p = prod(i.producto), r = rel(i.producto, DEP.version), ag = agente(i.agente);
    const prev = rel(i.producto, i.version);
    const servicios = Object.keys(r.imagenes);
    const fallo = DEP.simularFallo;
    DEP.servicioFallo = servicios[servicios.length - 1];
    DEP.fase = 'ejecutando';
    DEP.pasoEj = [
      { t: 'Lock de instalación', d: `/opt/accusys/${p.id}`, estado: 'esperando' },
      { t: 'Punto de retorno', d: `docker-compose.yml + digests de ${i.version}`, estado: 'esperando' },
      { t: 'Despliegue', d: 'docker compose up -d con las imágenes nuevas', estado: 'esperando' },
      { t: 'Verificación', d: 'healthchecks y smoke tests del manifiesto', estado: 'esperando' },
      { t: 'Cierre', d: 'resultado y logs al hub, mail a las dos partes', estado: 'esperando' }
    ];
    const P = DEP.pasoEj;
    mail(i.tenant, `Inicio de despliegue · ${p.nombre} ${r.version} en ${amb(i.ambiente)}`, `Operador: ${usuarioActual().nombre}.`);
    render();
    const script = [
      { fn: () => P[0].estado = 'corriendo', t: `orden ${DEP.orden} recibida por deploy-agent ${ag.version} (${ag.host})`, c: 'info', ms: 300 },
      { t: 'firma del manifiesto verificada · cosign · clave de publicación Accusys', c: 'ok' },
      { fn: () => { P[0].estado = 'ok'; P[1].estado = 'corriendo'; }, t: `lock tomado sobre /opt/accusys/${p.id}` },
      { t: `punto de retorno: docker-compose.yml → .deploycenter/retorno/${i.version}/` },
      { fn: () => { P[1].estado = 'ok'; P[2].estado = 'corriendo'; }, t: 'digests guardados: ' + Object.entries(prev.imagenes).map(([k, d]) => `${k}@${d}`).join(' '), c: 'ok' },
      { t: `compose generado desde plantilla ${p.id}-${r.version} + .env del cliente` },
      { t: 'docker compose up -d', c: 'info' },
      ...servicios.map(s => ({ t: ` ✔ Container ${p.id}-${s}  Recreated`, ms: 380 })),
      { fn: () => { P[2].estado = 'ok'; P[3].estado = 'corriendo'; }, t: 'esperando healthchecks (timeout 120 s)' },
      ...servicios.map((s, n) => (fallo && n === servicios.length - 1)
        ? { t: `${s}  GET http://${s}:8080/health → 503 Service Unavailable (reintentando)`, c: 'warn', ms: 900 }
        : { t: `${s}  GET http://${s}:8080/health → 200 (${(1.8 + n * 1.3).toFixed(1)} s)`, c: 'ok', ms: 700 })
    ];
    correrScript(script, () => {
      if (fallo) {
        log(`${DEP.servicioFallo}  healthcheck sin 200 dentro del timeout: verificación FALLIDA`, 'err');
        log(r.rollbackSeguro ? 'rollback automático programado en 60 s · cancelable desde la web' : 'rollback_seguro=false: se corta y se escala a la guardia', 'warn');
        P[3].estado = 'fallo';
        mail(i.tenant, `Fallo de verificación · ${p.nombre} ${r.version}`, `El healthcheck de ${DEP.servicioFallo} no respondió. Rollback automático en 60 s.`, 'guardia@accusys.example');
        DEP.fase = 'fallo'; DEP.cuenta = 60;
        refrescarDep();
        DEP.timer = setInterval(() => {
          if (!DEP || DEP.fase !== 'fallo') return clearInterval(DEP && DEP.timer);
          DEP.cuenta--;
          if (DEP.cuenta <= 0) { clearInterval(DEP.timer); rollback('auto'); } else refrescarDep();
        }, 1000);
        return;
      }
      correrScript([
        { t: 'smoke tests: 4/4 ok', c: 'ok' },
        { fn: () => { P[3].estado = 'ok'; P[4].estado = 'corriendo'; }, t: 'despliegue verificado', c: 'ok' },
        { fn: () => P[4].estado = 'ok', t: 'resultado y logs subidos a deploy.accusys.com.ar · lock liberado' }
      ], () => {
        cerrarOk(i, r);
      });
    });
  }

  function cerrarOk(i, r) {
    const p = prod(i.producto);
    const desde = i.version;
    i.puntoRetorno = { version: desde, fecha: ahora() };
    i.version = r.version; i.estado = 'ok';
    i.ultimoDeploy = { fecha: ahora(), por: usuarioActual().nombre };
    S.historial.unshift({ id: 'h' + Date.now(), fecha: ahora(), tenant: i.tenant, producto: i.producto, ambiente: i.ambiente, tipo: 'Despliegue', desde, hacia: r.version, resultado: 'ok', por: usuarioActual().nombre, duracion: durDep(), logs: DEP.logs.slice() });
    mail(i.tenant, `Despliegue verificado · ${p.nombre} ${r.version} en ${amb(i.ambiente)}`, `Desde ${desde}. Punto de retorno registrado.`, 'soporte@accusys.example');
    DEP.fase = 'ok';
    guardar(); refrescarDep();
    toast(`${p.nombre} ${r.version} desplegada y verificada`);
  }
  function durDep() { const s = Math.max(1, Math.round((Date.now() - DEP.inicio) / 1000)); return `${Math.floor(s / 60)} min ${String(s % 60).padStart(2, '0')} s`; }

  function rollback(modo) {
    const i = inst(DEP.instId), p = prod(i.producto), r = rel(i.producto, DEP.version);
    clearInterval(DEP.timer);
    DEP.fase = 'rollback';
    DEP.pasoEj.push({ t: 'Rollback', d: `restaurar ${DEP.desde} por digest`, estado: 'corriendo' });
    const prev = rel(i.producto, DEP.desde);
    log(modo === 'auto' ? 'cuenta regresiva vencida: iniciando rollback automático' : `rollback pedido por ${usuarioActual().nombre}`, 'warn');
    refrescarDep();
    correrScript([
      { t: `restaurando docker-compose.yml desde .deploycenter/retorno/${DEP.desde}/` },
      { t: 'docker compose up -d con digests guardados: ' + Object.entries(prev.imagenes).map(([k, d]) => `${k}@${d}`).join(' '), c: 'info' },
      ...Object.keys(prev.imagenes).map(s => ({ t: `${s}  GET http://${s}:8080/health → 200`, c: 'ok', ms: 600 })),
      { t: `instalación de vuelta en ${DEP.desde} · logs subidos al hub`, c: 'ok' }
    ], () => {
      DEP.pasoEj[DEP.pasoEj.length - 1].estado = 'ok';
      DEP.pasoEj[4].estado = 'ok';
      i.version = DEP.desde; i.estado = 'ok';
      const h = S.historial.find(x => x.id === DEP.histId);
      if (h) { h.resultado = modo === 'auto' ? 'rollback' : 'manual'; h.logs = DEP.logs.slice(); }
      else S.historial.unshift({ id: 'h' + Date.now(), fecha: ahora(), tenant: i.tenant, producto: i.producto, ambiente: i.ambiente, tipo: 'Despliegue', desde: DEP.desde, hacia: r.version, resultado: modo === 'auto' ? 'rollback' : 'manual', por: usuarioActual().nombre, duracion: durDep(), logs: DEP.logs.slice() });
      mail(i.tenant, `Rollback completado · ${p.nombre} volvió a ${DEP.desde}`, `El intento a ${r.version} falló en la verificación.`, 'guardia@accusys.example');
      DEP.fase = 'revertido';
      guardar(); refrescarDep();
    });
  }

  function cancelarRollback() {
    const i = inst(DEP.instId), r = rel(i.producto, DEP.version);
    clearInterval(DEP.timer);
    log(`rollback cancelado por ${usuarioActual().nombre}: la instalación queda en ${r.version} para diagnóstico`, 'warn');
    i.puntoRetorno = { version: DEP.desde, fecha: ahora() };
    i.version = r.version; i.estado = 'retenido';
    const id = 'h' + Date.now();
    DEP.histId = id;
    S.historial.unshift({ id, fecha: ahora(), tenant: i.tenant, producto: i.producto, ambiente: i.ambiente, tipo: 'Despliegue', desde: DEP.desde, hacia: r.version, resultado: 'retenido', por: usuarioActual().nombre, duracion: durDep(), logs: DEP.logs.slice() });
    DEP.fase = 'retenido';
    guardar(); refrescarDep();
  }

  // Rollback manual desde la instalación: usa el mismo motor con un guion corto.
  function rollbackManual(instId) {
    const i = inst(instId), p = prod(i.producto);
    const destino = i.puntoRetorno.version, desde = i.version;
    // en modo manual, "version" es la que corre hoy y "desde" es a dónde se vuelve
    DEP = { manual: true, instId, version: desde, desde: destino, fase: 'rollback', checks: [], vars: {}, logs: [], inicio: Date.now(), orden: 'ord-' + Math.random().toString(36).slice(2, 7),
      pasoEj: [{ t: 'Lock de instalación', d: `/opt/accusys/${p.id}`, estado: 'ok' }, { t: 'Rollback manual', d: `${desde} → ${destino} por digest`, estado: 'corriendo' }] };
    location.hash = 'desplegar'; UI.vista = 'desplegar';
    log(`rollback manual pedido por ${usuarioActual().nombre}`, 'info');
    render();
    const prev = rel(i.producto, destino);
    correrScript([
      { t: `restaurando docker-compose.yml desde .deploycenter/retorno/${destino}/` },
      { t: 'docker compose up -d con digests guardados: ' + Object.entries(prev.imagenes).map(([k, d]) => `${k}@${d}`).join(' '), c: 'info' },
      ...Object.keys(prev.imagenes).map(s => ({ t: `${s}  GET http://${s}:8080/health → 200`, c: 'ok', ms: 600 })),
      { t: `instalación en ${destino} · logs subidos al hub`, c: 'ok' }
    ], () => {
      DEP.pasoEj[DEP.pasoEj.length - 1].estado = 'ok';
      const eraRetenido = i.estado === 'retenido';
      i.version = destino; i.estado = 'ok'; i.puntoRetorno = retornoInicial(S, i);
      i.ultimoDeploy = { fecha: ahora(), por: usuarioActual().nombre };
      if (!eraRetenido) S.historial.unshift({ id: 'h' + Date.now(), fecha: ahora(), tenant: i.tenant, producto: i.producto, ambiente: i.ambiente, tipo: 'Rollback', desde, hacia: destino, resultado: 'manual', por: usuarioActual().nombre, duracion: durDep(), logs: DEP.logs.slice() });
      mail(i.tenant, `Rollback manual · ${p.nombre} volvió a ${destino}`, `Pedido por ${usuarioActual().nombre}.`, 'soporte@accusys.example');
      DEP.fase = 'revertido';
      guardar(); refrescarDep();
    });
  }

  /* ============================================================
     Vistas · Accusys
     ============================================================ */
  const VISTAS_ACCUSYS = {
    parque: { titulo: 'Tablero de parque', icono: I.grid, render: vParque },
    parametria: { titulo: 'Parametría', icono: I.sliders, render: vParametria },
    releases: { titulo: 'Releases', icono: I.tag, render: vReleases },
    agentes: { titulo: 'Agentes', icono: I.cpu, render: vAgentes, cuenta: () => S.agentes.filter(a => a.estado !== 'online').length || '' },
    auditoria: { titulo: 'Auditoría', icono: I.list, render: vAuditoria }
  };

  function vParque() {
    const insts = S.instalaciones;
    const offline = S.agentes.filter(a => a.estado === 'offline').length;
    const alDia = insts.filter(i => atraso(i) <= 1).length;
    const venc = [];
    S.tenants.forEach(t => t.productos.forEach(p => venc.push({ t, p, m: mant(p) })));
    venc.sort((a, b) => a.p.mantenimientoHasta.localeCompare(b.p.mantenimientoHasta));
    const cols = S.productos.map(p => `<th style="text-align:center">${p.nombre}</th>`).join('');
    const filas = S.tenants.map(t => `<tr><td><b>${esc(t.nombre)}</b><span class="sub">${t.estado === 'activo' ? 'activo' : 'suspendido'}${t.dobleAprobacion ? ' · doble aprobación' : ''}</span></td>
      ${S.productos.map(p => {
        const is = S.instalaciones.filter(i => i.tenant === t.id && i.producto === p.id);
        if (!is.length) return '<td><span class="celda na">—</span></td>';
        const i = is.find(x => x.ambiente === 'produccion') || is[0], ag = agente(i.agente), a = atraso(i);
        let c = 'al-dia', s = 'al día';
        if (ag.estado !== 'online') { c = 'offline'; s = 'agente caído'; }
        else if (i.estado === 'retenido') { c = 'offline'; s = 'con fallo'; }
        else if (a === 1) { c = 'atras-1'; s = '1 atrás'; }
        else if (a >= 2) { c = 'atras-2'; s = `${a} atrás`; }
        return `<td><button class="celda ${c}" data-a="ver-parque" data-t="${t.id}" data-p="${p.id}"><b>${esc(i.version)}</b><small>${s}</small></button></td>`;
      }).join('')}</tr>`).join('');
    const mep = S.instalaciones.filter(i => i.producto === 'mep');
    const porVer = {};
    mep.forEach(i => porVer[i.version] = (porVer[i.version] || 0) + 1);
    const vers = Object.keys(porVer).sort(cmpV).reverse();
    const maxN = Math.max(1, ...Object.values(porVer));
    const ueMep = ultimaEstable('mep');
    return `
      <div class="cabecera"><div class="t"><span class="eyebrow">Tablero de parque</span><h1>Qué corre cada cliente, en una pantalla</h1>
      <p>Producción de cada cliente por producto. El atraso se cuenta en releases estables respecto de la última publicada.</p></div></div>
      <div class="grid g4">
        <div class="panel kpi"><span class="l">Clientes</span><span class="v">${S.tenants.filter(t => t.estado === 'activo').length}<span class="suave" style="font-size:1rem"> / ${S.tenants.length}</span></span><span class="d">activos · ${S.tenants.filter(t => t.estado !== 'activo').length} suspendido</span></div>
        <div class="panel kpi"><span class="l">Instalaciones</span><span class="v">${insts.length}</span><span class="d">${S.agentes.length} agentes enrolados</span></div>
        <div class="panel kpi"><span class="l">En última o anteúltima</span><span class="v">${insts.length ? Math.round(alDia / insts.length * 100) + '%' : '—'}</span><span class="d">meta ≥ 90% a seis meses del piloto</span></div>
        <div class="panel kpi"><span class="l">Agentes fuera de línea</span><span class="v" style="color:${offline ? 'var(--rojo)' : 'inherit'}">${offline}</span><span class="d">botón de despliegue bloqueado</span></div>
      </div>
      <section class="panel">
        <div class="panel-cab"><h2>Producción por cliente y producto</h2>
          <div class="leyenda"><span><i style="background:var(--verde)"></i>Al día</span><span><i style="background:var(--azul)"></i>1 atrás</span><span><i style="background:var(--naranja)"></i>2 o más</span><span><i style="background:var(--rojo)"></i>Agente caído o fallo</span></div></div>
        <div class="tabla-wrap"><table class="matriz"><thead><tr><th>Cliente</th>${cols}</tr></thead><tbody>${filas}</tbody></table></div>
      </section>
      <div class="grid g2">
        <section class="panel">
          <div class="panel-cab"><h2>Vencimientos de mantenimiento</h2><span class="suave chico">avisos a 60, 30 y 15 días</span></div>
          <div class="tabla-wrap"><table><thead><tr><th>Cliente</th><th>Producto</th><th>Vence</th><th>Estado</th></tr></thead><tbody>
            ${venc.slice(0, 7).map(v => `<tr><td>${esc(v.t.nombre)}</td><td><span class="tag-prod">${prod(v.p.producto).codigo}</span></td><td class="num">${fecha(v.p.mantenimientoHasta)}</td><td><span class="pill ${v.m.pill}">${v.m.txt}</span></td></tr>`).join('')}
          </tbody></table></div>
        </section>
        <section class="panel">
          <div class="panel-cab"><h2>Versiones de MEP en el parque</h2><span class="suave chico">${mep.length} instalaciones · prod y homo</span></div>
          <div class="panel-cuerpo pila">
            ${vers.map(v => `<div class="barra-h"><code>${esc(v)}</code><span class="track"><i style="width:${porVer[v] / maxN * 100}%;background:${ueMep && v === ueMep.version ? 'var(--verde)' : v.includes('-') ? 'var(--violeta)' : cmpV(v, '4.6.0') >= 0 ? 'var(--azul)' : 'var(--naranja)'}"></i></span><span class="num" style="text-align:right">${porVer[v]}</span></div>`).join('')}
          </div>
        </section>
      </div>`;
  }

  function vParametria() {
    const u = usuarioActual();
    const edita = u.rol === 'comercial';
    if (!S.tenants.length) return `<div class="cabecera"><div class="t"><span class="eyebrow">Parametría comercial</span><h1>Todavía no hay clientes</h1></div>${edita ? `<button class="btn btn-pri" data-a="nuevo-cliente">${I.plus} Nuevo cliente</button>` : ''}</div>`;
    if (!tenant(UI.tenantParam)) UI.tenantParam = S.tenants[0].id;
    const t = tenant(UI.tenantParam);
    const dis = edita ? '' : 'disabled';
    // lo que el hub todavía no guarda se ve, pero no se edita
    const disHub = MODO === 'hub' ? 'disabled title="Todavía no se guarda en el hub"' : dis;
    const lista = S.tenants.map(x => `<button class="${x.id === t.id ? 'activo' : ''}" data-a="param-t" data-id="${x.id}"><span><b>${esc(x.nombre)}</b><small>${x.productos.map(p => prod(p.producto).nombre).join(' · ')}</small></span>${x.estado === 'suspendido' ? '<span class="pill p-crit sin-punto">Susp.</span>' : ''}</button>`).join('');
    const noTiene = S.productos.filter(p => !tp(t.id, p.id));
    const prods = t.productos.map(p => {
      const m = mant(p);
      return `<div class="prod-param">
        <div class="cab"><span class="tag-prod">${prod(p.producto).codigo}</span><h3>${prod(p.producto).nombre}</h3><span class="pill ${m.pill}">${m.txt}</span></div>
        <div class="campo"><label for="mh-${p.producto}">Mantenimiento hasta</label><input type="date" id="mh-${p.producto}" data-f="param" data-p="${p.producto}" data-k="mantenimientoHasta" value="${p.mantenimientoHasta}" ${dis}></div>
        <div class="campo"><label for="cn-${p.producto}">Canal</label><select id="cn-${p.producto}" data-f="param" data-p="${p.producto}" data-k="canal" ${dis}><option value="estable" ${p.canal === 'estable' ? 'selected' : ''}>Estable</option><option value="anticipado" ${p.canal === 'anticipado' ? 'selected' : ''}>Anticipado</option></select></div>
        <div class="campo"><span class="lbl">Ambientes</span><div class="fila">${['produccion', 'homologacion'].map(a => `<label class="check"><input type="checkbox" data-f="param-amb" data-p="${p.producto}" value="${a}" ${p.ambientes.includes(a) ? 'checked' : ''} ${disHub}> ${amb(a)}</label>`).join('')}</div></div>
        <div class="campo"><span class="lbl">Autoservicio</span><label class="switch"><input type="checkbox" data-f="param" data-p="${p.producto}" data-k="autoservicio" ${p.autoservicio ? 'checked' : ''} ${dis}> ${p.autoservicio ? 'Habilitado' : 'Lo despliega Accusys'}</label></div>
      </div>`;
    }).join('');
    return `
      <div class="cabecera"><div class="t"><span class="eyebrow">Parametría comercial</span><h1>Qué compró cada cliente, y hasta cuándo</h1>
      <p>Gobierna lo que la plataforma habilita. Se aplica en la web y también en el registry: la credencial de cada cliente alcanza solo los repos de sus productos.</p></div></div>
      ${edita ? '' : `<div class="aviso a-info">${I.info}<div>Estás como <b>${ROLES[u.rol].nombre}</b>: la parametría la edita el rol Comercial.${MODO === 'hub' ? '' : ' Cambiá de usuario arriba a la derecha para probarlo.'}</div></div>`}
      <div class="dos-col">
        <section class="panel"><div class="lista-t">${lista}</div></section>
        <div class="pila">
          <section class="panel">
            <div class="panel-cab"><div><h2>${esc(t.nombre)}</h2><p>Tenant <code>${t.id}</code> · ${instsDe(t.id).length} instalaciones</p></div>
              <div class="fila">
                ${MODO === 'hub' && edita ? `<button class="btn btn-sec btn-chico" data-a="alta-usuario-tenant">${I.plus} Alta de usuario</button><button class="btn btn-sec btn-chico" data-a="nuevo-cliente">${I.plus} Nuevo cliente</button>` : ''}
                <label class="switch"><input type="checkbox" data-f="tenant" data-k="dobleAprobacion" ${t.dobleAprobacion ? 'checked' : ''} ${disHub}> Doble aprobación</label>
                <select data-f="tenant" data-k="estado" aria-label="Estado del cliente" style="width:auto" ${dis}><option value="activo" ${t.estado === 'activo' ? 'selected' : ''}>Activo</option><option value="suspendido" ${t.estado === 'suspendido' ? 'selected' : ''}>Suspendido</option></select>
              </div>
            </div>
            ${prods}
            ${edita && noTiene.length ? `<div class="panel-cuerpo fila" style="border-top:1px solid var(--linea)"><select id="nuevo-prod" style="width:auto" aria-label="Producto a agregar">${noTiene.map(p => `<option value="${p.id}">${p.nombre}</option>`).join('')}</select><button class="btn btn-sec btn-chico" data-a="agregar-prod">${I.plus} Agregar producto adquirido</button></div>` : ''}
          </section>
          <div class="grid g2">
            <section class="panel panel-cuerpo pila">
              <h3>Destinatarios de aviso</h3>
              <textarea rows="3" data-f="tenant" data-k="avisos" aria-label="Destinatarios de aviso" ${disHub}>${esc(t.avisos.join('\n'))}</textarea>
              <span class="suave chico">Reciben versión nueva, inicio, fallo, rollback y vencimientos.</span>
            </section>
            <section class="panel panel-cuerpo pila">
              <h3>Credencial del registry</h3>
              <div class="cmd">robot$${t.id}-pull  (solo lectura)\n${t.productos.map(p => `  ✓ registry.accusys.com.ar/${p.producto}/*`).join('\n')}\n${noTiene.map(p => `  ✕ registry.accusys.com.ar/${p.id}/*`).join('\n')}</div>
              <span class="suave chico">Se recalcula al guardar. Aunque alguien saltee la web, no descarga lo que no compró.</span>
            </section>
          </div>
        </div>
      </div>`;
  }

  function vReleases() {
    const u = usuarioActual();
    if (!UI.prodRel) UI.prodRel = 'mep';
    const p = prod(UI.prodRel);
    const tabs = S.productos.map(x => `<button class="tab ${x.id === p.id ? 'activo' : ''}" data-a="rel-prod" data-id="${x.id}">${x.nombre}</button>`).join('');
    const filas = rels(p.id).slice().reverse().map(r => {
      const n = S.instalaciones.filter(i => i.producto === p.id && i.version === r.version).length;
      return `<tr class="click" data-a="ver-manifiesto" data-p="${p.id}" data-v="${r.version}">
        <td><b class="num">${esc(r.version)}</b></td><td class="num">${fecha(r.publicado)}</td>
        <td>${r.canal === 'anticipado' ? '<span class="pill p-vio">Anticipado</span>' : '<span class="pill p-neutro">Estable</span>'}</td>
        <td><div class="fila" style="gap:5px">${r.db ? '<span class="pill p-warn">Migración · asistido</span>' : '<span class="pill p-ok">Autoservicio</span>'}${r.critico ? '<span class="pill p-crit">Crítico</span>' : ''}${r.variables.length ? `<span class="pill p-info">${r.variables.length} var.</span>` : ''}</div></td>
        <td><code>${esc(r.desde)}</code></td><td class="num">${n}</td></tr>`;
    }).join('');
    return `
      <div class="cabecera"><div class="t"><span class="eyebrow">Releases</span><h1>Catálogo de versiones por producto</h1>
      <p>Cada release es un manifiesto firmado: imágenes por digest, variables nuevas, healthchecks y ruta de upgrade. Mismo formato para los seis productos.</p></div>
      <button class="btn btn-pri" data-a="publicar" ${MODO === 'hub' ? 'disabled title="Se publica desde el repo y el pipeline, con la firma fuera del hub"' : u.rol === 'publicador' ? '' : 'disabled title="Solo el rol Publicador firma y publica"'}>${I.plus} Publicar release</button></div>
      <div class="tabs">${tabs}</div>
      <section class="panel"><div class="tabla-wrap"><table><thead><tr><th>Versión</th><th>Publicado</th><th>Canal</th><th>Tipo</th><th>Desde</th><th>Instalaciones</th></tr></thead><tbody>${filas}</tbody></table></div></section>
      ${MODO === 'hub' ? `<div class="aviso a-info">${I.info}<div>El hub ofrece lo que está publicado en <code>productos/</code> del repo: un release nuevo entra por el pipeline, firmado con una clave que no vive en el hub.</div></div>` : u.rol !== 'publicador' ? `<div class="aviso a-info">${I.info}<div>Para publicar un release entrá como <b>Nicolás Vidal (Publicador)</b>. La clave de firma no vive en el hub.</div></div>` : ''}`;
  }

  function vAgentes() {
    const filas = S.agentes.map(a => {
      const stacks = S.instalaciones.filter(i => i.agente === a.id).map(i => prod(i.producto).codigo);
      const est = { online: '<span class="pill p-ok">En línea</span>', offline: '<span class="pill p-crit">Fuera de línea</span>', revocado: '<span class="pill p-neutro">Revocado</span>', pendiente: '<span class="pill p-warn">Esperando canje</span>' }[a.estado];
      return `<tr><td><code>${esc(a.host)}</code><span class="sub">${esc(a.id)}</span></td><td>${esc((tenant(a.tenant) || { nombre: a.tenant }).nombre)}</td>
        <td>${est}</td><td class="num">${esc(a.visto)}</td>
        <td class="num">${esc(a.version)} ${MODO !== 'hub' && a.version !== AGENTE_ULTIMA && a.estado !== 'pendiente' ? '<span class="pill p-warn">desactualizado</span>' : ''}</td>
        <td>${stacks.map(s => `<span class="tag-prod">${s}</span>`).join(' ') || '<span class="suave">—</span>'}</td>
        <td>${a.estado === 'pendiente' ? `<button class="btn btn-sec btn-chico" data-a="canjear" data-id="${a.id}">Simular canje</button>` : a.estado !== 'revocado' ? `<button class="btn btn-fantasma btn-chico" data-a="pedir-revocar" data-id="${a.id}">Revocar</button>` : ''}</td></tr>`;
    }).join('');
    return `
      <div class="cabecera"><div class="t"><span class="eyebrow">Agentes</span><h1>Un agente por host, todos los productos del host</h1>
      <p>Conexión siempre saliente por 443. Alta con código de un solo uso; baja revocando el token y la credencial del registry, sin tocar el servidor.</p></div>
      <button class="btn btn-pri" data-a="enrolar">${I.plus} Enrolar servidor</button></div>
      <section class="panel"><div class="tabla-wrap"><table><thead><tr><th>Host</th><th>Cliente</th><th>Estado</th><th>Último contacto</th><th>Versión</th><th>Stacks</th><th></th></tr></thead><tbody>${filas}</tbody></table></div></section>
      <div class="aviso a-info">${I.shield}<div><b>Catálogo cerrado de operaciones:</b> pull, up, down, logs y rollback, sobre los stacks de Accusys y con imágenes de <code>registry.accusys.com.ar</code>. No existe un shell remoto.</div></div>`;
  }

  function vAuditoria() {
    const f = UI.filtroAud;
    let filas = S.historial.map(h => ({ fecha: h.fecha, tenant: h.tenant, tipo: h.tipo, txt: `${prod(h.producto).nombre} ${amb(h.ambiente)}: ${h.desde} → ${h.hacia}`, res: h.resultado, por: h.por, id: h.id }))
      .concat(S.auditoria.map(a => ({ fecha: a.fecha, tenant: a.tenant, tipo: 'Administración', txt: a.accion, res: 'registro', por: a.por })));
    filas.sort((a, b) => b.fecha.localeCompare(a.fecha));
    if (f.tenant) filas = filas.filter(x => x.tenant === f.tenant);
    if (f.tipo) filas = filas.filter(x => x.tipo === f.tipo);
    const res = { ok: '<span class="pill p-ok">Verificado</span>', rollback: '<span class="pill p-warn">Rollback auto</span>', manual: '<span class="pill p-info">Rollback manual</span>', retenido: '<span class="pill p-crit">Retenido</span>', registro: '<span class="pill p-neutro">Registro</span>', error: '<span class="pill p-crit">Falló</span>', en_curso: '<span class="pill p-info">En curso</span>', cancelada: '<span class="pill p-neutro">Retirada</span>' };
    return `
      <div class="cabecera"><div class="t"><span class="eyebrow">Auditoría</span><h1>Historial completo, no editable</h1>
      <p>Despliegues, rollbacks y cambios de parametría de todo el parque.</p></div>
      <div class="fila">
        <select data-f="aud-t" aria-label="Filtrar por cliente" style="width:auto"><option value="">Todos los clientes</option>${S.tenants.map(t => `<option value="${t.id}" ${f.tenant === t.id ? 'selected' : ''}>${esc(t.nombre)}</option>`).join('')}</select>
        <select data-f="aud-tipo" aria-label="Filtrar por tipo" style="width:auto"><option value="">Todos los tipos</option>${['Despliegue', 'Asistido', 'Rollback', 'Administración'].map(x => `<option ${f.tipo === x ? 'selected' : ''}>${x}</option>`).join('')}</select>
      </div></div>
      <section class="panel"><div class="tabla-wrap"><table><thead><tr><th>Fecha</th><th>Cliente</th><th>Tipo</th><th>Detalle</th><th>Resultado</th><th>Por</th></tr></thead><tbody>
        ${filas.map(x => `<tr ${x.id ? `class="click" data-a="ver-log" data-id="${x.id}"` : ''}><td class="num">${esc(x.fecha)}</td><td>${x.tenant ? esc(tenant(x.tenant).nombre) : '<span class="suave">Accusys</span>'}</td><td>${esc(x.tipo)}</td><td>${esc(x.txt)}</td><td>${res[x.res] || ''}</td><td>${esc(x.por)}</td></tr>`).join('') || '<tr><td colspan="6" class="vacio">Sin registros para ese filtro.</td></tr>'}
      </tbody></table></div></section>`;
  }

  /* ============================================================
     Drawers y modales
     ============================================================ */
  function drawerHtml() {
    const d = UI.drawer;
    let titulo = '', cuerpo = '';
    if (d.tipo === 'mails') {
      titulo = 'Avisos por mail';
      const ms = misMails();
      cuerpo = `<p class="suave chico">Lo que la plataforma mandó. En producción sale por mail; acá queda la bandeja para ver el circuito.</p>
        <div>${ms.map(m => `<div class="mail"><b>${esc(m.asunto)}</b><span class="chico">${esc(m.cuerpo)}</span><small>${esc(m.fecha)} · para ${esc(m.para)}</small></div>`).join('') || '<div class="vacio">Sin avisos.</div>'}</div>`;
    } else if (d.tipo === 'usuarios' && MODO === 'hub') {
      const u = usuarioActual(), t = u.tenant ? tenant(u.tenant) : null, id = HUB.usuario() || {};
      titulo = 'Tu sesión';
      cuerpo = `<dl class="datos"><dt>Usuario</dt><dd>${esc(u.nombre)}<span class="sub">${esc(u.email || id.email || '')}</span></dd>
          <dt>Rol</dt><dd>${ROLES[u.rol].nombre} · ${t ? esc(t.nombre) : 'Accusys'}</dd>
          <dt>Segundo factor</dt><dd><span class="pill p-ok">aal2 · TOTP verificado</span></dd>
          <dt>Id</dt><dd><code>${esc(u.id)}</code></dd></dl>
        <p class="suave chico">Qué podés hacer lo deciden las tablas del hub, no el token: si te cambian el rol, vale desde el próximo pedido.</p>
        <div class="fila" style="border-top:1px solid var(--linea);padding-top:14px"><button class="btn btn-sec" data-a="logout">${I.logout} Cerrar sesión</button><button class="btn btn-fantasma" data-a="recargar">Recargar datos</button></div>`;
    } else if (d.tipo === 'usuarios') {
      titulo = 'Cambiar de usuario';
      cuerpo = `<p class="suave chico">Probá la plataforma desde cada rol. El estado del mock se conserva.</p>
        <div class="pila" style="gap:8px">${S.usuarios.map(x => `<button class="demo-user ${x.id === S.sesion ? 'sel' : ''}" data-a="cambiar-user" data-id="${x.id}"><b>${esc(x.nombre)}</b><small>${ROLES[x.rol].nombre} · ${x.tenant ? esc(tenant(x.tenant).nombre) : 'Accusys'}</small></button>`).join('')}</div>
        <div class="fila" style="border-top:1px solid var(--linea);padding-top:14px"><button class="btn btn-sec" data-a="logout">${I.logout} Cerrar sesión</button><button class="btn btn-fantasma" data-a="pedir-reset">Reiniciar datos de demo</button></div>`;
    } else if (d.tipo === 'manifiesto') {
      const r = rel(d.p, d.v);
      const man = MODO === 'hub' ? S.manifiestos[`${d.p}@${d.v}`] : manifiesto(d.p, r);
      titulo = `Manifiesto · ${prod(d.p).nombre} ${d.v}`;
      cuerpo = `<p class="suave chico">Firmado con la clave de publicación de Accusys. El agente valida la firma antes de aplicar.</p><div class="cmd" style="white-space:pre-wrap">${esc(JSON.stringify(man, null, 2))}</div>`;
    } else if (d.tipo === 'log' && MODO === 'hub') {
      const h = S.historial.find(x => x.id === d.id), o = d.orden;
      titulo = `${prod(h.producto).nombre} ${h.desde} → ${h.hacia}`;
      cuerpo = `<dl class="datos"><dt>Cliente</dt><dd>${esc((tenant(h.tenant) || { nombre: h.tenant }).nombre)}</dd><dt>Instalación</dt><dd>${esc(h.ambiente)}</dd><dt>Operación</dt><dd>${esc(h.tipo)} · orden <code>${esc(h.orden)}</code></dd><dt>Por</dt><dd>${esc(h.por)}</dd><dt>Fecha</dt><dd class="num">${esc(h.fecha)}${h.duracion ? ' · ' + esc(h.duracion) : ''}</dd>${o && o.detalle ? `<dt>Detalle</dt><dd>${esc(o.detalle)}</dd>` : ''}</dl>
        ${o ? consolaEventos(o) : '<div class="vacio">Cargando los eventos del agente…</div>'}`;
    } else if (d.tipo === 'log') {
      const h = S.historial.find(x => x.id === d.id);
      titulo = `${prod(h.producto).nombre} ${h.desde} → ${h.hacia}`;
      const logs = h.logs || logFicticio(h);
      cuerpo = `<dl class="datos"><dt>Cliente</dt><dd>${esc(tenant(h.tenant).nombre)}</dd><dt>Ambiente</dt><dd>${amb(h.ambiente)}</dd><dt>Operación</dt><dd>${esc(h.tipo)}</dd><dt>Por</dt><dd>${esc(h.por)}</dd><dt>Fecha</dt><dd class="num">${esc(h.fecha)} · ${esc(h.duracion)}</dd></dl>
        <div class="consola" style="max-height:none">${logs.map(l => `<span class="ts">[${l.h}]</span> <span class="${l.c || ''}">${esc(l.t)}</span>`).join('\n')}</div>`;
    } else if (d.tipo === 'parque') {
      const t = tenant(d.t), p = prod(d.p), tpr = tp(d.t, d.p), m = mant(tpr);
      const is = S.instalaciones.filter(i => i.tenant === d.t && i.producto === d.p);
      titulo = `${t.nombre} · ${p.nombre}`;
      cuerpo = `<dl class="datos"><dt>Mantenimiento</dt><dd><span class="pill ${m.pill}">${fecha(tpr.mantenimientoHasta)}</span></dd><dt>Autoservicio</dt><dd>${tpr.autoservicio ? 'Habilitado' : 'Deshabilitado'}</dd><dt>Canal</dt><dd>${tpr.canal}</dd><dt>Última estable</dt><dd>${esc((ultimaEstable(d.p) || { version: '—' }).version)}</dd></dl>
        ${is.map(i => { const ag = agente(i.agente); return `<div class="panel panel-cuerpo pila" style="box-shadow:none"><div class="fila" style="justify-content:space-between"><b>${amb(i.ambiente)} · ${esc(i.version)}</b>${ag.estado === 'online' ? '<span class="pill p-ok">Agente en línea</span>' : '<span class="pill p-crit">Agente caído</span>'}</div><span class="suave chico"><code>${esc(ag.host)}</code> · deploy-agent ${ag.version} · ${ag.visto}</span>${i.ultimoDeploy.fecha ? `<span class="suave chico">Último despliegue ${esc(i.ultimoDeploy.fecha)} por ${esc(i.ultimoDeploy.por)}</span>` : ''}</div>`; }).join('')}
        <div class="aviso a-info">${I.shield}<div>Accusys no despliega en un cliente sin habilitación explícita, registrada en el historial.${MODO === 'hub' ? ' La otorga el Aprobador del cliente desde su pantalla de usuarios.' : ''}</div></div>
        ${MODO === 'hub' ? habilitacionVigente(t.id) : `<button class="btn btn-sec" data-a="pedir-habilitacion" data-t="${t.id}" data-p="${p.id}">Pedir habilitación para asistir</button>`}`;
    } else if (d.tipo === 'publicar') {
      titulo = 'Publicar release';
      cuerpo = `
        <div class="campo"><label for="pub-json">Manifiesto (JSON)</label><textarea id="pub-json" rows="20" data-f="pub-json">${esc(d.json)}</textarea><span class="ayuda">La firma se agrega al publicar, con la clave que vive fuera del hub.</span></div>
        <div class="fila"><button class="btn btn-sec" data-a="validar-manifiesto">Validar</button><button class="btn btn-pri" data-a="firmar-publicar">${I.shield} Firmar y publicar</button></div>
        ${d.val ? `<div class="checks">${d.val.map(v => `<div class="chk"><span class="ic ${v.ok === true ? 'ok' : v.ok === 'warn' ? 'warn' : 'fallo'}">${v.ok === true ? '✓' : v.ok === 'warn' ? '!' : '✕'}</span><div><b>${esc(v.campo)}</b><small>${esc(v.msg)}</small></div><span></span></div>`).join('')}</div>` : ''}`;
    }
    return `<div class="velo" data-a="cerrar-drawer"><aside class="drawer" role="dialog" aria-label="${esc(titulo)}" data-stop="1">
      <div class="drawer-cab"><h2>${esc(titulo)}</h2><button class="icono-btn" data-a="cerrar-drawer" aria-label="Cerrar">${I.x}</button></div>
      <div class="drawer-cuerpo">${cuerpo}</div></aside></div>`;
  }

  function modalHtml() {
    const m = UI.modal;
    let t = '', c = '', pie = '';
    if (m.tipo === 'rollback') {
      const i = inst(m.id);
      t = `Volver a ${i.puntoRetorno.version}`;
      c = `<p>El agente restaura el <code>docker-compose.yml</code> y los digests guardados de ${esc(i.puntoRetorno.version)} en ${prod(i.producto).nombre} ${amb(i.ambiente)}. Solo se toca este stack.</p><div class="aviso a-info">${I.info}<div>Volver atrás nunca se bloquea por mantenimiento vencido.</div></div>`;
      pie = `<button class="btn btn-sec" data-a="cerrar-modal">Cancelar</button><button class="btn btn-peligro" data-a="confirmar-rollback" data-id="${i.id}">${I.undo} Volver a ${esc(i.puntoRetorno.version)}</button>`;
    } else if (m.tipo === 'coordinar' && MODO === 'hub') {
      t = 'Despliegue asistido';
      c = `<p>Este release no va por autoservicio: trae migración de base o el autoservicio está deshabilitado. Lo despliega Soporte de Accusys con una habilitación de tu organización, que otorga el Aprobador desde <a href="#usuarios">Usuarios y roles</a>.</p><p class="suave chico">El pedido de ventana todavía no sale desde el hub: coordinalo con Soporte por los canales habituales.</p>`;
      pie = `<button class="btn btn-pri" data-a="cerrar-modal">Entendido</button>`;
    } else if (m.tipo === 'coordinar') {
      t = 'Coordinar ventana con Accusys';
      c = `<p>Este release se despliega en modo asistido: backup obligatorio, ventana acordada y verificación con Soporte de Accusys.</p>
        <div class="campo"><label for="coord-fecha">Fecha propuesta</label><input type="date" id="coord-fecha" value="2026-10-02"></div>
        <div class="campo"><label for="coord-nota">Comentario</label><textarea id="coord-nota" rows="3" style="font-family:var(--cuerpo)">Preferimos después de las 20 h.</textarea></div>`;
      pie = `<button class="btn btn-sec" data-a="cerrar-modal">Cancelar</button><button class="btn btn-pri" data-a="enviar-coordinar">Enviar pedido</button>`;
    } else if (m.tipo === 'enrolar' && MODO === 'hub' && m.codigo) {
      t = 'Código de enrolamiento';
      c = `<p class="chico suave">Vale hasta ${esc(HUB.fechaLocal(m.expira))} y se usa una sola vez. El agente lo canjea por su propio token; el hub guarda solo el hash.</p>
        <div class="codigo-enrol">${esc(m.codigo)}</div>
        <p class="chico suave">En el servidor del cliente, con el compose del agente (<code>ejemplos/agente-conectado-compose.yml</code>):</p>
        <div class="cmd">docker compose run --rm agente enrolar \\\n  --hub ${esc(location.origin)} --codigo ${esc(m.codigo)}${m.host ? ` --host ${esc(m.host)}` : ''}\ndocker compose up -d</div>`;
      pie = `<button class="btn btn-sec" data-a="copiar-cmd">Copiar comando</button><button class="btn btn-pri" data-a="cerrar-modal">Listo</button>`;
    } else if (m.tipo === 'enrolar') {
      if (!m.codigo) {
        t = 'Enrolar servidor';
        c = `<div class="campo"><label for="enr-t">Cliente</label><select id="enr-t">${S.tenants.map(x => `<option value="${x.id}">${esc(x.nombre)}</option>`).join('')}</select></div>
          <div class="campo"><label for="enr-host">Host</label><input type="text" id="enr-host" value="srv-dock-03.cliente.local"></div>`;
        pie = `<button class="btn btn-sec" data-a="cerrar-modal">Cancelar</button><button class="btn btn-pri" data-a="generar-codigo">Generar código de un solo uso</button>`;
      } else {
        t = 'Código de enrolamiento';
        c = `<p class="chico suave">Vale por 24 horas y se usa una sola vez. El agente lo canjea por su propio token, que queda guardado con hash en el hub.</p>
          <div class="codigo-enrol">${m.codigo}</div>
          <div class="cmd">docker run -d --name deploy-agent --restart unless-stopped \\\n  -v /var/run/docker.sock:/var/run/docker.sock \\\n  -v /opt/accusys:/opt/accusys \\\n  registry.accusys.com.ar/deploy-agent:${AGENTE_ULTIMA} \\\n  enroll --hub https://deploy.accusys.com.ar --code ${m.codigo}</div>`;
        pie = `<button class="btn btn-sec" data-a="copiar-cmd">Copiar comando</button><button class="btn btn-pri" data-a="cerrar-modal">Listo</button>`;
      }
    } else if (m.tipo === 'revocar') {
      const a = agente(m.id);
      t = 'Revocar agente';
      c = `<p>Se revocan el token de <code>${esc(a.host)}</code> y su credencial del registry. El servidor no se toca; los stacks siguen corriendo como están.</p>`;
      pie = `<button class="btn btn-sec" data-a="cerrar-modal">Cancelar</button><button class="btn btn-peligro" data-a="revocar" data-id="${a.id}">Revocar</button>`;
    } else if (m.tipo === 'alta-usuario') {
      const fijo = m.tenant ? tenant(m.tenant) : tenant(usuarioActual().tenant);
      t = `Alta de usuario · ${fijo.nombre}`;
      c = `<p class="chico suave">La persona tiene que existir en el proveedor de identidad (el registro abierto está cerrado: la crea Accusys). Al ingresar sin alta, la pantalla le muestra su id: pedíselo y cargalo acá.</p>
        <div class="campo"><label for="alta-id">Id del usuario</label><input type="text" id="alta-id" placeholder="00000000-0000-0000-0000-000000000000" autocomplete="off"></div>
        <div class="campo"><label for="alta-mail">Email</label><input type="email" id="alta-mail" autocomplete="off"></div>
        <div class="campo"><label for="alta-nombre">Nombre</label><input type="text" id="alta-nombre" autocomplete="off"></div>
        <div class="campo"><label for="alta-rol">Rol</label><select id="alta-rol"><option value="lector">Lector</option><option value="operador" selected>Operador</option><option value="aprobador">Aprobador</option></select></div>`;
      pie = `<button class="btn btn-sec" data-a="cerrar-modal">Cancelar</button><button class="btn btn-pri" data-a="enviar-alta" data-t="${esc(fijo.id)}">Dar de alta</button>`;
    } else if (m.tipo === 'nuevo-cliente') {
      t = 'Nuevo cliente';
      c = `<div class="campo"><label for="nc-id">Identificador</label><input type="text" id="nc-id" placeholder="banco-andino" autocomplete="off"><span class="ayuda">Minúsculas, números y guiones. No se cambia después.</span></div>
        <div class="campo"><label for="nc-nombre">Nombre</label><input type="text" id="nc-nombre" placeholder="Banco Andino" autocomplete="off"></div>`;
      pie = `<button class="btn btn-sec" data-a="cerrar-modal">Cancelar</button><button class="btn btn-pri" data-a="crear-cliente">Crear</button>`;
    } else if (m.tipo === 'invitar') {
      t = 'Invitar usuario';
      c = `<div class="campo"><label for="inv-mail">Email</label><input type="email" id="inv-mail" value="nuevo.operador@${tenant(usuarioActual().tenant).id}.example"></div>
        <div class="campo"><label for="inv-rol">Rol</label><select id="inv-rol"><option value="lector">Lector</option><option value="operador" selected>Operador</option><option value="aprobador">Aprobador</option></select></div>`;
      pie = `<button class="btn btn-sec" data-a="cerrar-modal">Cancelar</button><button class="btn btn-pri" data-a="enviar-invitacion">Enviar invitación</button>`;
    } else if (m.tipo === 'reset') {
      t = 'Reiniciar datos de demo';
      c = '<p>Se descartan los despliegues, cambios de parametría y releases que hiciste en este navegador.</p>';
      pie = `<button class="btn btn-sec" data-a="cerrar-modal">Cancelar</button><button class="btn btn-peligro" data-a="reset">Reiniciar</button>`;
    }
    return `<div class="velo modal-velo" data-a="cerrar-modal"><div class="modal" role="dialog" aria-label="${esc(t)}" data-stop="1">
      <div class="panel-cab"><h2>${esc(t)}</h2></div><div class="panel-cuerpo">${c}</div><div class="modal-pie">${pie}</div></div></div>`;
  }

  /* ============================================================
     Modo hub
     ============================================================ */
  function vacio() {
    return { hoy: new Date().toISOString().slice(0, 10), sesion: null, productos: [], releases: {}, tenants: [], agentes: [], instalaciones: [],
      usuarios: [], historial: [], auditoria: [], mails: [], ordenes: [], habilitaciones: [], manifiestos: {} };
  }

  function vistaLoginHub() {
    const L = UI.loginHub || (UI.loginHub = { paso: 'credenciales' });
    const cfg = HUB.config() || {};
    const err = L.error ? `<div class="aviso a-crit">${I.alert}<div>${esc(L.error)}</div></div>` : '';
    const ocupado = L.ocupado ? 'disabled' : '';
    const digitos = `<div class="totp">${[0, 1, 2, 3, 4, 5].map(n => `<input type="text" inputmode="numeric" maxlength="6" id="totp-${n}" data-totp="${n}" aria-label="Dígito ${n + 1}" autocomplete="one-time-code">`).join('')}</div>`;
    let titulo = 'Ingresar', sub = 'Con tu usuario y el segundo factor (TOTP).', form = '';
    if (!cfg.identidad) {
      form = `<div class="aviso a-crit">${I.alert}<div><b>El hub no tiene configurada la identidad.</b> ${esc(cfg.motivo || 'Definí SUPABASE_URL y SUPABASE_PUBLISHABLE_KEY (o DC_JWT_SECRET para desarrollo) y reiniciá el hub.')}</div></div>`;
    } else if (L.paso === 'sin-alta') {
      titulo = 'Falta tu alta'; sub = 'Ingresaste bien, pero tu usuario no tiene rol en deployHub.';
      form = `<div class="aviso a-warn">${I.users}<div>Pedile el alta al Aprobador de tu organización (o a Accusys) con este id:</div></div>
        <div class="cmd">${esc(L.id || '')}</div>${L.email ? `<p class="suave chico">Usuario: ${esc(L.email)}</p>` : ''}
        <div class="fila"><button class="btn btn-sec" data-a="logout">${I.logout} Salir</button><button class="btn btn-pri" data-a="recargar">Ya tengo el alta</button></div>`;
    } else if (cfg.identidad === 'token') {
      sub = 'Hub de desarrollo: pegá un token de dc-hub token-dev.';
      form = `<div class="pila"><div class="campo"><label for="login-token">Token</label><textarea id="login-token" rows="4" autocomplete="off"></textarea><span class="ayuda"><code>dc-hub token-dev &lt;uuid&gt;</code> con el mismo DC_JWT_SECRET que el hub.</span></div>
        <button class="btn btn-pri" data-a="hub-token" ${ocupado}>Ingresar</button></div>`;
    } else if (L.paso === 'enrolar') {
      titulo = 'Activá el segundo factor'; sub = 'Es obligatorio. Se hace una sola vez.';
      form = `<div class="pila">
        <p class="chico">Escaneá el código con tu app de autenticación (Google Authenticator, Microsoft Authenticator, 1Password…) y escribí el código de 6 dígitos que muestra.</p>
        ${L.qr ? `<img src="${esc(L.qr)}" alt="Código QR para la app de autenticación" style="width:180px;height:180px;background:#fff;border-radius:8px;padding:8px">` : ''}
        <div class="campo"><span class="lbl">Si no podés escanear, cargá esta clave</span><div class="cmd" style="user-select:all">${esc(L.secreto || '')}</div></div>
        <div class="campo"><span class="lbl">Código de verificación</span>${digitos}</div>
        <div class="fila"><button class="btn btn-pri" data-a="hub-verificar" ${ocupado}>Verificar y entrar</button><button class="btn btn-fantasma" data-a="logout">Cancelar</button></div></div>`;
    } else if (L.paso === 'totp') {
      form = `<div class="pila"><div class="campo"><span class="lbl">Código de verificación (TOTP)</span>${digitos}<span class="ayuda">El de tu app de autenticación para deployHub.</span></div>
        <div class="fila"><button class="btn btn-pri" data-a="hub-verificar" ${ocupado}>Verificar y entrar</button><button class="btn btn-fantasma" data-a="logout">Usar otra cuenta</button></div></div>`;
    } else {
      form = `<div class="pila">
        <div class="campo"><label for="login-email">Email</label><input id="login-email" type="email" autocomplete="username" value="${esc(L.email || '')}"></div>
        <div class="campo"><label for="login-pass">Contraseña</label><input id="login-pass" type="password" autocomplete="current-password"></div>
        <button class="btn btn-pri" data-a="hub-ingresar" ${ocupado}>${L.ocupado ? 'Ingresando…' : 'Ingresar'}</button></div>`;
    }
    return `
      <div class="login">
        ${heroLogin()}
        <section class="login-form">
          <img class="logo-accusys" src="accusys.png" alt="Accusys">
          <div class="pila" style="gap:6px"><h2 style="font-size:1.4rem">${titulo}</h2><p class="suave">${sub}</p></div>
          ${err}${form}
        </section>
      </div>`;
  }

  function codigoTotp() { return [0, 1, 2, 3, 4, 5].map(k => ($('#totp-' + k) || {}).value || '').join(''); }

  async function paso(fn) {
    UI.loginHub.ocupado = true; UI.loginHub.error = null; render();
    try { await fn(); } catch (e) { if (UI.loginHub) UI.loginHub.error = e.message; }
    // si entró, recargar() ya dibujó la app y UI.loginHub quedó en null
    if (UI.loginHub) { UI.loginHub.ocupado = false; if (!S.sesion) render(); }
  }

  let refrescando = false;
  async function recargar(silencioso) {
    if (refrescando) return;
    refrescando = true;
    try {
      const nuevo = await HUB.cargar();
      const antes = JSON.stringify(S);
      S = nuevo;
      UI.loginHub = null;
      if (!DEPH) retomarOrdenAbierta();
      if (!silencioso || JSON.stringify(S) !== antes) render();
    } catch (e) {
      if (e.estado === 403 && /no tiene alta/.test(e.message)) {
        const id = HUB.usuario() || {};
        S = vacio(); UI.loginHub = { paso: 'sin-alta', id: id.id, email: id.email }; render();
      } else if (e.estado === 401) {
        S = vacio(); UI.loginHub = { paso: 'credenciales', error: 'La sesión venció: volvé a ingresar.' }; render();
      } else if (!silencioso) toast(e.message, true);
    } finally { refrescando = false; }
  }

  async function llamar(metodo, ruta, cuerpo, ok) {
    try {
      const r = await HUB.api(metodo, ruta, cuerpo);
      if (ok) toast(ok);
      return r;
    } catch (e) { toast(e.message, true); throw e; }
  }

  function habilitacionVigente(tid) {
    const h = S.habilitaciones.find(x => x.tenant === tid && x.vigente);
    return h ? `<span class="pill p-ok">Habilitación vigente hasta ${esc(HUB.fechaLocal(h.vence))}</span>` : '<span class="pill p-neutro">Sin habilitación vigente</span>';
  }

  function panelHabilitaciones(t, admin) {
    const hs = S.habilitaciones.filter(h => h.tenant === t.id);
    const vig = hs.find(h => h.vigente);
    const filas = hs.slice(0, 8).map(h => `<tr><td class="num">${esc(HUB.fechaLocal(h.otorgada))}</td><td class="num">${esc(HUB.fechaLocal(h.vence))}</td><td>${esc(h.motivo || '')}</td>
      <td>${h.vigente ? '<span class="pill p-ok">Vigente</span>' : h.revocada ? '<span class="pill p-neutro">Revocada</span>' : '<span class="pill p-neutro">Vencida</span>'}</td>
      <td>${h.vigente && admin ? `<button class="btn btn-fantasma btn-chico" data-a="revocar-habilitacion" data-id="${h.id}">Revocar</button>` : ''}</td></tr>`).join('');
    return `<section class="panel">
      <div class="panel-cab"><div><h2>Asistencia de Accusys</h2><p>Soporte de Accusys ordena sobre tus instalaciones solo con una habilitación vigente, que otorga un Aprobador y queda en la auditoría.</p></div>${vig ? '<span class="pill p-ok">Habilitada</span>' : '<span class="pill p-neutro">No habilitada</span>'}</div>
      ${admin ? `<div class="panel-cuerpo fila" style="align-items:end">
        <div class="campo" style="width:120px"><label for="hab-horas">Horas</label><input type="number" id="hab-horas" min="1" max="72" value="4"></div>
        <div class="campo" style="flex:1;min-width:200px"><label for="hab-motivo">Motivo</label><input type="text" id="hab-motivo" placeholder="Despliegue asistido de MEP 4.8.0"></div>
        <button class="btn btn-pri" data-a="habilitar">${I.shield} Habilitar a Accusys</button></div>` : ''}
      ${filas ? `<div class="tabla-wrap"><table><thead><tr><th>Otorgada</th><th>Vence</th><th>Motivo</th><th>Estado</th><th></th></tr></thead><tbody>${filas}</tbody></table></div>` : '<div class="vacio">Nunca se habilitó a Accusys.</div>'}
    </section>`;
  }

  // --- órdenes reales ---
  const ABIERTA = ['pendiente', 'entregada', 'en_curso'];
  const EVENTOS = {
    orden_recibida: d => `orden recibida por el agente (${d.tipo || ''})`,
    despliegue_iniciado: d => `desplegando ${d.desde || '—'} → ${d.hacia || ''}`,
    despliegue_ok: d => `${d.hacia || ''} desplegada y verificada`,
    despliegue_falla: d => `verificación FALLIDA: ${d.motivo || ''}`,
    requiere_intervencion: d => `requiere intervención: ${d.razon || ''}`,
    rollback_cancelado: () => 'vuelta atrás cancelada: la instalación queda para diagnóstico',
    rollback_ok: d => `vuelta atrás completada ${d.detalle || ''}`,
    rollback_falla: d => `la vuelta atrás falló: ${d.detalle || ''}`
  };
  const CLASE_EVENTO = { despliegue_ok: 'ok', rollback_ok: 'ok', despliegue_falla: 'err', rollback_falla: 'err', requiere_intervencion: 'err', rollback_cancelado: 'warn', orden_recibida: 'info', despliegue_iniciado: 'info' };
  function consolaEventos(o) {
    const ev = o.eventos || [];
    if (!ev.length) return `<div class="consola" role="log"><span class="suave">${ABIERTA.includes(o.estado) ? 'Todavía no llegaron eventos del agente…' : 'La orden no tiene eventos.'}</span></div>`;
    return `<div class="consola" role="log">${ev.map(e => {
      const h = (HUB.fechaLocal(e.ts, true) || '').slice(11) || '—';
      const txt = (EVENTOS[e.evento] || (d => `${e.evento} ${Object.keys(d || {}).length ? JSON.stringify(d) : ''}`))(e.datos || {});
      return `<span class="ts">[${esc(h)}]</span> <span class="${CLASE_EVENTO[e.evento] || ''}">${esc(txt)}</span>`;
    }).join('\n')}</div>`;
  }

  let sondeo = null;
  function seguir(orden, extra) {
    DEPH = Object.assign(DEPH || {}, extra || {}, { orden });
    clearInterval(sondeo);
    if (!ABIERTA.includes(orden.estado)) return;
    sondeo = setInterval(async () => {
      if (!DEPH || DEPH.orden.id !== orden.id) return clearInterval(sondeo);
      try {
        const o = await HUB.api('GET', `/ordenes/${encodeURIComponent(orden.id)}`);
        const cambio = JSON.stringify(o) !== JSON.stringify(DEPH.orden);
        DEPH.orden = o;
        if (!ABIERTA.includes(o.estado)) { clearInterval(sondeo); recargar(true); }
        if (cambio) refrescarDep();
      } catch (e) { /* un corte de red no corta el seguimiento */ }
    }, 2000);
  }

  function retomarOrdenAbierta() {
    const u = usuarioActual();
    if (!u || !u.tenant) return;
    const o = S.ordenes.find(x => x.tenant === u.tenant && ABIERTA.includes(x.estado));
    if (!o) return;
    const i = S.instalaciones.find(x => x.agente === o.agente && x.nombre === o.instalacion);
    if (!i) return;
    HUB.api('GET', `/ordenes/${encodeURIComponent(o.id)}`).then(d => { seguir(d, { instId: i.id, version: o.release, desde: i.version }); refrescarDep(); }).catch(() => {});
  }

  async function ordenar(i, tipo, release) {
    const o = await llamar('POST', '/ordenes', { agente: i.agente, instalacion: i.nombre, tipo, release: release || null });
    seguir(Object.assign(o, { eventos: [] }), { instId: i.id, version: release || (i.puntoRetorno && i.puntoRetorno.version), desde: i.version });
    UI.vista = 'desplegar';
    if (location.hash === '#desplegar') render(); else location.hash = 'desplegar';
  }

  function bannerHub() {
    if (!DEPH || UI.vista === 'desplegar' || !ABIERTA.includes(DEPH.orden.estado)) return '';
    const i = inst(DEPH.instId);
    if (!i) return '';
    const txt = { preflight: 'Preflight en curso', desplegar: 'Despliegue en curso', rollback: 'Vuelta atrás en curso' }[DEPH.orden.tipo];
    return `<div style="padding:14px 28px 0"><div class="aviso a-info" style="align-items:center">${I.rocket}<div style="flex:1"><b>${txt}</b> · ${prod(i.producto).nombre} ${esc(i.nombre)}${DEPH.orden.release ? ' → ' + esc(DEPH.orden.release) : ''}. La ejecuta el agente: podés navegar tranquilo.</div><a class="btn btn-sec btn-chico" href="#desplegar">Ver</a></div></div>`;
  }

  function vOrdenHub() {
    if (!DEPH) return `<div class="vacio">No hay ninguna operación en curso. Elegí una versión desde el <a href="#catalogo">catálogo</a>.</div>`;
    const o = DEPH.orden, i = inst(DEPH.instId), p = prod(i.producto), ag = agente(i.agente);
    const abierta = ABIERTA.includes(o.estado);
    const titulo = { preflight: 'Preflight', desplegar: 'Despliegue autoservicio', rollback: 'Vuelta atrás manual' }[o.tipo];
    // al terminar, la instalación recargada ya no tiene ese punto de retorno: vale el de la orden
    const destino = o.tipo === 'rollback' ? DEPH.version || o.release : o.release;
    const estado = {
      pendiente: `<span class="pill p-warn">Esperando al agente</span>`, entregada: `<span class="pill p-info">El agente la tomó</span>`,
      en_curso: `<span class="pill p-info">En curso</span>`, cancelada: `<span class="pill p-neutro">Retirada</span>`
    }[o.estado] || { ok: '<span class="pill p-ok">OK</span>', revertido: '<span class="pill p-warn">Revertido</span>', abortado: '<span class="pill p-crit">Bloqueado</span>' }[o.resultado] || `<span class="pill p-crit">${esc(o.resultado || o.estado)}</span>`;
    const falla = (o.eventos || []).some(e => e.evento === 'despliegue_falla');
    let panel = '';
    if (o.estado === 'pendiente') {
      panel = `<div class="aviso ${ag.estado === 'online' ? 'a-info' : 'a-warn'}">${I.clock}<div>${ag.estado === 'online' ? 'El agente la toma en segundos.' : `El agente de <code>${esc(ag.host)}</code> está fuera de línea: la orden espera a que reconecte.`}</div></div>
        <div class="fila"><button class="btn btn-sec" data-a="retirar-orden">Retirar la orden</button></div>`;
    } else if (abierta && falla && o.tipo === 'desplegar') {
      panel = `<div class="aviso a-crit">${I.alert}<div><b>La verificación falló.</b> Si nadie lo cancela, el agente vuelve a ${esc(DEPH.desde)} con los digests guardados.${o.cancelar_rollback ? ' <b>Pediste cancelar la vuelta atrás:</b> el agente lo ve en su próxima consulta.' : ''}</div></div>
        ${o.cancelar_rollback ? '' : `<div class="fila"><button class="btn btn-sec" data-a="cancelar-rollback-hub">Cancelar la vuelta atrás y dejar para diagnóstico</button></div>`}`;
    } else if (!abierta && o.tipo === 'preflight') {
      const comp = ((o.resumen || {}).preflight || {}).comprobaciones || [];
      const tabla = comp.map(c => `<div class="chk"><span class="ic ${c.ok ? 'ok' : c.bloqueante ? 'fallo' : 'warn'}">${c.ok ? '✓' : c.bloqueante ? '✕' : '!'}</span><div><b>${esc(c.nombre)}</b><small>${esc(c.detalle || '')}</small></div><span></span></div>`).join('');
      panel = `${tabla ? `<section class="panel"><div class="panel-cab"><h2>Comprobaciones</h2><span class="suave chico">sin tocar lo que está corriendo</span></div><div class="panel-cuerpo checks">${tabla}</div></section>` : ''}
        ${o.resultado === 'ok' ? `<section class="panel"><div class="panel-cab"><div><h2>Confirmá el despliegue</h2><p>El preflight pasó. El agente toma un punto de retorno antes de tocar nada y vuelve atrás solo si la verificación falla.</p></div></div>
          <div class="panel-cuerpo fila"><button class="btn btn-pri" data-a="desplegar-hub" ${puedeEjecutar().ok ? '' : 'disabled title="' + esc(puedeEjecutar().motivo) + '"'}>${I.rocket} Desplegar ${esc(o.release)} ahora</button><button class="btn btn-sec" data-a="cerrar-dep">Cancelar</button></div></section>`
        : `<div class="aviso a-crit">${I.alert}<div><b>El preflight no pasó.</b> ${esc(o.detalle || '')} Si falta una variable, cargala en el <code>.env</code> del servidor y repetí el preflight.</div></div>
          <div class="fila"><button class="btn btn-pri" data-a="repetir-preflight">Repetir preflight</button><button class="btn btn-sec" data-a="cerrar-dep">Cerrar</button></div>`}`;
    } else if (!abierta) {
      const res = {
        ok: ['a-ok', '✓', 'var(--verde)', o.tipo === 'rollback' ? `${p.nombre} volvió ${destino ? 'a ' + destino : 'al punto de retorno'}.` : `${p.nombre} ${o.release} desplegada y verificada.`],
        revertido: ['a-warn', '↩', 'var(--naranja)', `La verificación falló y el agente volvió a ${DEPH.desde}.`],
        degradado: ['a-crit', '!', 'var(--rojo)', 'La instalación quedó con fallo, sin vuelta atrás automática.']
      }[o.resultado] || ['a-crit', '!', 'var(--rojo)', o.estado === 'cancelada' ? 'La orden se retiró antes de que el agente la tomara.' : 'La orden no se completó.'];
      panel = `<div class="resultado aviso ${res[0]}" style="padding:18px"><span class="big" style="background:${res[2]}">${res[1]}</span><div class="pila" style="gap:6px"><b style="font-size:1.05rem">${esc(res[3])}</b>${o.detalle ? `<span class="suave chico">${esc(o.detalle)}</span>` : ''}
        <div class="fila"><button class="btn btn-sec btn-chico" data-a="ver-inst" data-id="${esc(i.id)}">Ver la instalación</button><button class="btn btn-fantasma btn-chico" data-a="cerrar-dep">Cerrar</button></div></div></div>`;
    } else {
      panel = `<div class="aviso a-info">${I.rocket}<div>La ejecuta el agente, no el navegador: si cerrás esta pestaña, sigue igual.</div></div>`;
    }
    return `
      <div class="migas"><a href="#catalogo">Catálogo</a><span>/</span><span>${p.nombre} · ${esc(i.nombre)}</span></div>
      <div class="cabecera"><div class="t"><span class="eyebrow">${titulo}</span><h1>${p.nombre} ${esc(DEPH.desde || i.version)} → ${esc(destino || '')}</h1>
        <p>Instalación <code>${esc(i.nombre)}</code> en <code>${esc(ag.host)}</code> · orden <code>${esc(o.id)}</code> · pedida por ${esc(o.pedida_por)}</p></div>${estado}</div>
      ${panel}
      <section class="panel"><div class="panel-cab"><h2>Eventos del agente</h2><span class="suave chico">se actualiza solo</span></div><div class="panel-cuerpo">${consolaEventos(o)}</div></section>`;
  }

  const ACCIONES_HUB = {
    // los campos se leen antes de paso(), que vuelve a dibujar el formulario
    'hub-ingresar': () => {
      const email = $('#login-email').value.trim(), clave = $('#login-pass').value;
      UI.loginHub.email = email;
      paso(async () => {
      const st = await HUB.ingresar(email, clave);
      if (st.paso === 'listo') return recargar();
      Object.assign(UI.loginHub, st);
      });
    },
    'hub-verificar': () => {
      const codigo = codigoTotp();
      if (!/^\d{6}$/.test(codigo)) { toast('Ingresá los 6 dígitos del código', true); return; }
      paso(async () => { await HUB.verificar(UI.loginHub.factorId, codigo); await recargar(); });
    },
    'hub-token': () => { const t = $('#login-token').value.trim(); paso(async () => { HUB.usarToken(t); await recargar(); }); },
    'logout': async () => { clearInterval(sondeo); DEPH = null; await HUB.salir(); S = vacio(); UI.drawer = null; UI.loginHub = { paso: 'credenciales' }; location.hash = ''; render(); },
    'recargar': () => { UI.drawer = null; recargar(); },
    'iniciar-deploy': d => ordenar(inst(UI.inst), 'preflight', d.v).catch(() => {}),
    'repetir-preflight': () => ordenar(inst(DEPH.instId), 'preflight', DEPH.orden.release).catch(() => {}),
    'desplegar-hub': () => ordenar(inst(DEPH.instId), 'desplegar', DEPH.orden.release).catch(() => {}),
    'confirmar-rollback': d => { UI.modal = null; ordenar(inst(d.id), 'rollback').catch(() => render()); },
    'retirar-orden': () => llamar('POST', `/ordenes/${encodeURIComponent(DEPH.orden.id)}/cancelar`, null, 'Orden retirada').then(() => recargar(true)).catch(() => {}),
    'cancelar-rollback-hub': () => llamar('POST', `/ordenes/${encodeURIComponent(DEPH.orden.id)}/cancelar-rollback`, null, 'Pedido enviado: el agente lo ve en segundos').then(() => { DEPH.orden.cancelar_rollback = true; refrescarDep(); }).catch(() => {}),
    'cerrar-dep': () => { clearInterval(sondeo); const id = DEPH && DEPH.instId; DEPH = null; if (id) { UI.inst = id; location.hash = 'instalacion'; } else location.hash = ''; render(); },
    'ver-log': async d => {
      UI.drawer = { tipo: 'log', id: d.id, orden: null }; render();
      try { const o = await HUB.api('GET', `/ordenes/${encodeURIComponent(d.id)}`); if (UI.drawer && UI.drawer.id === d.id) { UI.drawer.orden = o; render(); } } catch (e) { toast(e.message, true); }
    },
    'invitar': () => { UI.modal = { tipo: 'alta-usuario' }; render(); },
    'alta-usuario-tenant': () => { UI.modal = { tipo: 'alta-usuario', tenant: UI.tenantParam }; render(); },
    'enviar-alta': d => {
      const id = $('#alta-id').value.trim();
      llamar('PUT', `/usuarios/${encodeURIComponent(id)}`, { rol: $('#alta-rol').value, tenant: d.t, email: $('#alta-mail').value.trim() || null, nombre: $('#alta-nombre').value.trim() || null }, 'Usuario dado de alta')
        .then(() => { UI.modal = null; recargar(); }).catch(() => {});
    },
    'nuevo-cliente': () => { UI.modal = { tipo: 'nuevo-cliente' }; render(); },
    'crear-cliente': () => {
      const id = $('#nc-id').value.trim();
      llamar('POST', '/tenants', { id, nombre: $('#nc-nombre').value.trim() }, 'Cliente creado').then(() => { UI.modal = null; UI.tenantParam = id; recargar(); }).catch(() => {});
    },
    'agregar-prod': () => {
      const p = $('#nuevo-prod').value, hasta = new Date(Date.now() + 365 * 864e5).toISOString().slice(0, 10);
      llamar('PUT', `/tenants/${encodeURIComponent(UI.tenantParam)}/productos/${encodeURIComponent(p)}`, { mantenimiento_hasta: hasta, autoservicio: true, canal: 'estable' }, `${prod(p).nombre} agregado`).then(() => recargar()).catch(() => {});
    },
    'generar-codigo': () => {
      const t = $('#enr-t').value, host = $('#enr-host').value.trim() || null;
      llamar('POST', `/tenants/${encodeURIComponent(t)}/codigos`, { host }).then(r => { UI.modal = { tipo: 'enrolar', codigo: r.codigo, expira: r.expira, host }; render(); recargar(true); }).catch(() => {});
    },
    'revocar': d => llamar('POST', `/agentes/${encodeURIComponent(d.id)}/revocar`, null, 'Agente revocado').then(() => { UI.modal = null; recargar(); }).catch(() => {}),
    'habilitar': () => {
      const horas = parseInt($('#hab-horas').value, 10);
      llamar('POST', '/habilitaciones', { horas, motivo: $('#hab-motivo').value.trim() || null }, `Accusys habilitada por ${horas} h`).then(() => recargar()).catch(() => {});
    },
    'revocar-habilitacion': d => llamar('POST', `/habilitaciones/${d.id}/revocar`, null, 'Habilitación revocada').then(() => recargar()).catch(() => {}),
    'pedir-habilitacion': () => {},
    'publicar': () => {},
    'reset': () => {},
    'canjear': () => {}
  };

  function cambioHub(el, f) {
    if (f === 'rol-usuario') {
      const x = S.usuarios.find(u => u.id === el.dataset.id);
      llamar('PUT', `/usuarios/${encodeURIComponent(x.id)}`, { rol: el.value, tenant: x.tenant, nombre: x.nombre, email: x.email || null }, `${x.nombre} ahora es ${ROLES[el.value].nombre}`)
        .then(() => recargar(true)).catch(() => recargar());
      return true;
    }
    if (f === 'param') {
      const t = tenant(UI.tenantParam), p = tp(t.id, el.dataset.p), k = el.dataset.k;
      const nuevo = Object.assign({}, p, { [k]: el.type === 'checkbox' ? el.checked : el.value });
      llamar('PUT', `/tenants/${encodeURIComponent(t.id)}/productos/${encodeURIComponent(p.producto)}`,
        { mantenimiento_hasta: nuevo.mantenimientoHasta, autoservicio: nuevo.autoservicio, canal: nuevo.canal }, 'Parametría actualizada · queda en auditoría')
        .then(() => recargar()).catch(() => recargar());
      return true;
    }
    if (f === 'tenant' && el.dataset.k === 'estado') {
      llamar('PUT', `/tenants/${encodeURIComponent(UI.tenantParam)}/estado`, { estado: el.value }, 'Cliente actualizado · queda en auditoría').then(() => recargar()).catch(() => recargar());
      return true;
    }
    return ['tenant', 'param-amb', 'simular'].includes(f);
  }

  function manifiesto(p, r) {
    return {
      producto: p, release: r.version, publicado: r.publicado, canal: r.canal,
      imagenes: Object.fromEntries(Object.entries(r.imagenes).map(([k, d]) => [k, `registry.accusys.com.ar/${p}/${k}@${d}`])),
      desde_version: r.desde, db_migrations: r.db, rollback_seguro: r.rollbackSeguro, critico_seguridad: r.critico,
      variables_nuevas: r.variables.map(v => ({ nombre: v.nombre, obligatoria: v.obligatoria, default: v.def, descripcion: v.descripcion })),
      healthchecks: Object.keys(r.imagenes).map(s => ({ servicio: s, url: `http://${s}:8080/health`, espera: 200, timeout_s: 120 })),
      changelog: `${p}/releases/${r.version}.md`, firma: r.firma || 'cosign:MEUCIQC…'
    };
  }
  function logFicticio(h) {
    const [, hh] = h.fecha.split(' ');
    const L = (t, c) => ({ h: hh + ':' + String(Math.floor(Math.random() * 50) + 10), t, c });
    const base = [L(`orden recibida por deploy-agent`, 'info'), L('firma del manifiesto verificada', 'ok'), L(`punto de retorno: retorno/${h.desde}/`), L('docker compose up -d', 'info'), L('esperando healthchecks (timeout 120 s)')];
    if (h.resultado === 'rollback') return base.concat([L('api  GET /health → 500', 'err'), L('verificación FALLIDA · rollback en 60 s', 'warn'), L(`rollback automático: restaurado ${h.desde} por digest`, 'ok')]);
    if (h.tipo === 'Asistido') return [L('ventana asistida con Soporte Accusys', 'info'), L('backup de base verificado', 'ok')].concat(base.slice(2), [L('migraciones aplicadas al arrancar', 'warn'), L('verificación ok', 'ok')]);
    return base.concat([L('api  GET /health → 200', 'ok'), L('smoke tests: 4/4 ok', 'ok'), L('despliegue verificado', 'ok')]);
  }

  function validarManifiesto(txt) {
    const out = [];
    let m;
    try { m = JSON.parse(txt); } catch (e) { return [{ campo: 'JSON', ok: false, msg: 'No es JSON válido: ' + e.message }]; }
    out.push({ campo: 'producto', ok: !!prod(m.producto), msg: prod(m.producto) ? `${prod(m.producto).nombre}: se ofrece a ${S.tenants.filter(t => tp(t.id, m.producto)).length} clientes que lo tienen` : 'Producto desconocido: el hub no sabe a quién ofrecerlo' });
    const ult = prod(m.producto) ? ultimaEstable(m.producto) : null;
    const verOk = /^\d+\.\d+\.\d+(-[\w.]+)?$/.test(m.release || '') && (!ult || cmpV(m.release, ult.version) > 0) && !rel(m.producto, m.release);
    out.push({ campo: 'release', ok: verOk, msg: verOk ? `${m.release} es posterior a ${ult ? ult.version : '—'}` : `Tiene que ser una versión nueva, mayor a ${ult ? ult.version : '—'}` });
    out.push({ campo: 'publicado', ok: /^\d{4}-\d{2}-\d{2}$/.test(m.publicado || ''), msg: 'Contra esta fecha se compara el mantenimiento de cada cliente' });
    const imgs = m.imagenes && Object.values(m.imagenes);
    const digOk = imgs && imgs.length && imgs.every(x => /@sha256:/.test(x));
    out.push({ campo: 'imagenes', ok: !!digOk, msg: digOk ? `${imgs.length} imágenes identificadas por digest` : 'Sin digest no hay rollback exacto. Es obligatorio' });
    const hcOk = Array.isArray(m.healthchecks) && m.healthchecks.length > 0;
    out.push({ campo: 'healthchecks', ok: hcOk, msg: hcOk ? `${m.healthchecks.length} healthchecks declarados` : 'Sin healthchecks no hay criterio de éxito ni rollback automático' });
    out.push({ campo: 'desde_version', ok: /^>=\s*\d+\.\d+\.\d+/.test(m.desde_version || ''), msg: 'Rutas de upgrade soportadas; el preflight bloquea las demás' });
    if (m.db_migrations) out.push({ campo: 'db_migrations', ok: 'warn', msg: 'true: se publica como asistido, sin botón de autoservicio' });
    if (m.critico_seguridad) out.push({ campo: 'critico_seguridad', ok: 'warn', msg: 'true: se habilita también a clientes con mantenimiento vencido' });
    return out;
  }

  /* ============================================================
     Eventos
     ============================================================ */
  document.addEventListener('click', e => {
    const el = e.target.closest('[data-a]');
    if (!el) return;
    // clicks dentro de drawer/modal no deben cerrar el velo
    if ((el.dataset.a === 'cerrar-drawer' || el.dataset.a === 'cerrar-modal') && el.classList.contains('velo') && e.target.closest('[data-stop]')) return;
    const a = el.dataset.a, d = el.dataset;
    const acc = (MODO === 'hub' && ACCIONES_HUB[a]) || ACCIONES[a];
    if (acc) { e.preventDefault(); acc(d, el, e); }
  });

  document.addEventListener('keydown', e => {
    if (e.key === 'Escape' && (UI.drawer || UI.modal)) { UI.drawer = null; UI.modal = null; render(); }
    if (e.key === 'Enter' && e.target.matches('article[data-a]')) e.target.click();
    if (e.key === 'Enter' && e.target.closest('.login-form') && e.target.matches('input')) { const b = $('.login-form .btn-pri'); if (b && !b.disabled) b.click(); }
  });

  const ACCIONES = {
    'login-user': d => { UI.login = { user: d.id, paso: 1 }; render(); },
    'login-1': () => { UI.login.paso = 2; render(); const f = $('#totp-0'); if (f) f.focus(); },
    'login-autocompletar': () => { '482913'.split('').forEach((n, k) => { $('#totp-' + k).value = n; }); },
    'login-2': () => {
      const code = [0, 1, 2, 3, 4, 5].map(k => $('#totp-' + k).value).join('');
      if (!/^\d{6}$/.test(code)) { toast('Ingresá los 6 dígitos del código', true); return; }
      S.sesion = UI.login.user; UI.vista = ''; guardar(); location.hash = ''; render();
    },
    'menu': () => $('#shell').classList.toggle('menu-abierto'),
    'abrir-mails': () => { UI.drawer = { tipo: 'mails' }; render(); },
    'abrir-usuarios': () => { UI.drawer = { tipo: 'usuarios' }; render(); },
    'cerrar-drawer': () => { UI.drawer = null; render(); },
    'cerrar-modal': () => { UI.modal = null; render(); },
    'cambiar-user': d => { S.sesion = d.id; UI.drawer = null; UI.vista = ''; UI.inst = null; UI.prod = null; guardar(); location.hash = ''; render(); toast('Sesión cambiada a ' + usuarioActual().nombre); },
    'logout': () => { S.sesion = null; UI.drawer = null; UI.login = { user: 'u1', paso: 1 }; guardar(); render(); },
    'pedir-reset': () => { UI.drawer = null; UI.modal = { tipo: 'reset' }; render(); },
    'reset': () => { const ses = S.sesion; if (DEP) clearInterval(DEP.timer); DEP = null; S = nuevo(); S.sesion = ses; UI.modal = null; guardar(); render(); toast('Datos de demo reiniciados'); },
    'ver-inst': d => { UI.inst = d.id; UI.vista = 'instalacion'; location.hash = 'instalacion'; render(); },
    'ir-catalogo': (d, el, e) => { e.stopPropagation(); const i = inst(d.id); UI.prod = i.producto; UI.inst = i.id; UI.vista = 'catalogo'; if (location.hash === '#catalogo') render(); else location.hash = 'catalogo'; },
    'cat-prod': d => { UI.prod = d.id; UI.inst = null; render(); },
    'ver-manifiesto': (d, el, e) => { e.stopPropagation(); UI.drawer = { tipo: 'manifiesto', p: d.p, v: d.v }; render(); },
    'ver-log': d => { UI.drawer = { tipo: 'log', id: d.id }; render(); },
    'iniciar-deploy': d => iniciarDeploy(UI.inst, d.v),
    'coordinar': d => { UI.modal = { tipo: 'coordinar', v: d.v }; render(); },
    'enviar-coordinar': () => {
      const i = inst(UI.inst);
      mail(i.tenant, `Pedido de ventana asistida · ${prod(i.producto).nombre} ${UI.modal.v}`, `Fecha propuesta ${$('#coord-fecha').value}. ${$('#coord-nota').value}`, 'soporte@accusys.example');
      UI.modal = null; guardar(); render(); toast('Pedido enviado a Soporte de Accusys');
    },
    'guardar-vars': () => {
      const i = inst(DEP.instId), r = rel(i.producto, DEP.version);
      document.querySelectorAll('[data-var]').forEach(x => { if (x.value.trim()) DEP.vars[x.dataset.var] = x.value.trim(); });
      const falta = r.variables.filter(v => v.obligatoria && !(v.nombre in i.variables) && !(v.nombre in DEP.vars));
      if (falta.length) { toast(`Falta ${falta.map(v => v.nombre).join(', ')}. Es obligatoria.`, true); return; }
      Object.assign(i.variables, DEP.vars);
      toast('Variables escritas en el .env del servidor');
      correrPreflight(i, r);
    },
    'usar-defaults': () => { const i = inst(DEP.instId), r = rel(i.producto, DEP.version); r.variables.forEach(v => { const x = document.querySelector(`[data-var="${v.nombre}"]`); if (x && !x.value) x.value = v.def; }); },
    'pedir-aprobacion': () => { DEP.fase = 'aprobacion'; const i = inst(DEP.instId); mail(i.tenant, `Orden pendiente de aprobación · ${prod(i.producto).nombre} ${DEP.version}`, `Pedida por ${usuarioActual().nombre}.`); render(); toast('Orden enviada al Aprobador'); },
    'aprobar': () => { const t = tenant(inst(DEP.instId).tenant); const ap = S.usuarios.find(x => x.tenant === t.id && x.rol === 'aprobador'); log(`orden aprobada por ${ap ? ap.nombre : 'Aprobador'}`, 'ok'); toast('Orden aprobada'); ejecutar(); },
    'ejecutar': () => ejecutar(),
    'cancelar-dep': () => { DEP = null; location.hash = 'catalogo'; toast('Operación cancelada. No se tocó nada del servidor.'); },
    'revertir-ya': () => { if (DEP.fase === 'retenido') { const id = DEP.instId; DEP = null; rollbackManual(id); } else rollback('operador'); },
    'cancelar-rollback': () => cancelarRollback(),
    'cerrar-dep': () => { const id = DEP.instId; DEP = null; UI.inst = id; location.hash = 'instalacion'; render(); },
    'pedir-rollback': (d, el, e) => { e.stopPropagation(); UI.modal = { tipo: 'rollback', id: d.id }; render(); },
    'confirmar-rollback': d => {
      UI.modal = null;
      if (DEP && !['ok', 'revertido', 'retenido'].includes(DEP.fase)) { render(); toast('Hay otra operación en curso: el agente toma un lock por instalación.', true); return; }
      DEP = null; rollbackManual(d.id);
    },
    'invitar': () => { UI.modal = { tipo: 'invitar' }; render(); },
    'enviar-invitacion': () => {
      const u = usuarioActual(), mailTxt = $('#inv-mail').value, rol = $('#inv-rol').value;
      S.usuarios.push({ id: 'u' + Date.now(), nombre: mailTxt.split('@')[0].replace(/\./g, ' ').replace(/\b\w/g, c => c.toUpperCase()), email: mailTxt, tenant: u.tenant, rol });
      UI.modal = null; guardar(); render(); toast('Invitación enviada a ' + mailTxt);
    },
    // Accusys
    'ver-parque': d => { UI.drawer = { tipo: 'parque', t: d.t, p: d.p }; render(); },
    'pedir-habilitacion': d => { mail(d.t, `Accusys pide habilitación para asistir ${prod(d.p).nombre}`, `Pedido por ${usuarioActual().nombre}. Queda registrado en el historial.`); auditar(`Pedido de habilitación para asistir ${prod(d.p).nombre}`, d.t); UI.drawer = null; guardar(); render(); toast('Pedido enviado al cliente'); },
    'param-t': d => { UI.tenantParam = d.id; render(); },
    'agregar-prod': () => {
      const t = tenant(UI.tenantParam), p = $('#nuevo-prod').value;
      t.productos.push({ producto: p, mantenimientoHasta: '2027-09-30', autoservicio: true, canal: 'estable', ambientes: ['produccion'] });
      auditar(`${prod(p).nombre} agregado como producto adquirido`, t.id); guardar(); render(); toast(`${prod(p).nombre} agregado. Credencial del registry actualizada.`);
    },
    'rel-prod': d => { UI.prodRel = d.id; render(); },
    'publicar': () => {
      const p = UI.prodRel || 'mep', ult = rels(p).filter(r => !r.version.includes('-')).slice(-1)[0];
      const [a, b, c] = parseV(ult.version).n;
      const nueva = Object.assign({}, ult, { version: `${a}.${b}.${c + 1}`, publicado: S.hoy, variables: [], critico: false, db: false, rollbackSeguro: true, canal: 'estable' });
      const man = manifiesto(p, nueva); delete man.firma;
      man.imagenes = Object.fromEntries(Object.keys(man.imagenes).map(k => [k, `registry.accusys.com.ar/${p}/${k}@sha256:${Math.random().toString(16).slice(2, 8)}…`]));
      UI.drawer = { tipo: 'publicar', json: JSON.stringify(man, null, 2), val: null }; render();
    },
    'validar-manifiesto': () => { UI.drawer.json = $('#pub-json').value; UI.drawer.val = validarManifiesto(UI.drawer.json); render(); },
    'firmar-publicar': () => {
      UI.drawer.json = $('#pub-json').value;
      const val = validarManifiesto(UI.drawer.json);
      if (val.some(v => v.ok === false)) { UI.drawer.val = val; render(); toast('El manifiesto tiene errores: no se firma', true); return; }
      const m = JSON.parse(UI.drawer.json);
      const r = { version: m.release, publicado: m.publicado, canal: m.canal || 'estable', desde: m.desde_version, db: !!m.db_migrations, rollbackSeguro: m.rollback_seguro !== false, critico: !!m.critico_seguridad,
        imagenes: Object.fromEntries(Object.entries(m.imagenes).map(([k, v]) => [k, v.split('@')[1]])),
        variables: (m.variables_nuevas || []).map(v => ({ nombre: v.nombre, obligatoria: !!v.obligatoria, def: v.default || '', descripcion: v.descripcion || '' })),
        changelog: ['Release publicado desde el mock.'], firma: 'cosign:MEUCIQ' + Math.random().toString(36).slice(2, 8) + '…' };
      rels(m.producto).push(r);
      rels(m.producto).sort((x, y) => cmpV(x.version, y.version));
      const destinos = S.tenants.filter(t => tp(t.id, m.producto) && (r.canal === 'estable' || tp(t.id, m.producto).canal === 'anticipado'));
      destinos.forEach(t => mail(t.id, `${prod(m.producto).nombre} ${r.version} disponible`, r.db ? 'Release asistido: trae migración de base.' : 'Ya está en tu catálogo con su changelog.'));
      auditar(`Release ${prod(m.producto).nombre} ${r.version} firmado y publicado (canal ${r.canal})`);
      UI.prodRel = m.producto; UI.drawer = null; guardar(); render();
      toast(`${prod(m.producto).nombre} ${r.version} publicado · aviso a ${destinos.length} clientes`);
    },
    'enrolar': () => { UI.modal = { tipo: 'enrolar' }; render(); },
    'generar-codigo': () => {
      const t = $('#enr-t').value, host = $('#enr-host').value.trim() || 'srv-nuevo';
      const cod = 'DC-' + Math.random().toString(36).slice(2, 6).toUpperCase() + '-' + Math.random().toString(36).slice(2, 6).toUpperCase();
      S.agentes.push({ id: 'ag-' + t + '-' + Date.now().toString(36).slice(-3), tenant: t, host, version: AGENTE_ULTIMA, estado: 'pendiente', visto: 'nunca', disco: 60 });
      auditar(`Código de enrolamiento emitido para ${host}`, t);
      UI.modal = { tipo: 'enrolar', codigo: cod }; guardar(); render();
    },
    'copiar-cmd': () => {
      const txt = $('.modal .cmd').textContent;
      const sel = () => { const r = document.createRange(); r.selectNodeContents($('.modal .cmd')); const s = getSelection(); s.removeAllRanges(); s.addRange(r); };
      try { navigator.clipboard.writeText(txt).then(() => toast('Comando copiado'), () => { sel(); toast('Seleccionado: copialo con Ctrl+C'); }); } catch (e) { sel(); }
    },
    'canjear': d => { const a = agente(d.id); a.estado = 'online'; a.visto = 'hace 1 s'; auditar(`Agente ${a.host} enrolado (código canjeado)`, a.tenant); guardar(); render(); toast('Agente en línea'); },
    'pedir-revocar': d => { UI.modal = { tipo: 'revocar', id: d.id }; render(); },
    'revocar': d => { const a = agente(d.id); a.estado = 'revocado'; a.visto = '—'; auditar(`Agente ${a.host} revocado: token y credencial del registry`, a.tenant); UI.modal = null; guardar(); render(); toast('Agente revocado'); }
  };

  document.addEventListener('change', e => {
    const el = e.target, f = el.dataset.f;
    if (!f) return;
    if (MODO === 'hub' && cambioHub(el, f)) return;
    if (f === 'cat-inst') { UI.inst = el.value; render(); }
    else if (f === 'simular') DEP.simularFallo = el.checked;
    else if (f === 'rol-usuario') { const x = S.usuarios.find(u => u.id === el.dataset.id); x.rol = el.value; guardar(); toast(`${x.nombre} ahora es ${ROLES[x.rol].nombre}`); }
    else if (f === 'aud-t') { UI.filtroAud.tenant = el.value; render(); }
    else if (f === 'aud-tipo') { UI.filtroAud.tipo = el.value; render(); }
    else if (f === 'param') {
      const t = tenant(UI.tenantParam), p = tp(t.id, el.dataset.p), k = el.dataset.k;
      p[k] = el.type === 'checkbox' ? el.checked : el.value;
      auditar(`${prod(p.producto).nombre}: ${k} = ${p[k]}`, t.id); guardar(); render(); toast('Parametría actualizada · queda en auditoría');
    } else if (f === 'param-amb') {
      const t = tenant(UI.tenantParam), p = tp(t.id, el.dataset.p);
      p.ambientes = el.checked ? [...new Set(p.ambientes.concat(el.value))] : p.ambientes.filter(a => a !== el.value);
      auditar(`${prod(p.producto).nombre}: ambientes = ${p.ambientes.join(', ')}`, t.id); guardar(); toast('Ambientes actualizados');
    } else if (f === 'tenant') {
      const t = tenant(UI.tenantParam), k = el.dataset.k;
      t[k] = k === 'avisos' ? el.value.split(/\n+/).map(s => s.trim()).filter(Boolean) : el.type === 'checkbox' ? el.checked : el.value;
      auditar(`Cliente: ${k} = ${Array.isArray(t[k]) ? t[k].join(', ') : t[k]}`, t.id); guardar(); render(); toast('Cliente actualizado · queda en auditoría');
    }
  });

  document.addEventListener('input', e => {
    const el = e.target;
    if (el.dataset.totp != null) {
      const n = +el.dataset.totp, dig = el.value.replace(/\D/g, '');
      // pegar el código entero en cualquier casilla lo reparte
      if (dig.length > 1) { dig.slice(0, 6 - n).split('').forEach((c, k) => { $('#totp-' + (n + k)).value = c; }); const u = Math.min(5, n + dig.length - 1); $('#totp-' + u).focus(); return; }
      el.value = dig.slice(0, 1);
      if (el.value && n < 5) $('#totp-' + (n + 1)).focus();
    }
    if (el.dataset.var && DEP) DEP.vars[el.dataset.var] = el.value;
  });

  window.addEventListener('hashchange', () => {
    UI.vista = location.hash.replace('#', '');
    UI.drawer = null;
    const sh = $('#shell'); if (sh) sh.classList.remove('menu-abierto');
    render();
    window.scrollTo(0, 0);
  });

  UI.vista = location.hash.replace('#', '');
  HUB.iniciar().then(cfg => {
    if (!cfg) { render(); return; }   // sin hub: la maqueta
    MODO = 'hub';
    S = vacio(); DEP = null;
    document.title = 'deployHub';
    window.addEventListener('dc-sesion-vencida', () => { if (S.sesion) { S = vacio(); DEPH = null; UI.loginHub = { paso: 'credenciales', error: 'La sesión venció: volvé a ingresar.' }; render(); } });
    // el parque cambia solo (latidos, órdenes de otros): se refresca sin molestar
    setInterval(() => {
      const foco = document.activeElement;
      if (S.sesion && !UI.modal && !UI.drawer && document.visibilityState === 'visible' && !(foco && /INPUT|SELECT|TEXTAREA/.test(foco.tagName))) recargar(true);
    }, 20000);
    if (HUB.haySesion()) recargar(); else render();
  });
})();
