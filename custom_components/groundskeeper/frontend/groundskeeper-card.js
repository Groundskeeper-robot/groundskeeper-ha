/* Groundskeeper card: the robot's status, the yard map with the robot moving on it, and the camera that covers it.
 *
 * Served by the Groundskeeper integration and loaded on every dashboard; no build step. Everything it
 * shows comes from Home Assistant: the entities, and the integration's websocket commands (groundskeeper/map,
 * groundskeeper/track, groundskeeper/subscribe) and imagery view. It never talks to the panel, so the panel's token
 * stays on the Home Assistant server.
 *
 * Controls (Start, Pause, Resume, Stop, Dock) show only when the integration's Commands option is on. They ask the
 * integration (groundskeeper/plans, /preview, /command), which asks the panel; the panel sends only with its Control
 * switch on and every check passing. Start shows the panel's checks first and needs a confirm, Resume a second tap;
 * Pause, Stop and Dock, which stop the robot, go at once.
 *
 * The map is an SVG in metres around the yard (north up), over the panel's yard photo. The 3D view (the `view`
 * option, or the map's 3D button) draws the yard on the panel's ground with MapLibre and three.js instead, which
 * come from the panel through the integration only when they match the checksums it pins. Text from the panel or
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
const RESIGN_MS = 20 * 3600 * 1000;                      // the photo's signed address lasts 24 h: renew before
const PLANS_RETRY_MS = 60000;                            // the plans could not be read: ask again this often at most
const SAY_MS = 12000;                                    // how long a command's result stays under the buttons
const TAP_AGAIN_MS = 4000;                               // Resume: the second tap must come this soon
const PREVIEW_EVERY_MS = 30000;                          // Start's checks, asked again this often while it is open

// Which controls make sense in each mower state (Home Assistant's lawn_mower states). Unknown: all of them.
const CONTROLS = {
  mowing: ["pause", "stop", "cmd_recharge"],
  paused: ["resume", "stop", "cmd_recharge"],
  returning: ["pause", "stop"],
  docked: ["start_plan"],
  idle: ["start_plan", "cmd_recharge"],
  error: ["stop", "cmd_recharge"],
};
const CONTROL_ALL = ["start_plan", "pause", "resume", "stop", "cmd_recharge"];
// What a command waits on while its request is out. Start and Resume (they set the robot moving) wait on each other;
// Pause, Stop and Dock each wait only on themselves, so nothing ever holds back a Stop.
const MOVES = ["start_plan", "resume"];
const kindOf = (name) => (MOVES.includes(name) ? "move" : name);
// Errors that mean the integration answered and nothing was sent. Anything else (no answer from the panel, the
// connection to Home Assistant lost) leaves it unknown whether the command went out.
const NOT_SENT = ["commands_off", "rejected", "invalid_format", "not_found", "unauthorized"];
const CONTROL_LABEL = { start_plan: "Start", pause: "Pause", resume: "Resume", stop: "Stop", cmd_recharge: "Dock" };
const CONTROL_ICON = {
  start_plan: "mdi:play", pause: "mdi:pause", resume: "mdi:play-pause", stop: "mdi:stop",
  cmd_recharge: "mdi:home-import-outline",
};

const DEFAULTS = {
  show_status: true, show_controls: true, show_map: true, show_camera: true, camera: "auto", map_height: 320,
  view: "2d", ground_height: 2,
};

const LABELS = {
  entry_id: "Robot (Groundskeeper entry)",
  title: "Title",
  show_status: "Status",
  show_controls: "Controls",
  show_map: "Map",
  show_camera: "Follow camera",
  camera: "Camera source",
  map_height: "Map height",
  view: "Map view",
  ground_height: "3D: draw the ground this much taller",
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
    this._chips = {};
    this._cmd = {
      on: null, plans: [], plan: null, tried: 0, pending: {}, open: false, preview: null, asked: 0,
    };
    this._g3 = null;            // the 3D map (Yard3D) while it shows
    this._g3Token = null;       // set while the 3D view is loading or showing
    this._g3Fail = null;        // why the 3D view could not show, and when: {why, at}
    this._viewPick = null;      // the map's 3D / 2D button, over the view option
    this._heightPick = null;    // the 3D view's height choice, over the ground_height option
  }

  /* -- Lovelace API ------------------------------------------------------------------------------------------ */
  setConfig(config) {
    if (!config || typeof config !== "object") throw new Error("Invalid configuration");
    const next = { ...DEFAULTS, ...config };
    const entryChanged = this._config.entry_id !== next.entry_id;
    if (next.view !== this._config.view) this._viewPick = null;
    if (Number(next.ground_height) !== Number(this._config.ground_height)) this._heightPick = null;
    this._config = next;
    if (entryChanged) this._reset();
    this._built = false;
    if (this._hass) this._render();
    if (entryChanged) this._start();                      // the new robot's feed, without waiting for an update
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (this._notes.robot && this._device()) {        // the robot turned up after the card was drawn
      this._clearNote("robot");
      this._built = false;
    }
    if (!this._built) this._render();
    else { this._updateStatus(); this._updateControls(); }
    if (first || !this._unsub) this._start();
    this._updateCamera();
  }

  connectedCallback() {
    if (!this._hass) return;
    this._start();
    if (this._built && this._map && !this._g3Token && this._want3d()) this._drawMap();   // back on the page
  }

  disconnectedCallback() {
    this._stop();
    this._resize?.disconnect();
    this._drop3d();             // its WebGL context goes with it; it is drawn again when the card comes back
  }

  getCardSize() {
    const c = this._config;
    return (c.show_status ? 1 : 0) + (c.show_map ? Math.ceil(c.map_height / 50) : 0) + (c.show_camera ? 4 : 0);
  }

  getGridOptions() { return { columns: 12, rows: "auto", min_columns: 6 }; }   // height follows the content

  static getStubConfig() {
    return { show_status: true, show_controls: true, show_map: true, show_camera: true, camera: "auto" };
  }

  static getConfigForm() {
    return {
      schema: [
        { name: "entry_id", selector: { config_entry: { integration: DOMAIN } } },
        { name: "title", selector: { text: {} } },
        {
          type: "grid", name: "", schema: [
            { name: "show_status", default: true, selector: { boolean: {} } },
            { name: "show_controls", default: true, selector: { boolean: {} } },
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
        {
          name: "view",
          default: "2d",
          selector: {
            select: {
              mode: "dropdown",
              options: [
                { value: "2d", label: "Flat map" },
                { value: "3d", label: "3D on the yard's ground" },
                { value: "3d_during_runs", label: "3D during runs, flat otherwise" },
              ],
            },
          },
        },
        {
          name: "ground_height",
          default: "2",
          selector: {
            select: { mode: "dropdown", options: GROUND_X.map((v) => ({ value: String(v), label: `${v}×` })) },
          },
        },
        {
          name: "map_height",
          default: DEFAULTS.map_height,
          selector: { number: { min: 160, max: 900, step: 10, unit_of_measurement: "px" } },
        },
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
    this._drop3d();
    this._g3Fail = null;
    this._live = this._pose = this._map = this._mapError = null;
    this._mapSha = undefined;
    this._mapOff = false;
    this._loadingMap = false;
    this._mapToken = null;
    this._mapTried = 0;
    this._track = { run: null, points: [], since: 0, busy: false, at: 0 };
    this._trail = [];
    this._drawn = null;
    this._cmd = {
      on: null, plans: [], plan: null, tried: 0, pending: {}, open: false, preview: null, asked: 0,
    };
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
      this._mapToken = null;           // a map load still running belongs to the closed feed
      this._loadingMap = false;
      this._map = null;
      this._mapSha = undefined;
      this._mapOff = false;
      this._cmd.on = null;             // the Commands option may have changed too
      this._cmd.tried = 0;
      this._retry(RESUBSCRIBE_MS);
      return;
    }
    if (event === "snapshot") {
      this._connected = data.connected;
      if (this._cmd.on !== true) {                     // a (re)subscribe: the Commands option may be on now
        this._cmd.tried = 0;
        this._updateControls();
      }
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
    if (!s.position) this._pose = null;                  // no fix (or no map): the last pose is not where it is
    else if (!this._pose || (s.position.t || 0) >= (this._pose.t || 0)) this._pose = s.position;
    if (s.position) this._g3?.take(s.position);
    if (this._config.show_map && !this._mapOff) {
      // Not read yet, or the last try failed: ask again, at most every RETRY_MS.
      if (!this._map && !this._loadingMap && Date.now() - this._mapTried >= RETRY_MS) this._loadMap();
      // A card left open for a day: read the map again, which signs the photo's address afresh.
      else if (this._map && !this._loadingMap && Date.now() - this._mapTried >= RESIGN_MS) this._loadMap();
      else if (s.map !== undefined && this._mapSha !== undefined && s.map !== this._mapSha) this._loadMap();
    }
    const run = s.run ? s.run.id : null;
    if (run !== this._track.run) {                        // a new run, or none: draw what belongs now
      const ended = this._track.run !== null;
      this._track = { run, points: [], since: 0, busy: false, at: 0 };
      if (ended && this._g3) this._refreshGround();       // the run may have added heights
      if (this._map && this._want3d() !== !!this._g3Token) this._drawMap();   // 3D during runs
      else this._drawTrack();
    }
    if (run !== null && this._config.show_map && Date.now() - this._track.at >= TRACK_EVERY_MS) this._loadTrack();
    this._drawRobot();
    this._updateCamera();
  }

  _onPoses(poses) {
    for (const p of poses || []) {
      if (typeof p?.lon !== "number" || typeof p?.lat !== "number") continue;
      if (!this._pose || (p.t || 0) >= (this._pose.t || 0)) this._pose = p;
      this._trail.push([p.t, p.lon, p.lat]);
      this._g3?.take(p);
    }
    if (this._trail.length > TRAIL_MAX) this._trail.splice(0, this._trail.length - TRAIL_MAX);
    this._drawRobot();
    // A run's track changes only when it is fetched; until it has points, the live trail stands in for it.
    if (this._track.run === null || !this._track.points.length) this._drawTrack();
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
    this._controlsEl = c.show_controls ? el("div", { class: "controls", hidden: true }) : null;
    this._ctl = {};
    this._chips = {};
    this._liveEl = null;
    this._mapWrap = c.show_map ? el("div", { class: "map", style: `height:${Number(c.map_height) || 320}px` }) : null;
    this._camWrap = c.show_camera ? el("div", { class: "camera" }) : null;
    const two = this._mapWrap && this._camWrap ? " two" : "";
    const body = el("div", { class: `body${two}` }, this._mapWrap, this._camWrap);
    card.append(this._statusEl || "", this._controlsEl || "", this._noteEl, body);
    root.append(card);
    this._camEntity = null;
    this._camCard = null;
    this._built = true;
    if (!this._device()) {
      this._setNote("No Groundskeeper robot found. Add the Groundskeeper integration, "
        + "or pick the robot in the card's settings.");
      return;
    }
    this._updateStatus();
    this._updateControls();
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
    const off = this._connected === false;
    if (!this._liveEl) {                                  // status chips hidden: say it with the card's notes
      this._setNote(off ? "Live feed paused: the robot's position may be out of date." : null, "feed");
      return;
    }
    this._liveEl.textContent = off ? "Live feed paused" : "";
    this._liveEl.hidden = !off;
  }

  /* One button per chip, made once and updated in place, so keyboard focus survives hass updates. */
  _chip(key, icon) {
    let chip = this._chips[key];
    if (!chip) {
      const label = el("span");
      const button = el("button", {
        class: "chip",
        onclick: () => button.dataset.entity && this.dispatchEvent(new CustomEvent("hass-more-info",
          { detail: { entityId: button.dataset.entity }, bubbles: true, composed: true })),
      }, el("ha-icon", { icon }), label);
      chip = this._chips[key] = { button, label };
    }
    return chip;
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
    const rows = [
      ["mower", "mdi:robot-mower", e.mower, this._fmt(e.mower) ?? "Unavailable"],
      ["battery", "mdi:battery", e.battery, this._fmt(e.battery)],
      ["positioning", "mdi:crosshairs-gps", e.positioning, this._fmt(e.positioning)],
      ["start_check", "mdi:clipboard-check-outline", e.start_check, this._fmt(e.start_check)],
    ];
    if (!this._liveEl) {
      this._liveEl = el("span", { class: "paused", hidden: true });
      for (const [key, icon] of rows) this._statusEl.append(this._chip(key, icon).button);
      this._statusEl.append(this._liveEl);
      this._clearNote("feed");                            // the chips' own warning takes over
    }
    for (const [key, icon, entity, text] of rows) {
      const { button, label } = this._chip(key, icon);
      button.hidden = text === null;
      if (label.textContent !== (text ?? "")) label.textContent = text ?? "";
      button.title = text ?? "";
      if (entity) button.dataset.entity = entity;
      else delete button.dataset.entity;
    }
    this._updateLiveNote();
  }

  /* -- controls (the Commands option) ---------------------------------------------------------------------------- *
   * The row shows once groundskeeper/plans answers, which it does only with the Commands option on. Buttons are made
   * once and updated in place (keyboard focus survives hass updates); which show follows the mower's state. */
  async _loadPlans() {
    const entry = this._entryId();
    const cmd = this._cmd;
    if (!entry || cmd.loading || Date.now() - cmd.tried < PLANS_RETRY_MS) return;
    cmd.loading = true;
    cmd.tried = Date.now();
    let res = null, off = false;
    try {
      res = await this._hass.callWS({ type: `${DOMAIN}/plans`, entry_id: entry });
    } catch (err) {
      off = err?.code === "commands_off";
    }
    cmd.loading = false;
    if (this._cmd !== cmd) return;                         // another robot was picked meanwhile
    if (res) {
      cmd.on = true;
      cmd.plans = (Array.isArray(res.plans) ? res.plans : []).filter((p) => Number.isInteger(p?.id));
      if (!cmd.plans.some((p) => p.id === cmd.plan)) {
        cmd.plan = cmd.plans.some((p) => p.id === res.default) ? res.default : cmd.plans[0]?.id ?? null;
      }
    } else if (off) {
      cmd.on = false;
      cmd.open = false;
    } else {
      // Not answered (the entry still reloading, the panel away): ask again in RETRY_MS, not a whole minute.
      cmd.tried = Date.now() - PLANS_RETRY_MS + RETRY_MS;
    }
    if (res && cmd.open && cmd.preview?.plan !== cmd.plan) this._preview();   // the chosen plan went away
    this._updateControls();
  }

  /* Commands were switched off while the card showed them: hide the row (the integration refuses anyway). */
  _commandsOff() {
    this._cmd.on = false;
    this._cmd.open = false;
    this._updateControls();
  }

  _updateControls() {
    const wrap = this._controlsEl;
    if (!wrap || !this._hass) return;
    const cmd = this._cmd;
    if (cmd.on !== true || Date.now() - cmd.tried >= 5 * PLANS_RETRY_MS) this._loadPlans();  // plans rarely change
    wrap.hidden = cmd.on !== true;
    if (cmd.on !== true) return;
    const c = this._ctl;
    if (!c.row) this._buildControls();
    const e = this._entities();
    const state = e.mower ? this._hass.states[e.mower]?.state : undefined;
    const shown = CONTROLS[state] || CONTROL_ALL;
    for (const name of CONTROL_ALL) {
      const b = c.buttons[name];
      b.hidden = !shown.includes(name);
      b.disabled = Boolean(cmd.pending[kindOf(name)]);
      const label = name === "resume" && c.armed === "resume" ? "Tap again to resume" : CONTROL_LABEL[name];
      if (b.lastChild.textContent !== label) b.lastChild.textContent = label;
    }
    if (!shown.includes("start_plan")) cmd.open = false;
    c.buttons.start_plan.setAttribute("aria-expanded", String(cmd.open));
    c.panel.hidden = !cmd.open;
    if (cmd.open) {
      // The checks change with the robot and the yard: ask again when the mower's state moves, and now and then.
      const due = state !== c.previewState || Date.now() - (cmd.previewAt || 0) >= PREVIEW_EVERY_MS;
      if (due && !cmd.previewing) this._preview(true);
      this._drawStartPanel();
    }
    c.previewState = state;
  }

  _buildControls() {
    const c = this._ctl;
    c.buttons = {};
    c.row = el("div", { class: "ctl-row" });
    for (const name of CONTROL_ALL) {
      const b = el("button", { class: `ctl ${name}`, type: "button", onclick: () => this._press(name) },
        el("ha-icon", { icon: CONTROL_ICON[name] }), el("span", { text: CONTROL_LABEL[name] }));
      c.buttons[name] = b;
      c.row.append(b);
    }
    c.select = el("select", {
      "aria-label": "Plan",
      onchange: () => {
        this._cmd.plan = Number(c.select.value);
        this._preview();
      },
    });
    c.checks = el("ul", { class: "checks" });
    c.verdict = el("div", { class: "verdict" });
    c.go = el("button", { class: "ctl go", type: "button", onclick: () => this._send("start_plan", this._cmd.plan) },
      el("ha-icon", { icon: "mdi:play" }), el("span", { text: "Start this plan" }));
    c.cancel = el("button", {
      class: "ctl",
      type: "button",
      onclick: () => {
        this._cmd.open = false;
        this._updateControls();
      },
    }, el("span", { text: "Cancel" }));
    c.panel = el("div", { class: "start", hidden: true },
      el("label", {}, el("span", { text: "Plan" }), c.select), c.verdict, c.checks,
      el("div", { class: "ctl-row" }, c.go, c.cancel));
    c.say = el("div", { class: "say", hidden: true, role: "status" });
    this._controlsEl.replaceChildren(c.row, c.panel, c.say);
  }

  _drawStartPanel() {
    const c = this._ctl;
    const cmd = this._cmd;
    if (!c.panel) return;
    const key = JSON.stringify(cmd.plans);
    if (c.plansKey !== key) {
      c.plansKey = key;
      c.select.replaceChildren(...cmd.plans.map((p) => el("option", { value: p.id, text: p.name })));
    }
    if (cmd.plan !== null && c.select.value !== String(cmd.plan)) c.select.value = String(cmd.plan);
    const pv = cmd.preview;
    let verdict = "Asking the panel…";
    let ok = false;
    const items = [];
    if (!cmd.plans.length) verdict = "The panel has no plans. Make one in the Yarbo app.";
    else if (pv?.error) verdict = pv.error;
    else if (pv && pv.plan === cmd.plan) {
      ok = pv.would_send === true;
      verdict = ok ? "The panel would start it now." : "The panel would not start it now:";
      if (!ok) {
        for (const w of Array.isArray(pv.why) ? pv.why : []) {
          if (w?.title) items.push(w.fix ? `${w.title}. ${w.fix}.` : `${w.title}.`);
        }
        if (!items.length && pv.reason) items.push(String(pv.reason));
      }
    }
    c.verdict.textContent = verdict;
    c.verdict.classList.toggle("ok", ok);
    c.checks.replaceChildren(...items.map((t) => el("li", { text: t })));
    c.go.disabled = !ok || Boolean(cmd.pending.move);
  }

  _press(name) {
    const cmd = this._cmd;
    const c = this._ctl;
    if (name === "start_plan") {
      cmd.open = !cmd.open;
      if (cmd.open) this._preview();
      this._updateControls();
      return;
    }
    if (name === "resume" && c.armed !== "resume") {     // it sets the robot moving: a second tap, soon
      c.armed = "resume";
      clearTimeout(c.disarm);
      c.disarm = setTimeout(() => { c.armed = null; this._updateControls(); }, TAP_AGAIN_MS);
      this._updateControls();
      return;
    }
    c.armed = null;
    clearTimeout(c.disarm);
    this._send(name);
  }

  /* Start's checks for the chosen plan. `again`: the same plan asked afresh, so the last answer stays up meanwhile
   * (no flicker); a new plan clears it, since that answer was for another plan. */
  async _preview(again = false) {
    const cmd = this._cmd;
    const entry = this._entryId();
    const plan = cmd.plan;
    const asked = ++cmd.asked;
    cmd.previewAt = Date.now();
    if (!again || cmd.preview?.plan !== plan) {
      cmd.preview = null;
      this._drawStartPanel();
    }
    if (!entry || plan === null) return;
    cmd.previewing = true;
    let pv, off = false;
    try {
      pv = await this._hass.callWS({ type: `${DOMAIN}/preview`, entry_id: entry, cmd: "start_plan", plan_id: plan });
    } catch (err) {
      off = err?.code === "commands_off";
      pv = { error: off ? "Commands are switched off in the integration's options."
        : "The panel could not be asked; try again." };
    }
    if (asked === cmd.asked) cmd.previewing = false;
    if (this._cmd !== cmd || asked !== cmd.asked) return;    // another plan was picked, or another robot
    if (off) {
      this._commandsOff();
      return;
    }
    cmd.preview = { ...pv, plan };
    cmd.previewAt = Date.now();
    if (cmd.open) this._drawStartPanel();
  }

  async _send(name, plan) {
    const cmd = this._cmd;
    const entry = this._entryId();
    const kind = kindOf(name);
    if (!entry || cmd.pending[kind]) return;
    cmd.pending[kind] = true;
    this._updateControls();
    const msg = { type: `${DOMAIN}/command`, entry_id: entry, cmd: name };
    if (name === "start_plan") msg.plan_id = plan;
    let text;
    let ok = false;
    let off = false;
    try {
      const res = await this._hass.callWS(msg);
      ok = res?.sent === true;
      text = ok ? `${CONTROL_LABEL[name]} sent. The panel is checking that the robot follows.`
        : `Not sent: ${res?.why_not || "the panel did not send it"}.`;
    } catch (err) {
      off = err?.code === "commands_off";
      // "Not sent" only when the integration said so. No answer from the panel, or the connection to Home
      // Assistant lost mid-request: it may have gone out, so say that and never invite a blind retry.
      text = NOT_SENT.includes(err?.code) ? `Not sent: ${err?.message || "the integration turned it down"}.`
        : `No answer: ${CONTROL_LABEL[name]} may or may not have gone out. `
          + "Check what the robot is doing before trying again.";
    }
    if (this._cmd !== cmd) return;
    cmd.pending[kind] = false;
    if (off) {
      this._commandsOff();
      return;
    }
    if (ok && name === "start_plan") cmd.open = false;
    else if (name === "start_plan") this._preview(true);     // refused: show what the panel says now
    this._say(text, ok);
    this._updateControls();
  }

  _say(text, ok) {
    const c = this._ctl;
    if (!c.say) return;
    c.say.textContent = text;
    c.say.classList.toggle("bad", !ok);
    c.say.hidden = false;
    clearTimeout(c.sayTimer);
    c.sayTimer = setTimeout(() => { c.say.hidden = true; }, SAY_MS);
  }

  _drawMap() {
    const wrap = this._mapWrap;
    if (!wrap) return;
    const m = this._map;
    if (!m || !Array.isArray(m.bounds) || !m.geojson) {
      this._drop3d();
      wrap.replaceChildren(el("div", { class: "empty", text: this._mapError || "Loading the map…" }));
      this._svg = this._robotG = this._trackG = null;
      return;
    }
    if (this._want3d()) {
      this._draw3d();
      return;
    }
    this._drop3d();
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
    wrap.replaceChildren(svg, this._mapTools(false));
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

  /* The map's buttons: 3D or flat, the 3D ground's height, and follow the robot. */
  _mapTools(three) {
    const tools = el("div", { class: "tools" });
    tools.append(el("button", {
      class: "tool", text: three ? "2D" : "3D",
      title: three ? "Show the flat map" : "Show the yard in 3D, on its ground",
      onclick: () => {
        this._viewPick = three ? "2d" : "3d";
        if (three) this._clearNote("3d");
        else this._g3Fail = null;                       // asked for: try again now
        this._follow = false;
        this._drawMap();
      },
    }));
    if (three) {
      const now = this._height();
      tools.append(el("select", {
        class: "tool", title: "Draw the ground this much taller", "aria-label": "Draw the ground this much taller",
        onchange: (e) => {
          this._heightPick = Number(e.target.value);
          this._g3?.setHeight(this._heightPick);
        },
      }, ...GROUND_X.map((v) => el("option", { value: v, text: `${v}×`, selected: v === now }))));
    }
    const follow = el("button", {
      class: "tool round", title: "Follow the robot", "aria-pressed": String(this._follow),
      onclick: () => {
        this._follow = !this._follow;
        if (this._g3) this._follow = this._g3.setFollow(this._follow, true);
        else {
          this._view();
          this._drawRobot();
        }
        follow.setAttribute("aria-pressed", String(this._follow));
      },
    }, el("ha-icon", { icon: "mdi:crosshairs-gps" }));
    this._followBtn = follow;
    tools.append(follow);
    return tools;
  }

  _height() {
    const x = Number(this._heightPick ?? this._config.ground_height);
    return GROUND_X.includes(x) ? x : 2;
  }

  /* 3D when the option (or the button) asks for it, unless it failed lately. */
  _want3d() {
    if (!this._config.show_map) return false;
    if (this._g3Fail && Date.now() - this._g3Fail.at < GROUND_RETRY_MS) return false;
    const pick = this._viewPick ?? this._config.view;
    return pick === "3d" || (pick === "3d_during_runs" && this._track.run !== null);
  }

  async _draw3d() {
    const wrap = this._mapWrap, m = this._map, entry = this._entryId();
    this._drop3d();
    const token = (this._g3Token = {});
    this._svg = this._robotG = this._trackG = null;
    this._resize?.disconnect();
    wrap.replaceChildren(el("div", { class: "empty", text: "Loading the 3D view…" }));
    let ground, libs;
    try {
      if (!hasWebGL()) throw new Error("this browser cannot draw 3D (no WebGL)");
      ground = await this._hass.callWS({ type: `${DOMAIN}/ground`, entry_id: entry });
      libs = await loadLibs(this._hass, ground.vendor);
    } catch (err) {
      if (token !== this._g3Token) return;
      const why = {
        no_ground: "the panel has no ground for this yard yet",
        map_off: "the map is switched off in the integration's options",
        panel_unavailable: "the panel did not answer",
      }[err?.code] || err?.message || "it could not be loaded";
      this._fail3d(why);
      return;
    }
    if (token !== this._g3Token || this._map !== m) return;     // switched off, or a new map, meanwhile
    const box = el("div", { class: "gl" });
    wrap.replaceChildren(el("style", { text: libs.css }), box, this._mapTools(true));
    try {
      this._g3 = new Yard3D({
        container: box, libs, ground, map: m, height: this._height(), hass: () => this._hass,
        onFollow: (on) => {
          this._follow = on;
          this._followBtn?.setAttribute("aria-pressed", String(on));
        },
      });
    } catch (err) {
      this._fail3d(err?.message || "it could not be drawn");
      return;
    }
    this._clearNote("3d");
    if (this._pose) this._g3.take(this._pose);
    this._drawTrack();
  }

  _fail3d(why) {
    this._drop3d();
    this._g3Fail = { why, at: Date.now() };
    this._setNote(`No 3D view: ${why}. Showing the flat map.`, "3d");
    this._drawMap();
  }

  _drop3d() {
    this._g3Token = null;
    if (this._g3) {
      this._g3.destroy();
      this._g3 = null;
      this._follow = false;
    }
  }

  /* After a run: the ground again, which the run may have added heights to. */
  async _refreshGround() {
    const g3 = this._g3;
    try {
      const ground = await this._hass.callWS({ type: `${DOMAIN}/ground`, entry_id: this._entryId() });
      if (this._g3 === g3) g3.setGround(ground);
    } catch (_) { /* the ground on show stays */ }
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
    const t = this._track;
    const run = t.run !== null && t.points.length ? t.run : null;
    const trail = () => this._trail.map(([ts, lon, lat]) => [ts, lon, lat, -1]);
    if (this._g3) {
      this._g3.setTrack(run !== null ? t.points : trail());
      return;
    }
    const g = this._trackG;
    if (!g || !this._proj) return;
    const rows = run !== null ? t.points : trail();
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
    let caption = "";
    if (name && follow.covered === true) caption = `${name}: covers the robot`;
    else if (name && follow.covered === false) caption = `${name}: no camera covers the robot`;
    else if (name) caption = name;                                        // coverage unknown: just the camera
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
        if (this._camEntity === entity) this._camEntity = null;      // the next update tries again
        return;
      }
    }
    if (this._camCard) this._camCard.hass = this._hass;
    if (this._camCaption) this._camCaption.textContent = caption;
  }
}

/* -- the 3D view ------------------------------------------------------------------------------------------------ *
 * The yard on the panel's ground (the robot's own heights over the lawns, public lidar around them), with the robot
 * as a small 3D model. MapLibre and three.js are the panel's own copies, passed on by the integration only when each
 * matches the checksum it pins. They are fetched, signed in, the first time a card shows 3D, and shared by every card
 * on the page. Anything missing (no WebGL, no ground yet, a library that does not load) leaves the flat map. The model
 * and its motion follow the panel's web/robot3d.js. */
const GROUND_X = [1, 1.5, 2, 3];                         // the ground drawn this much taller
const DEG = Math.PI / 180;
const R3 = {
  LEN: 1.40,          // metres, the mower's front to the back of the tracks
  MIN_PX: 34,         // the model is never drawn shorter than this on screen
  AHEAD_S: 2.5,       // keep moving on the last speed and heading for this long, then wait
  EASE_S: 0.5,        // how quickly it closes on where it should be
  JUMP_M: 25,         // further behind than this (a new run, a long gap): put it there at once
  RESET_S: 30,        // nothing newer by the robot's clock this long: take the next sample anyway
  GATE_MPS: 2,        // faster than this between two samples is a stray reading, not the robot...
  GATE_PACE: 2.5,     // ...or than this many times the pace it has been keeping
  GATE_M: 3,          // ...beyond this much room for noise
  GATE_HOLD_S: 6,     // a far sample nothing confirms is dropped
  MOWER_PITCH: 10,    // degrees the mower turns about the hitch at most, nose up or down...
  MOWER_ROLL: 6,      // ...and side to side
  FOLLOW_SIDE: 45,    // the follow camera sits behind the robot and this far round to one side
  FOLLOW_TURN: 40,    // degrees a second it turns at most to stay behind the robot
  FOLLOW_LEAD: 1.5,   // metres ahead of the robot the camera looks at
};
const MODEL = { body: "#2A2D30", frame: "#4A5055", track: "#17191A", trim: "#F2C01E", stop: "#D8322B",
  light: "#F4F7FA" };
const GROUND_RETRY_MS = 5 * 60 * 1000;                   // 3D failed: try again after this long (or on a tap)
const metres = (lat) => [111320 * Math.cos(lat * DEG), 111320];
const angle = (a) => ((a % 360) + 540) % 360 - 180;     // -180..180
const LIBS = { promise: null };

function hasWebGL() {
  try {
    const c = document.createElement("canvas");
    return !!(c.getContext("webgl2") || c.getContext("webgl"));
  } catch (_) {
    return false;
  }
}

/* MapLibre (its script and style sheet) and three.js, through the integration (signed in), once per page. */
function loadLibs(hass, vendor) {
  if (!LIBS.promise) {
    LIBS.promise = (async () => {
      const get = async (name, type) => {
        const r = await hass.fetchWithAuth(vendor[name]);
        if (!r.ok) throw new Error(`${name} could not be read (${r.status})`);
        return new Blob([await r.arrayBuffer()], { type });
      };
      const [js, css, three] = await Promise.all([get("maplibre-gl.js", "text/javascript"),
        get("maplibre-gl.css", "text/css"), get("three.module.min.js", "text/javascript")]);
      if (!window.maplibregl) {
        const url = URL.createObjectURL(js);
        try {
          await new Promise((ok, fail) => {
            const s = document.createElement("script");
            s.src = url;
            s.onload = ok;
            s.onerror = () => fail(new Error("MapLibre did not load"));
            document.head.append(s);
          });
        } finally {
          URL.revokeObjectURL(url);
        }
      }
      const url = URL.createObjectURL(three);
      let THREE;
      try {
        THREE = await import(url);
      } finally {
        URL.revokeObjectURL(url);
      }
      if (!window.maplibregl || !THREE?.WebGLRenderer) throw new Error("the 3D libraries did not load");
      return { maplibregl: window.maplibregl, THREE, css: await css.text() };
    })();
    LIBS.promise.catch(() => { LIBS.promise = null; });  // a later try may work
  }
  return LIBS.promise;
}

/* The robot from simple solids about its size (forward is +Y, up is +Z, in metres): a Yarbo core with the Lawn
 * Mower Pro, which hangs on the core's hitch (returned as `mower`, to turn about it). */
function buildRobot(THREE) {
  const g = new THREE.Group();
  const mat = (c) => new THREE.MeshLambertMaterial({ color: c });
  // A soft shadow under it, so it stands on the lawn: three see-through ellipses, drawn through the ground but
  // before the robot.
  [[0.80, 1.0], [0.66, 0.86], [0.54, 0.72]].forEach(([rx, ry], i) => {
    const m = new THREE.Mesh(new THREE.CircleGeometry(1, 40), new THREE.MeshBasicMaterial({
      color: 0x000000, opacity: 0.28, transparent: false, blending: THREE.CustomBlending,
      blendSrc: THREE.SrcAlphaFactor, blendDst: THREE.OneMinusSrcAlphaFactor,
      depthTest: false, depthWrite: false, side: THREE.DoubleSide }));
    m.scale.set(rx, ry, 1);
    m.position.set(0, 0.04, 0.01);
    m.renderOrder = -1 - i;
    g.add(m);
  });
  const core = new THREE.Group(), mower = new THREE.Group();
  const HITCH = [0, 0.13, 0.17];
  mower.position.set(...HITCH);
  g.add(core, mower);
  const black = mat(MODEL.body), graphite = mat(MODEL.frame), yellow = mat(MODEL.trim), tread = mat(MODEL.track);
  const red = mat(MODEL.stop);
  const put = (to, off) => (geo, m, x, y, z) => {
    const o = new THREE.Mesh(geo, m);
    o.position.set(x - off[0], y - off[1], z - off[2]);
    to.add(o);
    return o;
  };
  const add = put(core, [0, 0, 0]), madd = put(mower, HITCH);
  const rounded = (w, l, r) => {                       // a rounded rectangle, w across and l long
    const s = new THREE.Shape(), x = w / 2, y = l / 2;
    s.moveTo(-x + r, -y);
    s.lineTo(x - r, -y); s.quadraticCurveTo(x, -y, x, -y + r);
    s.lineTo(x, y - r); s.quadraticCurveTo(x, y, x - r, y);
    s.lineTo(-x + r, y); s.quadraticCurveTo(-x, y, -x, y - r);
    s.lineTo(-x, -y + r); s.quadraticCurveTo(-x, -y, -x + r, -y);
    return s;
  };
  const slab = (w, l, h, r, bevel = 0.012, seg = 2) => {   // a slab of that outline, h tall, edges softened
    const geo = new THREE.ExtrudeGeometry(rounded(w - 2 * bevel, l - 2 * bevel, Math.max(0.001, r - bevel)), {
      depth: h - 2 * bevel, bevelEnabled: true, bevelSize: bevel, bevelThickness: bevel, bevelSegments: seg,
      curveSegments: 10 });
    geo.translate(0, 0, bevel);
    return geo;
  };
  const hip = (w, l, wt, lt, h, dy = 0) => {            // a w x l base narrowing to a wt x lt top, h up
    const b = [[-w / 2, -l / 2], [w / 2, -l / 2], [w / 2, l / 2], [-w / 2, l / 2]];
    const t = [[-wt / 2, -lt / 2 + dy], [wt / 2, -lt / 2 + dy], [wt / 2, lt / 2 + dy], [-wt / 2, lt / 2 + dy]];
    const v = [];
    const quad = (p, q, r, s) => v.push(...p, ...q, ...r, ...p, ...r, ...s);
    const B = (i) => [b[i][0], b[i][1], 0], T = (i) => [t[i][0], t[i][1], h];
    for (let i = 0; i < 4; i++) quad(B(i), B((i + 1) % 4), T((i + 1) % 4), T(i));
    quad(T(0), T(1), T(2), T(3));
    const geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.Float32BufferAttribute(v, 3));
    geo.computeVertexNormals();
    return geo;
  };
  const upright = (geo) => { geo.rotateX(Math.PI / 2); return geo; };

  // The core: two rubber tracks, the chassis between them, the rounded shell over them.
  const TL = 0.78, TH = 0.24, TW = 0.14, TX = 0.27, CY = -0.27, R = TH / 2;
  const belt = new THREE.ExtrudeGeometry(rounded(TL, TH, R - 0.001), {
    depth: TW - 0.02, bevelEnabled: true, bevelSize: 0.01, bevelThickness: 0.01, bevelSegments: 2, curveSegments: 14 });
  belt.rotateY(Math.PI / 2);
  belt.rotateX(Math.PI / 2);
  belt.translate(-(TW - 0.02) / 2, 0, R);
  for (const side of [1, -1]) add(belt, tread, side * TX, CY, 0);
  add(slab(0.40, 0.70, 0.13, 0.04), graphite, 0, CY, 0.11);
  add(slab(0.50, 0.64, 0.15, 0.13, 0.04, 4), black, 0, CY - 0.01, 0.235);
  add(hip(0.40, 0.52, 0.30, 0.40, 0.028, 0), black, 0, CY - 0.01, 0.378);
  const foot = upright(new THREE.CylinderGeometry(0.03, 0.036, 0.03, 16));
  const neck = upright(new THREE.CylinderGeometry(0.011, 0.013, 0.07, 10));
  const grip = upright(new THREE.CapsuleGeometry(0.023, 0.10, 4, 12));
  for (const side of [1, -1]) {                         // yellow shoulders, each with a stubby antenna
    add(slab(0.11, 0.32, 0.10, 0.035, 0.015, 3), yellow, side * 0.275, CY + 0.13, 0.235);
    add(foot, graphite, side * 0.285, CY + 0.10, 0.345);
    add(neck, black, side * 0.285, CY + 0.10, 0.395);
    add(grip, black, side * 0.285, CY + 0.10, 0.50);
  }
  add(slab(0.13, 0.085, 0.012, 0.012, 0.004), graphite, 0, CY - 0.19, 0.402);
  add(slab(0.10, 0.058, 0.032, 0.012, 0.006), red, 0, CY - 0.19, 0.410);              // the red stop button
  add(upright(new THREE.CylinderGeometry(0.026, 0.03, 0.022, 16)),                   // the beacon
    new THREE.MeshBasicMaterial({ color: C.robot }), 0, CY + 0.06, 0.416);

  // The Lawn Mower Pro: a hitch box, a wide two-step skirt and a faceted housing with its yellow handles.
  const MY = 0.44;
  madd(slab(0.28, 0.20, 0.19, 0.03, 0.012), black, 0, 0.13, 0.12);
  madd(slab(0.82, 0.60, 0.075, 0.15, 0.02, 3), black, 0, MY, 0.03);
  madd(slab(0.75, 0.53, 0.07, 0.14, 0.02, 3), graphite, 0, MY, 0.095);
  madd(slab(0.60, 0.42, 0.07, 0.10, 0.02, 3), black, 0, MY - 0.02, 0.155);
  madd(hip(0.54, 0.36, 0.28, 0.18, 0.12, -0.03), black, 0, MY - 0.02, 0.215);
  const knob = new THREE.SphereGeometry(0.055, 16, 10);
  knob.scale(1, 1, 0.6);
  const bar = slab(0.045, 0.24, 0.04, 0.015, 0.008), leg = slab(0.045, 0.045, 0.06, 0.012, 0.006);
  for (const side of [1, -1]) {
    madd(knob, yellow, side * 0.215, MY + 0.07, 0.235);
    madd(bar, yellow, side * 0.335, MY + 0.02, 0.205);
    for (const e of [1, -1]) madd(leg, yellow, side * 0.335, MY + 0.02 + e * 0.0975, 0.155);
  }
  const light = new THREE.MeshBasicMaterial({ color: MODEL.light });
  for (const side of [1, -1]) madd(new THREE.BoxGeometry(0.15, 0.012, 0.026), light, side * 0.17, MY + 0.262, 0.128);
  madd(new THREE.BoxGeometry(0.46, 0.012, 0.014), red, 0, MY + 0.297, 0.062);
  return { robot: g, mower };
}

/* One card's 3D map. `hass()` gives the card's current Home Assistant object (its token renews), `onFollow(on)` is
 * told when following stops or starts. */
class Yard3D {
  constructor({ container, libs, ground, map, height, hass, onFollow }) {
    const ml = libs.maplibregl;
    this.libs = libs;
    this.hass = hass;
    this.onFollow = onFollow;
    this.ground = ground;
    this.x = height;
    this.fix = null;            // the last live sample: {lon, lat, heading, pitch, roll, speed, t, at}
    this.held = null;           // a far sample waiting for the next one to confirm it
    this.pace = 0;              // metres a second between recent samples, smoothed
    this.pose = null;           // what is drawn
    this.raf = 0;
    this.last = 0;
    this.trackData = { type: "FeatureCollection", features: [] };
    this.follow = { on: false, offset: R3.FOLLOW_SIDE, cam: null, turning: false, hands: false, wheelAt: 0 };
    const [w, s, e, n] = map.bounds;
    this.fit = [[w, s], [e, n]];
    const origin = window.location.origin;
    this.map = new ml.Map({
      container, attributionControl: false, maxPitch: 80, pitch: 55, maxZoom: 23,
      bounds: this.fit, fitBoundsOptions: { padding: 24 },
      style: { version: 8, sources: {},
        layers: [{ id: "bg", type: "background", paint: { "background-color": "#1A1F1C" } }] },
      // The ground's tiles and the photo come from Home Assistant: signed in, like every other request of the card.
      transformRequest: (url) => (url.startsWith(`${origin}/api/`)
        ? { url, headers: { Authorization: `Bearer ${this.hass()?.auth?.data?.access_token || ""}` } } : undefined),
    });
    this.map.on("error", () => {});                   // a tile that did not come: the ground stays as it was
    this.map.addControl(new ml.NavigationControl({ showZoom: false, visualizePitch: true }), "top-left");
    this.map.on("load", () => this._layers(map));
    this._renew();
    this.renewTimer = setInterval(() => this._renew(), 30000);
    this._gestures();
  }

  /* The token in the tiles' header lasts 30 minutes: renew it in time, as the frontend does for its own calls. */
  _renew() {
    const a = this.hass()?.auth;
    if (a?.data?.expires && a.data.expires - Date.now() < 120000 && a.refreshAccessToken) {
      a.refreshAccessToken().catch(() => {});
    }
  }

  _dem(g) {
    return { type: "raster-dem", tiles: [window.location.origin + g.tiles], tileSize: 256, encoding: g.encoding,
      minzoom: g.minzoom, maxzoom: g.maxzoom, bounds: g.bounds };
  }

  _layers(m) {
    const mp = this.map;
    if (m.yard?.signed && Array.isArray(m.yard.bbox)) {
      const [w, s, e, n] = m.yard.bbox;
      mp.addSource("gk-photo", { type: "image", url: window.location.origin + m.yard.signed,
        coordinates: [[w, n], [e, n], [e, s], [w, s]] });
      mp.addLayer({ id: "gk-photo", type: "raster", source: "gk-photo",
        paint: { "raster-opacity": 0.95, "raster-fade-duration": 0 } });
    }
    this._addGround();
    mp.addSource("gk-shapes", { type: "geojson", data: m.geojson });
    const kind = (k) => ["==", ["get", "kind"], k];
    const metre = (m0) => ["interpolate", ["exponential", 2], ["zoom"], 15, m0 * 0.55, 22, m0 * 70];
    mp.addLayer({ id: "gk-area", type: "fill", source: "gk-shapes", filter: kind("areas"),
      paint: { "fill-color": C.area, "fill-opacity": 0.08 } });
    mp.addLayer({ id: "gk-area-edge", type: "line", source: "gk-shapes", filter: kind("areas"),
      paint: { "line-color": C.area, "line-width": 2 } });
    mp.addLayer({ id: "gk-novis", type: "line", source: "gk-shapes", filter: kind("novisionzones"),
      paint: { "line-color": C.novis, "line-width": 2, "line-dasharray": [3, 2] } });
    mp.addLayer({ id: "gk-nogo", type: "fill", source: "gk-shapes", filter: kind("nogozones"),
      paint: { "fill-color": C.nogo, "fill-opacity": 0.22 } });
    mp.addLayer({ id: "gk-nogo-edge", type: "line", source: "gk-shapes", filter: kind("nogozones"),
      paint: { "line-color": C.nogo, "line-width": 2 } });
    mp.addLayer({ id: "gk-road", type: "line", source: "gk-shapes", filter: kind("pathways"),
      layout: { "line-cap": "round", "line-join": "round" },
      paint: { "line-color": C.road, "line-width": metre(0.9), "line-opacity": 0.95 } });
    mp.addLayer({ id: "gk-road-mid", type: "line", source: "gk-shapes", filter: kind("pathways"),
      paint: { "line-color": "#F2F5F3", "line-width": 1, "line-dasharray": [4, 4] } });
    mp.addLayer({ id: "gk-dock", type: "circle", source: "gk-shapes", filter: kind("charging"),
      paint: { "circle-color": C.ink, "circle-radius": 5, "circle-stroke-color": C.dock,
        "circle-stroke-width": 2.5 } });
    mp.addSource("gk-track", { type: "geojson", data: this.trackData });
    mp.addLayer({ id: "gk-track", type: "line", source: "gk-track",
      layout: { "line-cap": "round", "line-join": "round" },
      paint: { "line-color": ["get", "c"], "line-width": 2.5, "line-opacity": 0.9 } });
    mp.addLayer(this._robotLayer());
    this.ready = true;
    this._kick();
  }

  /* The ground and its shading (lit from 335° on the map, as the panel's is, and the model). MapLibre wants a
   * source of its own for the shading. */
  _addGround() {
    const mp = this.map, dem = this._dem(this.ground);
    mp.addSource("gk-ground", dem);
    mp.addSource("gk-shade", { ...dem });
    mp.addLayer({ id: "gk-shade", type: "hillshade", source: "gk-shade",
      paint: { "hillshade-illumination-anchor": "map", "hillshade-illumination-direction": 335,
        "hillshade-exaggeration": 0.3, "hillshade-shadow-color": "#0E1311", "hillshade-accent-color": "#0E1311",
        "hillshade-highlight-color": "#FFFFFF" } }, mp.getLayer("gk-area") ? "gk-area" : undefined);
    mp.setTerrain({ source: "gk-ground", exaggeration: this.x });
  }

  /* A newer ground (a run added heights): the new tiles, or, over a different area, both sources again. */
  setGround(g) {
    if (!g || g.version === this.ground.version) return;
    const old = this.ground;
    this.ground = g;
    if (!this.ready) return;
    const mp = this.map;
    if (g.minzoom === old.minzoom && g.maxzoom === old.maxzoom && String(g.bounds) === String(old.bounds)) {
      for (const id of ["gk-ground", "gk-shade"]) mp.getSource(id).setTiles([window.location.origin + g.tiles]);
      return;
    }
    mp.setTerrain(null);
    mp.removeLayer("gk-shade");
    mp.removeSource("gk-shade");
    mp.removeSource("gk-ground");
    this._addGround();
  }

  setHeight(x) {
    this.x = x;
    if (this.ready) this.map.setTerrain({ source: "gk-ground", exaggeration: x });
  }

  /* The track, [t, lon, lat, rtk] rows (rtk -1: the live trail), as lines in its colours. */
  setTrack(rows) {
    const features = [];
    let cur = null;
    for (const [, lon, lat, rtk] of rows) {
      if (typeof lon !== "number" || typeof lat !== "number") continue;
      const c = rtk === -1 ? C.trail : RTK_COLOUR[rtk] || C.single;
      if (!cur || cur.properties.c !== c) {
        const from = cur ? cur.geometry.coordinates[cur.geometry.coordinates.length - 1] : null;
        cur = { type: "Feature", properties: { c }, geometry: { type: "LineString", coordinates: from ? [from] : [] } };
        features.push(cur);
      }
      cur.geometry.coordinates.push([lon, lat]);
    }
    this.trackData = { type: "FeatureCollection", features: features.filter((f) => f.geometry.coordinates.length > 1) };
    if (this.ready) this.map.getSource("gk-track").setData(this.trackData);
  }

  /* One position sample: {lon, lat, heading, pitch, roll, speed, t}, t the robot's own time. The same time again is
   * the same sample and an older one is late (the backlog after a dropout): both dropped, unless nothing newer has
   * come for R3.RESET_S (the robot's clock was set back). */
  take(s) {
    if (!s || typeof s.lon !== "number" || typeof s.lat !== "number") return;
    const now = performance.now(), f = this.fix;
    const t = typeof s.t === "number" ? s.t : null;
    if (f && f.t !== null && t !== null && (t === f.t || (t < f.t && now - f.at < R3.RESET_S * 1000))) return;
    if (f && t === null && s.lon === f.lon && s.lat === f.lat) return;
    if (!this._gate(s, t, now)) return;
    const num = (v, d) => (typeof v === "number" && Number.isFinite(v) ? v : d);
    this.fix = { lon: s.lon, lat: s.lat, heading: num(s.heading, f ? f.heading : 0), pitch: num(s.pitch, 0),
      roll: num(s.roll, 0), speed: num(s.speed, 0), t, at: now };
    if (!this.pose) this.pose = { ...this.fix };
    this._kick();
  }

  /* The speed gate: a sample further from the last than the robot could have driven is held, not drawn; the next
   * one decides whether the robot really moved there (taken) or it was a stray (dropped). */
  _gate(s, t, now) {
    const f = this.fix;
    if (!f || now - f.at > R3.GATE_HOLD_S * 1000) { this.held = null; this.pace = 0; return true; }
    const dist = (a, b) => {
      const [kx, ky] = metres(b.lat);
      return Math.hypot((a.lon - b.lon) * kx, (a.lat - b.lat) * ky);
    };
    const gap = (at, bt, aAt, bAt) => Math.max(0, at !== null && bt !== null ? at - bt : (aAt - bAt) / 1000);
    const fits = (d, secs) => d <= R3.GATE_M + Math.max(R3.GATE_MPS, R3.GATE_PACE * this.pace) * secs;
    const take = (d, secs) => {
      if (secs > 0.2 && secs <= 3) this.pace = 0.7 * this.pace + 0.3 * Math.min(10, d / secs);
      this.held = null;
      return true;
    };
    const df = dist(s, f), gf = gap(t, f.t, now, f.at);
    if (fits(df, gf)) return take(df, gf);
    const h = this.held;
    if (h && (t !== null ? t === h.t : s.lon === h.s.lon && s.lat === h.s.lat)) return false;   // the held one again
    if (h && now - h.at <= R3.GATE_HOLD_S * 1000) {
      const dh = dist(s, h.s), gh = gap(t, h.t, now, h.at);
      if (fits(dh, gh)) return take(dh, gh);
    }
    this.held = { s, t, at: now };
    return false;
  }

  _kick() {
    if (!this.raf && !this.dead) {
      this.last = performance.now();
      this.raf = requestAnimationFrame((ts) => this._tick(ts));
    }
  }

  /* One frame: move the drawn pose toward where the robot should be (carrying on at its speed and heading for up to
   * R3.AHEAD_S, then catching up, never in a jump); stop asking for frames once it is there. */
  _tick(now) {
    this.raf = 0;
    const p = this.pose, f = this.fix;
    if (this.dead || !this.ready || !p || !f) return;
    const dt = Math.max(0, Math.min(0.25, (now - this.last) / 1000));
    this.last = now;
    const [kx, ky] = metres(f.lat);
    const ahead = Math.min(R3.AHEAD_S, (now - f.at) / 1000) * (Math.abs(f.speed) > 0.03 ? f.speed : 0);
    const tx = f.lon + (Math.sin(f.heading * DEG) * ahead) / kx, ty = f.lat + (Math.cos(f.heading * DEG) * ahead) / ky;
    const ex = (tx - p.lon) * kx, ey = (ty - p.lat) * ky, err = Math.hypot(ex, ey);
    if (err > R3.JUMP_M) { p.lon = tx; p.lat = ty; }
    else {
      const k = 1 - Math.exp(-dt / R3.EASE_S), cap = (0.9 + err * 0.6) * dt;
      let sx = ex * k, sy = ey * k;
      const m = Math.hypot(sx, sy);
      if (m > cap && m > 1e-9) { sx *= cap / m; sy *= cap / m; }
      p.lon += sx / kx;
      p.lat += sy / ky;
    }
    const turn = angle(f.heading - p.heading), e = 1 - Math.exp(-dt * 4);
    p.heading += turn * e;
    p.pitch += (f.pitch - p.pitch) * e;
    p.roll += (f.roll - p.roll) * e;
    let busy = err > 0.01 || Math.abs(turn) > 0.3 || ((now - f.at) / 1000 < R3.AHEAD_S && Math.abs(f.speed) > 0.03);
    if (this.follow.on && this._followFrame(dt)) busy = true;
    this.map.triggerRepaint();
    if (busy) this.raf = requestAnimationFrame((ts) => this._tick(ts));
  }

  _robotLayer() {
    const { THREE, maplibregl: ml } = this.libs;
    const me = this;
    return {
      id: "gk-robot", type: "custom", renderingMode: "3d",
      onAdd(map, gl) {
        this.camera = new THREE.Camera();
        this.scene = new THREE.Scene();               // x east, y north, z up, in metres
        const sky = new THREE.HemisphereLight(0xffffff, 0x444444, 1.6);
        sky.position.set(0, 0, 1);
        this.scene.add(sky);
        const sun = new THREE.DirectionalLight(0xffffff, 1.2);
        sun.position.set(-1, 2, 3);                   // from about where the ground's shading is lit from
        this.scene.add(sun);
        const { robot, mower } = buildRobot(THREE);
        this.robot = robot;
        me.mower = mower;
        this.scene.add(robot);
        this.renderer = new THREE.WebGLRenderer({ canvas: map.getCanvas(), context: gl, antialias: true });
        this.renderer.autoClear = false;
      },
      onRemove() { this.renderer?.dispose(); },
      render(gl, matrix) {
        const p = me.pose, map = me.map;
        if (!p) return;
        const at = [p.lon, p.lat];
        const mc = ml.MercatorCoordinate.fromLngLat(at, map.queryTerrainElevation(at) || 0);
        const mpp = (40075016.686 * Math.cos(p.lat * DEG)) / (512 * 2 ** map.getZoom());
        const s = mc.meterInMercatorCoordinateUnits() * Math.max(1, (R3.MIN_PX * mpp) / R3.LEN);
        const x = map.getTerrain()?.exaggeration || 1;    // a taller ground: the tilt drawn taller to match
        const nose = Math.atan(x * Math.tan(-(p.pitch || 0) * DEG));
        const left = Math.atan(x * Math.tan((p.roll || 0) * DEG));
        this.robot.rotation.set(nose, left, -(p.heading || 0) * DEG, "ZXY");
        me._mowerOnGround(p, s / mc.meterInMercatorCoordinateUnits());
        const M = THREE.Matrix4;
        const place = new M().makeTranslation(mc.x, mc.y, mc.z).scale(new THREE.Vector3(s, -s, s));
        this.camera.projectionMatrix = new M().fromArray(matrix).multiply(place);
        this.renderer.resetState();
        this.renderer.render(this.scene, this.camera);
      },
    };
  }

  /* The mower lies on the ground under the hitch: where the slope under it differs from the slope under the tracks
   * it turns about the hitch by the difference (at most R3.MOWER_PITCH / MOWER_ROLL), eased. `k`: how much larger
   * than life the model is drawn. */
  _mowerOnGround(p, k) {
    const m = this.mower, map = this.map;
    if (!m) return;
    const h = (p.heading || 0) * DEG, [kx, ky] = metres(p.lat);
    const ground = (x, y) => map.queryTerrainElevation(
      [p.lon + (k * (x * Math.cos(h) + y * Math.sin(h))) / kx, p.lat + (k * (y * Math.cos(h) - x * Math.sin(h))) / ky]);
    const back = ground(0, -0.66), hitch = ground(0, 0.13), front = ground(0, 0.74);
    const cl = ground(-0.3, -0.27), cr = ground(0.3, -0.27), ml = ground(-0.35, 0.44), mr = ground(0.35, 0.44);
    let pitch = 0, roll = 0;
    if ([back, hitch, front, cl, cr, ml, mr].every((v) => typeof v === "number")) {
      const lim = (v, deg) => Math.max(-deg * DEG, Math.min(deg * DEG, v));
      pitch = lim(Math.atan2(front - hitch, 0.61 * k) - Math.atan2(hitch - back, 0.79 * k), R3.MOWER_PITCH);
      roll = lim(Math.atan2(ml - mr, 0.7 * k) - Math.atan2(cl - cr, 0.6 * k), R3.MOWER_ROLL);
    }
    m.rotation.x += (pitch - m.rotation.x) * 0.25;
    m.rotation.y += (roll - m.rotation.y) * 0.25;
  }

  /* Following: a drag lets go, a right-drag (two fingers) moves the camera round the robot and it keeps that angle,
   * and the camera holds still while a finger or button is on the map. */
  _gestures() {
    const mp = this.map, f = this.follow, box = mp.getCanvasContainer(), down = new Set();
    const free = () => { if (!f.hands) return; f.hands = false; if (f.on) this._kick(); };
    this._lift = (e) => { down.delete(e.pointerId); if (!down.size) free(); };
    this._blur = () => { down.clear(); free(); };
    box.addEventListener("pointerdown", (e) => { down.add(e.pointerId); f.hands = true; });
    box.addEventListener("wheel", () => { f.wheelAt = performance.now(); }, { passive: true });
    window.addEventListener("pointerup", this._lift);
    window.addEventListener("pointercancel", this._lift);
    window.addEventListener("blur", this._blur);
    mp.on("dragstart", (e) => { if (e.originalEvent && f.on) this.setFollow(false); });
    mp.on("rotatestart", (e) => { if (e.originalEvent) f.turning = true; });
    mp.on("zoomend", (e) => { if (e.originalEvent && f.on) this._kick(); });
    mp.on("rotateend", () => {
      if (!f.turning) return;
      f.turning = false;
      if (f.on && f.cam !== null) f.offset = angle(mp.getBearing() - f.cam);
    });
  }

  _ahead(p) {
    const [kx, ky] = metres(p.lat);
    return [p.lon + (Math.sin(p.heading * DEG) * R3.FOLLOW_LEAD) / kx,
      p.lat + (Math.cos(p.heading * DEG) * R3.FOLLOW_LEAD) / ky];
  }

  /* Follow on: fly behind the robot (and to one side), then stay with it. Off: back to the whole yard when `fit`. */
  setFollow(on, fit = false) {
    const f = this.follow;
    on = !!on && !!this.ready && !!this.pose;
    if (on !== f.on) {
      f.on = on;
      if (on) {
        const p = this.pose;
        f.cam = p.heading;
        f.offset = R3.FOLLOW_SIDE;
        this.map.easeTo({ center: this._ahead(p), bearing: p.heading + f.offset, zoom: Math.max(this.map.getZoom(), 21),
          pitch: 62, duration: 900 });
        setTimeout(() => this._kick(), 950);
      } else if (fit) {
        this.map.fitBounds(this.fit, { padding: 24, pitch: 55, bearing: 0, duration: 700 });
      }
    }
    this.onFollow(f.on);
    return f.on;
  }

  /* One frame of following: turn toward the robot's heading (at most R3.FOLLOW_TURN°/s) and keep it in view. True
   * while the camera is still turning. */
  _followFrame(dt) {
    const mp = this.map, f = this.follow, p = this.pose;
    if (!p || mp.isEasing() || f.hands || performance.now() - f.wheelAt < 400) return true;
    if (f.cam === null) f.cam = p.heading;
    const d = angle(p.heading - f.cam), cap = R3.FOLLOW_TURN * dt;
    f.cam += Math.max(-cap, Math.min(cap, d * (1 - Math.exp(-dt * 2.5))));
    mp.jumpTo(f.turning ? { center: this._ahead(p) } : { center: this._ahead(p), bearing: f.cam + f.offset });
    return Math.abs(d) > 0.5;
  }

  destroy() {
    this.dead = true;
    cancelAnimationFrame(this.raf);
    clearInterval(this.renewTimer);
    window.removeEventListener("pointerup", this._lift);
    window.removeEventListener("pointercancel", this._lift);
    window.removeEventListener("blur", this._blur);
    try { this.map.remove(); } catch (_) { /* already gone */ }
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
  .road { fill: none; stroke: ${C.road}; stroke-width: 0.9; stroke-linecap: round; stroke-linejoin: round;
    opacity: 0.95; }
  .road-mid { fill: none; stroke: #F2F5F3; stroke-width: 1; stroke-dasharray: 4 4; vector-effect: non-scaling-stroke; }
  .dock { fill: ${C.ink}; stroke: ${C.dock}; stroke-width: 2.5; vector-effect: non-scaling-stroke; }
  .track polyline { fill: none; stroke-width: 2; stroke-linecap: round; stroke-linejoin: round;
    vector-effect: non-scaling-stroke; opacity: 0.9; }
  .robot .halo { fill: ${C.robot}; fill-opacity: 0.25; }
  .robot .body { fill: ${C.robot}; stroke: #fff; stroke-width: 1.5; vector-effect: non-scaling-stroke; }
  .robot .nose { fill: #fff; }
  .gl { width: 100%; height: 100%; }
  .tools { position: absolute; right: 8px; bottom: 8px; display: flex; gap: 6px; align-items: center; }
  .tool { height: 36px; min-width: 36px; padding: 0 10px; border-radius: 18px; border: none; box-sizing: border-box;
    background: rgba(14, 19, 17, 0.75); color: #F2F5F3; cursor: pointer; display: flex; align-items: center;
    justify-content: center; font: inherit; font-size: 13px; font-weight: 600; }
  select.tool { padding: 0 6px; appearance: auto; }
  .tool.round { padding: 0; width: 36px; }
  .tool[aria-pressed="true"] { background: var(--primary-color, #03a9f4); }
  .camera { border-radius: 8px; overflow: hidden; }
  .camera hui-picture-entity-card, .camera > * { display: block; }
  .caption { font-size: 12px; color: var(--secondary-text-color); padding: 4px 2px 0; }
  .controls { padding: 4px 12px; }
  .ctl-row { display: flex; flex-wrap: wrap; gap: 6px; }
  .ctl { display: inline-flex; align-items: center; gap: 4px; padding: 6px 12px; border-radius: 18px; cursor: pointer;
    border: 1px solid var(--divider-color, #444); background: var(--card-background-color, transparent);
    color: var(--primary-text-color); font: inherit; font-size: 14px; }
  .ctl ha-icon { --mdc-icon-size: 18px; }
  .ctl:disabled { opacity: 0.5; cursor: default; }
  .ctl.go:not(:disabled) { background: var(--primary-color, #03a9f4); border-color: transparent;
    color: var(--text-primary-color, #fff); }
  .start { margin-top: 8px; padding: 10px; border-radius: 8px; border: 1px solid var(--divider-color, #444);
    display: grid; gap: 8px; font-size: 13px; }
  .start label { display: flex; align-items: center; gap: 8px; }
  .start select { flex: 1; min-width: 0; padding: 4px; font: inherit; color: var(--primary-text-color);
    background: var(--card-background-color, transparent); border: 1px solid var(--divider-color, #444);
    border-radius: 6px; }
  .verdict { color: var(--secondary-text-color); }
  .verdict.ok { color: var(--success-color, #8BD49A); }
  .checks { margin: 0; padding-left: 18px; color: var(--secondary-text-color); }
  .say { margin-top: 6px; font-size: 13px; color: var(--secondary-text-color); }
  .say.bad { color: var(--warning-color, #F0B955); }
`;

if (!customElements.get("groundskeeper-card")) {
  customElements.define("groundskeeper-card", GroundskeeperCard);
  window.customCards = window.customCards || [];
  window.customCards.push({
    type: "groundskeeper-card",
    name: "Groundskeeper",
    description: "Your Yarbo's status, the yard map with the robot on it, the camera that covers it, and (with "
      + "Commands on) start, pause and dock.",
    preview: true,
    documentationURL: "https://github.com/Groundskeeper-robot/groundskeeper-ha",
  });
  console.info(`%c GROUNDSKEEPER-CARD %c ${CARD_VERSION} `, "background:#2E7D4F;color:#fff", "");
}
