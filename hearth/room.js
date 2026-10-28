// The Team room: the house the helpers work in, drawn on a canvas. The desktop
// window and the phone app both load this file. Each page fills ROOM_HOST with
// what only it knows (what JARVIS is doing, the art, what a tap opens) and
// gives the elements by id: tm-stage holding the canvas tm-canvas, the overlay
// tm-labels for name tags and speech, and tm-rooms for the room switcher.
var ROOM_HOST = window.ROOM_HOST || {};
const _ROOM_HOST_DEFAULTS = {
  // The art: the manifest, then each picture by its file name.
  assets: null,
  name: () => 'JARVIS',
  // A small badge before a name for the brain someone runs on, as HTML.
  mini: () => '',
  // What JARVIS is doing in this view ('thinking…', 'calling read_file…', 'idle'),
  // and a turn running somewhere else ({state, surface}) or null.
  here: () => 'idle',
  elsewhere: () => null,
  mainRuntime: () => '',
  hasBrain: () => true,
  // Waiting on a yes, and the question it is waiting on, said as a person would.
  waitingOk: () => false,
  permLine: () => '',
  toolKind: name => roomToolKind(name),
  // The helpers as the page has them, and what a tap on someone opens.
  team: () => [],
  select: () => {},
  changed: () => {},
  look: () => 'her',
  home: () => 'cabin',
  wipe: () => 'pixel',
  reduceMotion: () => !!(window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches),
};
for (const [k, v] of Object.entries(_ROOM_HOST_DEFAULTS)) if (!(k in ROOM_HOST)) ROOM_HOST[k] = v;
window.ROOM_HOST = ROOM_HOST;
const _rEl = id => document.getElementById(id);
const _rEsc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
function _rMotionOff() { return typeof _reduceMotion === 'function' ? _reduceMotion() : ROOM_HOST.reduceMotion(); }
function _rEasing(period, damp) {
  return typeof springEasing === 'function' ? springEasing(period, damp) : { duration: Math.round(period * 1000), easing: 'cubic-bezier(.2,.8,.2,1)' };
}
// Where a spring with this period stands after t seconds, 0 to 1, without overshoot.
function _rSpring(t, period) {
  if (typeof _springStep === 'function') return _springStep(t, period, 1);
  const w = 2 * Math.PI / period;
  return 1 - (1 + w * t) * Math.exp(-w * t);
}

// The kind of thing a tool does, by its name, as the window's tool icons sort them.
function roomToolKind(name) {
  const n = String(name || '').toLowerCase(), has = (...ks) => ks.some(k => n.includes(k));
  if (n === 'delegate') return 'handoff';
  if (n === 'web_search') return 'web';
  if (has('plugin')) return 'plugin';
  if (has('subagent', 'launch_team', 'agent')) return 'users';
  if (has('list_dir', 'list_file', 'list_archive', 'extract_archive')) return 'folder';
  if (has('glob', 'find_file', 'grep', 'search')) return 'search';
  if (has('write_file', 'edit_file', 'create_file', 'append')) return 'edit';
  if (has('run_command', 'shell', 'exec')) return 'term';
  if (has('fetch', 'browse', 'open_url')) return 'globe';
  if (has('generate_video', 'check_video')) return 'video';
  if (has('image', 'screenshot')) return 'image';
  if (has('memory')) return 'brain';
  if (has('remind')) return 'bell';
  if (has('open_app')) return 'rocket';
  if (has('desktop')) return 'monitor';
  return 'file';
}

const TEAM_ROLES = ['coder', 'researcher', 'summarizer', 'librarian', 'archivist', 'coordinator', 'custom', 'main'];
const TEAM_ROLE_OF = { pdf_coordinator: 'coordinator' };

function teamRole(persona) {
  const p = (persona || '').toLowerCase();
  if (TEAM_ROLES.includes(p)) return p;
  return TEAM_ROLE_OF[p] || 'custom';
}
const READING_TOOLS = /^(web_search|web_fetch|read_file|grep_search|find_file|list_dir|summarize_file|search_chats|browse|memory_recall|read_pdf)/;
function teamNeedsYou(a) { return !!a.blocked && a.status === 'working'; }

// ------------------------------------------------------------ the room
// The room is built in layers: the empty room, then everything that stands in
// it (desks, people, chair backs, furniture) drawn back to front by where it
// touches the floor, each with a soft contact shadow. A worker sits in front
// of a desk with their back to the viewer and the chair drawn over them, and
// that desk's laptop opens while they are producing something.
// Coordinates are the room image's own pixels (1536 x 1024).
const ROOM = {
  W: 1536, H: 1024, raf: 0, last: 0, imgs: {}, bg: null, props: null, man: null,
  people: new Map(), sel: null, says: new Map(), hits: [], labelSig: '', intent: null,
  // The rooms, each the same floor plan in its own style, and the one in view.
  // JARVIS lives in the first; the next opens when every desk before it is taken.
  bgs: [], rooms: [], view: 0, order: [0], wipe: null,
  ind: null,
  // Desk anchors: the middle of each desk's front edge, on the floor.
  desks: [[330, 648], [610, 648], [900, 648], [1190, 648], [330, 886], [610, 886], [900, 886], [1190, 886]],
  spots: {
    door: [96, 905], fire: [760, 470], fireSide: [868, 478], hearthside: [640, 492], shelfL: [410, 398], shelfR: [1122, 398],
    board: [228, 480], windowSeat: [1316, 488], coffee: [1350, 650], windowL: [296, 430], rug: [700, 478],
    armchairSeat: [538, 468], hearth: [770, 398], pin: [178, 460],
    // On the floor in front of the armchair, leaning back against it.
    armchairFloor: [552, 466],
  },
  // How far up someone is drawn when seated there, so they sit on it and not in front of it.
  sitLift: { armchairSeat: 0 },
  fire: [770, 330], gone: new Set(), chatAt: 0,
  aisleY: 722, aisleTop: 492, aisleX: [186, 470, 755, 1045, 1352],
};
// SEAT_DY sets where a seated worker sorts (just in front of the desk);
// SIT_LIFT only raises where they are drawn, so head and shoulders clear the chair.
const SEAT_DY = 4, CHAIR_DY = 18, SIT_LIFT = 48;
// Everyone shares the drawn body for their role; hair and skin come from the
// person's id, so each helper looks like someone and keeps that look.
const HAIRS = [[0.07, 0.55, 0.13], [0.05, 0.45, 0.30], [0.02, 0.62, 0.34], [0.11, 0.55, 0.52],
               [0.08, 0.20, 0.62], [0.06, 0.35, 0.22], [0.98, 0.45, 0.34], [0.09, 0.08, 0.10]];
const SKINS = [0.86, 0.76, 0.66, 0.54, 0.44, 0.36];
function _rgb2hsl(r, g, b) {
  r /= 255; g /= 255; b /= 255;
  const mx = Math.max(r, g, b), mn = Math.min(r, g, b), l = (mx + mn) / 2;
  if (mx === mn) return [0, 0, l];
  const d = mx - mn, s = l > 0.5 ? d / (2 - mx - mn) : d / (mx + mn);
  let h = mx === r ? (g - b) / d + (g < b ? 6 : 0) : mx === g ? (b - r) / d + 2 : (r - g) / d + 4;
  return [h / 6, s, l];
}
function _hsl2rgb(h, s, l) {
  if (!s) return [l * 255, l * 255, l * 255];
  const q = l < 0.5 ? l * (1 + s) : l + s - l * s, p = 2 * l - q;
  const f = t => { t = (t + 1) % 1; return t < 1 / 6 ? p + (q - p) * 6 * t : t < 1 / 2 ? q : t < 2 / 3 ? p + (q - p) * (2 / 3 - t) * 6 : p; };
  return [f(h + 1 / 3) * 255, f(h) * 255, f(h - 1 / 3) * 255];
}
const _looks = new Map();
function roomLook(role, seed) {
  const key = role + ':' + seed;
  if (_looks.has(key)) return _looks.get(key);
  const src = ROOM.imgs[role] || ROOM.imgs.custom;
  const c = document.createElement('canvas');
  c.width = src.width; c.height = src.height;
  const x = c.getContext('2d');
  x.drawImage(src, 0, 0);
  if (!role.startsWith('main')) {
    const [hh, hs, hl] = HAIRS[seed % HAIRS.length];
    const skinL = SKINS[(seed >> 3) % SKINS.length];
    const [cw, ch] = ROOM.man.cell;
    const im = x.getImageData(0, 0, c.width, c.height), d = im.data;
    for (let py = 0; py < c.height; py++) {
      for (let px = 0; px < c.width; px++) {
        const i = (py * c.width + px) * 4;
        if (d[i + 3] < 10) continue;
        const [h, sa, l] = _rgb2hsl(d[i], d[i + 1], d[i + 2]);
        const yInCell = py % ch;
        let out = null;
        if (h > 0.03 && h < 0.13 && sa > 0.25 && l > 0.52 && l < 0.9) {
          // skin: keep the shading, move the tone
          out = _hsl2rgb(0.07, Math.min(0.62, sa), Math.max(0.12, Math.min(0.93, l * skinL / 0.74)));
        } else if (yInCell < ch * 0.42 && h > 0.02 && h < 0.14 && l < 0.46 && sa > 0.12) {
          // hair: the dark browns in the top of the frame
          out = _hsl2rgb(hh, hs, Math.max(0.05, Math.min(0.85, hl * (l / 0.26))));
        }
        if (out) { d[i] = out[0]; d[i + 1] = out[1]; d[i + 2] = out[2]; }
      }
    }
    x.putImageData(im, 0, 0);
  }
  _looks.set(key, c);
  return c;
}
// A character that wasn't drawn in a pose stands in the nearest one it has.
const FRAME_NEAREST = { sit: 'idle', sit_read: 'read', sit_coffee: 'coffee', talk: 'idle', nod: 'idle', wave: 'raise',
  side_idle: 'walk_side_1', side_coffee: 'coffee', side_read: 'read', tend_fire: 'gaze', sleep: 'gaze', pin: 'raise' };
function roomHas(skin, name) { return !!ROOM.man && ((ROOM.man.extra || {})[skin] || []).includes(name); }
function roomFrame(name, skin) {
  if (!ROOM.man) return 0;
  const base = ROOM.man.frames.indexOf(name);
  if (base >= 0) return base;
  const more = ((ROOM.man.extra || {})[skin] || []).indexOf(name);
  if (more >= 0) return ROOM.man.frames.length + more;
  return Math.max(0, ROOM.man.frames.indexOf(FRAME_NEAREST[name] || 'idle'));
}

// The assistant is drawn as whichever of the two characters was picked.
function roomSkin(a) {
  if (a.role !== 'main') return a.role;
  const look = ROOM_HOST.look() || 'her';
  return look === 'him' && ROOM.imgs.main_m ? 'main_m' : 'main';
}
// The art from a web address: the window's own server, /assets/team/.
function roomAssetsAt(base) {
  return {
    async manifest() {
      // A request to Hearth's own server can stall or be reset on its way (some
      // antivirus web shields sit on local traffic), so each piece is asked for
      // again after a few seconds instead of leaving the room empty for good.
      let man = null;
      for (let n = 0; n < 4 && !man; n++) {
        const stop = new AbortController(), t = setTimeout(() => stop.abort(), 6000);
        try { man = await (await fetch(base + 'manifest.json', { cache: 'no-store', signal: stop.signal })).json(); } catch {}
        clearTimeout(t);
      }
      return man;
    },
    async image(file, man) {
      const v = man.v ? '?v=' + man.v : '?';
      for (let n = 0; ; n++) {
        try { return await roomImage(base + file + v + (n ? (man.v ? '&' : '') + 'try=' + n : '')); }
        catch (e) { if (n >= 3) throw e; }
      }
    },
  };
}
// One picture, given up on after a few seconds.
function roomImage(src) {
  return new Promise((res, rej) => {
    const i = new Image(), t = setTimeout(() => { i.src = ''; rej(new Error('slow')); }, 6000);
    i.onload = () => { clearTimeout(t); res(i); };
    i.onerror = () => { clearTimeout(t); rej(new Error('failed')); };
    i.src = src;
  });
}
async function roomLoad() {
  if (ROOM.bg) return true;
  try {
    const src = ROOM_HOST.assets || roomAssetsAt('/assets/team/');
    const man = await src.manifest();
    if (!man) return false;
    ROOM.man = man;
    const load = file => src.image(file, man);
    // Everything is loaded before any of it is used: someone placed while the
    // rooms were half in would land in a room that isn't there yet.
    const rooms = man.rooms && man.rooms.length ? man.rooms : [{ id: 'cabin', name: 'Cabin', file: 'room.png' }];
    const bgs = [];
    for (const r of rooms) bgs.push(await load(r.file));
    if (man.props) ROOM.props = await load('props.png');
    for (const r of man.roles) ROOM.imgs[r] = await load(`${r}.png`);
    ROOM.bgs = bgs;
    ROOM.rooms = rooms;
    roomOrder();
    ROOM.bg = ROOM.bgs[ROOM.order[0]];
    return true;
  } catch { return false; }
}
// The first room is the one picked as home (JARVIS lives there); the others
// follow in their usual order. A room's number is its place in that order.
function roomHomeId() { return ROOM_HOST.home() || 'cabin'; }
function roomOrder() {
  const all = ROOM.rooms.map((_, i) => i), home = ROOM.rooms.findIndex(r => r.id === roomHomeId());
  ROOM.order = home > 0 ? [home, ...all.filter(i => i !== home)] : all;
}
function roomStyle(r) { return ROOM.order[r] != null ? ROOM.order[r] : 0; }
function roomWipeOn() {
  if (_rMotionOff()) return false;
  return ROOM_HOST.wipe() !== 'instant';
}

// A free desk: in the given room, or the first room that has one.
function roomDeskFor(id, only) {
  const used = new Set([...ROOM.people.values()].filter(p => p.id !== id && p.desk != null).map(p => (p.room || 0) + ':' + p.desk));
  const rooms = only != null ? [only] : ROOM.rooms.map((_, r) => r);
  for (const r of rooms) for (let i = 0; i < ROOM.desks.length; i++) if (!used.has(r + ':' + i)) return { room: r, desk: i };
  return null;
}
function roomRoute(from, to) {
  // A desk is reached and left from the open floor in front of it: walking
  // along its row of chairs to sit down went straight through the desks.
  const front = q => {
    const d = ROOM.desks.find(d => Math.abs(q[0] - d[0]) < DESK_HALF && q[1] > d[1] - DESK_BACK && q[1] < d[1] + DESK_FRONT);
    return d ? [q[0], d[1] + 70] : null;
  };
  const a = front(from), b = front(to);
  if (!a && !b) return roomRouteOpen(from, to);
  return [...(a ? [a] : []), ...roomRouteOpen(a || from, b || to), ...(b ? [to] : [])];
}
function roomRouteOpen(from, to) {
  // Along the aisles, so nobody walks through a desk.
  const near = x => ROOM.aisleX.reduce((b, v) => Math.abs(v - x) < Math.abs(b - x) ? v : b, ROOM.aisleX[0]);
  const top = y => y < 560;
  if (top(from[1]) && top(to[1])) return [[from[0], ROOM.aisleTop], [to[0], ROOM.aisleTop], to];
  // Up by the fire the aisles stop at the open strip in front of it: further
  // up, the one past the armchair runs along its arm and over the books.
  const pts = top(from[1]) ? [[from[0], ROOM.aisleTop], [near(from[0]), ROOM.aisleTop]] : [[near(from[0]), from[1]]];
  if (top(from[1]) !== top(to[1]) || Math.abs(from[1] - to[1]) > 40) {
    const y = ROOM.aisleY;
    pts.push([near(from[0]), y], [near(to[0]), y]);
  }
  if (top(to[1])) pts.push([near(to[0]), ROOM.aisleTop], [to[0], ROOM.aisleTop], to);
  else pts.push([near(to[0]), to[1]], to);
  return pts;
}
// The floor the furniture by the fire stands on, as boxes of where feet can't
// go (a body is about BODY_HALF either side of its feet).
const BODY_HALF = 14;
function roomBlocks() {
  if (!ROOM.man || !ROOM.man.room_empty) return [];
  const out = [{ name: 'armchair', x0: 472, x1: 608, y0: 395, y1: 442 }, { name: 'side_table', x0: 622, x1: 678, y0: 410, y1: 430 }];
  if (roomProp('cat_sleep')) out.push({ name: 'books', x0: 684, x1: 720, y0: 432, y1: 444 }, { name: 'stool', x0: 1217, x1: 1259, y0: 404, y1: 420 });
  return out;
}
// Nobody stands inside a desk or its chair. A place that falls on one (beside
// someone standing near the coffee, one step aside from another person) moves
// out to the nearest aisle at the same depth.
const DESK_HALF = 122, DESK_BACK = 98, DESK_FRONT = 30;
function roomFree(pt) {
  const deskAt = q => ROOM.desks.find(d => Math.abs(q[0] - d[0]) < DESK_HALF && q[1] > d[1] - DESK_BACK && q[1] < d[1] + DESK_FRONT);
  const inDesk = q => !!deskAt(q);
  const d = deskAt(pt);
  if (!d) {
    const b = roomBlocks().find(b => pt[0] + BODY_HALF > b.x0 && pt[0] - BODY_HALF < b.x1 && pt[1] > b.y0 && pt[1] < b.y1);
    return b ? [pt[0], b.y1 + 12] : pt;
  }
  // On the front half of a desk: just in front of it, where it was meant to be.
  if (pt[1] > d[1] - 40 && !inDesk([pt[0], d[1] + 70])) return [pt[0], d[1] + 70];
  const xs = [...ROOM.aisleX].sort((a, b) => Math.abs(a - pt[0]) - Math.abs(b - pt[0]));
  for (const x of xs) if (!inDesk([x, pt[1]])) return [x, pt[1]];
  return [pt[0], ROOM.aisleY];
}
// The free desk closest to where JARVIS stands, in the room it is in.
function roomNearestDesk(p) {
  const r = p.room || 0;
  const used = new Set([...ROOM.people.values()].filter(q => q.id !== p.id && q.desk != null && (q.room || 0) === r).map(q => q.desk));
  let best = null, bd = Infinity;
  ROOM.desks.forEach((d, i) => {
    if (used.has(i)) return;
    const dd = Math.hypot(d[0] - p.pos[0], d[1] + SEAT_DY - p.pos[1]);
    if (dd < bd) { bd = dd; best = i; }
  });
  return best;
}
function roomSeat(p) {
  const d = ROOM.desks[p.desk];
  return [d[0], d[1] + SEAT_DY];
}
// What JARVIS is doing picks the place: reading at a shelf, writing at a free
// desk, pinning a note to the board, pacing while it thinks.
const MAIN_IDLE = [['fire', 'gaze'], ['fire', 'think'], ['windowSeat', 'side_idle', 'f'], ['windowL', 'side_idle'], ['coffee', 'coffee'],
                   ['shelfR', 'read'], ['shelfL', 'read'], ['hearthside', 'gaze'], ['hearthside', 'coffee'], ['rug', 'stretch'],
                   ['hearthside', 'think'], ['board', 'think']];
const LEISURE = [['hearthside', 'gaze'], ['windowSeat', 'gaze'], ['coffee', 'coffee'], ['shelfL', 'read'], ['windowL', 'gaze'],
                 ['shelfR', 'read'], ['fireSide', 'coffee'], ['rug', 'stretch'], ['coffee', 'idle'], ['hearthside', 'coffee'], ['fireSide', 'idle']];
// The third entry: 's' is seated on that piece of furniture, 'f' is turned the other way.
const MAIN_IDLE_MORE = [['armchairSeat', 'sit', 's'], ['hearth', 'tend_fire'], ['windowSeat', 'side_coffee', 'f'],
                        ['shelfL', 'side_read'], ['armchairSeat', 'sit_read', 's'], ['coffee', 'side_coffee', 'f'],
                        ['shelfR', 'side_read', 'f'], ['armchairSeat', 'sit_coffee', 's']];
const MAIN_IDLE_WORDS = { fire: 'by the fire', windowSeat: 'at the window', coffee: 'making coffee', shelfR: 'browsing the shelves', hearthside: 'by the hearth',
                         shelfL: 'browsing the shelves', rug: 'stretching', windowL: 'at the window', board: 'at the board',
                         armchairSeat: 'in the armchair', hearth: 'tending the fire' };
// One entry of a list for this stretch of time. Stepping by 7 walks through a
// whole list before repeating (none here is a multiple of 7 long); the same
// place twice running is skipped.
function roomPick(list, seed, period) {
  const at = k => list[(k * 7 + seed) % list.length];
  const k = Math.floor(Date.now() / period);
  return at(k)[0] === at(k - 1)[0] ? at(k + 3) : at(k);
}
function roomMainIdle(p) {
  const skin = roomSkin(p.agent);
  // Only what this character was drawn doing.
  return roomPick(MAIN_IDLE.concat(MAIN_IDLE_MORE.filter(([, pose]) => roomHas(skin, pose))), p.seed, 45000);
}
function roomMainIdleWord(p) {
  const w = roomMainIdleSpot(p);
  return w === 'on the way' || w === 'right here' ? w : 'Free · ' + w;
}
// Where it is and what it does while nothing is asked of it. Said as leisure:
// "reading" alone read as if it were reading something for you.
function roomMainIdleSpot(p) {
  // Where it is, not where it is headed: "making coffee" is for the counter.
  if (p.path && p.path.length) return 'on the way';
  if (p.holdUntil > Date.now()) return 'right here';
  if (ROOM.intent && ROOM.intent.until > Date.now()) return MAIN_IDLE_WORDS[ROOM.intent.spot] || 'around';
  const [spot, pose] = roomMainIdle(p);
  if (pose === 'sit_read') return 'a book in the armchair';
  return MAIN_IDLE_WORDS[spot] + (/coffee/.test(pose) && spot !== 'coffee' ? ', with a coffee' : '');
}
// Working on a turn. Loading or ejecting a model, "ready" and the like are
// the app's business, not JARVIS thinking.
// What JARVIS is doing: here, or else in the terminal when a turn runs there.
const _BUSY = /^(thinking|calling|writing|processing|compacting|transcribing)/;
function roomMainState() {
  const here = String(ROOM_HOST.here() || 'idle'), away = ROOM_HOST.elsewhere();
  if (_BUSY.test(here) || !away) return here;
  return String(away.state || 'idle');
}
function roomMainElsewhere() { return !_BUSY.test(String(ROOM_HOST.here() || '')) && !!ROOM_HOST.elsewhere(); }
function roomMainBusy() {
  return _BUSY.test(roomMainState());
}
function roomHasBrain() {
  return !!ROOM_HOST.hasBrain();
}
function roomMainGoal(p) {
  const ms = roomMainState();
  const busy = roomMainBusy();
  const now = Date.now();
  if (busy && !p.wasBusy) { p.turn = (p.turn || 0) + 1; p.desk = null; p.pace = null; p.deskUntil = 0; }
  // The answer is given where it was worked out. It doesn't walk back to a
  // usual spot to speak; it stays put a while, then drifts off as before.
  if (!busy && p.wasBusy) { p.holdUntil = now + 9000; p.holdAt = [p.pos[0], p.pos[1]]; }
  p.wasBusy = busy;
  if (p.pokeUntil > now) return { at: p.pos, pose: p.pokePose || 'raise', sit: !!p.pokeSit, lift: p.pokeSit || 0 };
  // Waiting on a yes: a hand up where it is. Walking to the rug and back for
  // every command it asked about kept it crossing the room all turn.
  if (ROOM_HOST.waitingOk()) {
    if (p.desk != null && p.deskUntil > now) return { at: roomSeat(p), pose: 'type', seated: true };
    return { at: p.pos, pose: 'raise' };
  }
  // A fact just remembered in the background is pinned up before anything else.
  if (!busy && ROOM.pinUntil > now)
    return roomHas(roomSkin(p.agent), 'pin') ? { at: ROOM.spots.pin, pose: 'pin', flip: true } : { at: ROOM.spots.board, pose: 'raise' };
  if (ROOM.intent && ROOM.intent.until > now) return { at: ROOM.spots[ROOM.intent.spot], pose: ROOM.intent.pose };
  if (!busy) {
    if (p.holdUntil > now) return { at: p.holdAt || p.pos, pose: 'idle' };
    // Nothing to think with: asleep in the armchair until a model is loaded.
    if (!roomHasBrain() && roomHas(roomSkin(p.agent), 'sleep'))
      return { at: ROOM.spots.armchairFloor, pose: 'sleep', sit: true, lift: 0 };
    const [spot, pose, how] = roomMainIdle(p);
    return { at: ROOM.spots[spot], pose, sit: how === 's', flip: how === 'f', lift: how === 's' ? ROOM.sitLift[spot] || 0 : 0 };
  }
  const tool = (ms.match(/calling ([a-z_]+)/) || [])[1] || '';
  // Something worth remembering gets pinned to the board.
  if (/^memory_(save|update|write)|^remember/.test(tool)) {
    return roomHas(roomSkin(p.agent), 'pin') ? { at: ROOM.spots.pin, pose: 'pin', flip: true } : { at: ROOM.spots.board, pose: 'raise' };
  }
  if (/read|grep|find|list|search|fetch|browse|recall|pdf/.test(tool)) {
    const right = p.turn % 2 === 1, side = roomHas(roomSkin(p.agent), 'side_read') && Math.floor(now / 4200) % 2 === 0;
    return { at: right ? ROOM.spots.shelfR : ROOM.spots.shelfL, pose: side ? 'side_read' : 'read', flip: side && right };
  }
  // Writing is done at a desk. The tool itself is over in a moment, long
  // before anyone could walk there, so the desk is kept for the rest of this
  // stretch of work and it actually sits down and types.
  if (/write|edit|create|run_command|start_job|delegate/.test(tool)) p.deskUntil = now + 20000;
  if (busy && p.deskUntil > now && !/read|grep|find|list|search|fetch|browse|recall|pdf|memory_/.test(tool)) {
    if (p.desk == null) p.desk = roomNearestDesk(p);
    if (p.desk != null) return { at: roomSeat(p), pose: 'type', seated: true };
  }
  // Thinking is done on its feet: back and forth along the nearest clear
  // line from where it already was, a few steps each way, turning at the ends.
  if (!p.pace) {
    const y = Math.abs(p.pos[1] - ROOM.aisleTop) < Math.abs(p.pos[1] - ROOM.aisleY) ? ROOM.aisleTop : ROOM.aisleY;
    p.pace = { x: Math.max(340, Math.min(1180, p.pos[0])), y, t: now, span: 90 + (p.turn * 37 + p.seed) % 60 };
  }
  const leg = Math.floor((now - p.pace.t) / 3400) % 2;
  return { at: [p.pace.x + (leg ? p.pace.span : -p.pace.span), p.pace.y], pose: 'think' };
}

const ROOM_STAY = 150000;
// Walking pace in room pixels a second, and the length of one beat of the
// six-beat stride; together they keep the feet from sliding on the floor.
const WALK_SPEED = 100, WALK_BEAT = 125;
// Six beats a stride: each step is held twice as long as the moment the legs
// pass, which is drawn with the back foot well up; shown as long as the steps,
// the walk read as skipping. Facing or away, each step is held for half.
function roomWalkStep(dir, t, seed = 0) {
  const beat = Math.floor(t / WALK_BEAT + seed * 6) % 6;
  const side = dir === 'side' && ROOM.man && ROOM.man.frames.includes('walk_side_3');
  return side ? [1, 1, 3, 2, 2, 4][beat] : (beat < 3 ? 1 : 2);
}
function roomGoal(p, a) {
  // Where the person should be and what they are doing, from real state.
  if (a.role === 'main') return roomMainGoal(p);
  if (p.pokeUntil > Date.now()) return { at: p.pos, pose: p.pokePose || 'raise', seated: !!p.pokeSeated };
  // Gone over to tell another helper something, beside whoever it is, on the near side.
  const to = p.visit && p.visit.until > Date.now() && ROOM.people.get(p.visit.id);
  if (to && (to.room || 0) === (p.room || 0)) return { at: [to.pos[0] + (p.pos[0] < to.pos[0] ? -70 : 70), to.pos[1] + 8], pose: 'talk' };
  const seat = p.desk != null ? roomSeat(p) : ROOM.spots.rug;
  const beside = [seat[0] + 92, seat[1] + 6];
  // Beside JARVIS, where a helper goes to report or to ask.
  const main = ROOM.people.get('__main');
  // Beside JARVIS on the open side: next to a wall or the coffee counter only
  // one side is floor.
  const side = !main ? 1 : main.pos[0] > 1200 ? -1 : main.pos[0] < 320 ? 1 : (p.seed % 2 ? 1 : -1);
  // JARVIS is in the first room; in another, a helper says it from its desk.
  const byMain = (p.room || 0) > 0 ? beside : main ? [main.pos[0] + side * 76, main.pos[1] + 18] : ROOM.spots.rug;
  if (a.status === 'working') {
    // Needing an OK is a reason to go and ask, not to wait at the desk.
    if (a.blocked) return { at: byMain, pose: 'raise' };
    if (a.activity === 'tool' && READING_TOOLS.test(a.tool || '')) {
      return { at: (p.desk || 0) % 2 ? ROOM.spots.shelfR : ROOM.spots.shelfL, pose: 'read', spread: true };
    }
    return { at: seat, pose: 'type', seated: true };
  }
  const since = Date.now() - (p.doneAt || Date.now());
  // One that didn't finish waits by its desk with a hand up, then goes home.
  if (a.status === 'failed') return since > ROOM_STAY ? { at: ROOM.spots.door, pose: 'idle', leave: true } : { at: beside, pose: 'raise' };
  if (a.status === 'stopped') return since > 20000 ? { at: ROOM.spots.door, pose: 'idle', leave: true } : { at: beside, pose: 'idle' };
  if (since < 2600) return { at: p.pos, pose: 'stretch' };
  // A finished helper reports to JARVIS before it does anything else.
  if (p.reportedAt && Date.now() - p.reportedAt < 5200) return { at: p.pos, pose: 'idle' };
  if (since < 12000 && !p.reported) return { at: byMain, pose: 'idle', report: true };
  // The job is done and reported: a while by the fire or the coffee, then home.
  if (since > ROOM_STAY) return { at: ROOM.spots.door, pose: 'idle', leave: true };
  // Everyone who is done takes their break in the same place, so they meet,
  // and stays there until it is time to go instead of wandering from spot to spot.
  if (!p.rest) p.rest = roomPick(LEISURE, 0, 40000);
  const [spot, pose] = p.rest;
  return { at: ROOM.spots[spot], pose, spread: true };
}
function roomSync(list) {
  // Until the art is in there are no rooms to put anyone in; roomStart syncs once it is.
  if (!ROOM.rooms.length) return;
  // Someone who finished a while ago has gone home; they are still in the list.
  const old = a => a.status !== 'working' && !ROOM.people.has(a.id)
    && Date.now() / 1000 - (a.updated || 0) > ROOM_STAY / 1000;
  const want = list.filter(a => ['working', 'done', 'failed', 'stopped'].includes(a.status)
    && !ROOM.gone.has(a.id) && !old(a)).slice(0, ROOM.desks.length * Math.max(1, ROOM.rooms.length));
  want.push({ id: '__main', role: 'main', persona: 'main', name: ROOM_HOST.name() || 'JARVIS',
              status: 'working', runtime: ROOM_HOST.mainRuntime() });
  const ids = new Set(want.map(a => a.id));
  for (const [id, p] of ROOM.people) if (!ids.has(id) && !p.leaving) { p.leaving = true; p.path = roomRoute(p.pos, ROOM.spots.door); }
  for (const a0 of want) {
    const a = { ...a0, role: a0.role || teamRole(a0.persona) };
    let p = ROOM.people.get(a.id);
    if (!p) {
      p = { id: a.id, pos: a.role === 'main' ? [...ROOM.spots.fire] : [...ROOM.spots.door], path: [], pose: 'idle',
            seed: roomSeedOf(a.id), desk: null, dir: 1 };
      if (a.role !== 'main') {
        const at = roomDeskFor(a.id);
        if (at) { p.room = at.room; p.desk = at.desk; } else p.room = ROOM.rooms.length - 1;
        const queued = [...ROOM.people.values()].filter(q => q.enterAt && q.enterAt > performance.now()).length;
        p.enterAt = performance.now() + queued * 900;
      }
      ROOM.people.set(a.id, p);
    }
    if (a.status !== 'working' && p.agent && p.agent.status === 'working') p.doneAt = Date.now();
    if (a.status === 'working') ROOM.gone.delete(a.id);
    if (a.status !== 'working' && !p.doneAt) {
      p.doneAt = Math.min(Date.now() - 5000, (a.updated || Infinity) * 1000);
      // Finished before the room was open: nothing new to report.
      p.reported = Date.now() / 1000 - (a.updated || 0) > 60;
    }
    // A word from another helper, once it has been said: it answers.
    const h = a.heard;
    if (h && h.ts && p.heardSeen !== h.ts) {
      const fresh = p.heardSeen !== undefined || Date.now() - Date.parse(h.ts) < 15000;
      p.heardSeen = h.ts;
      if (fresh) setTimeout(() => { if (ROOM.people.has(a.id)) roomSay(a.id, `Got it, ${h.from}.`, 2600); }, 2600);
    }
    // A message it sent to another helper: it goes over and says it.
    const t = a.talk;
    if (t && t.ts && p.talkSeen !== t.ts) {
      const fresh = p.talkSeen !== undefined || Date.now() - Date.parse(t.ts) < 15000;
      p.talkSeen = t.ts;
      if (fresh) {
        p.visit = { id: t.to_id, until: Date.now() + 9000 };
        roomSay(a.id, `${t.to}, ${t.text}`, 7000);
      }
    }
    p.agent = a;
  }
  ROOM_HOST.changed();
}
// What JARVIS is doing right now, as it would say it: the question it is
// waiting on, the kind of tool in its hands, or that it is thinking.
const ROOM_DOING = { folder: 'Looking in a folder', search: 'Searching', file: 'Reading a file', edit: 'Changing a file',
  term: 'Running a command', globe: 'Checking the web', web: 'Searching the web', image: 'Looking at an image',
  video: 'Working on a video', brain: 'Checking my memory', bell: 'Setting a reminder', rocket: 'Opening an app',
  monitor: 'Using the desktop', users: 'Asking a helper', handoff: 'Handing off a task', plugin: 'Using a plugin' };
function roomMainLine() {
  const ask = ROOM_HOST.permLine();
  if (ask) return ask;
  const ms = roomMainState();
  if (/^loading/.test(ms)) return 'Waking up…';
  if (!roomMainBusy()) return '';
  const m = /^(?:calling|writing)\s+(\w+)/.exec(ms);
  if (m && /^memory_(save|update|write)|^remember/.test(m[1])) return 'Noting that down…';
  const line = (m ? (ROOM_DOING[ROOM_HOST.toolKind(m[1])] || 'Working') : 'Thinking') + '…';
  if (!roomMainElsewhere()) return line;
  const where = (ROOM_HOST.elsewhere() || {}).surface;
  return line + (where && where !== 'terminal' ? ' (on the PC)' : ' (in the terminal)');
}
// What a working helper has in its hands, in a few words: the tool and what it
// is pointed at.
const ROOM_VERBS = { folder: 'Looking in', search: 'Searching for', file: 'Reading', edit: 'Changing', term: 'Running',
  globe: 'Opening', web: 'Looking up', brain: 'Recalling' };
function roomHelperLine(a) {
  if (a.status !== 'working' || a.blocked) return '';
  if (a.activity !== 'tool') return 'Thinking…';
  const kind = ROOM_HOST.toolKind(a.tool || '');
  if (a.what && ROOM_VERBS[kind]) return ROOM_VERBS[kind] + ' ' + a.what;
  return (ROOM_DOING[kind] || 'Working') + '…';
}
// Two who have finished and ended up at the same place: one asks how it went,
// the other says what it found. Their own results, once per pair.
function roomSmallTalk() {
  const now = Date.now();
  if (now < ROOM.chatAt) return;
  ROOM.chatAt = now + 5000;
  const free = [...ROOM.people.values()].filter(p => !p.waiting && !p.walking && !p.leaving && p.agent
    && p.agent.role !== 'main' && p.agent.status === 'done' && p.reported && !((ROOM.says.get(p.id) || {}).until > now));
  for (const a of free) for (const b of free) {
    if (a === b || !b.agent.preview || (a.talked || (a.talked = new Set())).has(b.id)) continue;
    if ((a.room || 0) !== (b.room || 0) || Math.hypot(a.pos[0] - b.pos[0], a.pos[1] - b.pos[1]) > 150) continue;
    a.talked.add(b.id); (b.talked || (b.talked = new Set())).add(a.id);
    const prev = String(b.agent.preview).replace(/\s+/g, ' ').trim();
    roomSay(a.id, `How did yours go, ${b.agent.name || 'you'}?`, 3200);
    setTimeout(() => { if (ROOM.people.has(b.id)) roomSay(b.id, prev.length > 90 ? prev.slice(0, 89) + '…' : prev, 5200); }, 2200);
    ROOM.chatAt = now + 16000;
    return;
  }
}
function roomTick(t) {
  const dt = Math.min(0.1, (t - (ROOM.last || t)) / 1000);
  ROOM.last = t;
  // The live line replaces only itself: an answer JARVIS just gave stays up
  // for its time. Unchanged text is kept up, not posted again.
  const line = roomMainLine(), mainSay = ROOM.says.get('__main');
  if (line && (!mainSay || mainSay.live || mainSay.until < Date.now())) {
    if (mainSay && mainSay.live && mainSay.text === line) mainSay.until = Date.now() + 1500;
    else roomSay('__main', line, 1500, true);
  }
  roomSmallTalk();
  const claimed = [];
  // JARVIS first, so it keeps its place and the helpers stand around it.
  const everyone = [...ROOM.people].sort((a, b) => (b[0] === '__main') - (a[0] === '__main'));
  for (const [id, p] of everyone) {
    if (p.enterAt && t < p.enterAt) { p.waiting = true; continue; }
    p.waiting = false;
    if (!p.leaving) {
      const g = roomGoal(p, p.agent);
      let target = g.at;
      if (g.spread) {
        // Several people at one place stand apart, and away from the wall it is on.
        let dx = (p.seed % 5 - 2) * 26;
        if (g.at[0] > 1200) dx = -Math.abs(dx) - 14; else if (g.at[0] < 320) dx = Math.abs(dx) + 14;
        target = [g.at[0] + dx, g.at[1] + (p.seed % 3 - 1) * 10];
      }
      // Nobody stands in a desk or in someone else's place: the later one
      // steps aside, away from the wall, and never into a desk.
      if (!g.seated) target = roomFree(target);
      if (!g.seated && !g.leave && g.pose !== 'think') {
        const side = target[0] > 1200 ? -1 : 1;
        const here = p.room || 0;
        for (let n = 0; n < 6 && claimed.some(c => c[2] === here && Math.abs(c[0] - target[0]) < 52 && Math.abs(c[1] - target[1]) < 28); n++)
          target = roomFree([target[0] + side * 56, target[1]]);
        claimed.push([target[0], target[1], here]);
      }
      // A new place has to hold for a moment before anyone heads for it: two
      // people settling who stands where flicked a target back and forth, and
      // following each flick sent them turning on the spot.
      const near = (u, v) => u && Math.hypot(u[0] - v[0], u[1] - v[1]) < 28;
      if (!p.want || g.seated || g.leave) p.want = target;
      else if (!near(p.want, target)) {
        if (!near(p.cand, target)) { p.cand = target; p.candAt = t; }
        if (t - p.candAt > 450) { p.want = target; p.cand = null; }
      } else p.cand = null;
      target = p.want;
      // A new route only for a real change of place, and not every frame: a
      // target that drifts a little (someone it stands beside moved) re-planned
      // each tick sent people turning on the spot.
      const endPt = p.path.length ? p.path[p.path.length - 1] : p.pos;
      const off = Math.hypot(endPt[0] - target[0], endPt[1] - target[1]);
      if (off > (p.path.length ? 28 : 4) && (!p.path.length || !(t - (p.routedAt || 0) < 700))) {
        p.path = roomRoute(p.pos, target);
        p.routedAt = t;
      }
      // Whoever is speaking faces the room, not the wall.
      const say = ROOM.says.get(id);
      if (say && !say.live && say.until > Date.now() && !g.sit && !g.seated) {
        if (p.agent.role === 'main' && /^(gaze|idle|think|stretch|side_idle|tend_fire)$/.test(g.pose)) {
          g.pose = /^Noted/.test(say.text) ? 'nod' : 'talk';
          g.flip = false;
        } else if (g.pose === 'gaze') g.pose = 'idle';
      }
      p.goal = g;
      const doing = p.agent.role === 'main' ? '' : roomHelperLine(p.agent);
      if (doing && !p.path.length && (!say || say.live || say.until < Date.now())) {
        if (say && say.live && say.text === doing) say.until = Date.now() + 1500;
        else roomSay(id, doing, 1500, true);
      }
    }
    // Arrived beside JARVIS with a finished job: say so, once.
    if (!p.path.length && p.goal && p.goal.report && !p.reported) {
      p.reported = true;
      p.reportedAt = Date.now();                       // stays a moment, facing JARVIS
      const m = ROOM.people.get('__main');
      if (m) p.dir = m.pos[0] < p.pos[0] ? -1 : 1;
      const prev = String(p.agent.preview || '').replace(/\s+/g, ' ').trim();
      roomSay(p.id, prev ? 'Done. ' + (prev.length > 90 ? prev.slice(0, 89) + '…' : prev) : 'All done.', 4500);
      const who = p.agent.name || 'you';
      setTimeout(() => {
        const ms = ROOM.says.get('__main');
        if (ROOM.people.has(p.id) && !(ms && !ms.live && ms.until > Date.now()))
          roomSay('__main', p.agent.status === 'done' ? `Thanks, ${who}.` : `Got it, ${who}.`, 2600);
      }, 2400);
    }
    if (p.path.length) {
      const [tx, ty] = p.path[0];
      const dx = tx - p.pos[0], dy = ty - p.pos[1], dist = Math.hypot(dx, dy);
      const step = WALK_SPEED * dt;
      if (dist <= step) { p.pos = [tx, ty]; p.path.shift(); }
      else { p.pos = [p.pos[0] + dx / dist * step, p.pos[1] + dy / dist * step]; }
      p.walkDir = Math.abs(dx) > Math.abs(dy) ? 'side' : (dy < 0 ? 'up' : 'down');
      if (Math.abs(dx) > 0.5) p.dir = dx < 0 ? -1 : 1;
      p.walking = true;
    } else {
      p.walking = false;
      if (p.goal && p.goal.leave && !p.leaving) { p.leaving = true; ROOM.gone.add(id); }
      if (p.leaving) { ROOM.people.delete(id); ROOM_HOST.changed(); }
    }
  }
  roomDraw(t);
  ROOM.raf = requestAnimationFrame(roomTick);
}
// The name carved into the mantel, so a picture of the room says where it is from.
const ROOM_LETTERS = { H: ['10001', '10001', '10001', '11111', '10001', '10001', '10001'],
  E: ['11111', '10000', '10000', '11110', '10000', '10000', '11111'], A: ['01110', '10001', '10001', '11111', '10001', '10001', '10001'],
  R: ['11110', '10001', '10001', '11110', '10100', '10010', '10001'], T: ['11111', '00100', '00100', '00100', '00100', '00100', '00100'],
  I: ['11111', '00100', '00100', '00100', '00100', '00100', '11111'], N: ['10001', '11001', '10101', '10101', '10011', '10001', '10001'],
  G: ['01110', '10001', '10000', '10111', '10001', '10001', '01110'], L: ['10000', '10000', '10000', '10000', '10000', '10000', '11111'] };
function roomCarve(ctx, kx, ky) {
  const word = 'INGLEN', u = 3, gap = 2;
  const x0 = 767 - (word.length * (5 + gap) - gap) * u / 2, y0 = 157;
  // Lit edge first, a step down and right, then the cut itself.
  for (const [dx, dy, ink] of [[1, 1, 'rgba(255,196,128,0.30)'], [0, 0, 'rgba(58,26,10,0.72)']]) {
    ctx.fillStyle = ink;
    [...word].forEach((ch, i) => ROOM_LETTERS[ch].forEach((row, r) => [...row].forEach((on, c) => {
      if (on !== '1') return;
      const x = x0 + (i * (5 + gap) + c) * u + dx, y = y0 + r * u + dy;
      ctx.fillRect(Math.round(x * kx), Math.round(y * ky), Math.ceil(u * kx), Math.ceil(u * ky));
    })));
  }
}
// The fire in the grate burns. Each room's own flames are painted out of its
// firebox once (wall colour per row), and their colours paint new flames on
// the same pixel grid, layer over layer from the outer red to the white heart.
const FIRE = { x: 720, y: 212, w: 96, h: 84, u: 3, step: 0.09 };
const FIRE_FX = { plates: [], pals: [] };
function roomFirePrep(style) {
  if (FIRE_FX.plates[style]) return true;
  const img = ROOM.bgs[style]; if (!img || !img.naturalWidth) return false;
  const sx = img.naturalWidth / ROOM.W, sy = img.naturalHeight / ROOM.H;
  const c = document.createElement('canvas'); c.width = FIRE.w; c.height = FIRE.h;
  const g = c.getContext('2d');
  g.imageSmoothingEnabled = false;
  g.drawImage(img, FIRE.x * sx, FIRE.y * sy, FIRE.w * sx, FIRE.h * sy, 0, 0, FIRE.w, FIRE.h);
  const d = g.getImageData(0, 0, FIRE.w, FIRE.h), px = d.data, lit = [];
  const flame = i => px[i] >= 175 && px[i + 1] >= 80 && px[i] - px[i + 2] > 90;
  for (let y = 0; y < FIRE.h; y++) {
    const wall = [0, 0, 0]; let n = 0;
    for (let x = 0; x < FIRE.w; x++) {
      const i = (y * FIRE.w + x) * 4;
      if (!flame(i)) { wall[0] += px[i]; wall[1] += px[i + 1]; wall[2] += px[i + 2]; n++; }
    }
    if (n) wall.forEach((v, k) => { wall[k] = v / n; });
    for (let x = 0; x < FIRE.w; x++) {
      const i = (y * FIRE.w + x) * 4;
      if (!flame(i)) continue;
      lit.push([px[i], px[i + 1], px[i + 2]]);
      for (let k = 0; k < 3; k++) px[i + k] = wall[k];
    }
  }
  g.putImageData(d, 0, 0);
  lit.sort((a, b) => (a[0] + a[1] + a[2]) - (b[0] + b[1] + b[2]));
  const pick = q => lit.length ? lit[Math.min(lit.length - 1, Math.floor(q * lit.length))] : [230, 130, 50];
  // One colour per layer, from the outer edge to the near-white heart.
  FIRE_FX.pals[style] = [pick(0.02), pick(0.4), pick(0.75), pick(1)].map(c => `rgb(${c[0]},${c[1]},${c[2]})`);
  FIRE_FX.plates[style] = c;
  return true;
}
// Tongues across the grate as [centre 0..1, height in rows, half-width in columns]:
// one tall in the middle, two beside it, two small at the ends.
const FIRE_TONGUES = [[0.5, 27, 6.5], [0.31, 18, 5], [0.69, 19, 5], [0.14, 10, 3.5], [0.86, 9, 3.5]];
// Nested layers, outermost first: [height scale, width scale].
const FIRE_LAYERS = [[1, 1], [0.8, 0.74], [0.58, 0.5], [0.34, 0.3]];
function roomFireRows(t) {
  // Each column's flame height per layer. Tongues stretch, shrink and lean on
  // their own slow rhythms; time moves in steps so it flickers like drawn frames.
  const cols = Math.round(FIRE.w / FIRE.u), step = Math.floor(t / FIRE.step), tt = step * FIRE.step;
  return FIRE_LAYERS.map(([hs, ws], li) => {
    const out = new Float32Array(cols);
    FIRE_TONGUES.forEach(([cx, ht, hw], i) => {
      const k = i * 1.7 + 0.4;
      const tall = ht * hs * (0.84 + 0.1 * Math.sin(tt * 2.3 + k) + 0.06 * Math.sin(tt * 5.9 + k * 2.1));
      const lean = 1.1 * Math.sin(tt * 1.6 + k * 1.3) + 0.5 * Math.sin(tt * 4.1 + k);
      const mid = cx * cols + lean * hs, half = hw * ws;
      for (let c = 0; c < cols; c++) {
        const d = Math.abs(c + 0.5 - mid) / half;
        if (d < 1) out[c] = Math.max(out[c], tall * (0.45 * (1 - d ** 1.6) + 0.55 * (1 - d) ** 1.3));
      }
    });
    // The outer edges lick up and down a row from frame to frame.
    if (li < 2) for (let c = 0; c < cols; c++) if (out[c] > 1) out[c] += (_fireHash(c * 31 + li, step) - 0.5) * 1.8;
    return out;
  });
}
function _fireHash(a, b) { const s = Math.sin(a * 127.1 + b * 311.7) * 43758.5453; return s - Math.floor(s); }
function roomFire(ctx, kx, ky, t) {
  if (_rMotionOff() || !roomFirePrep(roomStyle(ROOM.view))) return;
  const style = roomStyle(ROOM.view), pal = FIRE_FX.pals[style];
  ctx.drawImage(FIRE_FX.plates[style], Math.round(FIRE.x * kx), Math.round(FIRE.y * ky), Math.round(FIRE.w * kx), Math.round(FIRE.h * ky));
  const u = FIRE.u, base = FIRE.y + FIRE.h, layers = roomFireRows(t / 1000);
  layers.forEach((tops, li) => {
    ctx.fillStyle = pal[li];
    tops.forEach((ht, c) => {
      const n = Math.round(ht);
      if (n < 1) return;
      const x = FIRE.x + c * u, y = base - n * u;
      ctx.fillRect(Math.round(x * kx), Math.round(y * ky), Math.ceil(u * kx), Math.round(base * ky) - Math.round(y * ky));
    });
  });
  // A few sparks leave the tips and fade out on the way up.
  const step = Math.floor(t / 1000 / FIRE.step), cols = layers[0].length;
  for (let i = 0; i < 3; i++) {
    const life = 14, k = step + i * 5, n = Math.floor(k / life), age = k % life;
    const c = Math.round(cols * (0.3 + 0.4 * _fireHash(i, n)));
    const r = layers[0][c] + 1 + age * 0.9;
    if (age > 9 || r * u > FIRE.h - 2) continue;
    ctx.fillStyle = pal[age < 4 ? 2 : 1];
    const x = FIRE.x + (c + Math.round(Math.sin(age * 0.9 + i) * 1.2)) * u, y = base - Math.round(r) * u;
    ctx.fillRect(Math.round(x * kx), Math.round(y * ky), Math.ceil(u * kx), Math.ceil(u * ky));
  }
}
function roomProp(name) { return ROOM.man && ROOM.man.props ? ROOM.man.props[name] : null; }
function roomDraw(t) {
  const cv = _rEl('tm-canvas'); if (!cv || !ROOM.bg) return;
  const stage = _rEl('tm-stage');
  const w = stage.clientWidth, h = stage.clientHeight, dpr = window.devicePixelRatio || 1;
  if (cv.width !== Math.round(w * dpr) || cv.height !== Math.round(h * dpr)) { cv.width = Math.round(w * dpr); cv.height = Math.round(h * dpr); }
  const ctx = cv.getContext('2d');
  ctx.imageSmoothingEnabled = false;
  const kx = cv.width / ROOM.W, ky = cv.height / ROOM.H;
  const inView = p => (p.room || 0) === ROOM.view;
  ctx.drawImage(ROOM.bgs[roomStyle(ROOM.view)] || ROOM.bg, 0, 0, cv.width, cv.height);
  roomCarve(ctx, kx, ky);
  roomFire(ctx, kx, ky, t);

  // Everything that stands on the floor, with the y where it touches it.
  const items = [];
  const seated = new Map();
  for (const p of ROOM.people.values()) {
    if (inView(p) && !p.waiting && !p.walking && p.desk != null && p.goal && p.goal.seated) seated.set(p.desk, p);
  }
  if (ROOM.props) {
    ROOM.desks.forEach((d, i) => {
      const worker = seated.get(i);
      items.push({ kind: 'prop', name: worker ? 'desk_on' : 'desk', x: d[0], y: d[1], shadow: [108, 12, 26] });
      items.push({ kind: 'prop', name: 'chair', x: d[0], y: d[1] + CHAIR_DY, shadow: [28, 7, 14], cover: !!worker });
    });
    if (ROOM.man.room_empty) {
      items.push({ kind: 'prop', name: 'armchair', x: 540, y: 440, shadow: [66, 15, 30], sy: -11 });
      items.push({ kind: 'prop', name: 'side_table', x: 650, y: 428, shadow: [22, 6, 16], sy: -3 });
    }
    // The cat sleeps on the rug by the fire and sits up to watch when there is work going on.
    if (roomProp('cat_sleep') && ROOM.view === 0) {
      const up = roomMainBusy() || ROOM.catUntil > Date.now();
      items.push({ kind: 'prop', name: up ? 'cat_sit' : 'cat_sleep', x: 868, y: 452, shadow: [19, 5, 5] });
      items.push({ kind: 'prop', name: 'books', x: 702, y: 442, shadow: [20, 5, 8] });
      items.push({ kind: 'prop', name: 'stool', x: 1238, y: 418, shadow: [20, 6, 14] });
    }
  }
  // Coming in and going out, a helper fades with the doorway instead of
  // popping into and out of the room.
  const doorFade = p => {
    if (p.agent && p.agent.role === 'main') return 1;
    const [dx, dy] = ROOM.spots.door;
    return Math.max(0, Math.min(1, (Math.hypot(p.pos[0] - dx, p.pos[1] - dy) - 8) / 100));
  };
  for (const p of ROOM.people.values()) {
    if (p.waiting || !inView(p)) continue;
    const fade = doorFade(p);
    if (fade <= 0.02) continue;
    items.push({ kind: 'person', p, x: p.pos[0], y: p.pos[1], shadow: [19, 6, 34], fade });
  }
  items.sort((a, b) => a.y - b.y);

  // Shadows first, so none lands on top of anything. The fire is the light:
  // each thing throws a long faint shadow away from it, longer and fainter the
  // further off it stands, and sits on a darker soft patch where it touches.
  const blob = (cx, cy, rx, ry, rot, alpha) => {
    ctx.save();
    ctx.translate(cx * kx, cy * ky);
    ctx.rotate(rot);
    ctx.scale(rx * kx, ry * ky);
    const g = ctx.createRadialGradient(0, 0, 0, 0, 0, 1);
    g.addColorStop(0, `rgba(10,6,4,${alpha})`); g.addColorStop(0.55, `rgba(10,6,4,${alpha * 0.72})`); g.addColorStop(1, 'rgba(10,6,4,0)');
    ctx.fillStyle = g;
    ctx.beginPath(); ctx.arc(0, 0, 1, 0, Math.PI * 2); ctx.fill();
    ctx.restore();
  };
  for (const it of items) {
    if (!it.shadow || (it.kind === 'person' && it.p.goal && (it.p.goal.seated || it.p.goal.sit) && !it.p.walking)) continue;
    const [rx, ry, tall] = it.shadow, y = it.y - 2 + (it.sy || 0);
    const dx = it.x - ROOM.fire[0], dy = (y - ROOM.fire[1]) * 1.6, d = Math.hypot(dx, dy) || 1;
    const far = Math.min(1, d / 760), len = tall * (0.5 + 1.1 * far);
    const f = it.fade == null ? 1 : it.fade;
    blob(it.x + dx / d * len * 0.8, y + dy / d * len * 0.34, rx + len * 0.45, ry * 1.05, Math.atan2(dy * 0.34, dx),
         (0.30 - 0.14 * far) * f);
    blob(it.x, y, rx * 0.94, ry, 0, 0.5 * f);
  }
  const [cw, ch] = ROOM.man.cell;
  const labels = [], hits = [];
  for (const it of items) {
    if (it.kind === 'prop') {
      const r = roomProp(it.name); if (!r) continue;
      const [sx, sy, sw, sh] = r;
      ctx.drawImage(ROOM.props, sx, sy, sw, sh, Math.round((it.x - sw / 2) * kx), Math.round((it.y - sh) * ky), Math.round(sw * kx), Math.round(sh * ky));
      if (it.name === 'desk_on') {
        // The screen breathes a little while someone works at it.
        const glow = 0.10 + 0.06 * Math.sin(t / 260 + it.x);
        const g = ctx.createRadialGradient(it.x * kx, (it.y - sh + 40) * ky, 2, it.x * kx, (it.y - sh + 40) * ky, 70 * kx);
        g.addColorStop(0, `rgba(127,179,191,${glow})`); g.addColorStop(1, 'rgba(127,179,191,0)');
        ctx.fillStyle = g; ctx.fillRect((it.x - 80) * kx, (it.y - sh - 20) * ky, 160 * kx, 130 * ky);
      }
      continue;
    }
    const p = it.p;
    const skin = roomSkin(p.agent);
    if (!ROOM.imgs[skin] && !ROOM.imgs.custom) continue;
    const img = roomLook(skin, p.seed);
    let frame, flip = false;
    if (p.walking) {
      frame = roomFrame(`walk_${p.walkDir}_${roomWalkStep(p.walkDir, t, p.seed)}`, skin);
      flip = p.walkDir === 'side' && p.dir < 0;
    } else {
      const pose = p.goal ? p.goal.pose : 'idle';
      frame = roomFrame(pose, skin);
      flip = !!(p.goal && p.goal.flip);
      // The side-on poses were drawn facing left; the walk frame that stands
      // in for one faces right, so it is turned the other way.
      if (/^side_/.test(pose) && !roomHas(skin, pose) && FRAME_NEAREST[pose] === 'walk_side_1') flip = !flip;
    }
    const typing = !p.walking && p.goal && p.goal.pose === 'type';
    // Walking, the body rises a little as the legs pass under it.
    // Facing or away, the body rises a little between steps; side-on, the
    // drawn steps already carry it.
    const midStep = p.walking && p.walkDir !== 'side' && Math.floor(t / WALK_BEAT + p.seed * 6) % 3 === 1;
    const bob = typing ? Math.round(Math.sin(t / 130 + p.seed) * 1.3) : (!p.walking ? Math.round(Math.sin(t / 700 + p.seed) * 0.8) : (midStep ? -1 : 0));
    ctx.save();
    const low = !p.walking && p.goal && (p.goal.sit || p.goal.pose === 'tend_fire');
    const lift = typing ? SIT_LIFT : (low ? p.goal.lift || 0 : 0);
    ctx.translate(Math.round(p.pos[0] * kx), Math.round((p.pos[1] + bob - lift) * ky));
    ctx.globalAlpha = it.fade == null ? 1 : it.fade;
    if (flip) ctx.scale(-1, 1);
    ctx.drawImage(img, frame * cw, 0, cw, ch, Math.round(-cw * kx / 2), Math.round(-ch * ky), Math.round(cw * kx), Math.round(ch * ky));
    ctx.restore();
    const headY = typing ? p.pos[1] - 112 - lift : low ? p.pos[1] - 120 - lift : p.pos[1] - 142;
    if (it.fade != null && it.fade < 0.6) continue;     // the name shows once they are properly in
    labels.push({ p, x: p.pos[0] * kx / dpr, y: headY * ky / dpr });
    hits.push({ p, x0: (p.pos[0] - 34) * kx / dpr, x1: (p.pos[0] + 34) * kx / dpr,
                y0: (p.pos[1] - (typing || low ? 118 : 138) - lift) * ky / dpr, y1: (p.pos[1] - lift + 4) * ky / dpr, z: p.pos[1] });
  }

  // Firelight over everything, so the room and the people share one light.
  const fl = 0.07 + 0.035 * Math.sin(t / 170) + 0.02 * Math.sin(t / 61);
  const grad = ctx.createRadialGradient(770 * kx, 290 * ky, 10 * kx, 770 * kx, 290 * ky, 640 * kx);
  grad.addColorStop(0, `rgba(255,150,60,${fl})`); grad.addColorStop(1, 'rgba(255,150,60,0)');
  ctx.fillStyle = grad; ctx.fillRect(0, 0, cv.width, cv.height);

  // The cat: woken by a click, it sits up, says its piece and goes back to sleep.
  if (ROOM.view === 0) hits.push({ cat: true, x0: (868 - 28) * kx / dpr, x1: (868 + 28) * kx / dpr, y0: (452 - 42) * ky / dpr, y1: (452 + 4) * ky / dpr, z: 452 });
  roomWipeDraw(ctx, cv);
  if (ROOM.view === 0 && ROOM.catSay && ROOM.catSay.until > Date.now()) labels.push({ cat: ROOM.catSay.text, x: 868 * kx / dpr, y: (452 - 44) * ky / dpr });
  ROOM.hits = hits;
  // Labels and speech are redrawn only when something about them changed, so
  // a click on one lands on the same element it was aimed at.
  const now = Date.now();
  const sig = labels.map(({ p, x, y, cat }) => {
    if (cat) return 'cat|' + cat;
    const say = ROOM.says.get(p.id);
    return [p.id, Math.round(x), Math.round(y), p.agent.status, p.agent.blocked, p.agent.runtime, ROOM.sel === p.id,
            say && say.until > now ? say.text : ''].join('|');
  }).join('~');
  if (sig === ROOM.labelSig) return;
  ROOM.labelSig = sig;
  const box = _rEl('tm-labels');
  box.innerHTML = '';
  const placed = [];
  for (const { p, x, y, cat } of labels) {
    if (cat) {
      const b = document.createElement('div');
      b.className = 'tm-say doing';
      b.style.left = x + 'px'; b.style.top = y + 'px';
      b.textContent = cat;
      box.appendChild(b);
      continue;
    }
    const a = p.agent;
    const st = teamNeedsYou(a) ? 'needs' : a.status;
    const el = document.createElement('div');
    el.className = 'tm-label ' + (a.role === 'main' ? '' : st) + (ROOM.sel === p.id ? ' sel' : '');
    el.style.left = x + 'px'; el.style.top = y + 'px';
    el.innerHTML = ROOM_HOST.mini(a) + _rEsc(a.name || a.persona || 'helper');
    el.addEventListener('click', () => roomPoke(p));
    box.appendChild(el);
    // Whole pixels, so the 1px border doesn't shimmer while someone walks.
    el.style.transform = 'none';
    el.style.left = Math.round(x - el.offsetWidth / 2) + 'px'; el.style.top = Math.round(y - el.offsetHeight) + 'px';
    const say = ROOM.says.get(p.id);
    if (say && say.until > now) {
      const b = document.createElement('div');
      b.className = 'tm-say' + (say.live && a.role !== 'main' ? ' doing' : '');
      b.style.left = x + 'px'; b.style.top = y + 'px';
      b.textContent = say.text;
      box.appendChild(b);
      // Kept inside the room; the tail still points at whoever is speaking.
      const bw = b.offsetWidth, sw = box.clientWidth;
      const cx = Math.max(bw / 2 + 6, Math.min(sw - bw / 2 - 6, x));
      b.style.left = cx + 'px';
      b.style.setProperty('--tail', (x - cx) + 'px');
      // Two people close together: the later bubble sits above the other.
      let r = b.getBoundingClientRect(), lift = 0;
      for (const o of placed) {
        if (r.left < o.right + 4 && r.right > o.left - 4 && r.top - lift < o.bottom + 4 && r.bottom - lift > o.top - 4)
          lift += (r.bottom - lift) - o.top + 6;
      }
      if (lift) { b.style.top = (y - lift) + 'px'; r = b.getBoundingClientRect(); }
      placed.push(r);
      if (!say.shown) {
        say.shown = true;
        const sp = _rEasing(0.4, 0.8);
        if (!b.classList.contains('doing'))
          b.animate([{ opacity: 0, transform: 'translate(-50%, calc(-100% - 18px)) scale(.9)' },
                     { opacity: 1, transform: 'translate(-50%, calc(-100% - 26px)) scale(1)' }], { duration: sp.duration, easing: sp.easing });
      }
    }
  }
}
// Something said over someone's head for a while.
function roomSay(id, text, ms = 3600, live = false) {
  ROOM.says.set(id, { text: String(text || '').slice(0, 220), until: Date.now() + ms, live });
  ROOM.labelSig = '';
}
// Clicking someone: they turn, wave and say where they are with the work.
const MAIN_HELLOS = ['Hey. What do you need?', 'Right here.', "Mm? I'm listening.", 'Say the word.', 'At your service.'];
function roomPoke(p) {
  const a = p.agent, now = Date.now();
  roomShow(p.room || 0);
  p.pokeUntil = now + 1400;
  p.pokePose = ['raise', 'stretch', 'raise', 'idle'][Math.floor(Math.random() * 4)];
  if (a.role === 'main') {
    const seatedNow = p.goal && p.goal.sit && !p.path.length;
    p.pokeSit = seatedNow ? (p.goal.lift || 0.01) : 0;      // stays in its seat, at the same height
    p.pokePose = seatedNow ? (p.goal.pose === 'sleep' ? 'sit' : p.goal.pose) : 'wave';
    roomSay(p.id, roomMainBusy() ? 'Still on it. Give me a moment.'
      : !roomHasBrain() ? "I can't think without a model. Load one from the top bar."
      : MAIN_HELLOS[Math.floor(Math.random() * MAIN_HELLOS.length)]);
    const inp = _rEl('tm-input'); if (inp) inp.focus();
    ROOM_HOST.select(p.id, true);
    return;
  }
  // At a desk it stays in its chair and keeps its pose: a stretch there would
  // stand it up through the desk.
  p.pokeSeated = !!(p.goal && p.goal.seated && !p.path.length);
  if (p.pokeSeated) p.pokePose = p.goal.pose;
  const short = (t, n = 90) => { t = String(t || '').replace(/\s+/g, ' ').trim(); return t.length > n ? t.slice(0, n - 1) + '…' : t; };
  let line;
  if (teamNeedsYou(a)) line = `I need your OK to use ${a.blocked}.`;
  else if (a.status === 'working') line = a.activity === 'tool' ? `Busy with ${a.tool}. ${short(a.prompt, 60)}` : `On it: ${short(a.prompt, 70)}`;
  else if (a.status === 'done') line = a.preview ? `Done. ${short(a.preview)}` : 'All done.';
  else if (a.status === 'failed') line = `Didn't make it. ${short(a.preview, 70)}`;
  else line = 'Stopped when Inglen closed.';
  roomSay(p.id, line, 4200);
  ROOM_HOST.select(p.id, true);
}

// Each person's look comes from their id, in the room and in the list alike.
function roomSeedOf(id) {
  return Math.abs([...String(id)].reduce((h, c) => (h * 31 + c.charCodeAt(0)) | 0, 7)) % 97;
}
function roomFace(role, seed) {
  // The head and shoulders of the idle frame, for the portrait strip.
  const img = (ROOM.imgs[role] || ROOM.imgs.custom) ? roomLook(role, seed || 0) : null;
  const c = document.createElement('canvas');
  c.width = 40; c.height = 40;
  if (!img) return c;
  const x = c.getContext('2d');
  x.imageSmoothingEnabled = false;
  const [cw, ch] = ROOM.man.cell;
  x.drawImage(img, (cw - 60) / 2, ch - 124, 60, 60, 0, 0, 40, 40);
  return c;
}
// Which room is in view. Shown once a second room has someone in it. The
// new room opens out of the point clicked as a circle of the room's own big
// pixels, on a spring with no overshoot: about a third of a second, and a
// click during it goes straight on from what is on screen.
function roomShow(r, x, y) {
  if (r === ROOM.view) return;
  const cv = _rEl('tm-canvas'), stage = _rEl('tm-stage');
  if (cv && cv.width && roomWipeOn()) {
    const snap = document.createElement('canvas');
    snap.width = cv.width; snap.height = cv.height;
    snap.getContext('2d').drawImage(cv, 0, 0);
    const k = cv.width / (stage.clientWidth || cv.width);
    ROOM.wipe = { snap, t0: performance.now(), x: (x != null ? x : stage.clientWidth / 2) * k, y: (y != null ? y : stage.clientHeight / 2) * k };
    stage.classList.add('tm-wipe-hide');
  }
  ROOM.view = r;
  ROOM.labelSig = '';
  roomRoomsRender();
}
const WIPE_PERIOD = 0.32;
function roomWipeDraw(ctx, cv) {
  const w = ROOM.wipe;
  if (!w) return;
  const t = (performance.now() - w.t0) / 1000, done = _rSpring(t, WIPE_PERIOD);
  if (done > 0.985) { ROOM.wipe = null; _rEl('tm-stage').classList.remove('tm-wipe-hide'); return; }
  // Everything outside the circle is still the room before, drawn on a grid
  // of big square cells so the edge steps like the art does.
  const cell = Math.max(8, Math.round(cv.width / 96));
  const R = done * Math.hypot(Math.max(w.x, cv.width - w.x), Math.max(w.y, cv.height - w.y));
  ctx.save();
  ctx.beginPath();
  ctx.rect(0, 0, cv.width, cv.height);
  for (let y = Math.floor((w.y - R) / cell) * cell; y < w.y + R; y += cell) {
    const dy = y + cell / 2 - w.y;
    if (Math.abs(dy) >= R) continue;
    const half = Math.sqrt(R * R - dy * dy);
    const x0 = Math.round((w.x - half) / cell) * cell, x1 = Math.round((w.x + half) / cell) * cell;
    if (x1 > x0) ctx.rect(x0, y, x1 - x0, cell);
  }
  ctx.clip('evenodd');
  ctx.drawImage(w.snap, 0, 0);
  ctx.restore();
}
function roomRoomsRender() {
  const box = _rEl('tm-rooms'); if (!box) return;
  const people = [...ROOM.people.values()].filter(p => !p.leaving);
  const used = ROOM.rooms.map((_, r) => people.filter(p => (p.room || 0) === r));
  const open = used.map((list, r) => r === 0 || list.length || r === ROOM.view);
  if (!open.slice(1).some(Boolean)) { box.hidden = true; box.innerHTML = ''; box.dataset.sig = ''; return; }
  box.hidden = false;
  const sig = used.map((list, r) => open[r] ? [r, list.length, list.some(p => p.agent.role !== 'main' && teamNeedsYou(p.agent)), r === ROOM.view].join(',') : '').join('|');
  if (box.dataset.sig === sig) return;
  const fresh = !box.dataset.sig;
  box.dataset.sig = sig;
  box.innerHTML = '<span class="tm-rooms-ind"></span>';
  ROOM.rooms.forEach((_, r) => {
    if (!open[r]) return;
    const room = ROOM.rooms[roomStyle(r)];
    const b = document.createElement('button');
    b.className = r === ROOM.view ? 'on' : '';
    b.dataset.r = r;
    b.title = `${room.name}: ${used[r].length} here`;
    b.innerHTML = `${_rEsc(room.name)}<em>${used[r].length}</em>${used[r].some(p => p.agent.role !== 'main' && teamNeedsYou(p.agent)) ? '<i></i>' : ''}`;
    b.addEventListener('click', e => {
      const st = _rEl('tm-stage').getBoundingClientRect();
      roomShow(r, e.clientX - st.left, e.clientY - st.top);
    });
    box.appendChild(b);
  });
  // The highlight slides to the room in view. Its two edges ride their own
  // springs, the leading one a little quicker, so it stretches as it goes.
  const on = box.querySelector('button.on');
  if (!on) return;
  const L = on.offsetLeft, Rt = on.offsetLeft + on.offsetWidth;
  // Without the page's springs the highlight moves on a plain transition.
  if (typeof Spring !== 'function' || typeof _springUsers === 'undefined') {
    const el = box.querySelector('.tm-rooms-ind');
    if (el) { el.style.left = L + 'px'; el.style.width = (Rt - L) + 'px'; }
    return;
  }
  if (!ROOM.ind) ROOM.ind = { l: new Spring(0, 0.34, 0.86), r: new Spring(0, 0.34, 0.86) };
  const ind = ROOM.ind;
  if (fresh || _rMotionOff()) { ind.l.jump(L); ind.r.jump(Rt); }
  else {
    const right = L > ind.l.target;
    ind.l.period = right ? 0.42 : 0.3; ind.r.period = right ? 0.3 : 0.42;
    ind.l.to(L); ind.r.to(Rt);
  }
  const paint = () => {
    const el = box.querySelector('.tm-rooms-ind');
    if (!el) return false;
    const l = Math.round(ind.l.value() / 2) * 2, r = Math.round(ind.r.value() / 2) * 2;
    el.style.left = l + 'px'; el.style.width = Math.max(0, r - l) + 'px';
    if (ind.l.settled() && ind.r.settled()) { _springUsers.delete(paint); return false; }
  };
  _springUsers.add(paint);
  paint();
  _springTick();
}

async function roomStart() {
  if (ROOM.raf) return;
  if (!(await roomLoad())) return;
  roomSync(ROOM_HOST.team());
  ROOM.last = 0;
  ROOM.raf = requestAnimationFrame(roomTick);
}
function roomStop() { if (ROOM.raf) cancelAnimationFrame(ROOM.raf); ROOM.raf = 0; }
// A click or tap at x, y on the canvas (its own CSS pixels): whoever stands
// there turns and answers, or the cat wakes. Returns who it was, if anyone.
function roomTap(x, y) {
  const hit = ROOM.hits.filter(h => x >= h.x0 && x <= h.x1 && y >= h.y0 && y <= h.y1).sort((a, b) => b.z - a.z)[0];
  if (hit && hit.cat) {
    const said = ['Mrrp.', 'Purr…', 'Mrow?', '…', 'Prrt.'];
    ROOM.catUntil = Date.now() + 5000;
    ROOM.catSay = { text: said[Math.floor(Math.random() * said.length)], until: Date.now() + 2200 };
    ROOM.labelSig = '';
    return 'cat';
  }
  if (hit) roomPoke(hit.p);
  return hit ? hit.p.id : '';
}
