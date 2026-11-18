// Código compartido por la ventana principal (index.html) y la pestaña del mapa (mapa.html).
"use strict";
const AIS = {};

AIS.$ = id => document.getElementById(id);
AIS.fmt = (v, d) => (v === null || v === undefined || Number.isNaN(Number(v))) ? "–" : Number(v).toFixed(d);
AIS.dosCifras = n => String(n).padStart(2, "0");
AIS.hora = ts => { const d = new Date(ts * 1000); return [d.getHours(), d.getMinutes(), d.getSeconds()].map(AIS.dosCifras).join(":"); };
AIS.fechaHora = ts => { const d = new Date(ts * 1000); return d.getFullYear() + "-" + AIS.dosCifras(d.getMonth() + 1) + "-" + AIS.dosCifras(d.getDate()) + " " + AIS.hora(ts); };
AIS.hace = ts => {
  const s = Math.max(0, Math.round(Date.now() / 1000 - ts));
  if (s < 60) return s + " s";
  if (s < 3600) return Math.floor(s / 60) + " min " + (s % 60) + " s";
  if (s < 86400) return Math.floor(s / 3600) + " h " + Math.floor((s % 3600) / 60) + " min";
  return Math.floor(s / 86400) + " d " + Math.floor((s % 86400) / 3600) + " h";
};
AIS.distanciaKm = (lat1, lon1, lat2, lon2) => {
  const r = Math.PI / 180, p1 = lat1 * r, p2 = lat2 * r, dp = p2 - p1, dl = (lon2 - lon1) * r;
  const a = Math.sin(dp / 2) ** 2 + Math.cos(p1) * Math.cos(p2) * Math.sin(dl / 2) ** 2;
  return 6371.0088 * 2 * Math.asin(Math.sqrt(Math.min(1, a)));
};
AIS.escapar = t => String(t).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
AIS.tienePos = o => o && o.lat !== null && o.lat !== undefined && o.lon !== null && o.lon !== undefined;
AIS.ESTADOS_NAV = ["En navegación (motor)", "Fondeado", "Sin gobierno", "Maniobra restringida", "Restringido por calado",
  "Amarrado", "Varado", "Pescando", "Navegando a vela", "Reservado", "Reservado", "Remolcando a popa",
  "Remolcando / empujando", "Reservado", "AIS-SART / emergencia", "Sin definir"];

// Quieto: velocidad < 0,5 nudos, o estado "fondeado", "amarrado" o "varado". null = sin datos.
AIS.esQuieto = b => {
  if (b.sog_kn !== null && b.sog_kn !== undefined) return b.sog_kn < 0.5;
  if ([1, 5, 6].includes(b.estado_nav)) return true;
  if (b.estado_nav === 0 || b.estado_nav === 8) return false;
  return null;
};
AIS.recepcion = b => b.directo === 1 ? "directa" : (b.directo === 0 ? "retransmitida" : null);

// Distancia receptor-barco. En vivo: desde la posición actual del receptor.
// En histórico (o sin posición actual): la guardada en la base de datos al capturar.
AIS.distancia = (b, rx, enVivo) => {
  if (!AIS.tienePos(b)) return null;
  if (enVivo && AIS.tienePos(rx)) return AIS.distanciaKm(rx.lat, rx.lon, b.lat, b.lon);
  if (b.dist_km !== null && b.dist_km !== undefined) return b.dist_km;
  if (AIS.tienePos(rx)) return AIS.distanciaKm(rx.lat, rx.lon, b.lat, b.lon);
  return null;
};

AIS.colorBarco = mmsi => "hsl(" + ((mmsi * 47) % 360) + ",70%,42%)";
AIS.rumbo = b => (b.rumbo !== null && b.rumbo !== undefined) ? b.rumbo : ((b.cog !== null && b.cog !== undefined) ? b.cog : null);

AIS.svgBarco = (color, rumbo, sel, tam, retransmitido) => {
  const giro = rumbo === null ? 0 : rumbo;
  const forma = rumbo === null ? '<circle cx="11" cy="11" r="6"/>' : '<polygon points="11,2 18,20 11,16 4,20"/>';
  const anillo = sel ? '<circle cx="11" cy="11" r="10" fill="none" stroke="' + color + '" stroke-width="1.5"/>' : "";
  const borde = retransmitido ? 'stroke="#fff" stroke-width="1.5" stroke-dasharray="2 1.5"' : 'stroke="#fff" stroke-width="1.5"';
  return '<svg width="' + tam + '" height="' + tam + '" viewBox="0 0 22 22" style="transform:rotate(' + giro + 'deg);overflow:visible">' +
    '<g fill="' + color + '" ' + borde + '>' + forma + "</g>" + anillo + "</svg>";
};
AIS.SVG_RECEPTOR = '<svg width="30" height="30" viewBox="0 0 30 30"><circle cx="15" cy="15" r="13" fill="#d32f2f" stroke="#fff" stroke-width="2"/>' +
  '<path d="M15 8 L15 22 M10 22 L20 22 M11 10 Q15 6 19 10 M9 7 Q15 1 21 7" fill="none" stroke="#fff" stroke-width="2" stroke-linecap="round"/></svg>';

AIS.filasDetalle = (contenedor, filas) => {
  for (const [k, v] of filas) {
    if (v === null || v === undefined || v === "") continue;
    const p = document.createElement("div");
    const e = document.createElement("span");
    e.className = "etq";
    e.textContent = k + ": ";
    p.append(e, document.createTextNode(String(v)));
    contenedor.append(p);
  }
};

AIS.contenidoBarco = (b, rx, enVivo) => {
  const div = document.createElement("div");
  div.className = "popup-ais";
  const t = document.createElement("b");
  t.textContent = (b.nombre || "Sin nombre") + " · " + b.mmsi;
  div.append(t);
  const r = AIS.rumbo(b);
  const d = AIS.distancia(b, rx, enVivo);
  const rec = AIS.recepcion(b);
  AIS.filasDetalle(div, [
    ["País", b.pais],
    ["Clase", b.categoria],
    ["Tipo de nave", b.tipo_nave_desc || (b.tipo_nave ? "código " + b.tipo_nave : null)],
    ["Indicativo", b.indicativo],
    ["Destino", b.destino],
    ["Posición", AIS.fmt(b.lat, 5) + ", " + AIS.fmt(b.lon, 5)],
    ["Velocidad", AIS.fmt(b.sog_kn, 1) + " kn"],
    ["Rumbo", r === null ? null : r + "°"],
    ["Estado", b.estado_nav === null || b.estado_nav === undefined ? null : AIS.ESTADOS_NAV[b.estado_nav]],
    [enVivo ? "Distancia a ti (ahora)" : "Distancia al capturar", d === null ? null : AIS.fmt(d, 2) + " km"],
    ["Tu receptor al capturar", AIS.tienePos({ lat: b.rx_lat, lon: b.rx_lon }) ?
      AIS.fmt(b.rx_lat, 5) + ", " + AIS.fmt(b.rx_lon, 5) + (b.dist_km !== null && b.dist_km !== undefined ? " (a " + AIS.fmt(b.dist_km, 2) + " km)" : "") : "sin registrar"],
    ["Recepción", rec === null ? null : (rec === "directa" ? "directa (tu antena)" : "retransmitida por un repetidor")],
    ["Mensajes", b.n_mensajes],
    ["Última posición", b.ultima_posicion ? AIS.fechaHora(b.ultima_posicion) : null],
    ["Visto hace", b.ultima_vez ? AIS.hace(b.ultima_vez) : null]
  ]);
  if (AIS.abrirHistorial) {
    const bt = AIS.el("button", "boton-historial", "Ver historial de mensajes");
    bt.type = "button";
    bt.onclick = () => AIS.abrirHistorial(b.mmsi);
    div.append(bt);
  }
  return div;
};

// ---------------------------------------------------------------- filtros
AIS.Filtros = class {
  constructor(contenedor, alCambiar) {
    this.alCambiar = alCambiar;
    this.rx = null;
    this.enVivo = true;
    contenedor.classList.add("filtros");
    const campo = (etiqueta, el) => {
      const c = document.createElement("div");
      c.className = "campo";
      const l = document.createElement("label");
      l.textContent = etiqueta;
      c.append(l, el);
      contenedor.append(c);
      return el;
    };
    const select = opciones => {
      const s = document.createElement("select");
      for (const [v, t] of opciones) {
        const o = document.createElement("option");
        o.value = v;
        o.textContent = t;
        s.append(o);
      }
      return s;
    };
    this.texto = campo("Buscar", Object.assign(document.createElement("input"), { type: "search", placeholder: "Nombre o MMSI" }));
    this.pais = campo("País", select([["", "Todos"]]));
    this.mov = campo("Movimiento", select([["", "Todos"], ["mov", "En movimiento"], ["quieto", "Quieto"]]));
    this.nombre = campo("Nombre", select([["", "Todos"], ["con", "Con nombre"], ["sin", "Sin nombre"]]));
    this.rec = campo("Recepción", select([["", "Todas"], ["directa", "Directa"], ["retransmitida", "Retransmitida"]]));
    this.radio = campo("Radio (km)", Object.assign(document.createElement("input"), { type: "number", min: "0", step: "0.5", placeholder: "sin límite" }));
    const limpiar = document.createElement("button");
    limpiar.textContent = "Quitar filtros";
    limpiar.type = "button";
    limpiar.onclick = () => {
      for (const el of [this.texto, this.pais, this.mov, this.nombre, this.rec, this.radio]) el.value = "";
      this.alCambiar();
    };
    contenedor.append(limpiar);
    this.nota = document.createElement("span");
    this.nota.className = "nota-filtro";
    contenedor.append(this.nota);
    for (const el of [this.texto, this.pais, this.mov, this.nombre, this.rec, this.radio]) {
      el.addEventListener("input", () => this.alCambiar());
      el.addEventListener("change", () => this.alCambiar());
    }
  }

  actualizarPaises(lista) {
    const actual = this.pais.value;
    const conteo = new Map();
    for (const b of lista) if (b.pais) conteo.set(b.pais, (conteo.get(b.pais) || 0) + 1);
    const paises = [...conteo.keys()].sort((a, b) => a.localeCompare(b, "es"));
    const clave = paises.map(p => p + conteo.get(p)).join("|");
    if (clave === this._clave) return;
    this._clave = clave;
    this.pais.textContent = "";
    const todos = document.createElement("option");
    todos.value = "";
    todos.textContent = "Todos";
    this.pais.append(todos);
    for (const p of paises) {
      const o = document.createElement("option");
      o.value = p;
      o.textContent = p + " (" + conteo.get(p) + ")";
      this.pais.append(o);
    }
    if (actual && !conteo.has(actual)) {
      const o = document.createElement("option");
      o.value = actual;
      o.textContent = actual + " (0)";
      this.pais.append(o);
    }
    this.pais.value = actual;
  }

  radioKm() {
    const v = parseFloat(this.radio.value);
    return Number.isFinite(v) && v > 0 ? v : null;
  }

  activo() {
    return !!(this.texto.value.trim() || this.pais.value || this.mov.value || this.nombre.value || this.rec.value || this.radioKm() !== null);
  }

  pasa(b) {
    const t = this.texto.value.trim().toLowerCase();
    if (t && !String(b.mmsi).includes(t) && !(b.nombre || "").toLowerCase().includes(t)) return false;
    if (this.pais.value && b.pais !== this.pais.value) return false;
    if (this.mov.value) {
      const q = AIS.esQuieto(b);
      if (q === null || q !== (this.mov.value === "quieto")) return false;
    }
    if (this.nombre.value === "con" && !b.nombre) return false;
    if (this.nombre.value === "sin" && b.nombre) return false;
    if (this.rec.value && AIS.recepcion(b) !== this.rec.value) return false;
    const radio = this.radioKm();
    if (radio !== null) {
      const d = AIS.distancia(b, this.rx, this.enVivo);
      if (d === null || d > radio) return false;
    }
    return true;
  }

  aplicar(lista) {
    const radio = this.radioKm();
    if (radio !== null && !AIS.tienePos(this.rx) && this.enVivo) this.nota.textContent = "Para el radio se usa la distancia guardada; fija tu ubicación para medir desde donde estás.";
    else this.nota.textContent = "";
    return lista.filter(b => this.pasa(b));
  }
};

// ---------------------------------------------------------------- mapa
AIS.TRANSPARENTE = "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7";

AIS.Mapa = class {
  constructor(idDiv, op) {
    this.op = op || {};
    this.seleccion = null;
    this.popupAbierto = false;
    this.ajustado = false;
    this.fijando = false;
    this.mosaicosOk = 0;
    this.mosaicosError = 0;
    const m = L.map(idDiv, { center: [10.5, -75.0], zoom: 8, worldCopyJump: true });
    this.m = m;
    m.createPane("baseSinConexion");
    m.getPane("baseSinConexion").style.zIndex = 150;
    m.getContainer().classList.add("mapa-agua");
    this._baseSinConexion();
    this.mosaicos = L.tileLayer("/tiles/{z}/{x}/{y}.png", {
      maxZoom: 18, errorTileUrl: AIS.TRANSPARENTE,
      attribution: '© <a href="https://www.openstreetmap.org/copyright" target="_blank">OpenStreetMap</a> · Natural Earth'
    });
    this.mosaicos.on("tileload", e => { if (e.tile && e.tile.src !== AIS.TRANSPARENTE) { this.mosaicosOk += 1; this._estadoMosaicos(); } });
    this.mosaicos.on("tileerror", () => { this.mosaicosError += 1; this._estadoMosaicos(); });
    this.mosaicos.addTo(m);
    const soloBase = L.layerGroup();
    L.control.layers({ "OpenStreetMap (con caché local)": this.mosaicos, "Mapa base sin conexión": soloBase }, null, { position: "topright" }).addTo(m);
    L.control.scale({ imperial: false }).addTo(m);
    this.gEstelas = L.layerGroup().addTo(m);
    this.gReceptor = L.layerGroup().addTo(m);
    this.gBarcos = L.layerGroup().addTo(m);
    this.gSeleccion = L.layerGroup().addTo(m);
    m.on("popupopen", () => { this.popupAbierto = true; });
    m.on("popupclose", () => { this.popupAbierto = false; if (this._pendiente) this.dibujar(this._pendiente); });
    m.on("click", e => {
      if (this.fijando && this.op.alFijar) {
        this.fijando = false;
        m.getContainer().style.cursor = "";
        this.op.alFijar(e.latlng.lat, e.latlng.lng);
      }
    });
  }

  _baseSinConexion() {
    const B = window.BASE_OFFLINE;
    if (!B) return;
    const pane = "baseSinConexion";
    L.geoJSON(B.tierra, { pane, interactive: false, style: { color: "#b9b2a5", weight: 0.8, fillColor: "#f2efe9", fillOpacity: 1 } }).addTo(this.m);
    L.geoJSON(B.lagos, { pane, interactive: false, style: { color: "#8fb8d0", weight: 0.6, fillColor: "#aad3df", fillOpacity: 1 } }).addTo(this.m);
    L.geoJSON(B.rios, { pane, interactive: false, style: { color: "#7fb2d6", weight: 1.4 } }).addTo(this.m);
    L.geoJSON(B.fronteras, { pane, interactive: false, style: { color: "#9b7fa6", weight: 1.2, dashArray: "6 4" } }).addTo(this.m);
    this.lugares = L.layerGroup().addTo(this.m);
    const dibujarLugares = () => {
      this.lugares.clearLayers();
      const z = this.m.getZoom();
      const max = z < 6 ? 2 : z < 7 ? 4 : z < 8 ? 6 : z < 9 ? 8 : 11;
      for (const [lat, lon, nombre, rango] of B.lugares) {
        if (rango > max) continue;
        L.marker([lat, lon], {
          pane, interactive: false,
          icon: L.divIcon({ className: "lugar-sc", html: "<span>" + AIS.escapar(nombre) + "</span>", iconSize: [6, 6] })
        }).addTo(this.lugares);
      }
    };
    this.m.on("zoomend", dibujarLugares);
    dibujarLugares();
  }

  _estadoMosaicos() {
    if (!this.op.alEstadoMosaicos) return;
    if (this.mosaicosOk === 0 && this.mosaicosError >= 3) this.op.alEstadoMosaicos("sin internet: mapa base sin conexión (y mosaicos ya guardados en caché)");
    else if (this.mosaicosOk > 0) this.op.alEstadoMosaicos(this.mosaicosError > 0 ? "OpenStreetMap + mapa base sin conexión donde faltan mosaicos" : "OpenStreetMap");
  }

  modoFijar() {
    this.fijando = true;
    this.m.getContainer().style.cursor = "crosshair";
  }

  // d = {barcos (ya filtrados), estelas Map, segEstela, rx, pistaRx, enVivo, radioKm}
  dibujar(d) {
    this._pendiente = d;
    if (this.popupAbierto) return;
    this._pendiente = null;
    this.d = d;
    this.gBarcos.clearLayers();
    this.gEstelas.clearLayers();
    this.gReceptor.clearLayers();
    this.gSeleccion.clearLayers();
    const ahora = Date.now() / 1000;
    const desde = d.segEstela ? ahora - d.segEstela : -Infinity;
    for (const b of d.barcos) {
      if (!AIS.tienePos(b)) continue;
      const color = AIS.colorBarco(b.mmsi);
      const pts = (d.estelas.get(String(b.mmsi)) || []).filter(p => p[0] >= desde);
      if (pts.length > 1) L.polyline(pts.map(p => [p[1], p[2]]), { color, weight: 2, opacity: 0.75, interactive: false }).addTo(this.gEstelas);
      const sel = this.seleccion === b.mmsi;
      const tam = sel ? 30 : 22;
      const viejo = d.enVivo && ahora - b.ultima_vez > 600;
      const icono = L.divIcon({ className: "", iconSize: [tam, tam], iconAnchor: [tam / 2, tam / 2],
        html: AIS.svgBarco(color, AIS.rumbo(b), sel, tam, AIS.recepcion(b) === "retransmitida") });
      const mk = L.marker([b.lat, b.lon], { icon: icono, opacity: viejo ? 0.5 : 1, zIndexOffset: sel ? 1000 : 0 }).addTo(this.gBarcos);
      mk.bindTooltip(b.nombre || String(b.mmsi));
      mk.bindPopup(() => AIS.contenidoBarco(b, d.rx, d.enVivo));
      mk.on("click", () => { this.seleccion = b.mmsi; this._lineaSeleccion(); if (this.op.alSeleccionar) this.op.alSeleccionar(b.mmsi); });
    }
    this._dibujarReceptor();
    this._lineaSeleccion();
    if (!this.ajustado && (d.barcos.some(AIS.tienePos) || AIS.tienePos(d.rx))) this.ajustar();
  }

  _dibujarReceptor() {
    const d = this.d;
    const pista = (d.pistaRx || []).filter(p => AIS.tienePos(p));
    if (pista.length > 1) L.polyline(pista.map(p => [p.lat, p.lon]), { color: "#d32f2f", weight: 2, dashArray: "4 4", opacity: 0.7 }).addTo(this.gReceptor);
    for (const p of pista) {
      const c = L.circleMarker([p.lat, p.lon], { radius: 4, color: "#d32f2f", weight: 1, fillColor: "#fff", fillOpacity: 1 }).addTo(this.gReceptor);
      const div = document.createElement("div");
      div.className = "popup-ais";
      const t = document.createElement("b");
      t.textContent = "Receptor (registro)";
      div.append(t);
      AIS.filasDetalle(div, [["Sesión", p.sesion_id], ["Hora", AIS.fechaHora(p.ts)], ["Posición", AIS.fmt(p.lat, 5) + ", " + AIS.fmt(p.lon, 5)],
        ["Precisión", p.precision_m ? "±" + Math.round(p.precision_m) + " m" : null], ["Origen", p.fuente]]);
      c.bindPopup(div);
    }
    const rx = d.rx;
    if (AIS.tienePos(rx)) {
      if (rx.precision_m && rx.precision_m < 5000) L.circle([rx.lat, rx.lon], { radius: rx.precision_m, color: "#d32f2f", weight: 1, fillOpacity: 0.06, interactive: false }).addTo(this.gReceptor);
      if (d.radioKm) L.circle([rx.lat, rx.lon], { radius: d.radioKm * 1000, color: "#d32f2f", weight: 1.5, dashArray: "8 6", fill: false, interactive: false }).addTo(this.gReceptor);
      const mk = L.marker([rx.lat, rx.lon], { icon: L.divIcon({ className: "", html: AIS.SVG_RECEPTOR, iconSize: [30, 30], iconAnchor: [15, 15] }), zIndexOffset: 2000 }).addTo(this.gReceptor);
      const div = document.createElement("div");
      div.className = "popup-ais";
      const t = document.createElement("b");
      t.textContent = d.enVivo ? "Tu receptor (ahora)" : "Tu receptor";
      div.append(t);
      AIS.filasDetalle(div, [["Posición", AIS.fmt(rx.lat, 5) + ", " + AIS.fmt(rx.lon, 5)],
        ["Precisión", rx.precision_m ? "±" + Math.round(rx.precision_m) + " m" : null], ["Origen", rx.fuente],
        ["Actualizada", rx.ts ? AIS.fechaHora(rx.ts) : null]]);
      mk.bindPopup(div);
      mk.bindTooltip("Receptor");
    }
  }

  _lineaSeleccion() {
    this.gSeleccion.clearLayers();
    const d = this.d;
    if (!d || this.seleccion === null) return;
    const b = d.barcos.find(x => x.mmsi === this.seleccion);
    if (!b || !AIS.tienePos(b)) return;
    let origen = null, texto = null;
    if (d.enVivo && AIS.tienePos(d.rx)) {
      origen = d.rx;
      texto = AIS.fmt(AIS.distanciaKm(d.rx.lat, d.rx.lon, b.lat, b.lon), 2) + " km (ahora)";
    } else if (AIS.tienePos({ lat: b.rx_lat, lon: b.rx_lon })) {
      origen = { lat: b.rx_lat, lon: b.rx_lon };
      texto = AIS.fmt(b.dist_km, 2) + " km (al capturar)";
    }
    if (d.enVivo && AIS.tienePos({ lat: b.rx_lat, lon: b.rx_lon }) && AIS.tienePos(d.rx) &&
        AIS.distanciaKm(b.rx_lat, b.rx_lon, d.rx.lat, d.rx.lon) > 0.05) {
      L.circleMarker([b.rx_lat, b.rx_lon], { radius: 6, color: "#d32f2f", weight: 2, fillOpacity: 0 }).addTo(this.gSeleccion)
        .bindTooltip("Tu receptor cuando se capturó este barco (" + AIS.fmt(b.dist_km, 2) + " km)");
      L.polyline([[b.rx_lat, b.rx_lon], [b.lat, b.lon]], { color: "#d32f2f", weight: 1.5, dashArray: "2 6", opacity: 0.7, interactive: false }).addTo(this.gSeleccion);
    }
    if (!origen) return;
    const linea = L.polyline([[origen.lat, origen.lon], [b.lat, b.lon]], { color: "#d32f2f", weight: 2, dashArray: "6 6" }).addTo(this.gSeleccion);
    linea.bindTooltip(texto, { permanent: true, direction: "center", className: "etq-distancia" });
  }

  seleccionar(mmsi, centrar) {
    this.seleccion = mmsi;
    const b = this.d && this.d.barcos.find(x => x.mmsi === mmsi);
    if (centrar && b && AIS.tienePos(b)) this.m.setView([b.lat, b.lon], Math.max(this.m.getZoom(), 13));
    if (this.d) this.dibujar(this.d);
  }

  ajustar() {
    const d = this.d;
    if (!d) return;
    const pts = d.barcos.filter(AIS.tienePos).map(b => [b.lat, b.lon]);
    if (AIS.tienePos(d.rx)) pts.push([d.rx.lat, d.rx.lon]);
    if (!pts.length) return;
    this.ajustado = true;
    const lim = L.latLngBounds(pts);
    if (lim.getSouthWest().equals(lim.getNorthEast())) this.m.setView(pts[0], 13);
    else this.m.fitBounds(lim, { padding: [30, 30], maxZoom: 15 });
  }

  refrescarTamano() { this.m.invalidateSize(); }
};

// ---------------------------------------------------------------- ubicación del receptor
AIS.Ubicacion = class {
  // enviar(lat, lon, precision, fuente) manda la posición al servidor; estado(texto) muestra mensajes.
  constructor(enviar, estado) {
    this.enviar = enviar;
    this.estado = estado;
    this.vigilancia = null;
  }

  activa() { return this.vigilancia !== null; }

  iniciar() {
    if (!("geolocation" in navigator)) {
      this.estado("Este navegador no ofrece ubicación. Fíjala con clic en el mapa o escribiendo las coordenadas.");
      return;
    }
    this.estado("Pidiendo ubicación… (acepta el permiso si aparece)");
    this.vigilancia = navigator.geolocation.watchPosition(
      p => {
        this.enviar(p.coords.latitude, p.coords.longitude, p.coords.accuracy, "gps");
        this.estado("Ubicación automática activa (±" + Math.round(p.coords.accuracy) + " m)");
      },
      e => {
        const motivo = e.code === 1 ? "permiso denegado" : (e.code === 2 ? "no disponible (¿ubicación de Windows apagada?)" : "tiempo agotado");
        this.estado("No se pudo obtener la ubicación: " + motivo + ". Usa «Abrir mapa en pestaña» (navegador) o fíjala con clic en el mapa.");
        this.detener(true);
      },
      { enableHighAccuracy: true, maximumAge: 10000, timeout: 30000 });
  }

  detener(silencioso) {
    if (this.vigilancia !== null) navigator.geolocation.clearWatch(this.vigilancia);
    this.vigilancia = null;
    if (!silencioso) this.estado("Ubicación automática detenida (se conserva la última posición).");
  }
};

// Abre una ruta del servidor: en la ventana de escritorio, en el navegador del sistema (para descargas).
AIS.abrirUrl = ruta => {
  if (window.pywebview && window.pywebview.api && window.pywebview.api.abrir_url) window.pywebview.api.abrir_url(ruta);
  else window.open(ruta, "_blank");
};

AIS.abrirPestanaMapa = () => {
  if (window.pywebview && window.pywebview.api && window.pywebview.api.abrir_mapa) window.pywebview.api.abrir_mapa();
  else window.open("/mapa", "_blank");
};

// ---------------------------------------------------------------- historial de un barco
AIS.TIPOS_MSG = { 1: "Posición A", 2: "Posición A", 3: "Posición A", 4: "Estación base", 5: "Datos A", 11: "Hora UTC",
  18: "Posición B", 19: "Posición B ext.", 21: "Ayuda nav.", 24: "Datos B" };
AIS.FILTRO_TIPOS = [["", "Todos los tipos"], ["1,2,3,18,19", "Solo posiciones"], ["5,24", "Solo datos (nombre, tipo)"]];

AIS.aLocal = ts => { const d = new Date(ts * 1000); d.setMinutes(d.getMinutes() - d.getTimezoneOffset()); return d.toISOString().slice(0, 16); };
AIS.deLocal = v => v ? new Date(v).getTime() / 1000 : null;

AIS.el = (tag, clase, texto) => {
  const e = document.createElement(tag);
  if (clase) e.className = clase;
  if (texto !== undefined && texto !== null) e.textContent = texto;
  return e;
};

AIS.Historial = class {
  // mapa: AIS.Mapa donde dibujar la trayectoria. abrirUrl(ruta): abre una URL (descarga del CSV).
  constructor(mapa, abrirUrl) {
    this.mapa = mapa;
    this.abrirUrl = abrirUrl || (ruta => window.open(ruta, "_blank"));
    this.mmsi = null;
    this.limite = 2000;
    const p = AIS.el("aside", "historial");
    p.setAttribute("aria-label", "Historial del barco");
    p.innerHTML = `
      <div class="h-cab"><div><b class="h-titulo"></b><div class="h-sub"></div></div>
        <button type="button" class="h-cerrar" title="Cerrar (Esc)">✕</button></div>
      <div class="h-cuerpo">
        <div class="h-ctl">
          <div class="campo"><label>Desde</label><input type="datetime-local" class="h-desde"></div>
          <div class="campo"><label>Hasta</label><input type="datetime-local" class="h-hasta"></div>
          <div class="campo"><label>Mensajes</label><select class="h-tipos"></select></div>
          <button type="button" class="h-aplicar principal">Consultar</button>
          <button type="button" class="h-limpiar">Todo el historial</button>
        </div>
        <div class="h-ctl">
          <label class="h-check"><input type="checkbox" class="h-tray" checked> Trayectoria en el mapa</label>
          <button type="button" class="h-centrar">Centrar en el mapa</button>
          <button type="button" class="h-csv">Descargar CSV</button>
        </div>
        <div class="h-estado"></div>
        <div class="h-tarjetas"></div>
        <div class="h-bloque"><h3>Velocidad en el tiempo</h3><div class="h-grafica"></div></div>
        <div class="h-bloque"><h3>Sesiones en que se recibió</h3><div class="h-sesiones"></div></div>
        <div class="h-bloque"><h3>Mensajes <small class="h-cuenta"></small></h3>
          <div class="h-tabla"><table><thead><tr><th>Fecha y hora</th><th>Tipo</th><th>Canal</th><th>Recepción</th>
            <th class="num">SNR</th><th class="num">Vel</th><th class="num">Rumbo</th><th class="num">Dist (km)</th></tr></thead>
            <tbody></tbody></table></div>
          <button type="button" class="h-mas">Cargar todos los mensajes</button>
        </div>
      </div>`;
    document.body.append(p);
    this.p = p;
    const q = s => p.querySelector(s);
    this.q = q;
    for (const [v, t] of AIS.FILTRO_TIPOS) { const o = AIS.el("option", null, t); o.value = v; q(".h-tipos").append(o); }
    q(".h-cerrar").onclick = () => this.cerrar();
    q(".h-aplicar").onclick = () => this.cargar();
    q(".h-limpiar").onclick = () => { q(".h-desde").value = ""; q(".h-hasta").value = ""; q(".h-tipos").value = ""; this.limite = 2000; this.cargar(); };
    q(".h-mas").onclick = () => { this.limite = 50000; this.cargar(); };
    q(".h-tray").onchange = () => this._trayectoria();
    q(".h-centrar").onclick = () => this._trayectoria(true);
    q(".h-csv").onclick = () => this.abrirUrl("/api/barco_csv?" + this._params(50000).toString());
    document.addEventListener("keydown", e => { if (e.key === "Escape" && this.abierto()) this.cerrar(); });
  }

  abierto() { return this.p.classList.contains("visible"); }

  abrir(mmsi) {
    if (mmsi !== this.mmsi) {
      this.q(".h-desde").value = "";
      this.q(".h-hasta").value = "";
      this.q(".h-tipos").value = "";
      this.limite = 2000;
    }
    this.mmsi = mmsi;
    this.p.classList.add("visible");
    document.body.classList.add("con-historial");
    setTimeout(() => this.mapa.refrescarTamano(), 250);
    this.cargar();
  }

  cerrar() {
    this.p.classList.remove("visible");
    document.body.classList.remove("con-historial");
    setTimeout(() => this.mapa.refrescarTamano(), 250);
    this.mapa.limpiarHistorial();
    this.datos = null;
  }

  _params(limite) {
    const p = new URLSearchParams({ mmsi: this.mmsi, limite: limite || this.limite });
    const d = AIS.deLocal(this.q(".h-desde").value), h = AIS.deLocal(this.q(".h-hasta").value);
    if (d !== null) p.set("desde", d);
    if (h !== null) p.set("hasta", h + 59);
    if (this.q(".h-tipos").value) p.set("tipos", this.q(".h-tipos").value);
    return p;
  }

  async cargar() {
    const pedido = this.mmsi;
    this.q(".h-estado").textContent = "Consultando la base de datos…";
    try {
      const r = await fetch("/api/barco?" + this._params().toString());
      if (!r.ok) throw new Error("HTTP " + r.status);
      const d = await r.json();
      if (pedido !== this.mmsi) return;
      this.datos = d;
      this._dibujar();
    } catch (e) {
      this.q(".h-estado").textContent = "No se pudo consultar el historial: " + e.message;
    }
  }

  _dibujar() {
    const d = this.datos, b = d.barco, r = d.resumen, q = this.q;
    q(".h-titulo").textContent = (b.nombre || "Sin nombre") + " · " + b.mmsi;
    q(".h-sub").textContent = [b.pais, b.tipo_nave_desc || b.categoria, b.indicativo ? "indicativo " + b.indicativo : null,
      b.destino ? "destino " + b.destino : null].filter(Boolean).join(" · ");
    q(".h-estado").textContent = r.n ? "" : "No hay mensajes de este MMSI con esos criterios.";
    const t = q(".h-tarjetas");
    t.textContent = "";
    const tarjeta = (k, v) => { if (v === null || v === undefined || v === "") return; const c = AIS.el("div", "h-tarjeta"); c.append(AIS.el("span", null, k), AIS.el("b", null, v)); t.append(c); };
    const rango = (a, z, dec, u) => a === null || a === undefined ? null : (Math.abs(a - z) < Math.pow(10, -dec) ? AIS.fmt(a, dec) : AIS.fmt(a, dec) + " – " + AIS.fmt(z, dec)) + u;
    tarjeta("Mensajes", r.n);
    tarjeta("Primera vez", r.primero ? AIS.fechaHora(r.primero) : null);
    tarjeta("Última vez", r.ultimo ? AIS.fechaHora(r.ultimo) : null);
    tarjeta("Sesiones", r.sesiones);
    tarjeta("Directos / retransmitidos", r.n ? (r.directos || 0) + " / " + (r.retransmitidos || 0) : null);
    tarjeta("Distancia al receptor", rango(r.dist_min, r.dist_max, 2, " km"));
    tarjeta("Velocidad media / máx.", r.vel_media === null ? null : AIS.fmt(r.vel_media, 1) + " / " + AIS.fmt(r.vel_max, 1) + " kn");
    tarjeta("Recorrido registrado", r.n ? AIS.fmt(r.recorrido_km, 2) + " km" : null);
    tarjeta("SNR medio", r.snr_medio === null ? null : AIS.fmt(r.snr_medio, 1) + " dB");
    tarjeta("Por tipo", Object.entries(r.por_tipo).map(([k, v]) => k + ": " + v).join(" · "));
    this._grafica();
    const s = q(".h-sesiones");
    s.textContent = "";
    for (const x of d.sesiones) {
      const c = AIS.el("button", "h-chip", "Sesión " + x.sesion_id + " · " + x.n + " msgs · " + AIS.fechaHora(x.primero).slice(5, 16) + "–" + AIS.hora(x.ultimo).slice(0, 5));
      c.type = "button";
      c.title = "Filtrar el historial a esta sesión";
      c.onclick = () => { q(".h-desde").value = AIS.aLocal(x.primero); q(".h-hasta").value = AIS.aLocal(x.ultimo); this.cargar(); };
      s.append(c);
    }
    if (!d.sesiones.length) s.textContent = "–";
    this._tabla();
    this._trayectoria(true);
  }

  _grafica() {
    const cont = this.q(".h-grafica");
    cont.textContent = "";
    const pts = this.datos.mensajes.filter(m => m.campos.sog_kn !== undefined && m.campos.sog_kn !== null && m.campos.sog_kn < 102.3)
      .map(m => [m.ts, m.campos.sog_kn]);
    if (pts.length < 2) { cont.append(AIS.el("div", "vacio", "No hay suficientes reportes de velocidad.")); return; }
    const W = 560, H = 120, L = 34, R = 8, T = 8, B = 20;
    const t0 = pts[0][0], t1 = pts[pts.length - 1][0], vmax = Math.max(1, ...pts.map(p => p[1])) * 1.1;
    const X = t => L + (t1 > t0 ? (t - t0) / (t1 - t0) : 0.5) * (W - L - R), Y = v => T + (1 - v / vmax) * (H - T - B);
    const ns = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(ns, "svg");
    svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
    svg.setAttribute("class", "h-svg");
    const linea = (x1, y1, x2, y2, cls) => { const l = document.createElementNS(ns, "line"); Object.entries({ x1, y1, x2, y2 }).forEach(([k, v]) => l.setAttribute(k, v)); l.setAttribute("class", cls); svg.append(l); };
    const texto = (x, y, s, anchor) => { const e = document.createElementNS(ns, "text"); e.setAttribute("x", x); e.setAttribute("y", y); e.setAttribute("text-anchor", anchor || "end"); e.textContent = s; svg.append(e); };
    for (const f of [0, 0.5, 1]) { const v = vmax / 1.1 * f; linea(L, Y(v), W - R, Y(v), "rej"); texto(L - 4, Y(v) + 4, AIS.fmt(v, 1)); }
    texto(L, H - 4, AIS.fechaHora(t0).slice(5, 16), "start");
    texto(W - R, H - 4, AIS.fechaHora(t1).slice(5, 16), "end");
    const pl = document.createElementNS(ns, "polyline");
    pl.setAttribute("points", pts.map(p => X(p[0]).toFixed(1) + "," + Y(p[1]).toFixed(1)).join(" "));
    pl.setAttribute("class", "serie");
    svg.append(pl);
    const tit = document.createElementNS(ns, "title");
    tit.textContent = "Velocidad sobre el fondo (nudos)";
    svg.append(tit);
    cont.append(svg);
  }

  _tabla() {
    const d = this.datos, cuerpo = this.q(".h-tabla tbody");
    cuerpo.textContent = "";
    const lista = d.mensajes.slice().reverse();
    for (const m of lista) {
      const c = m.campos;
      const tr = AIS.el("tr", "clic");
      const rec = m.repeticion === 0 ? "Directa" : (m.repeticion > 0 ? "Retransmitida" : "–");
      const etq = AIS.el("span", "etiqueta " + (m.repeticion === 0 ? "dir" : "rep"), rec);
      const tdRec = AIS.el("td");
      if (m.repeticion === null || m.repeticion === undefined) tdRec.textContent = "–"; else tdRec.append(etq);
      const rumbo = c.rumbo !== undefined && c.rumbo !== null && c.rumbo < 511 ? c.rumbo + "°" : (c.cog !== undefined && c.cog < 360 ? AIS.fmt(c.cog, 0) + "°" : "–");
      tr.append(AIS.el("td", null, AIS.fechaHora(m.ts)), AIS.el("td", null, m.tipo + " · " + (AIS.TIPOS_MSG[m.tipo] || "")),
        AIS.el("td", null, m.canal), tdRec, AIS.el("td", "num", AIS.fmt(m.snr_db, 1)),
        AIS.el("td", "num", c.sog_kn !== undefined && c.sog_kn < 102.3 ? AIS.fmt(c.sog_kn, 1) : "–"), AIS.el("td", "num", rumbo),
        AIS.el("td", "num", m.dist_km === null ? "–" : AIS.fmt(m.dist_km, 2)));
      tr.onclick = () => this._detalle(tr, m);
      cuerpo.append(tr);
    }
    const r = d.resumen;
    this.q(".h-cuenta").textContent = r.mostrados < r.n ? "(mostrando los " + r.mostrados + " más recientes de " + r.n + ")" : "(" + r.n + ")";
    this.q(".h-mas").style.display = r.mostrados < r.n ? "inline-block" : "none";
  }

  _detalle(tr, m) {
    const sig = tr.nextSibling;
    if (sig && sig.classList && sig.classList.contains("h-det")) { sig.remove(); this.mapa.marcarPunto(null); return; }
    for (const x of this.q(".h-tabla tbody").querySelectorAll(".h-det")) x.remove();
    const fila = AIS.el("tr", "h-det");
    const td = AIS.el("td");
    td.colSpan = 8;
    const c = m.campos;
    if (m.resumen) td.append(AIS.el("div", null, m.resumen));
    AIS.filasDetalle(td, [["Sesión", m.sesion_id], ["Desvío", AIS.fmt(m.desvio_hz, 0) + " Hz"],
      ["Tu receptor", AIS.tienePos({ lat: m.rx_lat, lon: m.rx_lon }) ? AIS.fmt(m.rx_lat, 5) + ", " + AIS.fmt(m.rx_lon, 5) : null],
      ["Estado de navegación", c.estado_nav === undefined || c.estado_nav === null ? null : AIS.ESTADOS_NAV[c.estado_nav]]]);
    for (const n of m.nmea) td.append(AIS.el("code", null, n));
    fila.append(td);
    tr.after(fila);
    if (AIS.tienePos(c)) this.mapa.marcarPunto(c.lat, c.lon, AIS.fechaHora(m.ts) + (c.sog_kn !== undefined ? " · " + AIS.fmt(c.sog_kn, 1) + " kn" : ""));
    else this.mapa.marcarPunto(null);
  }

  _trayectoria(ajustar) {
    if (!this.datos) return;
    if (this.q(".h-tray").checked) this.mapa.mostrarHistorial(this.datos.trayectoria, AIS.colorBarco(this.mmsi), ajustar);
    else this.mapa.limpiarHistorial();
  }
};

AIS.Mapa.prototype.mostrarHistorial = function (pts, color, ajustar) {
  if (!this.gHistorial) this.gHistorial = L.layerGroup().addTo(this.m);
  this.gHistorial.clearLayers();
  if (!pts || !pts.length) return;
  const ll = pts.map(p => [p[1], p[2]]);
  L.polyline(ll, { color: "#000", weight: 6, opacity: 0.25, interactive: false }).addTo(this.gHistorial);
  L.polyline(ll, { color, weight: 3, opacity: 0.95, interactive: false }).addTo(this.gHistorial);
  const paso = Math.max(1, Math.ceil(pts.length / 400));
  pts.forEach((p, i) => {
    if (i % paso && i !== pts.length - 1) return;
    L.circleMarker([p[1], p[2]], { radius: i === pts.length - 1 ? 6 : 3, color, weight: 1, fillColor: "#fff", fillOpacity: 1 })
      .bindTooltip(AIS.fechaHora(p[0])).addTo(this.gHistorial);
  });
  const ini = pts[0];
  L.circleMarker([ini[1], ini[2]], { radius: 6, color: "#2e7d32", weight: 2, fillColor: "#2e7d32", fillOpacity: 0.9 })
    .bindTooltip("Primer registro: " + AIS.fechaHora(ini[0])).addTo(this.gHistorial);
  if (ajustar) {
    const lim = L.latLngBounds(ll);
    if (lim.getSouthWest().equals(lim.getNorthEast())) this.m.setView(ll[0], Math.max(this.m.getZoom(), 14));
    else this.m.fitBounds(lim, { padding: [30, 30], maxZoom: 16 });
  }
};

AIS.Mapa.prototype.limpiarHistorial = function () {
  if (this.gHistorial) this.gHistorial.clearLayers();
  this.marcarPunto(null);
};

AIS.Mapa.prototype.marcarPunto = function (lat, lon, texto) {
  if (this.puntoHist) { this.m.removeLayer(this.puntoHist); this.puntoHist = null; }
  if (lat === null || lat === undefined) return;
  this.puntoHist = L.circleMarker([lat, lon], { radius: 11, color: "#ff6f00", weight: 3, fill: false }).addTo(this.m);
  if (texto) this.puntoHist.bindTooltip(texto, { permanent: true, direction: "top", offset: [0, -10] });
  this.m.panTo([lat, lon]);
};
