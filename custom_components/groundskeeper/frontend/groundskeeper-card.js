/* Groundskeeper card: the robot's status, the yard map with the robot moving on it, and the camera that covers it.
 *
 * Served by the Groundskeeper integration and loaded on every dashboard; no build step, no libraries. Everything it
 * shows comes from Home Assistant: the entities, and the integration's websocket commands (groundskeeper/map,
 * groundskeeper/track, groundskeeper/subscribe) and imagery view. It never talks to the panel, so the panel's token
 * stays on the Home Assistant server. It reads only: nothing here can send anything to the robot.
 *
 * The map is an SVG in metres around the yard (north up), over the panel's yard photo. Text from the panel or
 * Home Assistant is always set as text, never as HTML.
 */

const CARD_VERSION = "0.1.0";      // the integration's version (manifest.json)
const DOMAIN = "groundskeeper";
const SVG = "http://www.w3.org/2000/svg";

// The panel's map colours (they sit on the aerial photo, so they are the same in light and dark themes).
const C = {
  area: "#E8EEEA", nogo: "#E5484D", novis: "#79B8F0", road: "#69736E", ink: "#0E1311", dock: "#8BD49A",
  robot: "#79B8F0", fixed: "#8BD49A", float: "#F0B955", single: "#F2784B", trail: "#F2F5F3",
};
const RTK_COLOUR = { 4: C.fixed, 5: C.float };          // GGA quality: 4 RTK fixed, 5 RTK float, else single/none
const FOLLOW_M = 18;                                     // half the view when following the robot
const PAD_M = 6;                                         // around the yard when fitting it
const TRAIL_MAX = 3000;                                  // live samples kept outside a run's track
const RESUBSCRIBE_MS = 2000;
const ROBOT_PX = 14;
const RETRY_MS = 10000;                                  // a failed subscribe, or a map the panel has not read yet
const RETRY_OFF_MS = 60000;                              // the map and camera switched off in the options
const WATCHDOG_MS = 30000;                               // nothing on the feed for this long: subscribe again
const TRACK_EVERY_MS = 10000;                            // the current run's new points, at most this often

const DEFAULTS = { show_status: true, show_map: true, show_camera: true, camera: "auto", map_height: 320 };

const LABELS = {
  entry_id: "Robot (Groundskeeper entry)",
  title: "Title",
  show_status: "Status",
  show_map: "Map",
  show_camera: "Follow camera",
  camera: "Camera source",
  map_height: "Map height",
};

function el(tag, attrs = {}, ...children) {
  const svg = ["svg", "g", "path", "polyline", "polygon", "circle", "image", "title", "line", "rect"].includes(tag);
  const node = svg ? document.createElementNS(SVG, tag) : document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === undefined || v === null || v === false) continue;
    if (k === "text") node.textContent = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v === true ? "" : String(v));
  }
  for (const c of children) if (c) node.append(c);
  return node;
}

/* A flat projection in metres around the yard's middle: plenty for a garden, north up (SVG y grows south). */
class Projection {
  constructor([w, s, e, n]) {
    this.lon0 = (w + e) / 2;
    this.lat0 = (s + n) / 2;
    this.kx = 111320 * Math.cos((this.lat0 * Math.PI) / 180);
    this.ky = 110540;
  }
  xy(lon, lat) { return [(lon - this.lon0) * this.kx, -(lat - this.lat0) * this.ky]; }
  box([w, s, e, n]) {
    const [x0, y0] = this.xy(w, n);
    const [x1, y1] = this.xy(e, s);
    return { x: x0, y: y0, w: x1 - x0, h: y1 - y0 };
  }
}

class GroundskeeperCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._config = { ...DEFAULTS };
    this._live = null;          // the last state event
    this._pose = null;          // the newest position {t, lon, lat, heading}
    this._connected = null;
    this._map = null;           // groundskeeper/map's result
    this._mapSha = undefined;
    this._mapError = null;
    this._track = { run: null, points: [], since: 0, busy: false };
    this._trail = [];           // live samples (for when there is no run track)
    this._follow = false;
    this._unsub = null;
    this._camEntity = null;
    this._built = false;
    this._gen = 0;              // see _start
    this._again = null;
    this._watchdog = null;
    this._mapOff = false;
    this._mapTried = 0;
    this._drawn = null;         // how much of the track is drawn: {run, n, colour, last}
    this._notes = {};
  }

  /* -- Lovelace API ------------------------------------------------------------------------------------------ */
  setConfig(config) {
    if (!config || typeof config !== "object") throw new Error("Invalid configuration");
    const next = { ...DEFAULTS, ...config };
    const entryChanged = this._config.entry_id !== next.entry_id;
    this._config = next;
    if (entryChanged) this._reset();
    this._built = false;
    if (this._hass) this._render();
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (this._notes.robot && this._device()) {        // the robot turned up after the card was drawn
      this._clearNote("robot");
      this._built = false;
    }
    if (!this._built) this._render();
    else this._updateStatus();
    if (first || !this._unsub) this._start();
    this._updateCamera();
  }

  connectedCallback() { if (this._hass) this._start(); }
  disconnectedCallback() {
    this._stop();
    this._resize?.disconnect();
  }

  getCardSize() {
    const c = this._config;
    return (c.show_status ? 1 : 0) + (c.show_map ? Math.ceil(c.map_height / 50) : 0) + (c.show_camera ? 4 : 0);
  }

  getGridOptions() { return { columns: 12, rows: 8, min_columns: 6, min_rows: 3 }; }

  static getStubConfig() { return { show_status: true, show_map: true, show_camera: true, camera: "auto" }; }

  static getConfigForm() {
    return {
      schema: [
        { name: "entry_id", selector: { config_entry: { integration: DOMAIN } } },
        { name: "title", selector: { text: {} } },
        {
          type: "grid", name: "", schema: [
            { name: "show_status", default: true, selector: { boolean: {} } },
            { name: "show_map", default: true, selector: { boolean: {} } },
            { name: "show_camera", default: true, selector: { boolean: {} } },
          ],
        },
        {
          name: "camera",
          default: "auto",
          selector: {
            select: {
              mode: "dropdown",
              options: [
                { value: "auto", label: "Frigate's own camera entity when there is one" },
                { value: "integration", label: "Groundskeeper's follow camera (through the panel)" },
              ],
            },
          },
        },
        { name: "map_height", default: DEFAULTS.map_height, selector: { number: { min: 160, max: 900, step: 10, unit_of_measurement: "px" } } },
      ],
      computeLabel: (s) => LABELS[s.name],
    };
  }

  /* -- which robot, which entities ----------------------------------------------------------------------------- */
  _device() {
    const devices = Object.values(this._hass?.devices || {}).filter((d) =>
      (d.identifiers || []).some((i) => Array.isArray(i) && i[0] === DOMAIN));
    if (this._config.entry_id) return devices.find((d) => (d.config_entries || []).includes(this._config.entry_id));
    return devices[0];
  }

  _entryId() {
    if (this._config.entry_id) return this._config.entry_id;
    const d = this._device();
    return d ? d.primary_config_entry || (d.config_entries || [])[0] : null;
  }

  _entities() {
    const d = this._device();
    if (!d) return {};
    const out = {};
    for (const e of Object.values(this._hass.entities || {})) {
      if (e.device_id !== d.id) continue;
      const [domain] = e.entity_id.split(".");
      const st = this._hass.states[e.entity_id];
      const key = e.translation_key;
      if (domain === "lawn_mower") out.mower = e.entity_id;
      else if (domain === "camera" && key === "follow_camera") out.camera = e.entity_id;
      else if (domain === "sensor" && st?.attributes?.device_class === "battery") out.battery = e.entity_id;
      else if (domain === "sensor" && ["positioning", "start_check", "follow_camera", "lawn"].includes(key)) {
        out[key] = e.entity_id;
      }
    }
    return out;
  }

  /* -- the live feed --------------------------------------------------------------------------------------------- *
   * One subscription per card while it is on the page. `_gen` counts starts and stops: anything that finishes after
   * a stop, a reset or another robot was picked (a subscribe, a map or a track load) sees a different generation and
   * is dropped. A failed subscribe waits and tries again (never on every hass update), and a watchdog starts over
   * when nothing has come for WATCHDOG_MS, which also covers a subscription lost when Home Assistant restarts. */
  _reset() {
    this._stop();
    this._live = this._pose = this._map = this._mapError = null;
    this._mapSha = undefined;
    this._mapOff = false;
    this._loadingMap = false;
    this._mapToken = null;
    this._mapTried = 0;
    this._track = { run: null, points: [], since: 0, busy: false, at: 0 };
    this._trail = [];
    this._drawn = null;
  }

  async _start() {
    if (this._unsub || this._starting || this._again || !this._hass || !this.isConnected) return;
    const entry = this._entryId();
    if (!entry) return;
    const gen = ++this._gen;
    this._starting = true;
    if (this._config.show_map && !this._map) this._loadMap();     // at the same time as subscribing
    let unsub = null;
    try {
      unsub = await this._hass.connection.subscribeMessage(
        (msg) => { if (gen === this._gen) this._onEvent(msg); }, { type: `${DOMAIN}/subscribe`, entry_id: entry });
    } catch (err) {
      if (gen !== this._gen) return;
      this._liveOff = err?.code === "live_off";
      this._setNote(this._liveOff ? "The map and follow camera are switched off in the integration's options."
        : "The live feed is not available yet; trying again.", "live");
      this._retry(this._liveOff ? RETRY_OFF_MS : RETRY_MS);
      return;
    } finally {
      if (gen === this._gen) this._starting = false;
    }
    if (gen !== this._gen || !this.isConnected) {               // stopped or switched while subscribing
      Promise.resolve(unsub()).catch(() => {});
      return;
    }
    this._unsub = unsub;
    this._lastEvent = Date.now();
    this._clearNote("live");
    clearInterval(this._watchdog);
    this._watchdog = setInterval(() => {
      if (this._unsub && Date.now() - this._lastEvent > WATCHDOG_MS) { this._stop(); this._start(); }
    }, WATCHDOG_MS / 3);
  }

  _retry(ms) {
    clearTimeout(this._again);
    this._again = setTimeout(() => { this._again = null; this._start(); }, ms);
  }

  _stop() {
    this._gen++;
    this._starting = false;
    if (this._unsub) {
      const u = this._unsub;
      this._unsub = null;
      Promise.resolve(u()).catch(() => {});
    }
    clearTimeout(this._again);
    this._again = null;
    clearInterval(this._watchdog);
    this._watchdog = null;
  }

  _onEvent({ event, data }) {
    this._lastEvent = Date.now();
    if (event === "closed") {          // the entry reloaded: subscribe again shortly, and read the map again
      this._stop();                    // also drops it from the client's list, so a reconnect does not revive it
      this._map = null;
      this._mapSha = undefined;
      this._mapOff = false;
      this._retry(RESUBSCRIBE_MS);
      return;
    }
    if (event === "snapshot") {
      this._connected = data.connected;
      if (data.state) this._onState(data.state);
      if (data.pose) this._onPoses([data.pose]);
    } else if (event === "state") {
      this._connected = true;
      this._onState(data);
    } else if (event === "pose") {
      this._onPoses(data);
    } else if (event === "connection") {
      this._connected = data.connected;
    } else if (event === "stale") {
      this._connected = false;
    }
    this._updateLiveNote();
  }

  _onState(s) {
    this._live = s;
    if (s.position && (!this._pose || (s.position.t || 0) >= (this._pose.t || 0))) this._pose = s.position;
    if (this._config.show_map && !this._mapOff) {
      if (!this._map && !this._loadingMap && Date.now() - this._mapTried >= RETRY_MS) this._loadMap();   // failed: again
      else if (s.map !== undefined && this._mapSha !== undefined && s.map !== this._mapSha) this._loadMap();
    }
    const run = s.run ? s.run.id : null;
    if (run !== this._track.run) this._track = { run, points: [], since: 0, busy: false, at: 0 };
    if (run !== null && this._config.show_map && Date.now() - this._track.at >= TRACK_EVERY_MS) this._loadTrack();
    this._drawRobot();
    this._updateCamera();
  }

  _onPoses(poses) {
    for (const p of poses || []) {
      if (typeof p?.lon !== "number" || typeof p?.lat !== "number") continue;
      if (!this._pose || (p.t || 0) >= (this._pose.t || 0)) this._pose = p;
      this._trail.push([p.t, p.lon, p.lat]);
    }
    if (this._trail.length > TRAIL_MAX) this._trail.splice(0, this._trail.length - TRAIL_MAX);
    this._drawRobot();
    if (this._track.run === null) this._drawTrack();     // a run's track changes only when it is fetched
  }

  async _loadMap() {
    const entry = this._entryId();
    if (!entry || this._loadingMap) return;
    const token = (this._mapToken = {});
    this._loadingMap = true;
    this._mapTried = Date.now();
    let map = null, error = null, off = false;
    try {
      map = await this._hass.callWS({ type: `${DOMAIN}/map`, entry_id: entry });
      if (map?.yard?.url) {
        try {
          const signed = await this._hass.callWS({ type: "auth/sign_path", path: map.yard.url, expires: 86400 });
          map.yard.signed = signed.path;
        } catch (_) { /* no photo, the shapes still show */ }
      }
    } catch (err) {
      map = null;
      off = err?.code === "map_off";
      error = off ? "The map is switched off in the integration's options." : "The panel has not given its map yet.";
    }
    if (token !== this._mapToken) return;      // the card was reset (another robot) meanwhile
    this._loadingMap = false;
    this._map = map;
    this._mapSha = map ? map.map ?? null : undefined;
    this._mapError = error;
    this._mapOff = off;
    this._drawn = null;
    this._drawMap();
  }

  async _loadTrack() {
    const t = this._track;
    const entry = this._entryId();
    if (t.busy || t.run === null || !entry) return;
    t.busy = true;
    t.at = Date.now();
    try {
      const res = await this._hass.callWS({ type: `${DOMAIN}/track`, entry_id: entry, run_id: t.run, since: t.since });
      if (this._track !== t) return;            // a new run started, or the card was reset, meanwhile
      for (const p of res.points || []) if (typeof p?.[0] === "number") t.points.push(p);
      if (t.points.length) t.since = t.points[t.points.length - 1][0];
    } catch (_) {
      /* the next state event asks again */
    } finally {
      t.busy = false;
    }
    this._drawTrack();
  }

  /* -- rendering ------------------------------------------------------------------------------------------------- */
  _render() {
    if (!this._hass) return;
    const c = this._config;
    const root = this.shadowRoot;
    root.replaceChildren();
    root.append(el("style", { text: STYLE }));
    const card = el("ha-card", { header: c.title || undefined });
    this._noteEl = el("div", { class: "note", hidden: true });
    this._showNotes();
    this._statusEl = c.show_status ? el("div", { class: "status" }) : null;
    this._mapWrap = c.show_map ? el("div", { class: "map", style: `height:${Number(c.map_height) || 320}px` }) : null;
    this._camWrap = c.show_camera ? el("div", { class: "camera" }) : null;
    const body = el("div", { class: `body${this._mapWrap && this._camWrap ? " two" : ""}` }, this._mapWrap, this._camWrap);
    card.append(this._statusEl || "", this._noteEl, body);
    root.append(card);
    this._camEntity = null;
    this._camCard = null;
    this._built = true;
    if (!this._device()) {
      this._setNote("No Groundskeeper robot found. Add the Groundskeeper integration, or pick the robot in the card's settings.");
      return;
    }
    this._updateStatus();
    this._drawMap();
    this._updateCamera();
  }

  /* Notes by kind ("robot", "live"), so one going away leaves the other. */
  _setNote(text, kind = "robot") {
    this._notes[kind] = text;
    this._showNotes();
  }

  _clearNote(kind) {
    delete this._notes[kind];
    this._showNotes();
  }

  _showNotes() {
    if (!this._noteEl) return;
    const text = Object.values(this._notes).filter(Boolean).join(" ");
    this._noteEl.textContent = text;
    this._noteEl.hidden = !text;
  }

  _updateLiveNote() {
    if (!this._liveEl) return;
    const off = this._connected === false;
    this._liveEl.textContent = off ? "Live feed paused" : "";
    this._liveEl.hidden = !off;
  }

  _chip(entityId, icon, text, title) {
    return el("button", {
      class: "chip", title: title || text,
      onclick: () => entityId && this.dispatchEvent(new CustomEvent("hass-more-info",
        { detail: { entityId }, bubbles: true, composed: true })),
    }, el("ha-icon", { icon }), el("span", { text }));
  }

  _fmt(entityId) {
    const st = entityId && this._hass.states[entityId];
    if (!st) return null;
    if (st.state === "unavailable" || st.state === "unknown") return null;
    return this._hass.formatEntityState ? this._hass.formatEntityState(st) : st.state;
  }

  _updateStatus() {
    if (!this._statusEl || !this._hass) return;
    const e = this._entities();
    const chips = [];
    const mower = this._fmt(e.mower);
    chips.push(this._chip(e.mower, "mdi:robot-mower", mower ?? "Unavailable"));
    const bat = this._fmt(e.battery);
    if (bat !== null) chips.push(this._chip(e.battery, "mdi:battery", bat));
    const fix = this._fmt(e.positioning);
    if (fix !== null) chips.push(this._chip(e.positioning, "mdi:crosshairs-gps", fix));
    const check = this._fmt(e.start_check);
    if (check !== null) chips.push(this._chip(e.start_check, "mdi:clipboard-check-outline", check));
    this._liveEl = el("span", { class: "paused", hidden: true });
    this._statusEl.replaceChildren(...chips, this._liveEl);
    this._updateLiveNote();
  }

  _drawMap() {
    const wrap = this._mapWrap;
    if (!wrap) return;
    const m = this._map;
    if (!m || !Array.isArray(m.bounds) || !m.geojson) {
      wrap.replaceChildren(el("div", { class: "empty", text: this._mapError || "Loading the map…" }));
      this._svg = null;
      return;
    }
    const proj = (this._proj = new Projection(m.bounds));
    const fit = proj.box(m.bounds);
    this._fitBox = { x: fit.x - PAD_M, y: fit.y - PAD_M, w: fit.w + 2 * PAD_M, h: fit.h + 2 * PAD_M };
    const svg = el("svg", { class: "svg", preserveAspectRatio: "xMidYMid meet", role: "img",
      "aria-label": "Yard map" });
    if (m.yard?.signed && Array.isArray(m.yard.bbox)) {
      const b = proj.box(m.yard.bbox);
      svg.append(el("image", { href: m.yard.signed, x: b.x, y: b.y, width: b.w, height: b.h,
        preserveAspectRatio: "none", opacity: 0.95 }));
    }
    const shapes = el("g");
    const pts = (ring) => ring.map(([lon, lat]) => proj.xy(lon, lat).map((v) => v.toFixed(2)).join(",")).join(" ");
    const order = { areas: 0, novisionzones: 1, nogozones: 2, pathways: 3, charging: 4 };
    const feats = [...(m.geojson.features || [])].sort((a, b) =>
      (order[a.properties?.kind] ?? 9) - (order[b.properties?.kind] ?? 9));
    for (const f of feats) {
      const p = f.properties || {};
      const g = f.geometry || {};
      const label = el("title", { text: p.name || p.kind || "" });
      if (g.type === "Polygon" && Array.isArray(g.coordinates?.[0])) {
        const cls = { areas: "area", nogozones: "nogo", novisionzones: "novis" }[p.kind];
        if (cls) shapes.append(el("polygon", { class: cls, points: pts(g.coordinates[0]) }, label));
      } else if (g.type === "LineString" && Array.isArray(g.coordinates) && p.kind === "pathways") {
        const line = pts(g.coordinates);
        shapes.append(el("polyline", { class: "road", points: line }, label),
          el("polyline", { class: "road-mid", points: line }));
      } else if (g.type === "Point" && p.kind === "charging" && Array.isArray(g.coordinates)) {
        const [x, y] = proj.xy(g.coordinates[0], g.coordinates[1]);
        shapes.append(el("circle", { class: "dock", cx: x, cy: y, r: 0.6 }, label));
      }
    }
    svg.append(shapes);
    this._trackG = el("g", { class: "track" });
    this._robotG = el("g", { class: "robot", hidden: true },
      el("circle", { r: 1.6, class: "halo" }),
      el("circle", { r: 0.75, class: "body" }),
      el("polygon", { points: "0,-1.9 0.65,-0.75 -0.65,-0.75", class: "nose" }));
    svg.append(this._trackG, this._robotG);
    this._svg = svg;
    const follow = el("button", {
      class: "follow", title: "Follow the robot", "aria-pressed": String(this._follow),
      onclick: () => { this._follow = !this._follow; follow.setAttribute("aria-pressed", String(this._follow)); this._view(); this._drawRobot(); },
    }, el("ha-icon", { icon: "mdi:crosshairs-gps" }));
    wrap.replaceChildren(svg, follow);
    this._drawn = null;
    this._view();
    this._drawTrack();
    this._drawRobot();
    // The marker's size depends on the drawn size, unknown until laid out (and it changes with the window).
    this._resize?.disconnect();
    if (window.ResizeObserver) {
      this._resize = new ResizeObserver(() => this._drawRobot());
      this._resize.observe(svg);
    }
  }

  _view() {
    if (!this._svg || !this._fitBox) return;
    let b = this._fitBox;
    if (this._follow && this._pose && this._proj) {
      const [x, y] = this._proj.xy(this._pose.lon, this._pose.lat);
      b = { x: x - FOLLOW_M, y: y - FOLLOW_M, w: 2 * FOLLOW_M, h: 2 * FOLLOW_M };
    }
    this._svg.setAttribute("viewBox", `${b.x.toFixed(2)} ${b.y.toFixed(2)} ${b.w.toFixed(2)} ${b.h.toFixed(2)}`);
  }

  _drawRobot() {
    if (!this._robotG || !this._proj) return;
    const p = this._pose;
    if (!p || typeof p.lon !== "number") { this._robotG.setAttribute("hidden", ""); return; }
    const [x, y] = this._proj.xy(p.lon, p.lat);
    const h = typeof p.heading === "number" ? p.heading : 0;
    this._robotG.removeAttribute("hidden");
    if (this._follow) this._view();
    this._robotG.setAttribute("transform",
      `translate(${x.toFixed(2)} ${y.toFixed(2)}) rotate(${h}) scale(${this._markerScale().toFixed(3)})`);
  }

  /* The robot drawn about ROBOT_PX across on screen at any zoom, never smaller than its real size. */
  _markerScale() {
    const vb = this._svg?.viewBox?.baseVal;
    const w = this._svg?.clientWidth, h = this._svg?.clientHeight;
    if (!vb || !vb.width || !w || !h) return 1;
    const metresPerPx = Math.max(vb.width / w, vb.height / h);       // "meet": the larger ratio wins
    return Math.max(1, (metresPerPx * ROBOT_PX) / 1.5);              // the body is 1.5 m across
  }

  /* The run's track by RTK fix (or, without a run, the live trail). A run's track only grows, so only its new
   * points are added; the trail, a new run or a new map draws it all again. */
  _drawTrack() {
    const g = this._trackG;
    if (!g || !this._proj) return;
    const t = this._track;
    const run = t.run !== null && t.points.length ? t.run : null;
    const rows = run !== null ? t.points : this._trail.map(([ts, lon, lat]) => [ts, lon, lat, -1]);
    let d = this._drawn;
    if (!d || d.run !== run || run === null || rows.length < d.n) {
      g.replaceChildren();
      d = this._drawn = { run, n: 0, colour: null, last: null };
    }
    const parts = [];
    let cur = null;
    for (let i = d.n; i < rows.length; i++) {
      const [, lon, lat, rtk] = rows[i];
      if (typeof lon !== "number" || typeof lat !== "number") continue;
      const colour = rtk === -1 ? C.trail : RTK_COLOUR[rtk] || C.single;
      const xy = this._proj.xy(lon, lat).map((v) => v.toFixed(2)).join(",");
      if (!cur) {
        cur = { colour, pts: d.last ? [d.last, xy] : [xy] };    // join onto what is drawn already
        parts.push(cur);
      } else if (cur.colour !== colour) {
        cur.pts.push(xy);
        cur = { colour, pts: [xy] };
        parts.push(cur);
      } else {
        cur.pts.push(xy);
      }
      d.last = xy;
    }
    d.n = rows.length;
    for (const part of parts) {
      if (part.pts.length > 1) g.append(el("polyline", { points: part.pts.join(" "), stroke: part.colour }));
    }
  }

  async _updateCamera() {
    const wrap = this._camWrap;
    if (!wrap || !this._hass) return;
    const e = this._entities();
    const follow = this._live?.follow;
    const name = follow?.camera;
    let entity = null;
    const up = (id) => this._hass.states[id] && this._hass.states[id].state !== "unavailable";
    if (name && this._config.camera !== "integration" && up(`camera.${name}`)) entity = `camera.${name}`;
    else if (name && e.camera && up(e.camera)) entity = e.camera;
    if (!entity) {
      let why = "Waiting for the follow camera…";
      if (!e.camera && !e.follow_camera) why = "No follow camera: switch it on in the integration's options.";
      else if (this._live && !name) why = "No camera: the panel has no Frigate yard camera set up.";
      if (this._camEntity !== why) {
        wrap.replaceChildren(el("div", { class: "empty", text: why }));
        this._camEntity = why;
        this._camCard = null;
        this._camCaption = null;
      }
      return;
    }
    const caption = name ? (follow.covered === false ? `${name}: no camera covers the robot` : `${name}: covers the robot`) : "";
    if (entity !== this._camEntity) {
      this._camEntity = entity;
      const cfg = { type: "picture-entity", entity, camera_view: "live", show_name: false, show_state: false };
      try {
        if (!this._camCard) {
          const helpers = await window.loadCardHelpers();
          if (this._camEntity !== entity) return;      // changed while loading
          this._camCard = helpers.createCardElement(cfg);
          this._camCaption = el("div", { class: "caption" });
          wrap.replaceChildren(this._camCard, this._camCaption);
        } else {
          this._camCard.setConfig(cfg);
        }
      } catch (_) {
        wrap.replaceChildren(el("div", { class: "empty", text: "The camera could not be shown." }));
        this._camCard = null;
        return;
      }
    }
    if (this._camCard) this._camCard.hass = this._hass;
    if (this._camCaption) this._camCaption.textContent = caption;
  }
}

const STYLE = `
  :host { display: block; }
  [hidden] { display: none !important; }
  ha-card { overflow: hidden; container-type: inline-size; }
  .status { display: flex; flex-wrap: wrap; gap: 6px; padding: 12px 12px 4px; align-items: center; }
  .chip { display: inline-flex; align-items: center; gap: 4px; padding: 4px 10px; border-radius: 16px; cursor: pointer;
    border: 1px solid var(--divider-color, #444); background: var(--secondary-background-color, transparent);
    color: var(--primary-text-color); font: inherit; font-size: 13px; }
  .chip ha-icon { --mdc-icon-size: 16px; color: var(--secondary-text-color); }
  .paused { font-size: 12px; color: var(--warning-color, #F0B955); margin-left: auto; }
  .note { padding: 8px 12px; color: var(--secondary-text-color); font-size: 13px; }
  .body { display: grid; gap: 8px; padding: 8px 12px 12px; }
  @container (min-width: 640px) { .body.two { grid-template-columns: 3fr 2fr; } }
  .map { position: relative; border-radius: 8px; overflow: hidden; background: #1A1F1C; }
  .svg { width: 100%; height: 100%; display: block; }
  .empty { display: flex; align-items: center; justify-content: center; height: 100%; min-height: 120px;
    color: var(--secondary-text-color); font-size: 13px; text-align: center; padding: 8px; box-sizing: border-box; }
  .area { fill: ${C.area}; fill-opacity: 0.08; stroke: ${C.area}; stroke-width: 2; vector-effect: non-scaling-stroke; }
  .nogo { fill: ${C.nogo}; fill-opacity: 0.22; stroke: ${C.nogo}; stroke-width: 2; vector-effect: non-scaling-stroke; }
  .novis { fill: none; stroke: ${C.novis}; stroke-width: 2; stroke-dasharray: 6 4; vector-effect: non-scaling-stroke; }
  .road { fill: none; stroke: ${C.road}; stroke-width: 0.9; stroke-linecap: round; stroke-linejoin: round; opacity: 0.95; }
  .road-mid { fill: none; stroke: #F2F5F3; stroke-width: 1; stroke-dasharray: 4 4; vector-effect: non-scaling-stroke; }
  .dock { fill: ${C.ink}; stroke: ${C.dock}; stroke-width: 2.5; vector-effect: non-scaling-stroke; }
  .track polyline { fill: none; stroke-width: 2; stroke-linecap: round; stroke-linejoin: round;
    vector-effect: non-scaling-stroke; opacity: 0.9; }
  .robot .halo { fill: ${C.robot}; fill-opacity: 0.25; }
  .robot .body { fill: ${C.robot}; stroke: #fff; stroke-width: 1.5; vector-effect: non-scaling-stroke; }
  .robot .nose { fill: #fff; }
  .follow { position: absolute; right: 8px; bottom: 8px; width: 36px; height: 36px; border-radius: 50%; border: none;
    background: rgba(14, 19, 17, 0.75); color: #F2F5F3; cursor: pointer; display: flex; align-items: center;
    justify-content: center; }
  .follow[aria-pressed="true"] { background: var(--primary-color, #03a9f4); }
  .camera { border-radius: 8px; overflow: hidden; }
  .camera hui-picture-entity-card, .camera > * { display: block; }
  .caption { font-size: 12px; color: var(--secondary-text-color); padding: 4px 2px 0; }
`;

if (!customElements.get("groundskeeper-card")) {
  customElements.define("groundskeeper-card", GroundskeeperCard);
  window.customCards = window.customCards || [];
  window.customCards.push({
    type: "groundskeeper-card",
    name: "Groundskeeper",
    description: "Your Yarbo's status, the yard map with the robot on it, and the camera that covers it.",
    preview: false,
    documentationURL: "https://github.com/Groundskeeper-robot/groundskeeper-ha",
  });
  console.info(`%c GROUNDSKEEPER-CARD %c ${CARD_VERSION} `, "background:#2E7D4F;color:#fff", "");
}
