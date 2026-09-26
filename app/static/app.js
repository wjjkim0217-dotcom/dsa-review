/* DSA Review - browser front end.
 * Vanilla JS, no build step, no external resources.
 * All user-provided text is rendered with textContent / createElement (never innerHTML).
 */
import { createEditor } from './vendor/codemirror.bundle.js';

// ================================================================ constants
const RATINGS = [
  { value: 1, key: 'again', label: 'Again', guide: "Couldn't solve it without the solution" },
  { value: 2, key: 'hard', label: 'Hard', guide: 'Solved, but needed a hint, went well over time, or had real bugs' },
  { value: 3, key: 'good', label: 'Good', guide: 'Solved it on my own, about on time' },
  { value: 4, key: 'easy', label: 'Easy', guide: 'Fast and confident, interview-ready' },
];
const RATING_BY_VALUE = Object.fromEntries(RATINGS.map((r) => [r.value, r]));
const TARGET_MINUTES = { Easy: 15, Medium: 25, Hard: 40 };
const DEFAULT_TARGET_MINUTES = 30;
const SOURCES = ['LeetCode', 'NeetCode', 'Cracking the Coding Interview', 'HackerRank', 'Other'];
const LANGUAGES = ['python', 'java', 'c++', 'c', 'c#', 'javascript', 'typescript', 'go', 'kotlin', 'swift', 'rust', 'ruby', 'sql'];
const STATUS_LABEL = { due: 'Due', new: 'New', scheduled: 'Scheduled', suspended: 'Suspended' };
const STATUS_ORDER = { due: 0, new: 1, scheduled: 2, suspended: 3 };
const DIFF_ORDER = { Easy: 1, Medium: 2, Hard: 3 };
const DAY_MS = 86400000;

// ================================================================ DOM helpers
const SVG_NS = 'http://www.w3.org/2000/svg';
const PROP_KEYS = new Set(['value', 'checked', 'disabled', 'hidden', 'required', 'multiple']);

/** Create an element. props: class, text, dataset, on<event>, DOM props, or attributes. */
function h(tag, props, ...children) {
  const el = document.createElement(tag);
  if (props) {
    for (const [key, val] of Object.entries(props)) {
      if (val === undefined || val === null || val === false) continue;
      if (key === 'class') el.className = val;
      else if (key === 'text') el.textContent = val;
      else if (key === 'dataset') Object.assign(el.dataset, val);
      else if (key.startsWith('on') && typeof val === 'function') el.addEventListener(key.slice(2), val);
      else if (PROP_KEYS.has(key)) el[key] = val;
      else el.setAttribute(key, val === true ? '' : String(val));
    }
  }
  appendKids(el, children);
  return el;
}

function appendKids(el, kids) {
  for (const kid of kids.flat(Infinity)) {
    if (kid === null || kid === undefined || kid === false || kid === '') continue;
    el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
}

function srOnly(text) {
  return h('span', { class: 'sr-only', text });
}

// Simple original line icons (24px grid, stroked with currentColor).
const ICONS = {
  external: ['M14 4h6v6', 'M20 4l-9 9', 'M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5'],
  copy: ['M9 9h10a1 1 0 0 1 1 1v10a1 1 0 0 1-1 1H9a1 1 0 0 1-1-1V10a1 1 0 0 1 1-1z', 'M5 15H4a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h10a1 1 0 0 1 1 1v1'],
  check: ['M5 12.5l4.5 4.5L19 7.5'],
  back: ['M15 5l-7 7 7 7'],
  play: ['M8 5.5v13l10.5-6.5z'],
  pause: ['M9 5v14', 'M15 5v14'],
  reset: ['M4 12a8 8 0 1 0 2.35-5.65', 'M4 4v5h5'],
  close: ['M6 6l12 12', 'M18 6L6 18'],
  search: ['M11 18a7 7 0 1 0 0-14 7 7 0 0 0 0 14z', 'M20 20l-4-4'],
  download: ['M12 4v11', 'M7 10l5 5 5-5', 'M5 20h14'],
  upload: ['M12 15V4', 'M7 9l5-5 5 5', 'M5 20h14'],
  info: ['M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18z', 'M12 11v5', 'M12 7.5v.01'],
  plus: ['M12 5v14', 'M5 12h14'],
  shuffle: ['M16 4h4v4', 'M4 20L20 4', 'M20 16v4h-4', 'M15 15l5 5', 'M4 4l5 5'],
  done: ['M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18z', 'M8 12.5l2.8 2.8L16 10'],
  spark: ['M12 3l1.7 5 5 1.7-5 1.7-1.7 5-1.7-5-5-1.7 5-1.7z', 'M19 15l.8 2.3L22 18l-2.2.7-.8 2.3-.8-2.3L16 18l2.2-.7z'],
};

function icon(name, size = 16) {
  const svg = document.createElementNS(SVG_NS, 'svg');
  const attrs = {
    viewBox: '0 0 24 24', width: size, height: size, fill: 'none', stroke: 'currentColor',
    'stroke-width': 2, 'stroke-linecap': 'round', 'stroke-linejoin': 'round',
    'aria-hidden': 'true', focusable: 'false',
  };
  for (const [k, v] of Object.entries(attrs)) svg.setAttribute(k, String(v));
  for (const d of ICONS[name]) {
    const path = document.createElementNS(SVG_NS, 'path');
    path.setAttribute('d', d);
    svg.append(path);
  }
  return svg;
}

function debounce(fn, ms) {
  let timer;
  const wrapped = (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), ms);
  };
  wrapped.cancel = () => clearTimeout(timer);
  return wrapped;
}

function plural(n, word, pluralWord = `${word}s`) {
  return `${n} ${n === 1 ? word : pluralWord}`;
}

function isTypingTarget(el) {
  if (!el || el === document.body) return false;
  if (el.isContentEditable) return true;
  const tag = el.tagName;
  if (tag === 'TEXTAREA' || tag === 'SELECT') return true;
  if (tag === 'INPUT') {
    return !['checkbox', 'radio', 'button', 'submit', 'reset', 'range', 'file', 'color'].includes(el.type);
  }
  return false;
}

/** Scroll so the top of el is visible below the sticky header. */
function revealTop(el) {
  const header = document.querySelector('.topbar');
  const offset = (header ? header.offsetHeight : 0) + 12;
  const top = el.getBoundingClientRect().top;
  if (top < offset) window.scrollBy({ top: top - offset });
}

// ================================================================ API
class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

/** Call the backend. Non-GET requests always send JSON. Throws ApiError with the server's message.
 * `extra.signal` (optional) is an AbortSignal, so a caller can cancel a slow request
 * (e.g. Ask Claude's Cancel button) - fetch then rejects with a DOMException named "AbortError". */
async function api(method, path, body, extra) {
  const opts = { method, headers: { Accept: 'application/json' } };
  if (extra && extra.signal) opts.signal = extra.signal;
  if (method !== 'GET') {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(body ?? {});
  }
  let res;
  try {
    res = await fetch(path, opts);
  } catch (err) {
    if (err && err.name === 'AbortError') throw err;
    throw new ApiError("Can't reach the DSA Review server. Is it still running?", 0);
  }
  let data = null;
  try {
    data = await res.json();
  } catch {
    data = null;
  }
  if (!res.ok) {
    const msg = data && typeof data.error === 'string' ? data.error : `Request failed (${res.status})`;
    throw new ApiError(msg, res.status);
  }
  return data;
}

// ================================================================ toasts & dialog
const toastRoot = document.getElementById('toasts');

function toast(message, { type = 'info', actionLabel, onAction, duration } = {}) {
  const el = h('div', { class: `toast is-${type}`, role: type === 'error' ? 'alert' : 'status' });
  let timer;
  const close = () => {
    clearTimeout(timer);
    el.remove();
  };
  el.append(h('div', { class: 'toast-msg', text: message }));
  if (actionLabel) {
    el.append(h('button', {
      type: 'button',
      class: 't-action',
      onclick: () => {
        close();
        onAction();
      },
    }, actionLabel));
  }
  el.append(h('button', { type: 'button', class: 't-close', 'aria-label': 'Dismiss', onclick: close }, icon('close', 16)));
  toastRoot.append(el);
  while (toastRoot.children.length > 3) toastRoot.firstElementChild.remove();

  const ms = duration ?? (actionLabel ? 10000 : type === 'error' ? 8000 : 4500);
  const arm = () => {
    clearTimeout(timer);
    timer = setTimeout(close, ms);
  };
  el.addEventListener('mouseenter', () => clearTimeout(timer));
  el.addEventListener('mouseleave', arm);
  el.addEventListener('focusin', () => clearTimeout(timer));
  el.addEventListener('focusout', arm);
  arm();
  return { close };
}

function toastError(err) {
  toast(err && err.message ? err.message : String(err), { type: 'error' });
}

/** Modal confirmation using the <dialog> in index.html. Resolves true when confirmed. */
function confirmDialog({ title, body, confirmLabel = 'Confirm' }) {
  const dlg = document.getElementById('confirm-dialog');
  dlg.querySelector('#confirm-title').textContent = title;
  dlg.querySelector('#confirm-body').textContent = body;
  dlg.querySelector('[data-choice="confirm"]').textContent = confirmLabel;
  const opener = document.activeElement;
  return new Promise((resolve) => {
    const onClick = (e) => {
      const btn = e.target.closest('[data-choice]');
      if (btn) {
        dlg.close(btn.dataset.choice);
        return;
      }
      if (e.target === dlg) {
        // Click on the backdrop (outside the dialog box) cancels.
        const r = dlg.getBoundingClientRect();
        const inside = e.clientX >= r.left && e.clientX <= r.right && e.clientY >= r.top && e.clientY <= r.bottom;
        if (!inside) dlg.close('cancel');
      }
    };
    dlg.returnValue = '';
    dlg.addEventListener('click', onClick);
    dlg.addEventListener('close', () => {
      dlg.removeEventListener('click', onClick);
      const ok = dlg.returnValue === 'confirm';
      if (!ok && opener && opener.isConnected) opener.focus();
      resolve(ok);
    }, { once: true });
    dlg.showModal();
    dlg.querySelector('[data-choice="cancel"]').focus();
  });
}

/**
 * Like confirmDialog, but the user must type `word` (e.g. "DELETE") before the
 * confirm button unlocks. For actions that can't be undone from inside the app.
 * Builds its own <dialog> and removes it when closed.
 */
function typedConfirmDialog({ title, body, word, confirmLabel }) {
  const opener = document.activeElement;
  const inputId = `typed-confirm-${Math.random().toString(36).slice(2, 8)}`;
  const input = h('input', { type: 'text', id: inputId, autocomplete: 'off', spellcheck: 'false' });
  const confirmBtn = h('button', { type: 'button', class: 'btn btn-danger-solid', disabled: true }, confirmLabel);
  const cancelBtn = h('button', { type: 'button', class: 'btn' }, 'Cancel');
  const dlg = h('dialog', { 'aria-labelledby': `${inputId}-title`, 'aria-describedby': `${inputId}-body` },
    h('h2', { id: `${inputId}-title`, text: title }),
    h('p', { id: `${inputId}-body`, text: body }),
    h('label', { class: 'typed-confirm-label', for: inputId }, 'Type ', h('strong', { text: word }), ' to confirm'),
    input,
    h('div', { class: 'dialog-actions' }, cancelBtn, confirmBtn));
  document.body.append(dlg);
  const matches = () => input.value.trim() === word;
  input.addEventListener('input', () => { confirmBtn.disabled = !matches(); });
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && matches()) {
      e.preventDefault();
      dlg.close('confirm');
    }
  });
  confirmBtn.addEventListener('click', () => dlg.close('confirm'));
  cancelBtn.addEventListener('click', () => dlg.close('cancel'));
  return new Promise((resolve) => {
    dlg.addEventListener('close', () => {
      const ok = dlg.returnValue === 'confirm';
      dlg.remove();
      if (!ok && opener && opener.isConnected) opener.focus();
      resolve(ok);
    }, { once: true });
    dlg.showModal();
    input.focus();
  });
}

/**
 * Accessible "More" dropdown menu, built on <details>/<summary>.
 * items: array of { label, onClick, hidden, disabled, danger }, or a function
 * returning that array (called fresh each time the menu opens, so item text/
 * disabled state can reflect the latest data without rebuilding the menu).
 * Closes on Escape, on an outside click, and after an item is activated;
 * each close returns focus to the trigger (the <summary>).
 */
function menuButton({ label = 'More', icon: iconName = null, items, small = true }) {
  const summary = h('summary', { class: `btn${small ? ' btn-sm' : ''} menu-trigger` }, iconName ? icon(iconName, 14) : null, label);
  const list = h('div', { class: 'menu-list', role: 'menu' });
  const det = h('details', { class: 'menu' }, summary, list);
  let busy = false; // set via setBusy() below while an item's action is in flight

  function currentItems() {
    return typeof items === 'function' ? items() : items;
  }

  function renderItems() {
    const visible = currentItems().filter((it) => !it.hidden);
    list.replaceChildren(...visible.map((it) => h('button', {
      type: 'button',
      class: `menu-item${it.danger ? ' is-danger' : ''}`,
      role: 'menuitem',
      disabled: busy || Boolean(it.disabled),
      onclick: () => {
        close();
        it.onClick();
      },
    }, it.label)));
  }

  function close() {
    if (det.open) det.open = false;
  }
  function onDocClick(e) {
    if (!det.contains(e.target)) close();
  }
  function onKeydown(e) {
    if (e.key === 'Escape') {
      e.preventDefault();
      e.stopPropagation();
      close();
      summary.focus();
    } else if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      const focusable = [...list.querySelectorAll('button:not(:disabled)')];
      if (!focusable.length) return;
      e.preventDefault();
      const idx = focusable.indexOf(document.activeElement);
      const dir = e.key === 'ArrowDown' ? 1 : -1;
      const next = focusable[(idx + dir + focusable.length) % focusable.length];
      next.focus();
    }
  }
  // A click on <summary> is what the browser toggles <details> open/closed for by
  // default; preventDefault on it (while busy) stops the menu from opening at all.
  summary.addEventListener('click', (e) => {
    if (busy) e.preventDefault();
  });
  det.addEventListener('toggle', () => {
    if (det.open) {
      renderItems();
      document.addEventListener('click', onDocClick, true);
      document.addEventListener('keydown', onKeydown, true);
      const first = list.querySelector('button:not(:disabled)');
      if (first) first.focus();
    } else {
      document.removeEventListener('click', onDocClick, true);
      document.removeEventListener('keydown', onKeydown, true);
    }
  });
  renderItems();

  /** While busy, the trigger can't be opened and every item is disabled - so a click
   * that's already in flight (e.g. from an item's own onClick) can't be repeated before
   * it finishes, the way a disabled <button> would guard a normal button click. */
  function setBusy(v) {
    busy = Boolean(v);
    summary.setAttribute('aria-disabled', String(busy));
    if (busy) close();
  }

  return { el: det, close, refresh: renderItems, setBusy };
}

// ================================================================ formatting
function startOfLocalDay(d) {
  return new Date(d.getFullYear(), d.getMonth(), d.getDate());
}

/** Hours after midnight when a new study day begins (Settings → "New day starts at"). */
function dayStartMs() {
  const any = state.summaries.main || state.summaries.neetcode;
  const h = any && any.settings ? Number(any.settings.day_starts_at) : 4;
  return (Number.isFinite(h) ? h : 4) * 3600000;
}

/** The current study date (before the day-start hour it's still "yesterday"). */
function studyNow() {
  return new Date(Date.now() - dayStartMs());
}

/**
 * Whole study days from today to date (negative = in the past). Due dates from the
 * server are the instant their study day starts, so both sides are shifted by the
 * day-start hour; this keeps labels in step with what the Today queue shows.
 */
function calendarDiff(date, now = new Date()) {
  const shift = dayStartMs();
  const a = startOfLocalDay(new Date(date.getTime() - shift));
  const b = startOfLocalDay(new Date(now.getTime() - shift));
  return Math.round((a - b) / DAY_MS);
}

function parseLocalDate(ymd) {
  const [y, m, d] = ymd.split('-').map(Number);
  return new Date(y, m - 1, d);
}

/** Interval in days -> "5h", "11d", "2.5 mo". */
function fmtInterval(days) {
  if (days === null || days === undefined || Number.isNaN(Number(days))) return '—';
  if (days < 1) return `${Math.max(1, Math.round(days * 24))}h`;
  const d = Math.round(days);
  if (d < 60) return `${d}d`;
  return `${Math.round((days / 30) * 10) / 10} mo`;
}

function fmtDate(value, { weekday = true } = {}) {
  if (!value) return '—';
  const d = value instanceof Date ? value : new Date(value);
  const opts = { month: 'short', day: 'numeric' };
  if (weekday) opts.weekday = 'short';
  if (d.getFullYear() !== new Date().getFullYear()) opts.year = 'numeric';
  return d.toLocaleDateString(undefined, opts);
}

function fmtDateTime(value) {
  const d = new Date(value);
  const opts = { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' };
  if (d.getFullYear() !== new Date().getFullYear()) opts.year = 'numeric';
  return d.toLocaleString(undefined, opts);
}

/** "today", "tomorrow", "in 3d", "2d overdue", "—" */
function relShort(iso) {
  if (!iso) return '—';
  const n = calendarDiff(new Date(iso));
  if (n === 0) return 'today';
  if (n === 1) return 'tomorrow';
  if (n > 1) return `in ${n}d`;
  return `${-n}d overdue`;
}

/** Phrase that completes "Next review ..." */
function nextReviewPhrase(iso) {
  if (!iso) return 'not scheduled';
  const due = new Date(iso);
  const n = calendarDiff(due);
  if (n <= 0) {
    const hours = (due - Date.now()) / 3600000;
    return hours >= 1 ? `in ${plural(Math.round(hours), 'hour')}` : 'today';
  }
  if (n === 1) return 'tomorrow';
  if (n < 60) return `in ${n} days`;
  return `in ${Math.round((n / 30) * 10) / 10} months`;
}

function fmtClock(ms) {
  const total = Math.max(0, Math.floor(ms / 1000));
  const hrs = Math.floor(total / 3600);
  const mins = Math.floor((total % 3600) / 60);
  const secs = total % 60;
  const pad = (x) => String(x).padStart(2, '0');
  return hrs ? `${hrs}:${pad(mins)}:${pad(secs)}` : `${pad(mins)}:${pad(secs)}`;
}

function pct(x) {
  return x === null || x === undefined ? '—' : `${Math.round(x * 100)}%`;
}

function fmtStrength(days) {
  if (days === null || days === undefined) return '—';
  if (days < 1) return 'under a day';
  if (days < 60) return `~${plural(Math.round(days), 'day')}`;
  return `~${Math.round((days / 30) * 10) / 10} months`;
}

function fmtHour(hr) {
  return new Date(2000, 0, 1, hr).toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' });
}

// Same rules as the server (store._slug_tag): letters and digits from any language are kept.
function slugTag(tag) {
  return String(tag).trim().toLowerCase().replace(/[^\p{L}\p{N}+#]+/gu, '-').replace(/^-+|-+$/g, '').slice(0, 40)
    .replace(/-+$/, '');
}

function parseTagList(text) {
  return text.split(',').map((t) => t.trim()).filter(Boolean);
}

// state.library reads this at module init time (below), so it must be declared before
// `state` - a const declared after this point would still be in its temporal dead zone
// at that point, and loadLibraryFilters()'s try/catch would silently swallow that
// ReferenceError and fall back to defaults on every load, never actually restoring
// anything from sessionStorage.
const LIB_FILTERS_KEY = 'dsa-review:library-filters';

// ================================================================ app state
const state = {
  summaries: { main: null, neetcode: null }, // GET /api/summary?deck=..., cached per deck
  latestSummary: null, // whichever summary was fetched last (drives the nav badges)
  tags: [],
  sessions: { main: null, neetcode: null }, // per deck: { key, tag, order: [id], done }
  todayTags: { main: '', neetcode: '' },    // per-deck "Focus on" tag filter
  // Restored from sessionStorage (see loadLibraryFilters below), same as NeetCode's filters.
  library: Object.assign({ q: '', tag: '', status: '', deck: '', sortKey: 'next', sortDir: 1 }, loadLibraryFilters()),
  neetcodeFilters: null,
  cleanups: [],
  seq: 0,
  firstRender: true,
  activeReview: null, // review card controller that receives keyboard shortcuts
  today: null, // Today / NeetCode review view controller while mounted
  detail: null, // Problem view controller while mounted
};

function onCleanup(fn) {
  state.cleanups.push(fn);
}

function isCurrent(seq) {
  return seq === state.seq;
}

/** Fetch and cache the summary for one deck ('main' or 'neetcode'), then refresh both nav badges. */
async function refreshSummary(deck = 'main') {
  const s = await api('GET', `/api/summary?deck=${deck}`);
  state.summaries[deck] = s;
  state.latestSummary = s;
  updateBadges();
  return s;
}

/** Refresh both decks' summaries (used after any write, since a NeetCode change can affect
 * the main page when the "show NeetCode on main Today" toggle is on, and vice versa). */
function refreshSummaries() {
  return Promise.all([refreshSummary('main'), refreshSummary('neetcode')]).catch(() => {});
}

function updateBadges() {
  // Every summary carries deck_counts (for all decks) and the current settings, so the
  // most recently fetched one is always the freshest source for both badges.
  const latest = state.latestSummary;
  const dc = latest ? latest.deck_counts : null;
  const inMain = Boolean(latest && latest.settings.neetcode_in_main);
  const mainDue = dc ? dc.main.due + (inMain ? dc.neetcode.due : 0) : 0;
  const ncDue = dc ? dc.neetcode.due : 0;

  const mainBadge = document.getElementById('due-badge');
  mainBadge.hidden = mainDue === 0;
  mainBadge.replaceChildren(String(mainDue), srOnly(' due'));
  const ncBadge = document.getElementById('neetcode-badge');
  ncBadge.hidden = ncDue === 0;
  ncBadge.replaceChildren(String(ncDue), srOnly(' due'));
}

async function loadTags() {
  const r = await api('GET', '/api/tags');
  state.tags = r.tags;
  return r.tags;
}

// ================================================================ shared components
function pageHead(title, sub, ...right) {
  return h('div', { class: 'page-head' },
    h('div', null,
      h('h1', { tabindex: '-1', text: title }),
      sub ? h('p', { class: 'sub', text: sub }) : null),
    right.length ? h('div', { class: 'row' }, right) : null);
}

function diffBadge(difficulty, { showEmpty = false } = {}) {
  const level = DIFF_ORDER[difficulty] || 0;
  if (!level && !showEmpty) return null;
  if (!level) return h('span', { class: 'muted', text: '—', title: 'No difficulty set' });
  return h('span', { class: 'diff' },
    h('span', { class: 'diff-bars', 'aria-hidden': 'true' },
      [1, 2, 3].map((i) => h('i', { class: i <= level ? 'on' : null }))),
    difficulty);
}

function tagChips(tags, max = Infinity) {
  if (!tags || !tags.length) return null;
  const shown = tags.slice(0, max);
  const extra = tags.length - shown.length;
  return h('span', { class: 'chips' },
    shown.map((t) => h('span', { class: 'chip', text: t })),
    extra > 0 ? h('span', { class: 'chip', title: tags.slice(max).join(', '), text: `+${extra}` }) : null);
}

function statusPill(p) {
  return h('span', { class: `pill pill-${p.status}`, text: STATUS_LABEL[p.status] || p.status });
}

function deckPill(deck) {
  return h('span', { class: `pill pill-deck-${deck}`, text: deck === 'neetcode' ? 'NeetCode' : 'Main' });
}

function titleLink(p, iconSize) {
  if (!p.url) return p.title;
  return h('a', { href: p.url, target: '_blank', rel: 'noopener noreferrer' },
    p.title, icon('external', iconSize), srOnly(' (opens in a new tab)'));
}

function selectText(node) {
  const range = document.createRange();
  range.selectNodeContents(node);
  const sel = window.getSelection();
  sel.removeAllRanges();
  sel.addRange(range);
}

function codeBlock(code, language, label = 'Solution') {
  const codeEl = h('code', { text: code });
  const pre = h('pre', { tabindex: '0', 'aria-label': `${label} code` }, codeEl);
  const copyBtn = h('button', { type: 'button', class: 'btn btn-ghost btn-sm' }, icon('copy'), 'Copy');
  let resetTimer;
  copyBtn.addEventListener('click', async () => {
    try {
      await navigator.clipboard.writeText(code);
      copyBtn.replaceChildren(icon('check'), 'Copied');
      clearTimeout(resetTimer);
      resetTimer = setTimeout(() => copyBtn.replaceChildren(icon('copy'), 'Copy'), 1800);
    } catch {
      selectText(codeEl);
      toast("Couldn't copy automatically. The code is selected; press Ctrl+C (Cmd+C on a Mac).", { type: 'error' });
    }
  });
  return h('div', { class: 'code' },
    h('div', { class: 'code-head' },
      h('span', null, label, language ? ` · ${language}` : ''),
      copyBtn),
    pre);
}

// ================================================================ attempt editor
// One shared "recode it" component: a CodeMirror editor + Run/stdin/reset/copy
// toolbar + an output panel, used both by the problem page's "Attempt" card
// (autosaving to that problem's draft) and by the review card's "Code it here"
// panel (blank each time, saved to the draft only on request).
//
// `attempt-assist` used to be a deliberately empty hook; it now holds the
// "Ask Claude" button (see claudeAssist() below), which opens a panel under
// the output for debugging help.

// ================================================================ safe Markdown renderer
// Renders Claude's replies. Deliberately hand-written and small (headings,
// paragraphs, bold/italic, inline code, fenced code blocks with a Copy
// button, lists, and http(s)-only links) - it builds DOM nodes directly and
// never touches innerHTML with untrusted text, so there's no way for a reply
// to inject markup (e.g. an `<img onerror=...>` just renders as plain text).
function mdInline(text, container) {
  const re = /(`[^`\n]+`)|(\*\*[^*\n]+\*\*)|(\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\))|(\*[^*\n]+\*)|(_[^_\n]+_)/g;
  let last = 0;
  let m;
  while ((m = re.exec(text))) {
    if (m.index > last) container.append(document.createTextNode(text.slice(last, m.index)));
    if (m[1]) container.append(h('code', { text: m[1].slice(1, -1) }));
    else if (m[2]) container.append(h('strong', { text: m[2].slice(2, -2) }));
    else if (m[3]) container.append(h('a', { href: m[5], target: '_blank', rel: 'noopener noreferrer' }, m[4]));
    else if (m[6]) container.append(h('em', { text: m[6].slice(1, -1) }));
    else if (m[7]) container.append(h('em', { text: m[7].slice(1, -1) }));
    last = re.lastIndex;
  }
  if (last < text.length) container.append(document.createTextNode(text.slice(last)));
}

function mdCodeBlock(code, lang) {
  const codeEl = h('code', { text: code });
  const pre = h('pre', { class: 'md-pre', tabindex: '0', 'aria-label': lang ? `${lang} code` : 'code' }, codeEl);
  const copyBtn = h('button', { type: 'button', class: 'btn btn-ghost btn-sm' }, icon('copy', 14), 'Copy');
  let resetTimer;
  copyBtn.addEventListener('click', async () => {
    try {
      await navigator.clipboard.writeText(code);
      copyBtn.replaceChildren(icon('check', 14), 'Copied');
      clearTimeout(resetTimer);
      resetTimer = setTimeout(() => copyBtn.replaceChildren(icon('copy', 14), 'Copy'), 1800);
    } catch {
      toast("Couldn't copy automatically. Select the code and press Ctrl+C.", { type: 'error' });
    }
  });
  return h('div', { class: 'md-code' },
    h('div', { class: 'md-code-head' }, h('span', { text: lang || 'code' }), copyBtn),
    pre);
}

const LIST_RE = /^(\s*)([-*]|\d+\.)\s+(.*)$/;

/** Renders a Markdown string into a DOM node (never innerHTML). */
function renderMarkdown(text) {
  const root = h('div', { class: 'md' });
  const lines = String(text ?? '').replace(/\r\n/g, '\n').split('\n');
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) {
      i++;
      continue;
    }
    const fence = line.match(/^```\s*(\S*)\s*$/);
    if (fence) {
      const lang = fence[1] || '';
      const codeLines = [];
      i++;
      while (i < lines.length && !/^```\s*$/.test(lines[i])) {
        codeLines.push(lines[i]);
        i++;
      }
      i++; // skip the closing fence (or end of text, if it was never closed)
      root.append(mdCodeBlock(codeLines.join('\n'), lang));
      continue;
    }
    const heading = line.match(/^(#{1,6})\s+(.*)$/);
    if (heading) {
      const level = Math.min(heading[1].length + 2, 6); // nest under the panel's own headings
      const hEl = h(`h${level}`, { class: 'md-heading' });
      mdInline(heading[2], hEl);
      root.append(hEl);
      i++;
      continue;
    }
    if (LIST_RE.test(line)) {
      const ordered = /^\d+\.$/.test(line.match(LIST_RE)[2]);
      const listEl = h(ordered ? 'ol' : 'ul');
      while (i < lines.length && LIST_RE.test(lines[i])) {
        const li = h('li');
        mdInline(lines[i].match(LIST_RE)[3], li);
        listEl.append(li);
        i++;
      }
      root.append(listEl);
      continue;
    }
    const paraLines = [];
    while (i < lines.length && lines[i].trim() && !/^```/.test(lines[i]) &&
           !/^#{1,6}\s/.test(lines[i]) && !LIST_RE.test(lines[i])) {
      paraLines.push(lines[i]);
      i++;
    }
    const p = h('p');
    mdInline(paraLines.join(' '), p);
    root.append(p);
  }
  return root;
}

// ================================================================ Ask Claude panel
// Shared by the problem page's Attempt card and the review card's "Code it
// here" panel (via attemptEditor, below). One request/response round trip at
// a time; Cancel aborts the in-flight fetch. `history` mirrors the shape the
// server expects for /api/claude/help (a plain {role, content} transcript) -
// content here doesn't need to match the server's own prompt wording exactly,
// it just needs to carry enough of the same information for a follow-up.
const CLAUDE_ASSIST_MODES = [
  { key: 'hint', label: 'Hint' },
  { key: 'debug', label: 'Debug' },
  { key: 'explain', label: 'Explain' },
  { key: 'review', label: 'Review' },
];

function clip(text, limit) {
  if (typeof text !== 'string') return '';
  return text.length > limit ? `${text.slice(0, limit)}\n...[truncated]` : text;
}

function claudeHistoryEntry(problem, code, lastRun, question, isFirst) {
  const parts = [];
  if (isFirst) {
    parts.push(`Problem: ${problem.title}${problem.difficulty ? ` (${problem.difficulty})` : ''}`);
    if (problem.prompt) parts.push(clip(problem.prompt, 4000));
  }
  parts.push(`Code:\n${clip(code, 8000)}`);
  if (lastRun) {
    parts.push(`Run result: exit ${lastRun.exit_code}, timed_out=${Boolean(lastRun.timed_out)}\n` +
      `stdout: ${clip(lastRun.stdout || '', 3000)}\nstderr: ${clip(lastRun.stderr || '', 3000)}`);
  }
  if (question) parts.push(`Question: ${clip(question, 1000)}`);
  return clip(parts.join('\n\n'), 19500);
}

/**
 * options: problem, getCode(), getLastRun(), alwaysDefaultHint (true for review
 * cards, which always default to Hint instead of Hint-unless-the-run-failed).
 * Returns { toggleBtn, panel, destroy() }.
 */
function claudeAssist({ problem, getCode, getLastRun, alwaysDefaultHint = false }) {
  let mode = 'hint';
  let modeDefaulted = false;
  let history = [];
  let statusInfo = null;
  let busy = false;
  let abortCtrl = null;
  // Bumped by "New conversation" so a response for a conversation that was reset while
  // it was in flight gets ignored instead of appearing to answer the new one.
  let convoGen = 0;

  const panelId = `claude-assist-${problem.id}-${Math.random().toString(36).slice(2, 8)}`;
  const threadEl = h('div', { class: 'claude-thread', role: 'log', 'aria-label': 'Conversation with Claude' });

  const modeInputs = CLAUDE_ASSIST_MODES.map((m) => {
    const id = `${panelId}-${m.key}`;
    const input = h('input', {
      type: 'radio', name: `${panelId}-mode`, id, value: m.key, checked: m.key === mode,
      onchange: () => { if (input.checked) mode = m.key; },
    });
    return h('label', { class: 'claude-mode-choice', for: id }, input, h('span', { text: m.label }));
  });
  const modeGroup = h('div', {
    class: 'claude-mode-group', role: 'radiogroup', 'aria-label': 'Kind of help',
  }, modeInputs);

  const questionInput = h('textarea', {
    rows: '2', class: 'claude-question', placeholder: 'Ask something specific (optional)',
    'aria-label': 'Ask something specific (optional)',
    onkeydown: (e) => {
      if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
        e.preventDefault();
        ask();
      }
    },
  });

  const askBtn = h('button', { type: 'button', class: 'btn btn-primary btn-sm', onclick: () => ask() }, 'Ask');
  const cancelBtn = h('button', {
    type: 'button', class: 'btn btn-sm', hidden: true, onclick: () => { if (abortCtrl) abortCtrl.abort(); },
  }, 'Cancel');
  const newConvoBtn = h('button', {
    type: 'button', class: 'btn btn-sm btn-ghost', onclick: () => {
      convoGen++; // any in-flight ask() for the old conversation gets ignored when it resolves
      if (abortCtrl) abortCtrl.abort();
      history = [];
      threadEl.replaceChildren();
    },
  }, 'New conversation');
  const loadingRow = h('p', { class: 'claude-loading', hidden: true, role: 'status' }, 'Asking Claude…');
  const privacyNote = h('p', { class: 'hint claude-privacy' });

  function paintPrivacy() {
    const via = statusInfo && statusInfo.mode === 'cli' ? 'the Claude Code CLI' : 'your API key';
    privacyNote.textContent = statusInfo && statusInfo.mode !== 'off'
      ? `Sends this problem, your code and its output to Claude via ${via}.`
      : '';
  }

  const offNotice = h('div', { class: 'claude-off-notice', hidden: true },
    h('p', null, 'Ask Claude is turned off. Turn it on in ',
      h('a', { href: '#/settings' }, 'Settings → Claude help'), ' to use it.'));
  const form = h('div', { class: 'claude-form' },
    modeGroup, questionInput,
    h('div', { class: 'btn-group claude-actions' }, askBtn, cancelBtn, newConvoBtn),
    loadingRow, privacyNote);
  const panel = h('div', {
    class: 'claude-panel', id: panelId, hidden: true, 'aria-label': 'Ask Claude',
  }, offNotice, threadEl, form);

  function addMessage(role, node) {
    const row = h('div', { class: `claude-msg claude-msg-${role}` },
      h('div', { class: 'claude-msg-role', text: role === 'user' ? 'You' : 'Claude' }),
      h('div', { class: 'claude-msg-body' }, node));
    threadEl.append(row);
    return row;
  }

  async function ensureStatus() {
    if (!statusInfo) {
      try {
        statusInfo = await api('GET', '/api/claude/status');
      } catch {
        statusInfo = { mode: 'off' };
      }
    }
    return statusInfo;
  }

  async function refresh() {
    const st = await ensureStatus();
    const off = st.mode === 'off';
    offNotice.hidden = !off;
    form.hidden = off;
    paintPrivacy();
    if (!modeDefaulted) {
      modeDefaulted = true;
      const run = getLastRun();
      if (!alwaysDefaultHint && run && (run.timed_out || run.exit_code !== 0)) {
        mode = 'debug';
        const input = modeGroup.querySelector('input[value="debug"]');
        if (input) input.checked = true;
      }
    }
  }

  async function ask() {
    if (busy) return;
    const st = await ensureStatus();
    if (st.mode === 'off') {
      toast('Ask Claude is off. Turn it on in Settings.', { type: 'error' });
      return;
    }
    const question = questionInput.value.trim();
    const modeLabel = CLAUDE_ASSIST_MODES.find((m) => m.key === mode).label;
    addMessage('user', document.createTextNode(question ? `${modeLabel}: ${question}` : `${modeLabel} on the code above`));
    // The question is already captured above (in `question`, for the request) and in the
    // thread (as the message just added) - clear the box so it's not left sitting there
    // looking unsent. Restored below if the request doesn't actually go through.
    const questionBeforeClear = questionInput.value;
    questionInput.value = '';
    const code = getCode();
    const lastRun = getLastRun();
    const isFirst = history.length === 0;
    const gen = convoGen; // if "New conversation" runs before this resolves, ignore the result
    busy = true;
    askBtn.disabled = true;
    cancelBtn.hidden = false;
    loadingRow.hidden = false;
    abortCtrl = new AbortController();
    try {
      const body = {
        problem_id: problem.id, code, mode, question,
        run: lastRun ? {
          stdout: lastRun.stdout, stderr: lastRun.stderr,
          exit_code: lastRun.exit_code, timed_out: Boolean(lastRun.timed_out),
        } : null,
        history,
      };
      const res = await api('POST', '/api/claude/help', body, { signal: abortCtrl.signal });
      if (gen !== convoGen) return; // stale: the conversation was reset while this was in flight
      history = [...history,
        { role: 'user', content: claudeHistoryEntry(problem, code, lastRun, question, isFirst) },
        { role: 'assistant', content: clip(res.text, 19500) }];
      addMessage('assistant', renderMarkdown(res.text));
    } catch (err) {
      if (gen === convoGen) questionInput.value = questionBeforeClear; // nothing was sent; give it back
      if (err && err.name === 'AbortError') {
        threadEl.lastElementChild?.remove(); // drop the user turn we just added; nothing was answered
      } else if (gen === convoGen) {
        addMessage('assistant', h('p', { class: 'claude-error', text: err.message || String(err) }));
      }
    } finally {
      busy = false;
      askBtn.disabled = false;
      cancelBtn.hidden = true;
      loadingRow.hidden = true;
      abortCtrl = null;
    }
  }

  const toggleBtn = h('button', {
    type: 'button', class: 'btn btn-sm', 'aria-expanded': 'false', 'aria-controls': panelId,
    onclick: () => {
      const show = panel.hidden;
      panel.hidden = !show;
      toggleBtn.setAttribute('aria-expanded', String(show));
      if (show) refresh();
    },
  }, icon('spark', 14), 'Ask Claude');

  return {
    toggleBtn,
    panel,
    destroy() {
      if (abortCtrl) abortCtrl.abort();
    },
  };
}

/** What a blank editor starts with. NeetCode 150 problems get LeetCode-style starter
 * code from the server (`starter_code`: the class and method signatures, built by
 * tools/neetcode/build_scaffolds.py); anything else gets a generic stub. */
function starterTemplate(p) {
  if (p.starter_code) return `# ${p.title}\n${p.starter_code}`;
  return `# ${p.title}\nclass Solution:\n    def solve(self):\n        pass\n\n\n# Try it:\n# print(Solution().solve())\n`;
}

/**
 * options:
 *   problem       - the problem this attempt is for (used for the title and starter code)
 *   initialCode   - code to load the editor with
 *   initialLanguage - defaults to 'python'
 *   ariaLabel     - accessible label for the editor region
 *   autosaveDraft - if true, changes are saved to the problem's draft ~800ms after
 *                   typing stops, and flushed immediately when the component is
 *                   destroyed (leaving the page). This is "your attempt" - the one
 *                   saving concept for this problem's draft, used both on the
 *                   problem page (starts from the saved draft) and in review cards'
 *                   "Code it here" panel (starts blank, but autosaves - replacing
 *                   the previous draft - as soon as you start typing).
 *   showSaveAsSolution - if true, shows "Save as my solution" (PATCHes the
 *                        problem's `solution` field). Problem page only.
 *
 * Returns { el, getCode(), getLastRun(), problem, destroy() }.
 */
function attemptEditor(options) {
  const {
    problem, initialCode = '', initialLanguage = 'python', ariaLabel = 'Code editor',
    autosaveDraft = false, showSaveAsSolution = false, alwaysDefaultHint = false,
  } = options;
  let lastRun = null;
  let destroyed = false;

  // ---------- editor
  const editorHost = h('div', { class: 'attempt-editor-wrap' });
  const editor = createEditor({
    parent: editorHost,
    doc: initialCode,
    ariaLabel,
    onRun: () => run(),
    onChange: () => {
      if (autosaveDraft) scheduleSave();
    },
  });

  // ---------- save status (autosave mode only)
  const saveStatus = h('span', { class: 'attempt-save-status', 'aria-live': 'polite' });
  let saveTimer = null;
  let saveInFlight = null;
  let dirty = false;
  function scheduleSave() {
    dirty = true;
    saveStatus.textContent = '';
    clearTimeout(saveTimer);
    saveTimer = setTimeout(flushSave, 800);
  }
  async function flushSave() {
    clearTimeout(saveTimer);
    if (!dirty || destroyed) return;
    dirty = false;
    saveStatus.textContent = 'Saving…';
    saveStatus.classList.remove('is-error');
    const code = editor.getValue();
    try {
      saveInFlight = api('PUT', `/api/problems/${problem.id}/draft`, { code, language: initialLanguage });
      await saveInFlight;
      if (!destroyed) saveStatus.textContent = 'Saved';
    } catch (err) {
      if (!destroyed) {
        saveStatus.textContent = "Couldn't save";
        saveStatus.classList.add('is-error');
      }
    }
  }
  const onBeforeUnload = () => {
    // Best-effort: fires on tab close/refresh; a debounced save may still be pending.
    if (dirty) {
      try {
        navigator.sendBeacon(`/api/problems/${problem.id}/draft`,
          new Blob([JSON.stringify({ code: editor.getValue(), language: initialLanguage })],
            { type: 'application/json' }));
      } catch { /* best effort only */ }
    }
  };
  if (autosaveDraft) window.addEventListener('beforeunload', onBeforeUnload);

  // ---------- stdin (collapsible, toggled from the More menu)
  const stdinArea = h('textarea', { rows: '3', 'aria-label': 'Standard input for the run' });
  const stdinPanel = h('div', { class: 'attempt-stdin', hidden: true },
    h('label', { class: 'block-label', text: 'Input (stdin)' }), stdinArea);
  function toggleStdin() {
    const show = stdinPanel.hidden;
    stdinPanel.hidden = !show;
    if (show) stdinArea.focus();
  }

  // ---------- output (hidden until the first run)
  const summaryEl = h('span', { class: 'attempt-summary' });
  const badgeEl = h('span', { hidden: true });
  const outHead = h('div', { class: 'attempt-output-head', role: 'status', 'aria-live': 'polite' },
    badgeEl, summaryEl);
  const stdoutEl = h('pre', { class: 'attempt-stream' });
  const stderrEl = h('pre', { class: 'attempt-stream is-stderr', hidden: true });
  const outputPanel = h('div', { class: 'attempt-output', hidden: true }, outHead, stdoutEl, stderrEl);
  const preRunHint = h('p', {
    class: 'attempt-hint',
    text: 'Run your code with Ctrl+Enter. Print results to see them here.',
  });

  function badge(text, kind) {
    badgeEl.hidden = false;
    badgeEl.className = `attempt-badge is-${kind}`;
    badgeEl.textContent = text;
  }

  async function run() {
    if (runBtn.disabled || destroyed) return;
    preRunHint.hidden = true;
    outputPanel.hidden = false;
    runBtn.disabled = true;
    runBtn.textContent = 'Running…';
    summaryEl.textContent = 'Running your code…';
    badgeEl.hidden = true;
    try {
      const result = await api('POST', '/api/run', { code: editor.getValue(), stdin: stdinArea.value });
      if (destroyed) return;
      lastRun = result;
      stdoutEl.textContent = result.stdout || 'No output.';
      stdoutEl.classList.toggle('is-empty', !result.stdout);
      stderrEl.textContent = result.stderr;
      stderrEl.hidden = !result.stderr;
      if (result.timed_out) {
        badge('Timed out', 'timeout');
        summaryEl.textContent = `Timed out after ${(result.duration_ms / 1000).toFixed(1)}s.`;
      } else if (result.exit_code === 0) {
        badge('Exit 0', 'ok');
        summaryEl.textContent = `Ran successfully in ${result.duration_ms}ms.`;
      } else {
        badge(`Exit ${result.exit_code}`, 'error');
        summaryEl.textContent = `Exited with code ${result.exit_code} after ${result.duration_ms}ms.`;
      }
      if (result.truncated) summaryEl.textContent += ' Output was truncated.';
    } catch (err) {
      if (destroyed) return;
      lastRun = null;
      badge('Error', 'error');
      summaryEl.textContent = err.message;
      toastError(err);
    } finally {
      if (!destroyed) {
        runBtn.disabled = false;
        runBtn.textContent = 'Run';
      }
    }
  }

  // ---------- toolbar: Run + Ask Claude on the left; save status + More on the right
  const runBtn = h('button', {
    type: 'button', class: 'btn btn-primary btn-sm', 'aria-keyshortcuts': 'Control+Enter',
    title: 'Run (Ctrl+Enter)', onclick: () => run(),
  }, icon('play', 14), 'Run');

  // ---------- Ask Claude (see claudeAssist() above)
  const assist = claudeAssist({
    problem, getCode: () => editor.getValue(), getLastRun: () => lastRun,
    alwaysDefaultHint, // review cards ("Code it here") always default to Hint
  });

  const more = menuButton({
    label: 'More',
    items: () => [
      {
        label: stdinPanel.hidden ? 'Input (stdin)' : 'Hide input (stdin)',
        onClick: toggleStdin,
      },
      {
        label: 'Copy code',
        onClick: async () => {
          try {
            await navigator.clipboard.writeText(editor.getValue());
            toast('Code copied', { duration: 1800 });
          } catch {
            toast("Couldn't copy automatically. Select the code and press Ctrl+C.", { type: 'error' });
          }
        },
      },
      {
        label: 'Reset to starter code',
        onClick: async () => {
          const ok = await confirmDialog({
            title: 'Reset to starter code?',
            body: 'Your current code in this editor will be replaced with the starter code. This can’t be undone.',
            confirmLabel: 'Reset',
          });
          if (!ok || destroyed) return;
          editor.setValue(starterTemplate(problem));
          if (autosaveDraft) scheduleSave();
        },
      },
      {
        label: 'Save as my solution',
        hidden: !showSaveAsSolution,
        onClick: async () => {
          if (problem.solution && problem.solution.trim()) {
            const ok = await confirmDialog({
              title: 'Replace your saved solution?',
              body: 'This overwrites the Solution saved on this problem with the code currently in the editor.',
              confirmLabel: 'Save as my solution',
            });
            if (!ok) return;
          }
          try {
            const updated = await api('PATCH', `/api/problems/${problem.id}`, { solution: editor.getValue() });
            problem.solution = updated.solution;
            toast('Saved as your solution', { type: 'success' });
          } catch (err) {
            toastError(err);
          }
        },
      },
    ],
  });

  const toolbar = h('div', { class: 'attempt-toolbar' },
    runBtn, h('span', { class: 'attempt-assist' }, assist.toggleBtn),
    h('span', { class: 'spacer' }),
    autosaveDraft ? saveStatus : null,
    more.el);

  const el = h('div', { class: 'attempt' }, toolbar, editorHost, stdinPanel, preRunHint, outputPanel, assist.panel);

  return {
    el,
    problem,
    getCode: () => editor.getValue(),
    getLastRun: () => lastRun,
    destroy() {
      if (destroyed) return;
      destroyed = true;
      clearTimeout(saveTimer);
      if (autosaveDraft) {
        window.removeEventListener('beforeunload', onBeforeUnload);
        if (dirty) flushSave(); // best effort; not awaited, the page is already navigating away
      }
      assist.destroy();
      editor.destroy();
    },
  };
}

function notesContent(p) {
  const parts = [];
  if (p.insight) {
    parts.push(h('div', { class: 'insight' },
      h('span', { class: 'label', text: 'Key insight' }),
      h('p', { text: p.insight })));
  }
  if (p.notes && p.notes.trim()) {
    parts.push(h('div', null,
      h('span', { class: 'block-label', text: 'Notes' }),
      h('div', { class: 'prewrap', text: p.notes })));
  }
  if (p.solution && p.solution.trim()) parts.push(codeBlock(p.solution, p.language));
  if (!parts.length) parts.push(h('p', { class: 'empty-note', text: 'No notes or solution saved for this problem yet.' }));
  return parts;
}

function errorState(err, retry) {
  return h('div', { class: 'card empty', role: 'alert' },
    h('h2', { text: "Couldn't load this page" }),
    h('p', { text: err && err.message ? err.message : String(err) }),
    h('div', { class: 'btn-group' },
      h('button', { type: 'button', class: 'btn btn-primary', onclick: retry }, 'Try again')));
}

function loadingEl(text = 'Loading') {
  return h('div', { class: 'loading', role: 'status', text });
}

// ================================================================ review card
/**
 * One problem to recode and rate. Used by the Today session (mode "session")
 * and by "Review now" on the problem page (mode "single").
 */
function createReviewCard(p, { mode, onRated, onSkip, onClose }) {
  const isNew = p.status === 'new';
  const targetMs = (TARGET_MINUTES[p.difficulty] || DEFAULT_TARGET_MINUTES) * 60000;
  let busy = false;
  let destroyed = false;

  // ---------- header
  const labels = [];
  if (isNew) {
    labels.push(h('span', { class: 'pill pill-new', text: 'New' }));
  } else if (p.status === 'due') {
    labels.push(h('span', { class: 'pill pill-due', text: 'Due' }));
    const diff = p.due ? calendarDiff(new Date(p.due)) : 0;
    if (diff < 0) labels.push(h('span', { class: 'pill pill-overdue', text: `${plural(-diff, 'day')} overdue` }));
  } else if (p.status === 'scheduled') {
    labels.push(h('span', { class: 'pill pill-scheduled', text: `Early review · due ${relShort(p.due)}` }));
  } else {
    labels.push(statusPill(p));
  }
  const kicker = h('div', { class: 'rc-kicker' },
    h('span', { class: 'row' }, labels),
    !isNew && p.retrievability !== null
      ? h('span', { class: 'rc-recall' }, 'Predicted recall: ', h('strong', { text: pct(p.retrievability) }))
      : null);
  const title = h('h2', { class: 'rc-title', tabindex: '-1' }, titleLink(p, 18));
  const meta = h('div', { class: 'meta-row' },
    diffBadge(p.difficulty),
    p.source ? h('span', { class: 'source', text: p.source }) : null,
    tagChips(p.tags));
  const head = h('div', { class: 'rc-head' }, kicker, title, meta);

  // ---------- prompt & instruction
  const hasPrompt = Boolean(p.prompt && p.prompt.trim());
  const prompt = hasPrompt
    ? h('div', { class: 'prompt', text: p.prompt })
    : h('div', {
      class: 'prompt is-empty',
      text: p.url
        ? 'No prompt saved — open the link above.'
        : 'No prompt saved — work from the title, or add a prompt from the problem page.',
    });
  const instruction = h('p', { class: 'instruction' }, icon('info', 16),
    h('span', { text: 'Recode it from a blank file (your editor or LeetCode). No peeking at your notes until you’re done.' }));

  // ---------- timer (timestamps, so it stays right in background tabs)
  const timer = { acc: 0, start: null, used: false };
  const elapsed = () => timer.acc + (timer.start !== null ? performance.now() - timer.start : 0);
  const timeEl = h('span', { class: 'timer-time', role: 'timer', text: '00:00' });
  const fill = h('div', { class: 'timer-fill' });
  const stateEl = h('div', { class: 'timer-state', text: 'Not started' });
  const startBtn = h('button', { type: 'button', class: 'btn btn-primary', 'aria-keyshortcuts': 'Space', onclick: () => toggleTimer() });
  const resetBtn = h('button', { type: 'button', class: 'btn', disabled: true, onclick: () => resetTimer() }, icon('reset'), 'Reset');
  const timerBox = h('div', { class: 'timer', role: 'group', 'aria-label': 'Timer' },
    h('div', { class: 'timer-readout' },
      srOnly('Elapsed time'), timeEl,
      h('span', { class: 'timer-target', text: `of ${fmtClock(targetMs)} target` })),
    h('div', { class: 'timer-meter' },
      h('div', { class: 'timer-track' }, fill),
      stateEl),
    h('div', { class: 'btn-group' }, startBtn, resetBtn));

  function paintButtons() {
    const running = timer.start !== null;
    startBtn.replaceChildren(icon(running ? 'pause' : 'play'), running ? 'Pause' : timer.used ? 'Resume' : 'Start');
    resetBtn.disabled = !timer.used;
  }
  function paintTimer() {
    const ms = elapsed();
    timeEl.textContent = fmtClock(ms);
    fill.style.width = `${Math.min(100, (ms / targetMs) * 100)}%`;
    const over = ms > targetMs;
    timerBox.classList.toggle('is-over', over);
    let text;
    if (!timer.used) text = 'Not started';
    else if (over) text = `${timer.start === null ? 'Paused · ' : ''}Over target by ${fmtClock(ms - targetMs)}`;
    else text = timer.start === null ? 'Paused' : 'Running';
    if (stateEl.textContent !== text) stateEl.textContent = text;
  }
  function toggleTimer() {
    if (destroyed) return;
    if (timer.start !== null) {
      timer.acc += performance.now() - timer.start;
      timer.start = null;
    } else {
      timer.start = performance.now();
      timer.used = true;
    }
    paintButtons();
    paintTimer();
  }
  function pauseTimer() {
    if (timer.start !== null) toggleTimer();
  }
  function resetTimer() {
    timer.acc = 0;
    timer.start = null;
    timer.used = false;
    paintButtons();
    paintTimer();
  }
  const tick = setInterval(paintTimer, 250);
  paintButtons();

  // ---------- notes
  const notesId = `notes-${p.id}-${Math.random().toString(36).slice(2, 8)}`;
  const notesPanel = h('div', { class: 'notes-panel', id: notesId, hidden: true }, notesContent(p));
  const notesBtn = h('button', {
    type: 'button', class: 'btn notes-toggle', 'aria-expanded': 'false', 'aria-controls': notesId,
    'aria-keyshortcuts': 'N', onclick: () => toggleNotes(),
  }, 'Show my notes');
  function toggleNotes() {
    if (destroyed) return;
    const show = notesPanel.hidden;
    notesPanel.hidden = !show;
    notesBtn.setAttribute('aria-expanded', String(show));
    notesBtn.textContent = show ? 'Hide my notes' : 'Show my notes';
  }

  // ---------- code it here (collapsed by default; state remembered across cards)
  const CODE_PANEL_KEY = 'dsa-review:code-it-here-open';
  let codeAttempt = null;
  const codePanelId = `code-${p.id}-${Math.random().toString(36).slice(2, 8)}`;
  const codeHint = h('p', {
    class: 'hint rc-code-hint',
    text: 'Typing here saves it as your latest attempt for this problem (replacing the previous one).',
  });
  const codePanel = h('div', { class: 'rc-code-panel', id: codePanelId, hidden: true }, codeHint);
  const codeBtn = h('button', {
    type: 'button', class: 'btn rc-code-toggle', 'aria-expanded': 'false', 'aria-controls': codePanelId,
    onclick: () => toggleCode(),
  }, 'Code it here');
  function rememberCodeOpen(open) {
    try { localStorage.setItem(CODE_PANEL_KEY, open ? '1' : '0'); } catch { /* private mode, etc. */ }
  }
  function readRememberedCodeOpen() {
    try { return localStorage.getItem(CODE_PANEL_KEY) === '1'; } catch { return false; }
  }
  function mountCodeAttempt() {
    if (codeAttempt || destroyed) return;
    // Deliberately starts blank (never the saved draft, which would spoil a recode
    // attempt), but autosaves to that same draft - "your attempt" for this problem -
    // as soon as you start typing, same as the problem page's editor.
    codeAttempt = attemptEditor({
      problem: p,
      initialCode: starterTemplate(p),
      initialLanguage: p.language || 'python',
      ariaLabel: `Recode ${p.title} here`,
      autosaveDraft: true,
      alwaysDefaultHint: true,
    });
    codePanel.append(codeAttempt.el);
  }
  function toggleCode() {
    if (destroyed) return;
    const show = codePanel.hidden;
    codePanel.hidden = !show;
    codeBtn.setAttribute('aria-expanded', String(show));
    codeBtn.textContent = show ? 'Hide code editor' : 'Code it here';
    rememberCodeOpen(show);
    if (show) mountCodeAttempt();
  }
  if (readRememberedCodeOpen()) {
    codePanel.hidden = false;
    codeBtn.setAttribute('aria-expanded', 'true');
    codeBtn.textContent = 'Hide code editor';
    mountCodeAttempt();
  }

  // ---------- rating
  const rateButtons = RATINGS.map((r) => {
    const interval = p.preview && p.preview[r.key] ? p.preview[r.key].interval_days : null;
    return h('button', {
      type: 'button', class: `rate rate-${r.key}`, dataset: { rating: r.value },
      'aria-keyshortcuts': String(r.value), onclick: () => rate(r.value),
    },
    h('span', { class: 'rate-top' },
      h('span', null,
        h('span', { class: 'rate-name', text: r.label }),
        h('span', { class: 'rate-interval' }, ' · ', srOnly('next review in '), fmtInterval(interval))),
      h('kbd', { 'aria-hidden': 'true', text: String(r.value) })),
    h('span', { class: 'rate-guide', text: r.guide }));
  });
  const rateSection = h('div', { class: 'rate-section', role: 'group', 'aria-label': 'How did it go?' },
    h('h3', { text: 'How did it go?' }),
    h('div', { class: 'rate-grid' }, rateButtons));

  async function rate(value) {
    if (busy || destroyed) return;
    busy = true;
    el.classList.add('is-busy');
    el.setAttribute('aria-busy', 'true');
    rateButtons.forEach((b) => { b.disabled = true; });
    if (skipBtn) skipBtn.disabled = true;
    const duration = timer.used ? Math.round(elapsed()) : null;
    try {
      const updated = await api('POST', `/api/problems/${p.id}/review`, { rating: value, duration_ms: duration });
      pauseTimer();
      await onRated(updated, value);
    } catch (err) {
      toastError(err);
      if (!destroyed) {
        busy = false;
        el.classList.remove('is-busy');
        el.removeAttribute('aria-busy');
        rateButtons.forEach((b) => { b.disabled = false; });
        if (skipBtn) skipBtn.disabled = false;
      }
    }
  }

  // ---------- footer
  // Shared by the button and the "S" keyboard shortcut, so both go through the same
  // busy/destroyed guard a rating is already subject to (see `rate` above).
  function doSkip() {
    if (!busy && !destroyed) onSkip();
  }
  const skipBtn = mode === 'session'
    ? h('button', { type: 'button', class: 'btn btn-sm', 'aria-keyshortcuts': 'S', onclick: () => doSkip() }, 'Skip for now')
    : null;
  const hint = (keys, label) => h('span', null, keys, ' ', label);
  const foot = h('div', { class: 'rc-foot' },
    h('div', { class: 'btn-group' },
      mode === 'session'
        ? [
          skipBtn,
          h('a', { class: 'btn btn-sm btn-ghost', href: `#/problem/${p.id}` }, 'Open details'),
        ]
        : h('button', { type: 'button', class: 'btn btn-sm', onclick: () => onClose() }, 'Cancel review')),
    h('p', { class: 'kbd-hint' },
      h('span', { text: 'Shortcuts:' }),
      hint([h('kbd', { text: '1' }), '–', h('kbd', { text: '4' })], 'rate'),
      hint(h('kbd', { text: 'Space' }), 'timer'),
      hint(h('kbd', { text: 'N' }), 'notes'),
      mode === 'session' ? hint(h('kbd', { text: 'S' }), 'skip') : null));

  const el = h('article', { class: 'card review-card', 'aria-label': `Review: ${p.title}` },
    head,
    h('div', { class: 'rc-body' },
      prompt, instruction, timerBox, notesBtn, notesPanel, codeBtn, codePanel, rateSection),
    foot);

  return {
    el,
    id: p.id,
    rate,
    toggleTimer,
    toggleNotes,
    skip: mode === 'session' ? doSkip : null,
    focusTitle: () => title.focus({ preventScroll: true }),
    destroy() {
      destroyed = true;
      clearInterval(tick);
      if (codeAttempt) codeAttempt.destroy();
    },
  };
}

// Keyboard shortcuts for whichever review card is on screen.
document.addEventListener('keydown', (e) => {
  const card = state.activeReview;
  if (!card || e.defaultPrevented || e.ctrlKey || e.metaKey || e.altKey) return;
  if (isTypingTarget(e.target) || document.querySelector('dialog[open]')) return;
  const key = e.key;
  if (key >= '1' && key <= '4' && key.length === 1) {
    e.preventDefault();
    if (!e.repeat) card.rate(Number(key));
  } else if (key === ' ' || key === 'Spacebar') {
    e.preventDefault();
    if (!e.repeat) card.toggleTimer();
  } else if (key === 'n' || key === 'N') {
    e.preventDefault();
    if (!e.repeat) card.toggleNotes();
  } else if ((key === 's' || key === 'S') && card.skip) {
    e.preventDefault();
    if (!e.repeat) card.skip();
  }
});
// A focused button would otherwise also "click" on Space keyup after we handled it on keydown.
document.addEventListener('keyup', (e) => {
  if (!state.activeReview || (e.key !== ' ' && e.key !== 'Spacebar')) return;
  if (isTypingTarget(e.target) || document.querySelector('dialog[open]')) return;
  e.preventDefault();
});

function showReviewToast(updated, context, deck) {
  // The review's id goes along with Undo, so a pop-up whose review was already undone
  // (e.g. from the problem page) can't remove an older review by mistake.
  const reviewId = updated.history && updated.history.length ? updated.history[0].id : undefined;
  toast(`“${updated.title}”: next review ${nextReviewPhrase(updated.due)}`, {
    type: 'success',
    actionLabel: 'Undo',
    onAction: () => undoReview(updated.id, context, reviewId, deck),
  });
}

/** `deck` is the deck the review actually happened in, captured when the toast was
 * shown - never read from whatever session happens to be on screen when Undo is
 * clicked, since the user may have navigated to a different deck's session (or away
 * from Today entirely) in the meantime. */
async function undoReview(pid, context, reviewId, deck) {
  try {
    const p = await api('POST', `/api/problems/${pid}/undo`, reviewId === undefined ? {} : { review_id: reviewId });
    toast(`Review undone for “${p.title}”`);
    const s = deck ? state.sessions[deck] : null;
    if (context === 'session' && s) {
      s.order = [pid, ...s.order.filter((x) => x !== pid)];
      s.done = Math.max(0, s.done - 1);
    }
    await refreshSummaries();
    if (state.today && state.today.deck === deck) {
      await syncQueue(state.today);
      renderToday(state.today, { focusCard: true });
    } else if (state.detail && state.detail.id === pid) {
      state.detail.p = p;
      state.detail.mode = 'view';
      renderDetail(state.detail);
    }
  } catch (err) {
    toastError(err);
  }
}

// ================================================================ Today / NeetCode review view
// Both #/today (deck 'main') and #/neetcode/review (deck 'neetcode') are the same view,
// parameterized by deck: separate summary, session, tag filter and API scope per deck.
const DECK_LABEL = { main: 'Today', neetcode: 'NeetCode review' };

async function viewToday(main, { seq, deck = 'main' }) {
  const ncNudgeEl = h('div', null);
  const statsEl = h('section', { class: 'stats is-loading', 'aria-label': 'Your progress' });
  renderStats(statsEl, null, null);
  const sessionEl = h('section', { class: 'session', 'aria-labelledby': 'session-title' }, loadingEl('Loading your queue'));
  const forecastEl = h('section', { class: 'card forecast', 'aria-labelledby': 'forecast-title' });
  const sub = deck === 'neetcode'
    ? 'Review session for your NeetCode 150 deck.'
    : studyNow().toLocaleDateString(undefined, { weekday: 'long', month: 'long', day: 'numeric' });
  main.append(
    pageHead(DECK_LABEL[deck], sub),
    deck === 'main' ? ncNudgeEl : null,
    statsEl, sessionEl, forecastEl,
  );
  forecastEl.hidden = true;

  const ctrl = { seq, deck, statsEl, sessionEl, forecastEl, queue: null, card: null, syncToken: 0, cardToken: 0 };
  state.today = ctrl;
  onCleanup(() => {
    if (ctrl.card) ctrl.card.destroy();
    if (state.today === ctrl) state.today = null;
  });

  const [mainSummary, tags] = await Promise.all([refreshSummary(deck), loadTags()]);
  if (!isCurrent(seq)) return;
  if (state.todayTags[deck] && !tags.some((t) => t.tag === state.todayTags[deck])) state.todayTags[deck] = '';
  await syncQueue(ctrl);
  if (!isCurrent(seq)) return;
  renderToday(ctrl);

  // When NeetCode problems aren't already folded into this Today session, nudge
  // toward them separately if there's anything waiting there.
  if (deck === 'main' && !mainSummary.settings.neetcode_in_main) {
    refreshSummary('neetcode').then((ncSummary) => {
      if (!isCurrent(seq)) return;
      renderNeetcodeNudge(ncNudgeEl, ncSummary);
    }).catch(() => {});
  }
}

function renderNeetcodeNudge(el, ncSummary) {
  const c = ncSummary.counts;
  if (c.due === 0 && c.new === 0) {
    el.replaceChildren();
    return;
  }
  const parts = [];
  if (c.due) parts.push(`${c.due} due`);
  if (c.new) parts.push(`${c.new} new`);
  el.replaceChildren(h('div', { class: 'card nc-nudge' },
    h('span', { class: 'nc-nudge-text' }, h('strong', { text: 'NeetCode: ' }), parts.join(' · ')),
    h('a', { class: 'btn btn-primary btn-sm', href: '#/neetcode/review' }, 'Start NeetCode review')));
}

/** Fetch the queue (and optionally the summary) and merge it into the in-memory session. */
async function syncQueue(ctrl, { withSummary = false } = {}) {
  const token = ++ctrl.syncToken;
  const tag = state.todayTags[ctrl.deck];
  const qs = new URLSearchParams({ deck: ctrl.deck });
  if (tag) qs.set('tag', tag);
  const [queue] = await Promise.all([
    api('GET', `/api/queue?${qs}`),
    withSummary ? refreshSummary(ctrl.deck) : null,
  ]);
  if (token !== ctrl.syncToken) return false;
  ctrl.queue = queue;
  const items = [...queue.due, ...queue.new];
  const ids = new Set(items.map((p) => p.id));
  const summary = state.summaries[ctrl.deck];
  const key = `${tag}|${summary ? summary.day_start : ''}`;
  let s = state.sessions[ctrl.deck];
  if (!s || s.key !== key) {
    s = { key, tag, order: [], done: 0 };
    state.sessions[ctrl.deck] = s;
  }
  s.order = s.order.filter((id) => ids.has(id));
  for (const p of items) if (!s.order.includes(p.id)) s.order.push(p.id);
  return true;
}

function renderToday(ctrl, { focusCard = false } = {}) {
  if (!isCurrent(ctrl.seq)) return;
  const summary = state.summaries[ctrl.deck];
  ctrl.statsEl.hidden = summary.counts.total === 0;
  renderStats(ctrl.statsEl, summary, ctrl.queue);
  renderSession(ctrl, { focusCard });
  renderForecast(ctrl.forecastEl, summary);
}

function renderStats(el, summary, queue) {
  el.classList.toggle('is-loading', !summary);
  // `sub` is usually a string, but can be an array of strings/nodes (see the "New
  // available" tile below, which adds a link) - h()'s children handle both the same way.
  const tile = (label, value, unit, sub, primary) => h('div', { class: `stat${primary ? ' is-primary' : ''}` },
    h('div', { class: 'stat-label', text: label }),
    h('div', { class: 'stat-value' }, String(value), unit ? h('span', { class: 'unit', text: unit }) : null),
    h('div', { class: 'stat-sub' }, sub || ' '));
  if (!summary || !queue) {
    el.replaceChildren(...['Due today', 'New available', 'Reviewed today', 'Streak', '30-day recall']
      .map((l) => tile(l, '–', '', '')));
    return;
  }
  const c = summary.counts;
  const newAvail = Math.min(queue.new_left_today, c.new);
  let newSub = c.new ? `${c.new} waiting` : 'None waiting';
  if (c.new && queue.new_left_today === 0) {
    newSub = [`Limit reached · ${c.new} waiting · `, h('a', { href: '#/settings' }, 'Raise the limit in Settings')];
  }
  const rate = summary.recall_rate_30d;
  el.replaceChildren(
    tile('Due today', c.due, '', c.total ? `of ${plural(c.total, 'problem')}` : 'No problems yet', true),
    tile('New available', newAvail, '', newSub),
    tile('Reviewed today', c.reviewed_today, '', c.reviewed_today ? 'Nice work' : 'Nothing yet'),
    tile('Streak', summary.streak_days, summary.streak_days === 1 ? 'day' : 'days',
      summary.streak_days ? 'Days in a row' : 'Review to start one'),
    tile('30-day recall', rate === null ? '—' : pct(rate), '',
      rate === null ? 'No repeat reviews yet' : `across ${plural(summary.reviews_30d, 'review')}`),
  );
}

function renderSession(ctrl, { focusCard = false } = {}) {
  const el = ctrl.sessionEl;
  const s = state.sessions[ctrl.deck];
  const summary = state.summaries[ctrl.deck];
  if (ctrl.card) {
    ctrl.card.destroy();
    ctrl.card = null;
  }
  state.activeReview = null;

  if (summary.counts.total === 0) {
    el.replaceChildren(welcomeState(ctrl.deck));
    return;
  }

  const total = s.done + s.order.length;
  const bar = h('div', { class: 'session-bar' },
    h('div', { class: 'session-progress' },
      h('h2', { id: 'session-title', text: 'Review session' }),
      s.order.length
        ? [
          h('span', { class: 'progress-count', text: `Problem ${s.done + 1} of ${total}` }),
          h('div', {
            class: 'progress-track', role: 'progressbar', 'aria-label': 'Session progress',
            'aria-valuemin': '0', 'aria-valuemax': String(total), 'aria-valuenow': String(s.done),
          }, progressFill(total ? s.done / total : 0)),
        ]
        : null),
    state.tags.length ? tagFilter(ctrl) : null);

  if (!s.order.length) {
    el.replaceChildren(bar, caughtUpState(ctrl));
    return;
  }

  const holder = h('div', { class: 'card review-card' }, loadingEl('Loading problem'));
  el.replaceChildren(bar, holder);
  showCurrent(ctrl, holder, focusCard);
}

function progressFill(fraction) {
  const fill = h('div', { class: 'progress-fill' });
  fill.style.width = `${Math.round(fraction * 100)}%`;
  return fill;
}

function tagFilter(ctrl) {
  const select = h('select', { id: 'today-tag', class: 'compact' },
    h('option', { value: '' }, 'All tags'),
    state.tags.map((t) => h('option', { value: t.tag }, `${t.tag} (${t.count})`)));
  select.value = state.todayTags[ctrl.deck];
  select.addEventListener('change', async () => {
    state.todayTags[ctrl.deck] = select.value;
    ctrl.sessionEl.setAttribute('aria-busy', 'true');
    try {
      if (await syncQueue(ctrl)) renderToday(ctrl);
      const again = document.getElementById('today-tag');
      if (again) again.focus();
    } catch (err) {
      toastError(err);
    } finally {
      ctrl.sessionEl.removeAttribute('aria-busy');
    }
  });
  return h('div', { class: 'field-inline' }, h('label', { for: 'today-tag', text: 'Focus on' }), select);
}

/**
 * Load the session's current problem into `holder` (a loading placeholder).
 * With `replacing` (the card controller already on screen), the new card takes
 * that card's place once it has loaded, without scrolling: used by "Skip for now"
 * so the page doesn't jump.
 */
async function showCurrent(ctrl, holder, focusCard, { replacing = null } = {}) {
  const s = state.sessions[ctrl.deck];
  const id = s.order[0];
  const token = ++ctrl.cardToken;
  let p;
  try {
    p = await api('GET', `/api/problems/${id}`);
  } catch (err) {
    if (token !== ctrl.cardToken || !isCurrent(ctrl.seq)) return;
    if (err.status === 404) {
      s.order = s.order.filter((x) => x !== id);
      renderSession(ctrl);
    } else if (replacing) {
      toastError(err);
      renderSession(ctrl); // fall back to a normal re-render, which shows a Retry
    } else {
      holder.replaceChildren(errorState(err, () => renderSession(ctrl)));
    }
    return;
  }
  if (token !== ctrl.cardToken || !isCurrent(ctrl.seq) || s.order[0] !== id) return;
  if (replacing) replacing.destroy();

  const card = createReviewCard(p, {
    mode: 'session',
    onRated: async (updated) => {
      s.order = s.order.filter((x) => x !== updated.id);
      s.done += 1;
      showReviewToast(updated, 'session', ctrl.deck);
      try {
        await syncQueue(ctrl, { withSummary: true });
      } catch (err) {
        toastError(err);
      }
      renderToday(ctrl, { focusCard: true });
    },
    onSkip: () => {
      if (s.order.length < 2) {
        toast('This is the last problem in the session. Rate it when you’re ready.');
        return;
      }
      s.order.push(s.order.shift());
      // Swap the card in place: the current card stays on screen (dimmed and inert,
      // so its buttons and shortcuts can't fire) until the next problem has loaded,
      // then the new card takes its exact spot. Nothing else re-renders and the page
      // doesn't scroll. (The progress bar doesn't change on a skip.)
      const current = ctrl.card;
      state.activeReview = null;
      // If focus is still inside the card (e.g. the Skip button itself), blur it first -
      // otherwise the browser can do its own focus-repair scroll once the element below
      // becomes inert, undoing the "stay in place" swap this is all about.
      if (current.el.contains(document.activeElement)) document.activeElement.blur();
      current.el.classList.add('is-busy');
      current.el.inert = true;
      current.el.setAttribute('aria-busy', 'true');
      showCurrent(ctrl, current.el, true, { replacing: current });
    },
  });
  holder.replaceWith(card.el);
  ctrl.card = card;
  state.activeReview = card;
  if (focusCard) {
    if (!replacing) revealTop(ctrl.sessionEl);
    // The Skip / rating button that had focus is gone now; move focus to the new
    // problem's title (focusTitle never scrolls).
    const active = document.activeElement;
    if (!active || active === document.body || !active.isConnected) card.focusTitle();
  }
}

function caughtUpState(ctrl) {
  const summary = state.summaries[ctrl.deck];
  const q = ctrl.queue;
  const tag = state.todayTags[ctrl.deck];
  const s = state.sessions[ctrl.deck];
  const isNc = ctrl.deck === 'neetcode';
  const f = summary.forecast;
  const nextIdx = f.findIndex((d, i) => i > 0 && d.count > 0);
  let nextText;
  if (nextIdx === -1) {
    nextText = 'Nothing scheduled in the next 2 weeks';
  } else {
    const d = parseLocalDate(f[nextIdx].date);
    const when = nextIdx === 1
      ? 'tomorrow'
      : `${d.toLocaleDateString(undefined, { weekday: 'long', month: 'short', day: 'numeric' })} (in ${nextIdx} days)`;
    nextText = `Next review ${when} · ${plural(f[nextIdx].count, 'problem')}`;
  }

  const lines = [];
  if (tag) {
    lines.push(`Nothing left to practice for the tag “${tag}”.`);
    if (summary.counts.due > 0) {
      lines.push(`${plural(summary.counts.due, 'problem')} with other tags ${summary.counts.due === 1 ? 'is' : 'are'} still due.`);
    }
  } else {
    lines.push(s.done
      ? `You worked through ${plural(s.done, 'problem')} this session. Your schedule is up to date.`
      : 'Nothing is due right now. Your schedule is up to date.');
  }
  if (q.new_waiting > 0 && q.new_left_today === 0) {
    const limit = isNc ? summary.settings.neetcode_new_per_day : summary.settings.new_per_day;
    lines.push(`${plural(q.new_waiting, 'new problem')} ${q.new_waiting === 1 ? 'is' : 'are'} waiting, but today’s limit of ${limit} is used up. You can raise it in Settings.`);
  }

  const addLink = isNc
    ? h('a', { class: `btn${tag ? '' : ' btn-primary'}`, href: '#/neetcode' }, icon('plus'), 'Pick a new problem')
    : h('a', { class: `btn${tag ? '' : ' btn-primary'}`, href: '#/add' }, icon('plus'), 'Add a problem');
  const browseLink = isNc
    ? null
    : h('a', { class: 'btn', href: '#/library' }, 'Browse library');

  return h('div', { class: 'card empty' },
    h('div', { class: 'empty-icon', 'aria-hidden': 'true' }, icon('done', 28)),
    h('h2', { tabindex: '-1', text: tag ? 'Nothing left for this tag' : 'All caught up' }),
    lines.map((t) => h('p', { text: t })),
    h('span', { class: 'next-up', text: nextText }),
    h('div', { class: 'btn-group' },
      tag
        ? h('button', {
          type: 'button',
          class: 'btn btn-primary',
          onclick: async () => {
            state.todayTags[ctrl.deck] = '';
            try {
              if (await syncQueue(ctrl)) renderToday(ctrl);
            } catch (err) {
              toastError(err);
            }
          },
        }, 'Show all tags')
        : null,
      addLink, browseLink));
}

function welcomeState(deck = 'main') {
  if (deck === 'neetcode') {
    return h('div', { class: 'card welcome' },
      h('h2', { text: 'Your NeetCode 150 deck is empty' }),
      h('p', { text: 'Add problems from the NeetCode 150 roadmap (or move some over from your main library) to start a review schedule just for them.' }),
      h('div', { class: 'btn-group' },
        h('a', { class: 'btn btn-primary btn-lg', href: '#/neetcode' }, icon('plus'), 'Browse NeetCode 150')));
  }
  const step = (title, text) => h('li', null, h('strong', { text: title }), h('span', { text }));
  return h('div', { class: 'card welcome' },
    h('h2', { text: 'Welcome to DSA Review' }),
    h('p', { text: 'Solving a problem once doesn’t mean you can solve it in an interview three weeks later. DSA Review brings each problem back right before you’d forget it, so you can recode it from scratch.' }),
    h('ol', { class: 'steps' },
      step('Solve a problem', 'On LeetCode, NeetCode, or wherever you practice.'),
      step('Add it here', 'Save the prompt, your key insight and solution, and rate how it went.'),
      step('Recode it when it’s due', 'Come back to Today, solve it again from a blank file, and rate yourself.')),
    h('div', { class: 'btn-group' },
      h('a', { class: 'btn btn-primary btn-lg', href: '#/add' }, icon('plus'), 'Add your first problem'),
      h('a', { class: 'btn btn-lg', href: '#/settings' }, 'Import a backup')));
}

// ---------------------------------------------------------------- forecast chart
function renderForecast(el, summary) {
  const forecast = summary.forecast || [];
  if (!summary.counts.total || !forecast.length) {
    el.hidden = true;
    return;
  }
  el.hidden = false;
  const total = forecast.reduce((a, d) => a + d.count, 0);
  el.replaceChildren(
    h('div', { class: 'card-head' },
      h('div', null,
        h('h2', { id: 'forecast-title', text: 'Review forecast' }),
        h('p', { text: 'Problems coming due each day over the next 2 weeks. Today, the darker bar, includes anything overdue.' })),
      h('span', { class: 'muted small', text: `${plural(total, 'review')} in 14 days` })),
    forecastChart(forecast, total),
  );
}

function forecastChart(forecast, total) {
  const PLOT_H = 100;
  const max = Math.max(1, ...forecast.map((d) => d.count));
  const wrap = h('div', { class: 'chart' });
  const tip = h('div', { class: 'chart-tip', hidden: true, 'aria-hidden': 'true' });
  const plot = h('div', {
    class: 'chart-plot',
    role: 'group',
    'aria-label': `Reviews due per day for the next 14 days, ${plural(total, 'review')} in total. Use the arrow keys to move between days.`,
  });
  const xAxis = h('div', { class: 'chart-x', 'aria-hidden': 'true' });
  const rows = [];

  forecast.forEach((d, i) => {
    const date = parseLocalDate(d.date);
    const isToday = i === 0;
    const longDate = date.toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' });
    const countText = plural(d.count, 'problem');
    const label = `${isToday ? 'Today, ' : ''}${longDate}: ${countText}${isToday ? ' (includes overdue)' : ''}`;
    const col = h('div', {
      class: `chart-col${isToday ? ' is-today' : ''}`,
      role: 'img',
      'aria-label': label,
      tabindex: isToday ? '0' : '-1',
    });
    let bar = null;
    let valEl = null;
    if (d.count) {
      valEl = h('span', { class: 'chart-val', text: String(d.count) });
      col.append(valEl);
      bar = h('div', { class: 'chart-bar' });
      bar.style.height = `${Math.max(4, Math.round((d.count / max) * PLOT_H))}px`;
      col.append(bar);
    }
    const show = () => {
      tip.replaceChildren(h('strong', { text: countText }), isToday ? `Today · ${longDate}` : longDate);
      tip.hidden = false;
      const center = col.offsetLeft + col.offsetWidth / 2;
      // Sit just above the value label (or the baseline for empty days); may overlap the card header.
      const top = valEl ? valEl.offsetTop - 2 : col.offsetHeight - 4;
      const half = tip.offsetWidth / 2;
      tip.style.left = `${Math.min(Math.max(center, half), wrap.offsetWidth - half)}px`;
      tip.style.top = `${top}px`;
    };
    const hide = () => { tip.hidden = true; };
    col.addEventListener('pointerenter', show);
    col.addEventListener('pointerleave', hide);
    col.addEventListener('focus', show);
    col.addEventListener('blur', hide);
    plot.append(col);

    xAxis.append(h('span', { class: isToday ? 'is-today' : null },
      h('b', null, isToday
        ? [
          h('span', { class: 'wd-long', text: 'Today' }),
          h('span', { class: 'wd-short', text: date.toLocaleDateString(undefined, { weekday: 'narrow' }) }),
        ]
        : [
          h('span', { class: 'wd-long', text: date.toLocaleDateString(undefined, { weekday: 'short' }) }),
          h('span', { class: 'wd-short', text: date.toLocaleDateString(undefined, { weekday: 'narrow' }) }),
        ]),
      String(date.getDate())));
    rows.push(h('tr', null,
      h('td', { text: isToday ? `Today, ${longDate} (includes overdue)` : longDate }),
      h('td', { class: 'num', text: String(d.count) })));
  });

  plot.addEventListener('keydown', (e) => {
    const cols = [...plot.children];
    const idx = cols.indexOf(document.activeElement);
    if (idx < 0) return;
    let next = idx;
    if (e.key === 'ArrowRight') next = Math.min(cols.length - 1, idx + 1);
    else if (e.key === 'ArrowLeft') next = Math.max(0, idx - 1);
    else if (e.key === 'Home') next = 0;
    else if (e.key === 'End') next = cols.length - 1;
    else return;
    e.preventDefault();
    cols[idx].tabIndex = -1;
    cols[next].tabIndex = 0;
    cols[next].focus();
  });

  const table = h('details', { class: 'table-view' },
    h('summary', null, 'Show as table'),
    h('table', { class: 'data-table' },
      h('caption', { class: 'sr-only', text: 'Reviews due per day' }),
      h('thead', null, h('tr', null,
        h('th', { scope: 'col', text: 'Day' }),
        h('th', { scope: 'col', class: 'num', text: 'Problems due' }))),
      h('tbody', null, rows)));

  wrap.append(plot, xAxis, tip);
  return h('div', null, wrap, h('div', { class: 'chart-foot' }, table));
}

// ================================================================ problem form (add & edit)
let formSeq = 0;
const SERVER_FIELD_ALIASES = { first_rating: 'rating', duration_ms: 'duration' };

// Lazily fetched once and cached, for the Add/Edit form's "recognize a NeetCode 150 URL"
// autofill (and reusable by anything else that just needs the flat problem list).
let neetcodeTrackerPromise = null;
function fetchNeetcodeProblems() {
  if (!neetcodeTrackerPromise) {
    neetcodeTrackerPromise = api('GET', '/api/neetcode').catch((err) => {
      neetcodeTrackerPromise = null;
      throw err;
    });
  }
  return neetcodeTrackerPromise.then((t) => t.problems);
}
function titleCaseSlug(slug) {
  return slug.split('-').filter(Boolean).map((w) => w[0].toUpperCase() + w.slice(1)).join(' ');
}

function problemForm({ initial = {}, tags = [], withRating = false, submitText = 'Save', anotherText, onSubmit, onCancel }) {
  const uid = `pf${++formSeq}`;
  const fid = (name) => `${uid}-${name}`;
  const fields = {};

  function field(name, labelText, control, { hint, required, optional, extra, className } = {}) {
    control.id = fid(name);
    control.name = name;
    const err = h('p', { class: 'field-error', id: fid(`${name}-err`), hidden: true });
    const hintEl = hint ? h('p', { class: 'hint', id: fid(`${name}-hint`), text: hint }) : null;
    control.setAttribute('aria-describedby', [hintEl ? hintEl.id : null, err.id].filter(Boolean).join(' '));
    fields[name] = { control, err };
    return h('div', { class: `field${className ? ` ${className}` : ''}` },
      h('label', { for: control.id },
        labelText,
        required ? h('span', { class: 'req', 'aria-hidden': 'true', text: ' *' }) : null,
        optional ? h('span', { class: 'opt', text: 'optional' }) : null),
      control, hintEl, extra, err);
  }

  const val = (k, fallback = '') => (initial[k] === undefined || initial[k] === null ? fallback : initial[k]);
  const title = h('input', { type: 'text', maxlength: '200', autocomplete: 'off', required: true, value: val('title') });
  const deck = h('select', null,
    h('option', { value: 'main' }, 'Main'),
    h('option', { value: 'neetcode' }, 'NeetCode'));
  deck.value = val('deck', 'main');
  const url = h('input', { type: 'url', maxlength: '2000', inputmode: 'url', autocomplete: 'off', placeholder: 'https://leetcode.com/problems/two-sum/', value: val('url') });
  const sourceList = h('datalist', { id: fid('sources') }, SOURCES.map((s) => h('option', { value: s })));
  const source = h('input', { type: 'text', maxlength: '100', list: sourceList.id, autocomplete: 'off', placeholder: 'LeetCode', value: val('source') });
  const difficulty = h('select', null,
    h('option', { value: '' }, '—'),
    ['Easy', 'Medium', 'Hard'].map((d) => h('option', { value: d }, d)));
  difficulty.value = val('difficulty');
  const tagsInput = h('input', { type: 'text', autocomplete: 'off', placeholder: 'arrays, hash-map', value: (initial.tags || []).join(', ') });
  const prompt = h('textarea', { rows: '6', maxlength: '20000', placeholder: 'Paste the problem statement or a short summary you can solve from' });
  prompt.value = val('prompt');
  const insight = h('input', { type: 'text', maxlength: '1000', autocomplete: 'off', placeholder: 'e.g. Hash map of value → index; look up target − x', value: val('insight') });
  const notes = h('textarea', { rows: '5', placeholder: 'Approach, time and space complexity, edge cases, mistakes to avoid' });
  notes.value = val('notes');
  const solution = h('textarea', { class: 'code-input', rows: '12', spellcheck: 'false', autocapitalize: 'off', autocomplete: 'off', wrap: 'off', placeholder: 'def two_sum(nums, target):\n    ...' });
  solution.value = val('solution');
  const langList = h('datalist', { id: fid('langs') }, LANGUAGES.map((l) => h('option', { value: l })));
  const language = h('input', { type: 'text', maxlength: '40', list: langList.id, autocomplete: 'off', value: val('language', 'python') });

  // Tab inserts 4 spaces in the solution. Esc, then Tab, moves focus on (no keyboard trap).
  let escArmed = false;
  solution.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      escArmed = true;
      return;
    }
    if (e.key === 'Tab' && !e.shiftKey && !e.ctrlKey && !e.altKey && !e.metaKey && !escArmed) {
      e.preventDefault();
      let inserted = false;
      try {
        inserted = document.execCommand('insertText', false, '    ');
      } catch {
        inserted = false;
      }
      if (!inserted) solution.setRangeText('    ', solution.selectionStart, solution.selectionEnd, 'end');
    }
    escArmed = false;
  });

  // Tag suggestion chips toggle a tag in the comma-separated input.
  const chipBox = h('div', { class: 'chips', role: 'group', 'aria-label': 'Your existing tags' });
  function renderChips(list) {
    chipBox.replaceChildren(...list.slice(0, 40).map((t) => h('button', {
      type: 'button', class: 'chip', dataset: { tag: t.tag }, 'aria-pressed': 'false',
      onclick: () => {
        const cur = parseTagList(tagsInput.value);
        const idx = cur.findIndex((x) => slugTag(x) === t.tag);
        if (idx >= 0) cur.splice(idx, 1);
        else cur.push(t.tag);
        tagsInput.value = cur.join(', ');
        syncChips();
      },
    }, t.tag)));
    chipBox.hidden = list.length === 0;
    syncChips();
  }
  function syncChips() {
    const cur = new Set(parseTagList(tagsInput.value).map(slugTag));
    for (const b of chipBox.children) b.setAttribute('aria-pressed', String(cur.has(b.dataset.tag)));
  }
  tagsInput.addEventListener('input', syncChips);
  renderChips(tags);

  // "How did it go?" group
  let ratingGroup = null;
  let durationWrap = null;
  let duration = null;
  const ratingName = fid('rating');
  const ratingErr = h('p', { class: 'field-error', id: fid('rating-err'), hidden: true });
  if (withRating) {
    const choice = (value, titleText, desc, cls) => h('label', { class: `choice${cls ? ` ${cls}` : ''}` },
      h('input', { type: 'radio', name: ratingName, value }),
      h('span', null,
        h('span', { class: 'choice-title', text: titleText }),
        h('span', { class: 'choice-desc', text: desc })));
    const notYet = choice('', 'I haven’t solved it yet', 'Add it to my new queue. It will show up in Today as a new problem.');
    notYet.querySelector('input').checked = true;
    duration = h('input', { type: 'number', min: '0', max: '1440', step: 'any', inputmode: 'decimal', placeholder: 'e.g. 22' });
    durationWrap = field('duration', 'Time taken (minutes)', duration, { optional: true, className: 'duration-field' });
    durationWrap.hidden = true;
    ratingGroup = h('fieldset', { class: 'field', 'aria-describedby': ratingErr.id },
      h('legend', { text: 'How did it go?' }),
      h('div', { class: 'choice-list' },
        notYet,
        h('p', { class: 'subgroup-label', text: 'I just solved it:' }),
        h('div', { class: 'choice-grid' },
          RATINGS.map((r) => choice(String(r.value), r.label, r.guide, `c-${r.key}`)))),
      ratingErr);
    ratingGroup.addEventListener('change', () => {
      const solved = Boolean(selectedRating());
      durationWrap.hidden = !solved;
      if (!solved) duration.value = '';
    });
  }
  function selectedRating() {
    const input = ratingGroup && ratingGroup.querySelector(`input[name="${ratingName}"]:checked`);
    return input && input.value ? Number(input.value) : null;
  }

  // ---------- "More details": Deck, Source, Prompt, Notes, Solution, Language.
  // Closed by default on Add; auto-opened when any of these already has a value
  // (editing an existing problem, or a NeetCode prefill).
  const moreHasValue = val('deck', 'main') !== 'main' || Boolean(val('source')) || Boolean(val('prompt'))
    || Boolean(val('notes')) || Boolean(val('solution')) || val('language', 'python') !== 'python';
  const moreDetails = h('details', { class: 'more-details', open: moreHasValue },
    h('summary', { class: 'section-summary' }, 'More details'),
    h('div', { class: 'details-body stack' },
      field('deck', 'Deck', deck, {
        hint: 'Which review deck this problem belongs to. NeetCode problems get their own review session.',
      }),
      h('div', null, field('source', 'Source', source, { optional: true }), sourceList),
      field('prompt', 'Prompt', prompt, { hint: 'Enough detail that you could solve it again without the original page.' }),
      field('notes', 'Notes', notes, { optional: true }),
      field('solution', 'Solution', solution, {
        optional: true,
        hint: 'Tab inserts 4 spaces. To leave the field with the keyboard, press Esc and then Tab.',
      }),
      h('div', null, field('language', 'Language', language), langList)));

  // ---------- autofill from a pasted LeetCode link (never overwrites a field the user
  // has typed into themselves - only a field that's still empty, or still holds exactly
  // what a *previous* autofill wrote there).
  const urlAutofillNote = h('p', { class: 'hint', hidden: true });
  // What autofill itself last wrote into each field, so a later autofill (for a different
  // URL) - or a revert, below - only ever touches a field the user hasn't since edited.
  const lastAutofill = { title: null, difficulty: null, tags: null, deck: null };
  const stillAutofilled = (current, remembered, emptyValue) => current === emptyValue || current === remembered;

  /** Called when the URL no longer points at a matched NeetCode 150 problem: undo
   * whatever a previous match autofilled into deck/tags/difficulty (title is left
   * alone - it's still a reasonable guess even without a NeetCode match), but only
   * fields the user hasn't edited since. */
  function revertNcAutofill() {
    if (stillAutofilled(difficulty.value, lastAutofill.difficulty, '')) difficulty.value = '';
    if (stillAutofilled(tagsInput.value.trim(), lastAutofill.tags, '')) {
      tagsInput.value = '';
      syncChips();
    }
    if (stillAutofilled(deck.value, lastAutofill.deck, 'main')) deck.value = 'main';
    lastAutofill.difficulty = lastAutofill.tags = lastAutofill.deck = null;
    urlAutofillNote.hidden = true;
  }

  const onUrlInput = debounce(async () => {
    const m = /leetcode\.com\/problems\/([a-z0-9-]+)/i.exec(url.value);
    if (!m) {
      revertNcAutofill();
      return;
    }
    const slug = m[1].toLowerCase();
    let matched = null;
    try {
      const problems = await fetchNeetcodeProblems();
      matched = problems.find((p) => p.slug === slug) || null;
    } catch {
      matched = null;
    }
    // Resolve the real NeetCode title first - Title-Casing the slug gets plenty of
    // titles wrong ("lru-cache" -> "Lru Cache" instead of "LRU Cache"), so that's only
    // a fallback for when there's no match at all.
    const guessedTitle = matched ? matched.title : titleCaseSlug(slug);
    if (stillAutofilled(title.value.trim(), lastAutofill.title, '')) {
      title.value = guessedTitle;
      lastAutofill.title = guessedTitle;
    }
    if (!matched) {
      revertNcAutofill();
      return;
    }
    if (stillAutofilled(difficulty.value, lastAutofill.difficulty, '')) {
      difficulty.value = matched.difficulty;
      lastAutofill.difficulty = matched.difficulty;
    }
    if (stillAutofilled(tagsInput.value.trim(), lastAutofill.tags, '')) {
      const tagList = [slugTag(matched.category), 'neetcode-150'];
      if (matched.blind75) tagList.push('blind-75');
      tagsInput.value = tagList.join(', ');
      lastAutofill.tags = tagsInput.value;
      syncChips();
    }
    if (stillAutofilled(deck.value, lastAutofill.deck, 'main')) {
      deck.value = 'neetcode';
      lastAutofill.deck = 'neetcode';
    }
    moreDetails.open = true;
    urlAutofillNote.hidden = false;
    urlAutofillNote.textContent = `Recognized NeetCode 150 #${matched.id} — added to your NeetCode deck. Change under More details.`;
  }, 300);
  url.addEventListener('input', onUrlInput);

  const formError = h('div', { class: 'form-error', role: 'alert', hidden: true });
  const submitBtn = h('button', { type: 'submit', class: 'btn btn-primary btn-lg' }, submitText);
  const anotherBtn = anotherText ? h('button', { type: 'button', class: 'btn btn-lg', onclick: () => submit(true) }, anotherText) : null;
  const cancelBtn = onCancel ? h('button', { type: 'button', class: 'btn btn-ghost btn-lg', onclick: () => onCancel() }, 'Cancel') : null;

  const form = h('form', { class: 'form', novalidate: true, 'aria-label': withRating ? 'Add a problem' : 'Edit problem' },
    formError,
    field('title', 'Title', title, { required: true }),
    h('div', { class: 'form-grid' },
      field('url', 'Link', url, { optional: true, extra: urlAutofillNote }),
      field('difficulty', 'Difficulty', difficulty)),
    field('tags', 'Tags', tagsInput, {
      hint: 'Separate with commas. Click a tag below to add or remove it.',
      extra: chipBox,
    }),
    field('insight', 'Key insight', insight, { hint: 'The one idea that unlocks the problem.' }),
    withRating ? [h('hr', { class: 'divider' }), ratingGroup, durationWrap] : null,
    moreDetails,
    h('div', { class: 'form-actions' }, submitBtn, anotherBtn, cancelBtn));

  form.addEventListener('submit', (e) => {
    e.preventDefault();
    submit(false);
  });

  function clearErrors() {
    formError.hidden = true;
    formError.textContent = '';
    for (const { control, err } of Object.values(fields)) {
      control.removeAttribute('aria-invalid');
      err.hidden = true;
      err.textContent = '';
    }
    ratingErr.hidden = true;
    ratingErr.textContent = '';
  }

  function showError(name, message) {
    const f = fields[name];
    if (f) {
      f.control.setAttribute('aria-invalid', 'true');
      f.err.textContent = message;
      f.err.hidden = false;
      return f.control;
    }
    if (name === 'rating' && ratingGroup) {
      ratingErr.textContent = message;
      ratingErr.hidden = false;
      return ratingGroup.querySelector('input');
    }
    formError.textContent = message;
    formError.hidden = false;
    return null;
  }

  function getData() {
    const data = {
      title: title.value.trim(),
      url: url.value.trim(),
      source: source.value.trim(),
      difficulty: difficulty.value,
      deck: deck.value,
      tags: parseTagList(tagsInput.value),
      prompt: prompt.value,
      insight: insight.value.trim(),
      notes: notes.value,
      solution: solution.value,
      language: language.value.trim() || 'python',
    };
    if (withRating) {
      data.first_rating = selectedRating();
      const mins = duration.value.trim();
      if (data.first_rating && mins !== '') data.duration_ms = Math.round(Number(mins) * 60000);
    }
    return data;
  }

  function validate(data) {
    const errs = [];
    if (!data.title) errs.push(['title', 'Give the problem a title.']);
    if (data.url && !/^https?:\/\//i.test(data.url)) errs.push(['url', 'The link must start with http:// or https://']);
    if (withRating && data.first_rating && duration.value.trim() !== '') {
      const mins = Number(duration.value);
      if (!Number.isFinite(mins) || mins < 0 || mins > 1440) errs.push(['duration', 'Enter a number of minutes between 0 and 1440.']);
    }
    return errs;
  }

  let busy = false;
  async function submit(another) {
    if (busy) return;
    clearErrors();
    const data = getData();
    const errs = validate(data);
    if (errs.length) {
      let first = null;
      for (const [name, msg] of errs) first = first || showError(name, msg);
      if (first) first.focus();
      return;
    }
    busy = true;
    form.setAttribute('aria-busy', 'true');
    [submitBtn, anotherBtn].forEach((b) => { if (b) b.disabled = true; });
    try {
      await onSubmit(data, { another });
    } catch (err) {
      if (err.status === 400) {
        const m = /^(\w+)\b/.exec(err.message || '');
        const key = m ? (SERVER_FIELD_ALIASES[m[1]] || m[1]) : '';
        const target = showError(fields[key] || key === 'rating' ? key : '', err.message);
        (target || formError).focus?.();
      } else {
        toastError(err);
      }
    } finally {
      busy = false;
      form.removeAttribute('aria-busy');
      [submitBtn, anotherBtn].forEach((b) => { if (b) b.disabled = false; });
    }
  }

  function reset() {
    clearErrors();
    for (const input of [title, url, source, tagsInput, prompt, insight, notes, solution]) input.value = '';
    difficulty.value = '';
    // Deck is deliberately NOT reset here (unlike every other field) - "Save & add
    // another" keeps whatever deck was selected, so adding several NeetCode problems
    // in a row doesn't mean re-picking the deck each time.
    language.value = 'python';
    moreDetails.open = false;
    urlAutofillNote.hidden = true;
    // The fields just cleared above are all empty now, so the next autofill will fill
    // them regardless of what's remembered - only title/difficulty/tags need resetting
    // here for clarity; `deck` is left as-is, in step with the kept deck.value.
    lastAutofill.title = lastAutofill.difficulty = lastAutofill.tags = null;
    if (withRating) {
      ratingGroup.querySelector(`input[name="${ratingName}"][value=""]`).checked = true;
      duration.value = '';
      durationWrap.hidden = true;
    }
    syncChips();
  }

  formError.tabIndex = -1;
  return {
    el: form,
    reset,
    setTags: renderChips,
    focusTitle: () => title.focus(),
  };
}

// ================================================================ Add view
async function viewAdd(main, { seq, params }) {
  const ncSlug = new URLSearchParams(params && params[0] ? params[0] : '').get('nc');
  let ncProblem = null;
  if (ncSlug) {
    try {
      const tracker = await api('GET', '/api/neetcode');
      ncProblem = tracker.problems.find((p) => p.slug === ncSlug) || null;
    } catch (err) {
      toastError(err);
    }
    if (!isCurrent(seq)) return;
  }

  main.append(pageHead('Add a problem', 'Save a problem you want to keep sharp. Include enough of the prompt to solve it again cold.'));

  if (ncSlug) {
    if (ncProblem) {
      const already = ncProblem.status !== 'not_started';
      main.append(h('div', { class: 'card nc-add-note' },
        h('a', { class: 'back-link', href: '#/neetcode' }, icon('back'), 'NeetCode 150'),
        h('p', null,
          h('strong', { text: `NeetCode 150 #${ncProblem.id}` }), ` · ${ncProblem.category}`,
          already
            ? [' — already in your library (', h('a', { href: `#/problem/${ncProblem.problem.id}`, text: 'open it' }), '). Saving again adds a second entry.']
            : '.')));
    } else {
      main.append(h('div', { class: 'card nc-add-note' },
        h('a', { class: 'back-link', href: '#/neetcode' }, icon('back'), 'NeetCode 150'),
        h('p', { text: `Couldn't find that NeetCode 150 problem (“${ncSlug}”). Fill in the form below instead.` })));
    }
  }

  const holder = h('div', { class: 'card' }, loadingEl());
  main.append(holder);
  let tags = [];
  try {
    tags = await loadTags();
  } catch (err) {
    toastError(err);
  }
  if (!isCurrent(seq)) return;

  const initial = { deck: ncSlug ? 'neetcode' : 'main' };
  if (ncProblem) {
    initial.title = ncProblem.title;
    initial.url = ncProblem.leetcode_url;
    initial.source = 'NeetCode';
    initial.difficulty = ncProblem.difficulty;
    initial.tags = [slugTag(ncProblem.category), 'neetcode-150'];
    if (ncProblem.blind75) initial.tags.push('blind-75');
  }

  const form = problemForm({
    initial,
    tags,
    withRating: true,
    submitText: 'Save',
    anotherText: 'Save & add another',
    onSubmit: async (data, { another }) => {
      const p = await api('POST', '/api/problems', data);
      const when = p.status === 'new'
        ? 'it’s in your new queue'
        : `next review ${nextReviewPhrase(p.due)} (${fmtDate(p.due)})`;
      toast(`Added “${p.title}” — ${when}.`, { type: 'success' });
      refreshSummaries();
      if (!isCurrent(seq)) return;
      if (another) {
        form.reset();
        loadTags().then((t) => { if (isCurrent(seq)) form.setTags(t); }).catch(() => {});
        window.scrollTo(0, 0);
        form.focusTitle();
      } else {
        location.hash = `#/problem/${p.id}`;
      }
    },
  });
  holder.replaceChildren(form.el);
  form.focusTitle();
}

// ================================================================ NeetCode 150 view
const NC_STATUS_LABEL = { not_started: 'Not started', added: 'Added', solved: 'Solved', mastered: 'Mastered' };
const NC_FILTERS_KEY = 'dsa-review:neetcode-filters';

function getNcFilters() {
  if (!state.neetcodeFilters) {
    let saved = null;
    try {
      const raw = sessionStorage.getItem(NC_FILTERS_KEY);
      if (raw) saved = JSON.parse(raw);
    } catch {
      saved = null;
    }
    state.neetcodeFilters = Object.assign({ q: '', difficulty: '', status: '', blind75: false }, saved || {});
  }
  return state.neetcodeFilters;
}

function saveNcFilters(f) {
  try {
    sessionStorage.setItem(NC_FILTERS_KEY, JSON.stringify(f));
  } catch {
    /* ignore: sessionStorage unavailable (private browsing, etc.) */
  }
}

function ncStatusPill(status) {
  return h('span', { class: `pill pill-nc-${status}`, text: NC_STATUS_LABEL[status] || status });
}

function miniBar(fraction) {
  const fill = h('div', { class: 'nc-progress-fill' });
  fill.style.width = `${Math.round(Math.max(0, Math.min(1, fraction)) * 100)}%`;
  return h('div', { class: 'nc-progress-track' }, fill);
}

function ncMatchesFilters(p, f) {
  if (f.q && !p.title.toLowerCase().includes(f.q)) return false;
  if (f.difficulty && p.difficulty !== f.difficulty) return false;
  if (f.blind75 && !p.blind75) return false;
  if (f.status === 'not_solved') {
    if (p.status !== 'not_started' && p.status !== 'added') return false;
  } else if (f.status && p.status !== f.status) return false;
  return true;
}

function ncNextText(lp) {
  if (lp.suspended) return 'Suspended';
  if (lp.status === 'new') return 'In your new queue';
  if (lp.status === 'due') return lp.overdue_days ? `Due · ${plural(lp.overdue_days, 'day')} overdue` : 'Due today';
  return `Next review ${nextReviewPhrase(lp.due)}`;
}

function ncRowEl(p) {
  const notStarted = p.status === 'not_started';
  return h('li', { class: `nc-row nc-row-${p.status}` },
    h('div', { class: 'nc-row-main' },
      h('span', { class: 'muted nc-row-num', text: `#${p.id}` }),
      h('a', { class: 'nc-row-title', href: p.leetcode_url, target: '_blank', rel: 'noopener noreferrer' },
        p.title, icon('external', 14), srOnly(' (opens in a new tab)')),
      diffBadge(p.difficulty, { showEmpty: true }),
      p.blind75 ? h('span', { class: 'chip', text: 'Blind 75' }) : null),
    h('div', { class: 'nc-row-side' },
      notStarted ? null : ncStatusPill(p.status),
      p.problem ? h('span', { class: 'muted small nc-row-next', text: ncNextText(p.problem) }) : null,
      p.video_url ? h('a', { class: 'btn btn-ghost btn-sm', href: p.video_url, target: '_blank', rel: 'noopener noreferrer' }, 'Video', srOnly(' (opens in a new tab)')) : null,
      p.solution_url ? h('a', { class: 'nc-row-solution', href: p.solution_url, target: '_blank', rel: 'noopener noreferrer' }, 'Solution', srOnly(' (opens in a new tab)')) : null,
      notStarted
        ? h('a', { class: 'btn btn-sm btn-primary', href: `#/add?nc=${encodeURIComponent(p.slug)}` }, icon('plus'), 'Add')
        : h('a', { class: 'btn btn-sm', href: `#/problem/${p.problem.id}` }, 'Open')));
}

// Category sections remember open/closed per category across visits.
const NC_CAT_OPEN_KEY = 'dsa-review:neetcode-cat-open';
function loadCatOpenState() {
  try {
    return JSON.parse(localStorage.getItem(NC_CAT_OPEN_KEY) || '{}');
  } catch {
    return {};
  }
}
function saveCatOpen(name, open) {
  try {
    const st = loadCatOpenState();
    st[name] = open;
    localStorage.setItem(NC_CAT_OPEN_KEY, JSON.stringify(st));
  } catch { /* private mode, etc. */ }
}

function ncCategorySection(cat, problems, { open, persist }) {
  const pct2 = cat.total ? cat.solved / cat.total : 0;
  const list = h('ul', { class: 'nc-row-list' }, problems.map(ncRowEl));
  const empty = h('p', { class: 'muted small nc-cat-empty' }, 'No problems match the current filters.');
  const det = h('details', { class: 'card nc-cat', open },
    h('summary', null,
      h('div', { class: 'nc-cat-head' },
        h('h2', { text: cat.name }),
        h('span', { class: 'nc-cat-count', text: `${cat.solved} / ${cat.total}` })),
      miniBar(pct2)),
    problems.length ? list : empty);
  if (persist) det.addEventListener('toggle', () => saveCatOpen(cat.name, det.open));
  return det;
}

async function viewNeetcode(main, { seq }) {
  const f = getNcFilters();
  main.append(pageHead('NeetCode 150', 'Your own NeetCode 150 deck, with its own review schedule.'));

  const topEl = h('section', { class: 'card nc-top' }, loadingEl('Loading your NeetCode status'));
  const adoptEl = h('section', null);
  const summaryEl = h('section', { class: 'card nc-summary', 'aria-labelledby': 'nc-summary-title' }, loadingEl('Loading your progress'));
  const filtersEl = h('div', { class: 'filters nc-filters', role: 'search', hidden: true });
  const countEl = h('p', { class: 'count', role: 'status' });
  const catsHost = h('div', { 'aria-busy': 'true' });
  main.append(topEl, adoptEl, summaryEl, filtersEl, countEl, catsHost);

  let tracker;
  try {
    tracker = await api('GET', '/api/neetcode');
  } catch (err) {
    if (!isCurrent(seq)) return;
    topEl.replaceChildren();
    summaryEl.replaceChildren();
    catsHost.replaceChildren(errorState(err, () => router()));
    return;
  }
  if (!isCurrent(seq)) return;

  function renderAdoptBanner() {
    if (!tracker.adoptable.length) {
      adoptEl.replaceChildren();
      return;
    }
    const moveBtn = h('button', { type: 'button', class: 'btn btn-primary btn-sm' }, 'Move to NeetCode');
    moveBtn.addEventListener('click', async () => {
      const titles = tracker.adoptable.map((a) => a.title);
      const shown = titles.slice(0, 8);
      const preview = titles.length > shown.length ? `${shown.join(', ')}, and ${titles.length - shown.length} more` : shown.join(', ');
      const ok = await confirmDialog({
        title: 'Move problems to the NeetCode deck?',
        body: `${plural(tracker.adoptable.length, 'problem')} from your main library will move to the NeetCode deck (tagged neetcode-150) and join its own review schedule. Their schedule and history stay the same. Moving: ${preview}.`,
        confirmLabel: 'Move to NeetCode',
      });
      if (!ok) return;
      moveBtn.disabled = true;
      try {
        const r = await api('POST', '/api/neetcode/adopt', {});
        toast(`Moved ${plural(r.moved, 'problem')} to the NeetCode deck`, { type: 'success' });
        refreshSummaries();
        if (!isCurrent(seq)) return;
        // Refresh this page's own data in place (progress, next up, the review card and
        // the category list all changed) instead of router(), which would re-render the
        // whole page from scratch and jump back to the top.
        await refreshPage();
      } catch (err) {
        toastError(err);
        if (moveBtn.isConnected) moveBtn.disabled = false;
      }
    });
    adoptEl.replaceChildren(h('div', { class: 'card nc-adopt-banner' },
      h('p', null, `${plural(tracker.adoptable.length, 'problem')} in your main library ${tracker.adoptable.length === 1 ? 'is a' : 'are'} NeetCode 150 problem${tracker.adoptable.length === 1 ? '' : 's'}.`),
      moveBtn));
  }

  // ---------- top card: "Review" (left) + "Learn something new" (right)
  // The pick respects the filters below. With no status filter it only picks problems
  // you haven't solved yet (falling back to everything if they're all solved).
  let lastPickId = null;
  const randomBtn = () => h('button', { type: 'button', class: 'btn btn-sm', onclick: randomPick }, icon('shuffle'), 'Random problem');
  const learnHost = h('div', { class: 'nc-top-half nc-learn' });

  function renderPick(p, label, note) {
    const inLibrary = p.status !== 'not_started';
    learnHost.replaceChildren(
      h('h2', { text: 'Learn something new' }),
      h('div', { class: 'nc-pick-info' },
        h('span', { class: 'muted small', text: label }),
        h('p', null,
          h('a', { class: 'nc-pick-title', href: p.leetcode_url, target: '_blank', rel: 'noopener noreferrer' },
            `#${p.id} ${p.title}`, icon('external', 14), srOnly(' (opens in a new tab)')),
          ` · ${p.category} `, diffBadge(p.difficulty)),
        note ? h('span', { class: 'muted small', text: note }) : null),
      h('div', { class: 'btn-group' },
        inLibrary
          ? h('a', { class: 'btn btn-primary btn-sm', href: `#/problem/${p.problem.id}` }, 'Open')
          : h('a', { class: 'btn btn-primary btn-sm', href: `#/add?nc=${encodeURIComponent(p.slug)}` }, icon('plus'), 'Add'),
        randomBtn()));
  }
  function randomPick() {
    const filtered = tracker.problems.filter((p) => ncMatchesFilters(p, f));
    let pool = filtered;
    let kind = '';
    if (!f.status) {
      const unsolved = filtered.filter((p) => p.status === 'not_started' || p.status === 'added');
      if (unsolved.length) {
        pool = unsolved;
        kind = 'unsolved ';
      }
    }
    const filtersOn = Boolean(f.q || f.difficulty || f.status || f.blind75);
    if (!pool.length) {
      toast('No problems match your filters. Clear them to pick from all 150.');
      return;
    }
    // Avoid showing the same problem twice in a row when there's a choice.
    const choices = pool.length > 1 ? pool.filter((p) => p.id !== lastPickId) : pool;
    const pick = choices[Math.floor(Math.random() * choices.length)];
    lastPickId = pick.id;
    renderPick(pick, 'Random pick', `Picked from ${pool.length} ${kind}${pool.length === 1 ? 'problem' : 'problems'}${filtersOn ? ' matching your filters' : ''}`);
    learnHost.querySelector('.nc-pick-title').focus();
  }
  function renderLearnPanel() {
    const nextUp = tracker.problems.find((p) => p.status === 'not_started');
    if (nextUp) {
      renderPick(nextUp, 'Next up', null);
    } else {
      learnHost.replaceChildren(
        h('h2', { text: 'Learn something new' }),
        h('p', { class: 'muted small', text: 'You’ve added every problem in the NeetCode 150. Nice work.' }),
        h('div', { class: 'btn-group' }, randomBtn()));
    }
  }
  renderLearnPanel();

  const reviewHost = h('div', { class: 'nc-top-half nc-review-half' }, loadingEl('Loading your review status'));
  async function renderReviewHalf() {
    let ncSummary;
    let ncQueue;
    try {
      [ncSummary, ncQueue] = await Promise.all([
        refreshSummary('neetcode'),
        api('GET', '/api/queue?deck=neetcode'),
      ]);
    } catch (err) {
      if (!isCurrent(seq)) return;
      reviewHost.replaceChildren(h('h2', { text: 'Review' }), errorState(err, renderReviewHalf));
      return;
    }
    if (!isCurrent(seq)) return;
    const c = ncSummary.counts;
    const newAvail = Math.min(ncQueue.new_left_today, c.new);
    const canReview = c.due > 0 || newAvail > 0;
    const tile = (label, value) => h('div', { class: 'nc-review-stat' },
      h('div', { class: 'stat-label', text: label }), h('div', { class: 'stat-value', text: String(value) }));
    reviewHost.replaceChildren(
      h('h2', { text: 'Review' }),
      h('div', { class: 'nc-review-stats' },
        tile('Due', c.due), tile('New today', newAvail), tile('Streak', ncSummary.streak_days)),
      canReview
        ? h('a', { class: 'btn btn-primary', href: '#/neetcode/review' }, 'Start review')
        : h('p', { class: 'muted small', text: 'Nothing to review right now.' }));
  }
  await renderReviewHalf();
  if (!isCurrent(seq)) return;
  topEl.replaceChildren(h('div', { class: 'nc-top-grid' }, reviewHost, learnHost));
  renderAdoptBanner();

  // ---------- summary
  function renderSummaryCard() {
    const t = tracker.totals;
    const overallPct = t.total ? t.solved / t.total : 0;
    summaryEl.replaceChildren(
      h('div', { class: 'card-head' },
        // tabindex so refreshPage() (below) can send focus here after an in-place
        // refresh, e.g. once "Move to NeetCode" removes the button that had focus.
        h('h2', { id: 'nc-summary-title', tabindex: '-1', text: 'Your progress' }),
        h('span', { class: 'muted small', text: `${plural(t.mastered, 'problem')} mastered` })),
      h('div', { class: 'nc-overall' },
        h('span', { class: 'nc-overall-num', text: `${t.solved} / ${t.total}` }),
        miniBar(overallPct),
        h('span', { class: 'muted', text: `${Math.round(overallPct * 100)}%` })),
      h('div', { class: 'nc-stats-grid' },
        ['Easy', 'Medium', 'Hard'].map((d) => {
          const dd = t.by_difficulty[d];
          return h('div', { class: 'nc-stat' },
            h('div', { class: 'stat-label', text: d }),
            h('div', { class: 'stat-value', text: `${dd.solved} / ${dd.total}` }),
            miniBar(dd.total ? dd.solved / dd.total : 0));
        }),
        h('div', { class: 'nc-stat' },
          h('div', { class: 'stat-label', text: 'Blind 75' }),
          h('div', { class: 'stat-value', text: `${t.blind75.solved} / ${t.blind75.total}` }),
          miniBar(t.blind75.total ? t.blind75.solved / t.blind75.total : 0))));
  }
  renderSummaryCard();

  // ---------- filters
  const search = h('input', { type: 'search', id: 'nc-q', value: f.q, autocomplete: 'off', placeholder: 'Search titles' });
  const diffSel = h('select', { id: 'nc-diff' },
    h('option', { value: '' }, 'All difficulties'),
    ['Easy', 'Medium', 'Hard'].map((d) => h('option', { value: d }, d)));
  diffSel.value = f.difficulty;
  const statusSel = h('select', { id: 'nc-status' },
    h('option', { value: '' }, 'All statuses'),
    h('option', { value: 'not_solved' }, 'Not solved yet'),
    h('option', { value: 'not_started' }, 'Not started'),
    h('option', { value: 'added' }, 'Added'),
    h('option', { value: 'solved' }, 'Solved'),
    h('option', { value: 'mastered' }, 'Mastered'));
  statusSel.value = f.status;
  const blindChk = h('input', { type: 'checkbox', id: 'nc-blind75', checked: f.blind75 });
  filtersEl.hidden = false;
  filtersEl.replaceChildren(
    h('div', { class: 'search-wrap' }, h('label', { for: 'nc-q', class: 'sr-only', text: 'Search problems' }), icon('search'), search),
    h('div', null, h('label', { for: 'nc-diff', class: 'sr-only', text: 'Filter by difficulty' }), diffSel),
    h('div', null, h('label', { for: 'nc-status', class: 'sr-only', text: 'Filter by status' }), statusSel),
    h('label', { class: 'check nc-blind75-check', for: 'nc-blind75' }, blindChk, 'Blind 75 only'));

  function renderList() {
    const filtered = tracker.problems.filter((p) => ncMatchesFilters(p, f));
    const active = Boolean(f.q || f.difficulty || f.status || f.blind75);
    countEl.textContent = active ? `${plural(filtered.length, 'problem')} match` : plural(filtered.length, 'problem');
    const byCat = new Map();
    for (const p of filtered) {
      if (!byCat.has(p.category)) byCat.set(p.category, []);
      byCat.get(p.category).push(p);
    }
    if (!filtered.length) {
      catsHost.replaceChildren(h('div', { class: 'card empty-inline' },
        h('p', { text: 'No problems match these filters.' }),
        h('div', { class: 'btn-group' },
          h('button', { type: 'button', class: 'btn', onclick: clearNcFilters }, 'Clear filters'))));
      return;
    }
    // Collapsed by default, except the first not-fully-solved category; remembered
    // per category in localStorage. While a filter is active, every rendered
    // category has a match (empty ones are excluded above), so all expand.
    const catOpen = loadCatOpenState();
    const firstUnsolved = tracker.categories.find((cat) => cat.total && cat.solved < cat.total);
    catsHost.replaceChildren(...tracker.categories
      .filter((cat) => byCat.has(cat.name))
      .map((cat) => {
        let open;
        if (active) open = true;
        else if (Object.prototype.hasOwnProperty.call(catOpen, cat.name)) open = catOpen[cat.name];
        else open = Boolean(firstUnsolved) && cat.name === firstUnsolved.name;
        return ncCategorySection(cat, byCat.get(cat.name), { open, persist: !active });
      }));
  }

  function clearNcFilters() {
    f.q = '';
    f.difficulty = '';
    f.status = '';
    f.blind75 = false;
    search.value = '';
    diffSel.value = '';
    statusSel.value = '';
    blindChk.checked = false;
    saveNcFilters(f);
    renderList();
  }

  const onSearch = debounce(() => {
    f.q = search.value.trim().toLowerCase();
    saveNcFilters(f);
    renderList();
  }, 200);
  onCleanup(onSearch.cancel);
  search.addEventListener('input', onSearch);
  diffSel.addEventListener('change', () => { f.difficulty = diffSel.value; saveNcFilters(f); renderList(); });
  statusSel.addEventListener('change', () => { f.status = statusSel.value; saveNcFilters(f); renderList(); });
  blindChk.addEventListener('change', () => { f.blind75 = blindChk.checked; saveNcFilters(f); renderList(); });

  /** Re-fetches the tracker and repaints everything that depends on it (top card,
   * adopt banner, progress card, category list) in place - used after "Move to
   * NeetCode" so the page doesn't jump back to the top the way a full router()
   * re-render would. Scroll position is left alone; focus moves to the progress
   * card's heading, since the button that had focus (in the adopt banner) may no
   * longer exist once the banner is gone or rebuilt. */
  async function refreshPage() {
    let newTracker;
    try {
      newTracker = await api('GET', '/api/neetcode');
    } catch (err) {
      toastError(err);
      return;
    }
    if (!isCurrent(seq)) return;
    tracker = newTracker;
    renderLearnPanel();
    await renderReviewHalf();
    if (!isCurrent(seq)) return;
    renderAdoptBanner();
    renderSummaryCard();
    renderList();
    const heading = document.getElementById('nc-summary-title');
    if (heading) heading.focus({ preventScroll: true });
  }

  catsHost.removeAttribute('aria-busy');
  renderList();
}

// ================================================================ Library view
/** Loads state.library's saved filters (q, tag, status, deck, sortKey, sortDir), same
 * pattern as NeetCode's getNcFilters/saveNcFilters below. A function declaration (not
 * const) so it's hoisted and usable from the `state` object literal above, which reads
 * it once at module load. */
function loadLibraryFilters() {
  try {
    const raw = sessionStorage.getItem(LIB_FILTERS_KEY);
    if (raw) return JSON.parse(raw);
  } catch {
    /* ignore: sessionStorage unavailable (private browsing, etc.) */
  }
  return {};
}
function saveLibraryFilters(L) {
  try {
    sessionStorage.setItem(LIB_FILTERS_KEY, JSON.stringify(L));
  } catch {
    /* ignore: sessionStorage unavailable (private browsing, etc.) */
  }
}

const LIB_COLUMNS = [
  { key: 'title', label: 'Title' },
  { key: 'difficulty', label: 'Difficulty' },
  { key: 'tags', label: 'Tags' },
  { key: 'status', label: 'Status' },
  { key: 'next', label: 'Next review' },
  { key: 'recall', label: 'Recall', num: true },
  { key: 'reps', label: 'Reviews', num: true },
  { key: 'lapses', label: 'Lapses', num: true },
];
const MOBILE_SORTS = [
  ['next:1', 'Next review'], ['title:1', 'Title (A–Z)'], ['difficulty:1', 'Difficulty'],
  ['status:1', 'Status'], ['recall:1', 'Recall (weakest first)'], ['reps:-1', 'Most reviewed'],
  ['lapses:-1', 'Most lapses'],
];

function sortValue(p, key) {
  switch (key) {
    case 'title': return p.title.toLowerCase();
    case 'difficulty': return DIFF_ORDER[p.difficulty] ?? null;
    case 'tags': return p.tags.length ? p.tags[0] : null;
    case 'status': return STATUS_ORDER[p.status] ?? 9;
    case 'next': return p.due ? Date.parse(p.due) : null;
    case 'recall': return p.retrievability;
    case 'reps': return p.reps;
    case 'lapses': return p.lapses;
    default: return null;
  }
}

function sortProblems(list, key, dir) {
  return list
    .map((p, i) => ({ p, i, v: sortValue(p, key) }))
    .sort((a, b) => {
      if (a.v === null && b.v === null) return a.i - b.i;
      if (a.v === null) return 1; // blanks always last
      if (b.v === null) return -1;
      const c = typeof a.v === 'string' ? a.v.localeCompare(b.v) : a.v - b.v;
      return c * dir || a.i - b.i;
    })
    .map((x) => x.p);
}

function nextReviewCell(p) {
  if (p.status === 'new' || !p.due) return h('span', { class: 'muted', text: '—', title: 'Not practiced yet' });
  const n = calendarDiff(new Date(p.due));
  let cls = null;
  if (p.status === 'suspended') cls = 'muted';
  else if (n < 0) cls = 'overdue';
  return h('span', { class: cls, title: fmtDate(p.due), text: relShort(p.due) });
}

async function viewLibrary(main, { seq }) {
  const L = state.library;
  main.append(pageHead('Library', 'Every problem you’ve saved, and where it stands.',
    h('a', { class: 'btn btn-primary', href: '#/add' }, icon('plus'), 'Add problem')));

  const search = h('input', {
    type: 'search', id: 'lib-q', value: L.q, autocomplete: 'off',
    placeholder: 'Search titles, insights, sources, tags',
  });
  const tagSel = h('select', { id: 'lib-tag' }, h('option', { value: '' }, 'All tags'));
  const statusSel = h('select', { id: 'lib-status' },
    ['', 'due', 'new', 'scheduled', 'suspended'].map((v) => h('option', { value: v }, v ? STATUS_LABEL[v] : 'All statuses')));
  statusSel.value = L.status;
  const sortSel = h('select', { id: 'lib-sort', class: 'compact' },
    MOBILE_SORTS.map(([v, label]) => h('option', { value: v }, label)));

  // Deck segmented control (Main / NeetCode / Both), counts from the deck summaries.
  const deckSeg = h('div', { class: 'segmented', role: 'radiogroup', 'aria-label': 'Deck' });
  function deckSegOptions() {
    const dc = state.latestSummary ? state.latestSummary.deck_counts : null;
    return [
      { value: '', label: 'Main', count: dc ? dc.main.total : null },
      { value: 'neetcode', label: 'NeetCode', count: dc ? dc.neetcode.total : null },
      { value: 'all', label: 'Both', count: dc ? dc.main.total + dc.neetcode.total : null },
    ];
  }
  // Built once (below); later calls only update the checked state and count text of
  // the existing radios in place - rebuilding them on every repaint (e.g. once the
  // deck counts load) would steal focus off a radio the user has tabbed to.
  function paintDeckSeg() {
    const options = deckSegOptions();
    if (!deckSeg.children.length) {
      deckSeg.replaceChildren(...options.map((o) => {
        const id = `lib-deck-${o.value || 'main'}`;
        const input = h('input', {
          type: 'radio', name: 'lib-deck', id, value: o.value, checked: L.deck === o.value,
          onchange: () => {
            L.deck = o.value;
            L.tag = '';
            tagSel.value = '';
            saveLibraryFilters(L);
            load();
            loadTagsForDeck();
          },
        });
        return h('label', { class: 'seg-option', for: id }, input, o.label, h('span', { class: 'seg-count' }));
      }));
      return;
    }
    options.forEach((o, i) => {
      const label = deckSeg.children[i];
      label.querySelector('input').checked = L.deck === o.value;
      label.querySelector('.seg-count').textContent = o.count !== null ? ` (${o.count})` : '';
    });
  }
  paintDeckSeg();

  const countEl = h('p', { class: 'count', role: 'status' });
  const clearBtn = h('button', { type: 'button', class: 'btn btn-ghost btn-sm', hidden: true }, 'Clear filters');
  const filters = h('div', { class: 'filters', role: 'search' },
    h('div', { class: 'search-wrap' }, h('label', { for: 'lib-q', class: 'sr-only', text: 'Search problems' }), icon('search'), search),
    h('div', null, h('label', { for: 'lib-tag', class: 'sr-only', text: 'Filter by tag' }), tagSel),
    h('div', null, h('label', { for: 'lib-status', class: 'sr-only', text: 'Filter by status' }), statusSel));
  const meta = h('div', { class: 'list-meta' },
    h('div', { class: 'row' }, countEl, clearBtn),
    h('div', { class: 'mobile-sort field-inline' }, h('label', { for: 'lib-sort', text: 'Sort' }), sortSel));

  // Table (desktop) - header built once so sort buttons keep focus.
  const headRow = h('tr');
  const sortButtons = {};
  for (const col of LIB_COLUMNS) {
    const arrow = h('span', { class: 'arrow', 'aria-hidden': 'true' });
    const btn = h('button', { type: 'button', class: 'sort-btn', onclick: () => setSort(col.key) }, col.label, arrow);
    sortButtons[col.key] = { btn, arrow };
    headRow.append(h('th', { scope: 'col', class: col.num ? 'num' : null, dataset: { key: col.key } }, btn));
  }
  const tbody = h('tbody');
  const table = h('table', { class: 'data-table lib-table' },
    h('caption', { class: 'sr-only', text: 'Problems. Column headers are buttons that sort the table.' }),
    h('thead', null, headRow), tbody);
  const tableWrap = h('div', { class: 'table-wrap lib-wrap' }, table);
  const cardList = h('ul', { class: 'card-list', 'aria-label': 'Problems' });
  const emptyEl = h('div', { class: 'card empty-inline', hidden: true });
  const listHost = h('div', { 'aria-busy': 'true' }, tableWrap, cardList, emptyEl);
  main.append(deckSeg, filters, meta, listHost);

  let problems = [];
  let fetchToken = 0;

  function setSort(key) {
    if (L.sortKey === key) L.sortDir = -L.sortDir;
    else {
      L.sortKey = key;
      L.sortDir = key === 'reps' || key === 'lapses' ? -1 : 1;
    }
    saveLibraryFilters(L);
    render();
  }

  const openProblem = (e) => {
    if (e.target.closest('a, button')) return;
    if (String(window.getSelection() || '')) return;
    const host = e.target.closest('[data-id]');
    if (host) location.hash = `#/problem/${host.dataset.id}`;
  };
  tbody.addEventListener('click', openProblem);
  cardList.addEventListener('click', openProblem);

  function render() {
    for (const col of LIB_COLUMNS) {
      const th = sortButtons[col.key].btn.parentElement;
      const active = L.sortKey === col.key;
      if (active) th.setAttribute('aria-sort', L.sortDir === 1 ? 'ascending' : 'descending');
      else th.removeAttribute('aria-sort');
      sortButtons[col.key].arrow.textContent = active ? (L.sortDir === 1 ? '▲' : '▼') : '';
    }
    const sortVal = `${L.sortKey}:${L.sortDir}`;
    if (![...sortSel.options].some((o) => o.value === sortVal)) {
      sortSel.append(h('option', { value: sortVal }, `${LIB_COLUMNS.find((c) => c.key === L.sortKey).label} (${L.sortDir === 1 ? 'ascending' : 'descending'})`));
    }
    sortSel.value = sortVal;

    const filtered = Boolean(L.q || L.tag || L.status || L.deck);
    const n = problems.length;
    countEl.textContent = filtered ? `${plural(n, 'problem')} match` : plural(n, 'problem');
    clearBtn.hidden = !filtered;

    const sorted = sortProblems(problems, L.sortKey, L.sortDir);
    tbody.replaceChildren(...sorted.map((p) => h('tr', { dataset: { id: p.id } },
      h('td', { class: 't-title' },
        h('a', { href: `#/problem/${p.id}`, text: p.title }),
        p.source ? h('span', { class: 'source', text: p.source }) : null),
      h('td', null, diffBadge(p.difficulty, { showEmpty: true })),
      h('td', { class: 't-tags' }, tagChips(p.tags, 3) || h('span', { class: 'muted', text: '—' })),
      h('td', null, statusPill(p)),
      h('td', { class: 'nowrap' }, nextReviewCell(p)),
      h('td', { class: 'num' }, pct(p.retrievability)),
      h('td', { class: 'num', text: String(p.reps) }),
      h('td', { class: 'num', text: String(p.lapses) }))));

    cardList.replaceChildren(...sorted.map((p) => h('li', null,
      h('article', { class: 'p-card', dataset: { id: p.id } },
        h('div', { class: 'p-card-title' }, h('a', { href: `#/problem/${p.id}`, text: p.title })),
        h('div', { class: 'row' }, statusPill(p), diffBadge(p.difficulty), p.source ? h('span', { class: 'muted small', text: p.source }) : null),
        p.tags.length ? h('div', { class: 'row' }, tagChips(p.tags, 4)) : null,
        h('div', { class: 'p-card-meta' },
          h('span', null, 'Next: ', h('b', null, nextReviewCell(p))),
          h('span', null, 'Recall: ', h('b', { text: pct(p.retrievability) })),
          h('span', null, 'Reviews: ', h('b', { text: String(p.reps) })),
          h('span', null, 'Lapses: ', h('b', { text: String(p.lapses) })))))));

    const empty = n === 0;
    tableWrap.hidden = empty;
    cardList.hidden = empty;
    emptyEl.hidden = !empty;
    if (empty) {
      const noneAtAll = !filtered && state.summaries.main && state.summaries.main.counts.total === 0;
      emptyEl.replaceChildren(
        h('p', { text: noneAtAll ? 'Your library is empty.' : 'No problems match these filters.' }),
        h('div', { class: 'btn-group' },
          noneAtAll
            ? h('a', { class: 'btn btn-primary', href: '#/add' }, icon('plus'), 'Add your first problem')
            : h('button', { type: 'button', class: 'btn', onclick: clearFilters }, 'Clear filters')));
    }
  }

  async function load() {
    const token = ++fetchToken;
    const qs = new URLSearchParams();
    if (L.q) qs.set('q', L.q);
    if (L.tag) qs.set('tag', L.tag);
    if (L.status) qs.set('status', L.status);
    if (L.deck) qs.set('deck', L.deck);
    listHost.setAttribute('aria-busy', 'true');
    listHost.style.opacity = problems.length ? '0.6' : '';
    try {
      const res = await api('GET', `/api/problems${qs.toString() ? `?${qs}` : ''}`);
      if (token !== fetchToken || !isCurrent(seq)) return;
      problems = res.problems;
      render();
    } catch (err) {
      if (isCurrent(seq)) toastError(err);
    } finally {
      if (token === fetchToken) {
        listHost.removeAttribute('aria-busy');
        listHost.style.opacity = '';
      }
    }
  }

  function clearFilters() {
    L.q = '';
    L.tag = '';
    L.status = '';
    search.value = '';
    tagSel.value = '';
    statusSel.value = '';
    saveLibraryFilters(L);
    load();
    search.focus();
  }
  clearBtn.addEventListener('click', clearFilters);

  const onSearch = debounce(() => {
    L.q = search.value.trim();
    saveLibraryFilters(L);
    load();
  }, 250);
  onCleanup(onSearch.cancel);
  search.addEventListener('input', onSearch);
  tagSel.addEventListener('change', () => { L.tag = tagSel.value; saveLibraryFilters(L); load(); });
  statusSel.addEventListener('change', () => { L.status = statusSel.value; saveLibraryFilters(L); load(); });
  sortSel.addEventListener('change', () => {
    const [key, dir] = sortSel.value.split(':');
    L.sortKey = key;
    L.sortDir = Number(dir);
    saveLibraryFilters(L);
    render();
  });

  async function loadTagsForDeck() {
    let tags = [];
    try {
      tags = await api('GET', `/api/tags${L.deck ? `?deck=${L.deck}` : ''}`).then((r) => r.tags);
    } catch {
      tags = [];
    }
    if (!isCurrent(seq)) return;
    tagSel.replaceChildren(h('option', { value: '' }, 'All tags'),
      ...tags.map((t) => h('option', { value: t.tag }, `${t.tag} (${t.count})`)));
    if (L.tag && !tags.some((t) => t.tag === L.tag)) {
      L.tag = '';
      saveLibraryFilters(L);
    }
    tagSel.value = L.tag;
  }

  await Promise.all([loadTagsForDeck(), refreshSummaries()]);
  if (!isCurrent(seq)) return;
  paintDeckSeg();
  await load();
}

// ================================================================ Problem view
async function viewProblem(main, { seq, params }) {
  const id = Number(params[0]);
  const ctrl = { id, seq, main, mode: 'view', p: null, card: null, attempt: null };
  state.detail = ctrl;
  onCleanup(() => {
    if (ctrl.card) ctrl.card.destroy();
    if (ctrl.attempt) ctrl.attempt.destroy();
    if (state.detail === ctrl) state.detail = null;
  });
  main.append(backLink(), h('h1', { class: 'sr-only', tabindex: '-1', text: 'Loading problem' }), loadingEl('Loading problem'));

  try {
    ctrl.p = await api('GET', `/api/problems/${id}`);
  } catch (err) {
    if (!isCurrent(seq)) return;
    if (err.status === 404) {
      main.replaceChildren(backLink(), h('div', { class: 'card empty' },
        h('h1', { tabindex: '-1', text: 'Problem not found' }),
        h('p', { text: 'It may have been deleted.' }),
        h('div', { class: 'btn-group' }, h('a', { class: 'btn btn-primary', href: '#/library' }, 'Go to library'))));
    } else {
      main.replaceChildren(backLink(), errorState(err, () => router()));
    }
    return;
  }
  if (!isCurrent(seq)) return;
  renderDetail(ctrl);
}

function backLink() {
  return h('a', { class: 'back-link', href: '#/library' }, icon('back'), 'Library');
}

function renderDetail(ctrl, { focus } = {}) {
  if (!isCurrent(ctrl.seq)) return;
  const { p, main } = ctrl;
  const hadHeadingFocus = document.activeElement && document.activeElement.tagName === 'H1';
  if (ctrl.card) {
    ctrl.card.destroy();
    ctrl.card = null;
  }
  if (ctrl.attempt) {
    ctrl.attempt.destroy();
    ctrl.attempt = null;
  }
  state.activeReview = null;
  document.title = `${p.title} · DSA Review`;

  let focusTarget;
  let actions = null; // the view-mode actions row (Review now / Edit / More) - see below
  if (ctrl.mode === 'review') {
    const heading = h('h1', { tabindex: '-1', text: 'Review now' });
    const card = createReviewCard(p, {
      mode: 'single',
      onRated: async (updated) => {
        showReviewToast(updated, 'single', updated.deck);
        ctrl.p = updated;
        ctrl.mode = 'view';
        refreshSummaries();
        renderDetail(ctrl, { focus: 'view' });
      },
      onClose: () => {
        ctrl.mode = 'view';
        renderDetail(ctrl, { focus: 'view' });
      },
    });
    let note = 'Recode it from scratch, then rate how it went.';
    if (p.status === 'scheduled') note = `Early review: this isn’t due until ${fmtDate(p.due)}. That’s fine, FSRS accounts for the shorter gap.`;
    if (p.status === 'suspended') note = 'This problem is suspended. Reviewing it still updates its schedule.';
    main.replaceChildren(backLink(),
      h('div', { class: 'single-review-head' }, h('div', null, heading, h('p', { class: 'muted', text: note }))),
      card.el);
    ctrl.card = card;
    state.activeReview = card;
    focusTarget = heading;
  } else if (ctrl.mode === 'edit') {
    const heading = h('h1', { tabindex: '-1', text: 'Edit problem' });
    const holder = h('div', { class: 'card' }, loadingEl());
    main.replaceChildren(backLink(), h('div', { class: 'page-head' }, h('div', null, heading, h('p', { class: 'sub', text: p.title }))), holder);
    focusTarget = heading;
    loadTags().catch(() => []).then((tags) => {
      if (!isCurrent(ctrl.seq) || ctrl.mode !== 'edit' || !holder.isConnected) return;
      const form = problemForm({
        initial: p,
        tags,
        submitText: 'Save changes',
        onCancel: () => {
          ctrl.mode = 'view';
          renderDetail(ctrl, { focus: true });
        },
        onSubmit: async (data) => {
          const updated = await api('PATCH', `/api/problems/${p.id}`, data);
          toast('Changes saved', { type: 'success' });
          ctrl.p = updated;
          ctrl.mode = 'view';
          renderDetail(ctrl, { focus: true });
        },
      });
      holder.replaceChildren(form.el);
      form.focusTitle();
    });
  } else {
    const heading = h('h1', { tabindex: '-1' }, titleLink(p, 20));
    actions = detailActions(ctrl);
    main.replaceChildren(
      backLink(),
      detailHeader(ctrl, heading),
      memoryStatsRow(p),
      actions,
      detailBody(p, ctrl),
    );
    focusTarget = heading;
  }

  if (ctrl.mode === 'review' && focus === 'review') {
    // Entering review mode: scroll just enough for the review card to be visible,
    // instead of jumping to the very top of the page.
    revealTop(ctrl.card.el);
    focusTarget.focus({ preventScroll: true });
    return;
  }
  if (actions && focus === 'view') {
    // Back from reviewing or cancelling a review: a minimal scroll (only if the actions
    // row ended up above the viewport) instead of jumping to the top of the page.
    revealTop(actions);
    actions.querySelector('.btn-primary').focus({ preventScroll: true });
    return;
  }
  if (actions && focus === 'more') {
    // After Suspend/Unsuspend or More -> Undo last review: keep the scroll position and
    // send focus back to the (freshly re-rendered) More trigger that opened it.
    const trigger = actions.querySelector('.menu-trigger');
    if (trigger) trigger.focus({ preventScroll: true });
    return;
  }
  if (focus || hadHeadingFocus) {
    window.scrollTo(0, 0);
    focusTarget.focus({ preventScroll: true });
  }
}

function detailHeader(ctrl, heading) {
  const p = ctrl.p;
  return h('header', { class: 'detail-head' },
    heading,
    h('div', { class: 'meta-row' },
      statusPill(p),
      deckPill(p.deck),
      diffBadge(p.difficulty),
      p.source ? h('span', { class: 'source', text: p.source }) : null,
      tagChips(p.tags)));
}

/** Compact one-line row of memory stats, replacing the old tall "Memory" sidebar card. */
function memoryStatsRow(p) {
  const isNew = p.status === 'new';
  let note = null;
  if (p.suspended) note = 'Suspended. It won’t appear in Today until you unsuspend it.';
  else if (isNew) note = 'Not practiced yet. It’ll show up in Today as a new problem.';

  const item = (label, value, sub) => h('div', { class: 'mstat' },
    h('span', { class: 'mstat-label', text: label }),
    h('span', { class: 'mstat-value' }, value, sub ? h('span', { class: 'mstat-sub', text: sub }) : null));

  return h('div', { class: 'card memory-row', 'aria-label': 'Memory stats' },
    note ? h('p', { class: 'memory-note', text: note }) : null,
    h('div', { class: 'mstats' },
      item('Next review', isNew ? 'Not scheduled' : fmtDate(p.due, { weekday: false }), isNew ? null : relShort(p.due)),
      item('Predicted recall', pct(p.retrievability)),
      item('Memory strength', fmtStrength(p.stability)),
      item('Difficulty for you', p.fsrs_difficulty === null ? '—' : `${p.fsrs_difficulty.toFixed(1)} / 10`),
      item('Reviews · Lapses', `${p.reps} · ${p.lapses}`)));
}

/** Review now / Edit / More (Suspend, Undo, Delete) - the primary actions for a problem. */
function detailActions(ctrl) {
  const reviewBtn = h('button', { type: 'button', class: 'btn btn-primary' }, 'Review now');
  reviewBtn.addEventListener('click', () => {
    ctrl.mode = 'review';
    renderDetail(ctrl, { focus: 'review' });
  });
  const editBtn = h('button', { type: 'button', class: 'btn' }, 'Edit');
  editBtn.addEventListener('click', () => {
    ctrl.mode = 'edit';
    renderDetail(ctrl, { focus: true });
  });

  // Disables the More trigger itself (so it can't be reopened) and every item in it
  // while an action is in flight - unlike a plain disabled button on one item, this
  // also blocks a second click from a freshly reopened menu.
  const busyWrap = async (fn) => {
    more.setBusy(true);
    try {
      await fn();
    } catch (err) {
      toastError(err);
    } finally {
      more.setBusy(false);
    }
  };
  const more = menuButton({
    label: 'More',
    small: false, // sits next to full-size Review now / Edit buttons
    // A function (not a static array) so it always reads ctrl.p fresh when the menu
    // opens - e.g. "Undo last review" reflects the current history length even after
    // an action above has replaced ctrl.p without rebuilding this menu.
    items: () => {
      const p = ctrl.p;
      return [
        {
          label: p.suspended ? 'Unsuspend' : 'Suspend',
          onClick: () => busyWrap(async () => {
            const updated = await api('PATCH', `/api/problems/${p.id}`, { suspended: !p.suspended });
            toast(updated.suspended ? 'Suspended. It won’t show up in Today until you unsuspend it.' : 'Unsuspended. It’s back on your schedule.', { type: 'success' });
            ctrl.p = updated;
            refreshSummaries();
            renderDetail(ctrl, { focus: 'more' });
          }),
        },
        {
          label: 'Undo last review',
          disabled: !p.history.length,
          onClick: () => busyWrap(async () => {
            const updated = await api('POST', `/api/problems/${p.id}/undo`, {});
            toast('Last review undone. The schedule is back to how it was.', { type: 'success' });
            ctrl.p = updated;
            refreshSummaries();
            renderDetail(ctrl, { focus: 'more' });
          }),
        },
        {
          label: 'Delete',
          danger: true,
          onClick: async () => {
            const n = p.history.length;
            const ok = await confirmDialog({
              title: 'Delete this problem?',
              body: `“${p.title}”${n ? ` and its ${plural(n, 'review')}` : ''} will be permanently deleted. This can’t be undone.`,
              confirmLabel: 'Delete problem',
            });
            if (!ok) return;
            await busyWrap(async () => {
              await api('DELETE', `/api/problems/${p.id}`, {});
              for (const s of Object.values(state.sessions)) {
                if (s) s.order = s.order.filter((x) => x !== p.id);
              }
              toast(`Deleted “${p.title}”`, { type: 'success' });
              refreshSummaries();
              location.hash = '#/library';
            });
          },
        },
      ];
    },
  });

  return h('div', { class: 'btn-group detail-actions' }, reviewBtn, editBtn, more.el);
}

function attemptCard(p, ctrl) {
  const holder = h('div', null, loadingEl('Loading your attempt'));
  const section = h('section', { class: 'card attempt-card', 'aria-labelledby': 'attempt-title' },
    h('div', { class: 'card-head' },
      h('h2', { id: 'attempt-title', text: 'Your attempt' })),
    holder);
  (async () => {
    let draft;
    try {
      draft = await api('GET', `/api/problems/${p.id}/draft`);
    } catch {
      draft = { code: '', language: p.language || 'python' };
    }
    if (!isCurrent(ctrl.seq) || !holder.isConnected) return;
    const code = draft.code || starterTemplate(p);
    const attempt = attemptEditor({
      problem: p,
      initialCode: code,
      initialLanguage: draft.language || p.language || 'python',
      ariaLabel: `Attempt editor for ${p.title}`,
      autosaveDraft: true,
      showSaveAsSolution: true,
    });
    ctrl.attempt = attempt;
    holder.replaceWith(attempt.el);
  })();
  return section;
}

function detailBody(p, ctrl) {
  const emptyNote = (...parts) => h('p', { class: 'empty-note' }, parts);
  const promptCard = h('section', { class: 'card', 'aria-labelledby': 'prompt-title' },
    h('div', { class: 'card-head' }, h('h2', { id: 'prompt-title', text: 'Problem' })),
    p.prompt.trim()
      ? h('div', { class: 'prompt', text: p.prompt })
      : emptyNote(
        'No problem statement saved. ',
        p.url ? h('a', { href: p.url, target: '_blank', rel: 'noopener noreferrer' }, 'Open it on LeetCode', icon('external', 12), srOnly(' (opens in a new tab)')) : null,
        p.url ? ' or add one with Edit.' : 'Add one with Edit.',
      ));

  // ---------- notes (spoilers while attempting - collapsed by default)
  const hasInsight = Boolean(p.insight);
  const hasNotes = Boolean(p.notes && p.notes.trim());
  const hasSolution = Boolean(p.solution && p.solution.trim());
  const notesBody = (hasInsight || hasNotes || hasSolution)
    ? h('div', { class: 'stack' },
      hasInsight ? h('div', null, h('h3', { class: 'content-sub', text: 'Key insight' }), h('div', { class: 'insight' }, h('p', { text: p.insight }))) : null,
      hasNotes ? h('div', null, h('h3', { class: 'content-sub', text: 'Notes' }), h('div', { class: 'prewrap', text: p.notes })) : null,
      hasSolution ? h('div', null, h('h3', { class: 'content-sub', text: 'Solution' }), codeBlock(p.solution, p.language)) : null)
    : emptyNote('Nothing saved yet — add a key insight with Edit.');
  const notesDetails = h('details', { class: 'card section-details' },
    h('summary', { class: 'section-summary' }, 'Show your notes (key insight, notes, solution)'),
    h('div', { class: 'details-body' }, notesBody));

  // ---------- review history
  const historyBody = p.history.length
    ? h('div', { class: 'table-wrap' },
      h('table', { class: 'data-table history-table' },
        h('thead', null, h('tr', null,
          h('th', { scope: 'col', text: 'Date' }),
          h('th', { scope: 'col', text: 'Rating' }),
          h('th', { scope: 'col', class: 'num', text: 'Time taken' }),
          h('th', { scope: 'col', class: 'h-next', text: 'Next review set to' }))),
        h('tbody', null, p.history.map((r) => {
          const rating = RATING_BY_VALUE[r.rating];
          const gapDays = r.interval_days ?? null;
          return h('tr', null,
            h('td', { class: 'nowrap', text: fmtDateTime(r.reviewed_at) }),
            h('td', null,
              h('span', { class: `rating-label r-${rating.key}`, text: rating.label }),
              r.kind === 'added' ? h('span', { class: 'muted history-kind', text: 'when added' }) : null),
            h('td', { class: 'num', text: r.duration_ms === null ? '—' : fmtClock(r.duration_ms) }),
            h('td', { class: 'h-next' }, r.next_due ? `${fmtDate(r.next_due)} · ${fmtInterval(gapDays)}` : '—'));
        }))))
    : emptyNote('No reviews yet.');
  const historyDetails = h('details', { class: 'card section-details' },
    h('summary', { class: 'section-summary' }, `Review history (${p.history.length})`),
    h('div', { class: 'details-body' }, historyBody));

  return h('div', { class: 'detail-stack' }, promptCard, attemptCard(p, ctrl), notesDetails, historyDetails);
}

// ================================================================ Settings view
function previewSequence(days) {
  const out = [];
  for (let i = 0; i < days.length && out.length < 6; i++) {
    out.push(fmtInterval(days[i]));
    if (i + 1 < days.length && days[i + 1] === days[i]) break; // reached the cap
  }
  return `${out.join(' → ')} …`;
}

async function viewSettings(main, { seq }) {
  main.append(pageHead('Settings', 'Tune how often problems come back, and keep a backup of your data.'));
  const loader = loadingEl();
  main.append(loader);
  const res = await api('GET', '/api/settings');
  if (!isCurrent(seq)) return;
  loader.remove();
  const s = res.settings;

  // ---------- scheduling
  const minR = Math.min(0.8, s.desired_retention);
  const maxR = Math.max(0.97, s.desired_retention);
  const retention = h('input', {
    type: 'range', id: 'set-retention', min: String(minR), max: String(maxR), step: '0.01',
    'aria-describedby': 'set-retention-hint',
  });
  retention.value = String(s.desired_retention);
  const retentionOut = h('output', { class: 'range-value', for: 'set-retention' });
  const paintRetention = () => {
    const text = pct(Number(retention.value));
    retentionOut.textContent = text;
    retention.setAttribute('aria-valuetext', text);
  };
  paintRetention();

  const previewBox = h('dl', { class: 'preview-box', 'aria-label': 'How far apart reviews get' });
  const renderPreview = (ip) => {
    previewBox.replaceChildren(
      ...[['Good every time', ip.good_every_time], ['Hard first, then Good', ip.hard_first_then_good], ['Hard every time', ip.hard_every_time]]
        .map(([label, days]) => h('div', { class: 'preview-row' }, h('dt', { text: label }), h('dd', { text: previewSequence(days) }))));
  };
  renderPreview(res.interval_preview);

  const maxInt = h('input', { type: 'number', min: '1', max: '3650', step: '1', inputmode: 'numeric', value: String(s.maximum_interval) });
  const dayStart = h('select', null, Array.from({ length: 24 }, (_, hr) => h('option', { value: String(hr) }, fmtHour(hr))));
  dayStart.value = String(s.day_starts_at);
  const againNext = h('input', { type: 'checkbox', id: 'set-again', checked: Boolean(s.again_next_day) });

  const errs = {};
  const numField = (id, label, control, hint) => {
    control.id = id;
    const err = h('p', { class: 'field-error', id: `${id}-err`, hidden: true });
    const hintEl = h('p', { class: 'hint', id: `${id}-hint`, text: hint });
    control.setAttribute('aria-describedby', `${hintEl.id} ${err.id}`);
    errs[id] = { control, err };
    return h('div', { class: 'field' }, h('label', { for: id, text: label }), control, hintEl, err);
  };
  const setErr = (id, msg) => {
    const { control, err } = errs[id];
    err.textContent = msg || '';
    err.hidden = !msg;
    if (msg) control.setAttribute('aria-invalid', 'true');
    else control.removeAttribute('aria-invalid');
  };
  const intIn = (control, lo, hi) => {
    const raw = control.value.trim();
    if (!/^\d+$/.test(raw)) return null;
    const n = Number(raw);
    return n >= lo && n <= hi ? n : null;
  };

  let previewToken = 0;
  const updatePreview = debounce(async () => {
    const mi = intIn(maxInt, 1, 3650);
    if (mi === null) return;
    const token = ++previewToken;
    previewBox.classList.add('is-stale');
    try {
      const qs = new URLSearchParams({ desired_retention: retention.value, maximum_interval: String(mi) });
      const r = await api('GET', `/api/settings/preview?${qs}`);
      if (token === previewToken && isCurrent(seq)) renderPreview(r.interval_preview);
    } catch (err) {
      if (isCurrent(seq)) toastError(err);
    } finally {
      if (token === previewToken) previewBox.classList.remove('is-stale');
    }
  }, 200);
  onCleanup(updatePreview.cancel);
  retention.addEventListener('input', () => {
    paintRetention();
    previewBox.classList.add('is-stale');
    updatePreview();
  });
  maxInt.addEventListener('input', () => {
    setErr('set-maxint', intIn(maxInt, 1, 3650) === null ? 'Enter a whole number of days from 1 to 3650.' : '');
    updatePreview();
  });

  // ---------- scheduler parameters (fsrs_parameters: null = defaults, list = fitted by optimize.py)
  const paramsRow = h('div', { class: 'param-row', role: 'group', 'aria-labelledby': 'set-params-label' });
  const renderParams = (params) => {
    const personalized = Array.isArray(params) && params.length > 0;
    paramsRow.replaceChildren();
    appendKids(paramsRow, [
      h('div', { class: 'param-text' },
        h('span', { class: 'field-label', id: 'set-params-label', text: 'Scheduler parameters' }),
        h('span', { class: 'param-value', id: 'set-params-value', tabindex: '-1' },
          h('span', { class: `param-dot${personalized ? ' is-custom' : ''}`, 'aria-hidden': 'true' }),
          personalized ? 'Personalized from your review history' : 'FSRS-6 defaults'),
        h('p', {
          class: 'hint',
          text: personalized
            ? 'Fitted to your own reviews by optimize.py. Resetting reschedules every problem with the standard FSRS-6 parameters.'
            : 'After ~500 spaced reviews you can personalize these with optimize.py (see README).',
        })),
      personalized
        ? h('button', { type: 'button', class: 'btn btn-sm', id: 'set-params-reset', onclick: (e) => resetParams(e.currentTarget) }, 'Reset to defaults')
        : null,
    ]);
  };
  renderParams(s.fsrs_parameters);

  async function resetParams(btn) {
    const ok = await confirmDialog({
      title: 'Reset scheduler parameters?',
      body: 'Your personalized parameters will be replaced with the FSRS-6 defaults, and every problem will be rescheduled from its review history. You can personalize them again later with optimize.py.',
      confirmLabel: 'Reset to defaults',
    });
    if (!ok) return;
    btn.disabled = true;
    try {
      // Only this key: unsaved edits in the form above are left alone.
      const r = await api('PATCH', '/api/settings', { fsrs_parameters: null });
      toast('Reset to defaults — schedule updated', { type: 'success' });
      state.sessions.main = null;
      state.sessions.neetcode = null;
      refreshSummaries();
      if (!isCurrent(seq)) return;
      renderParams(r.settings.fsrs_parameters);
      document.getElementById('set-params-value').focus();
      // Show the new schedule for the values currently in the form (saved or not).
      if (intIn(maxInt, 1, 3650) === null) renderPreview(r.interval_preview);
      else {
        previewBox.classList.add('is-stale');
        updatePreview();
      }
    } catch (err) {
      toastError(err);
      if (btn.isConnected) btn.disabled = false;
    }
  }

  const saveBtn = h('button', { type: 'submit', class: 'btn btn-primary btn-lg' }, 'Save settings');
  const form = h('form', { class: 'form', novalidate: true, 'aria-labelledby': 'sched-title' },
    h('div', { class: 'field' },
      h('label', { for: 'set-retention', text: 'Target recall' }),
      h('div', { class: 'range-row' }, retention, retentionOut),
      h('div', { class: 'range-scale', 'aria-hidden': 'true' }, h('span', { text: pct(minR) }), h('span', { text: pct(maxR) })),
      h('p', { class: 'hint', id: 'set-retention-hint', text: 'Your chance of still being able to solve a problem when it comes back. Higher means more reviews and better recall; 90% is a good default.' })),
    h('div', { class: 'field' },
      h('span', { class: 'field-label', text: 'How far apart reviews get with this target' }),
      previewBox),
    h('div', { class: 'settings-fields' },
      numField('set-maxint', 'Maximum interval (days)', maxInt, 'The longest gap between reviews. Keeps problems from vanishing for months.'),
      numField('set-daystart', 'New day starts at', dayStart, 'Reviews after midnight still count for the previous day until this hour.')),
    h('label', { class: 'check', for: 'set-again' },
      againNext,
      h('span', null,
        h('span', { class: 'choice-title', text: 'Again brings it back tomorrow' }),
        h('span', { class: 'choice-desc', text: 'When you rate a problem Again, it is due the next day no matter what the algorithm suggests.' }))),
    paramsRow,
    h('div', { class: 'form-actions' },
      saveBtn,
      h('span', { class: 'hint', text: 'Saving reschedules every problem, so this needs an explicit save.' })));

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const mi = intIn(maxInt, 1, 3650);
    setErr('set-maxint', mi === null ? 'Enter a whole number of days from 1 to 3650.' : '');
    if (mi === null) {
      maxInt.focus();
      return;
    }
    saveBtn.disabled = true;
    try {
      // Deliberately no fsrs_parameters (and no new_per_day - that's not a scheduling key
      // and lives in the Daily new problems card, which saves itself) here, so saving
      // can't overwrite personalized parameters or trigger an extra reschedule.
      const r = await api('PATCH', '/api/settings', {
        desired_retention: Number(retention.value),
        maximum_interval: mi,
        again_next_day: againNext.checked,
        day_starts_at: Number(dayStart.value),
      });
      if (isCurrent(seq)) {
        // The response already reflects the saved form values: drop any pending preview refresh.
        updatePreview.cancel();
        previewToken += 1;
        previewBox.classList.remove('is-stale');
        renderPreview(r.interval_preview);
        renderParams(r.settings.fsrs_parameters);
      }
      toast('Saved — schedule updated', { type: 'success' });
      state.sessions.main = null;
      state.sessions.neetcode = null;
      refreshSummaries();
    } catch (err) {
      toastError(err);
    } finally {
      saveBtn.disabled = false;
    }
  });

  const schedCard = h('section', { class: 'card', 'aria-labelledby': 'sched-title' },
    h('div', { class: 'card-head' }, h('h2', { id: 'sched-title', text: 'Scheduling' })),
    h('p', { class: 'explainer', text: 'Scheduling uses FSRS-6, the algorithm behind Anki’s modern scheduler. Each problem gets a memory strength (stability) and a personal difficulty; it comes back when your predicted chance of solving it drops to your target.' }),
    form);

  // ---------- rating guide
  const guideCard = h('section', { class: 'card', 'aria-labelledby': 'guide-title' },
    h('div', { class: 'card-head' },
      h('h2', { id: 'guide-title', text: 'Rating guide' }),
      h('p', { text: 'Be honest; the schedule is only as good as your ratings.' })),
    h('dl', { class: 'guide-list' },
      RATINGS.map((r) => h('div', { class: `guide-item g-${r.key}` },
        h('dt', { text: `${r.value} · ${r.label}` }),
        h('dd', { text: r.guide })))));

  // ---------- backup
  const fileInput = h('input', { type: 'file', accept: '.json,application/json', id: 'import-file' });
  fileInput.addEventListener('change', async () => {
    const file = fileInput.files && fileInput.files[0];
    if (!file) return;
    try {
      const text = await file.text();
      let data;
      try {
        data = JSON.parse(text);
      } catch {
        throw new Error('That file isn’t valid JSON. Choose a backup exported from DSA Review.');
      }
      if (!data || typeof data !== 'object' || Array.isArray(data)) {
        throw new Error('That file isn’t a DSA Review backup. Choose a file made with Export backup.');
      }
      const r = await api('POST', '/api/import', data);
      toast(`Added ${plural(r.added, 'problem')}, skipped ${r.skipped} already here`, { type: 'success' });
      state.sessions.main = null;
      state.sessions.neetcode = null;
      refreshSummaries();
    } catch (err) {
      toastError(err);
    } finally {
      fileInput.value = '';
    }
  });
  const backupCard = h('section', { class: 'card', 'aria-labelledby': 'backup-title' },
    h('div', { class: 'card-head' },
      h('h2', { id: 'backup-title', text: 'Backup' }),
      h('p', { text: 'Export everything to a JSON file, or merge a backup back in.' })),
    h('div', { class: 'btn-group' },
      h('a', { class: 'btn', href: '/api/export', download: '' }, icon('download'), 'Export backup'),
      h('label', { class: 'btn file-btn' }, icon('upload'), 'Import backup', fileInput)),
    h('p', { class: 'hint backup-note', text: 'Importing skips problems that are already here, so it’s safe to run more than once. A copy of your database is also saved to data/backups every time the app starts (last 10 kept).' }));

  // ---------- Start over: delete all data (e.g. to hand the app to someone new)
  const resetSettingsChk = h('input', { type: 'checkbox', id: 'reset-settings' });
  const resetBtn = h('button', { type: 'button', class: 'btn btn-danger' }, 'Delete all data…');
  resetBtn.addEventListener('click', async () => {
    const ok = await typedConfirmDialog({
      title: 'Delete all data?',
      body: 'Every problem, review, and saved attempt in both decks will be deleted'
        + (resetSettingsChk.checked ? ', and settings go back to their defaults' : '')
        + '. A backup copy is saved in data/backups first.',
      word: 'DELETE',
      confirmLabel: 'Delete all data',
    });
    if (!ok) return;
    resetBtn.disabled = true;
    try {
      const r = await api('POST', '/api/reset', { confirm: 'DELETE', reset_settings: resetSettingsChk.checked });
      // Forget everything cached about the old data, then show the fresh app.
      state.summaries = { main: null, neetcode: null };
      state.latestSummary = null;
      state.sessions = { main: null, neetcode: null };
      state.todayTags = { main: '', neetcode: '' };
      state.library.q = '';
      state.library.tag = '';
      state.library.status = '';
      state.tags = [];
      const where = r.backup ? ` A backup was saved as data/backups/${r.backup}.` : '';
      toast(`All data deleted.${where}`, { type: 'success', duration: 8000 });
      refreshSummaries();
      location.hash = '#/today';
    } catch (err) {
      toastError(err);
      if (resetBtn.isConnected) resetBtn.disabled = false;
    }
  });
  const resetCard = h('section', { class: 'card danger-card', 'aria-labelledby': 'reset-title' },
    h('div', { class: 'card-head' },
      h('h2', { id: 'reset-title', text: 'Start over' }),
      h('p', { text: 'Empty the app, like a fresh install.' })),
    h('p', { class: 'explainer', text: 'Deletes every problem, review, and saved attempt in both decks. A backup copy of your database is saved in data/backups first, and your Claude API key is kept.' }),
    h('label', { class: 'check', for: 'reset-settings' },
      resetSettingsChk,
      h('span', null,
        h('span', { class: 'choice-title', text: 'Also reset settings to their defaults' }),
        h('span', { class: 'choice-desc', text: 'Daily limits, scheduling, code runner and Claude mode.' }))),
    h('div', { class: 'btn-group reset-actions' }, resetBtn));

  // ---------- Daily new problems: Main + NeetCode per-day limits, and the toggle that
  // folds NeetCode into the main Today/Library. All three autosave on change.
  async function autosavePatch(patch, { onError } = {}) {
    try {
      await api('PATCH', '/api/settings', patch);
      toast('Saved', { type: 'success', duration: 1800 });
      return true;
    } catch (err) {
      toastError(err);
      if (onError) onError();
      return false;
    }
  }

  const dailyMain = h('input', { type: 'number', min: '0', max: '100', step: '1', inputmode: 'numeric', value: String(s.new_per_day) });
  async function commitDailyMain() {
    const n = intIn(dailyMain, 0, 100);
    setErr('set-daily-main', n === null ? 'Enter a whole number from 0 to 100.' : '');
    if (n === null) return;
    const ok = await autosavePatch({ new_per_day: n });
    if (ok) refreshSummaries();
  }
  dailyMain.addEventListener('change', commitDailyMain);

  const dailyNc = h('input', { type: 'number', min: '0', max: '100', step: '1', inputmode: 'numeric', value: String(s.neetcode_new_per_day) });
  async function commitDailyNc() {
    const n = intIn(dailyNc, 0, 100);
    setErr('set-daily-nc', n === null ? 'Enter a whole number from 0 to 100.' : '');
    if (n === null) return;
    const ok = await autosavePatch({ neetcode_new_per_day: n });
    if (ok) refreshSummaries();
  }
  dailyNc.addEventListener('change', commitDailyNc);

  const ncInMain = h('input', { type: 'checkbox', id: 'set-nc-in-main', checked: Boolean(s.neetcode_in_main) });
  ncInMain.addEventListener('change', async () => {
    const checked = ncInMain.checked;
    ncInMain.disabled = true;
    const ok = await autosavePatch({ neetcode_in_main: checked }, { onError: () => { ncInMain.checked = !checked; } });
    ncInMain.disabled = false;
    if (ok) {
      state.sessions.main = null; // the main queue may now include (or exclude) NeetCode problems
      refreshSummaries();
    }
  });

  const dailyCard = h('section', { class: 'card', 'aria-labelledby': 'daily-title' },
    h('div', { class: 'card-head' },
      h('h2', { id: 'daily-title', text: 'Daily new problems' }),
      h('p', { text: 'How many unsolved problems show up in each review session per day.' })),
    h('div', { class: 'settings-fields' },
      numField('set-daily-main', 'Main per day', dailyMain, 'New problems introduced in Today each day.'),
      numField('set-daily-nc', 'NeetCode per day', dailyNc, 'New NeetCode problems introduced in the NeetCode review each day.')),
    h('label', { class: 'check settings-check', for: 'set-nc-in-main' },
      ncInMain,
      h('span', null,
        h('span', { class: 'choice-title', text: 'Show NeetCode problems on Today and Library' }),
        h('span', { class: 'choice-desc', text: 'When on, due and new NeetCode problems also appear in your regular Today session and library view.' }))));

  // ---------- code runner
  const allowRun = h('input', { type: 'checkbox', id: 'set-allow-run', checked: Boolean(s.allow_code_run) });
  allowRun.addEventListener('change', async () => {
    const checked = allowRun.checked;
    allowRun.disabled = true;
    try {
      await api('PATCH', '/api/settings', { allow_code_run: checked });
      toast(checked ? 'Run is turned on' : 'Run is turned off', { type: 'success' });
    } catch (err) {
      allowRun.checked = !checked;
      toastError(err);
    } finally {
      allowRun.disabled = false;
    }
  });
  const runCard = h('section', { class: 'card', 'aria-labelledby': 'run-title' },
    h('div', { class: 'card-head' }, h('h2', { id: 'run-title', text: 'Code runner' })),
    h('p', { class: 'explainer', text: 'The Attempt editor’s Run button executes your code as a real Python process on this computer, only from this app’s own page (see README → Security notes).' }),
    h('label', { class: 'check', for: 'set-allow-run' },
      allowRun,
      h('span', null,
        h('span', { class: 'choice-title', text: 'Allow running code' }),
        h('span', { class: 'choice-desc', text: 'Turn off to disable the Run button everywhere. You can still write and save your attempts.' }))));

  // ---------- Claude help
  const claudeCard = await claudeHelpCard(s, seq);

  main.append(h('div', { class: 'settings-grid' },
    claudeCard, dailyCard, schedCard, runCard, backupCard, guideCard, resetCard));
}

/** The "Claude help" card on Settings: mode, API key, model, CLI status, test connection. */
async function claudeHelpCard(s, seq) {
  let status;
  try {
    status = await api('GET', '/api/claude/status');
  } catch {
    status = { mode: s.claude_mode, model: s.claude_model, api_key: { set: false, hint: null, source: null },
              cli: { found: false, path: null } };
  }
  if (!isCurrent(seq)) return h('section', { class: 'card', hidden: true });

  const MODE_OPTIONS = [
    { value: 'off', title: 'Off', desc: 'Ask Claude is turned off everywhere.' },
    { value: 'api', title: 'API key',
      desc: 'Billed per use from your Anthropic Console account (platform.claude.com).' },
    { value: 'cli', title: 'Claude Code CLI',
      desc: 'Uses the `claude` command-line tool with your own Claude plan. This app never sees your login.' },
  ];
  const modeInputs = MODE_OPTIONS.map((o) => h('label', { class: 'check claude-settings-mode', for: `set-claude-${o.value}` },
    h('input', { type: 'radio', name: 'set-claude-mode', id: `set-claude-${o.value}`, value: o.value, checked: status.mode === o.value }),
    h('span', null,
      h('span', { class: 'choice-title', text: o.title }),
      h('span', { class: 'choice-desc', text: o.desc }))));
  const modeGroup = h('div', { class: 'form', role: 'radiogroup', 'aria-label': 'How Ask Claude connects' }, modeInputs);

  async function saveMode(value) {
    try {
      await api('PATCH', '/api/settings', { claude_mode: value });
      toast('Saved', { type: 'success' });
    } catch (err) {
      toastError(err);
    }
  }
  for (const input of modeGroup.querySelectorAll('input')) {
    input.addEventListener('change', () => { if (input.checked) { saveMode(input.value); renderSections(); } });
  }

  // ---- API key
  const keyInput = h('input', { type: 'password', autocomplete: 'off', 'aria-label': 'Anthropic API key', placeholder: 'sk-ant-…' });
  const keyStatus = h('span', { class: 'hint claude-key-status' });
  function paintKeyStatus() {
    keyStatus.textContent = status.api_key.set
      ? `Key saved (${status.api_key.hint}${status.api_key.source === 'env' ? ' · from ANTHROPIC_API_KEY' : ''})`
      : 'No key saved yet.';
  }
  paintKeyStatus();
  const keySaveBtn = h('button', { type: 'button', class: 'btn btn-primary btn-sm' }, 'Save');
  const keyRemoveBtn = h('button', { type: 'button', class: 'btn btn-sm', disabled: !status.api_key.set }, 'Remove');
  keySaveBtn.addEventListener('click', async () => {
    const value = keyInput.value.trim();
    if (!value) {
      toast('Paste your API key first.', { type: 'error' });
      return;
    }
    keySaveBtn.disabled = true;
    try {
      await api('PUT', '/api/claude/key', { api_key: value });
      keyInput.value = '';
      status.api_key = { set: true, hint: `…${value.slice(-4)}`, source: 'file' };
      paintKeyStatus();
      keyRemoveBtn.disabled = false;
      toast('API key saved', { type: 'success' });
    } catch (err) {
      toastError(err);
    } finally {
      keySaveBtn.disabled = false;
    }
  });
  keyRemoveBtn.addEventListener('click', async () => {
    keyRemoveBtn.disabled = true;
    try {
      await api('DELETE', '/api/claude/key', {});
      status.api_key = { set: false, hint: null, source: null };
      paintKeyStatus();
      toast('API key removed', { type: 'success' });
    } catch (err) {
      toastError(err);
      keyRemoveBtn.disabled = false;
    }
  });
  const keyField = h('div', { class: 'field claude-key-field' },
    h('label', { for: 'set-claude-key', text: 'Anthropic API key' }),
    Object.assign(keyInput, { id: 'set-claude-key' }),
    h('div', { class: 'btn-group' }, keySaveBtn, keyRemoveBtn),
    keyStatus);

  // ---- model
  const MODEL_OPTIONS = [
    { value: 'haiku', label: 'Haiku 4.5 — fastest & cheapest' },
    { value: 'sonnet', label: 'Sonnet 5 — recommended' },
    { value: 'opus', label: 'Opus 5.5 — most capable, slower & pricier' },
  ];
  const modelSelect = h('select', { id: 'set-claude-model' },
    MODEL_OPTIONS.map((o) => h('option', { value: o.value }, o.label)));
  modelSelect.value = status.model;
  modelSelect.addEventListener('change', async () => {
    try {
      await api('PATCH', '/api/settings', { claude_model: modelSelect.value });
      toast('Saved', { type: 'success' });
    } catch (err) {
      toastError(err);
    }
  });
  const modelField = h('div', { class: 'field' },
    h('label', { for: 'set-claude-model', text: 'Model' }), modelSelect);

  // ---- CLI status + install steps
  const cliStatus = h('p', { class: 'hint' },
    status.cli.found
      ? `Found the \`claude\` command at ${status.cli.path}.`
      : 'The `claude` command wasn’t found on this computer’s PATH yet.');
  const cliSteps = h('ol', { class: 'cli-steps' },
    h('li', null, 'Install Node.js.'),
    h('li', null, h('code', { text: 'npm install -g @anthropic-ai/claude-code' })),
    h('li', null, 'Run ', h('code', { text: 'claude' }), ' in a terminal and sign in.'));

  // ---- test connection
  const testResult = h('p', { class: 'hint claude-test-result', role: 'status' });
  const testBtn = h('button', { type: 'button', class: 'btn btn-sm' }, 'Test connection');
  testBtn.addEventListener('click', async () => {
    testBtn.disabled = true;
    testResult.className = 'hint claude-test-result';
    testResult.textContent = 'Testing…';
    try {
      const r = await api('POST', '/api/claude/test', {});
      testResult.classList.add('is-success');
      testResult.textContent = `Connected via ${r.via === 'cli' ? 'the Claude Code CLI' : 'your API key'}. Claude replied: "${r.text}"`;
    } catch (err) {
      testResult.classList.add('is-error');
      testResult.textContent = err.message || String(err);
    } finally {
      testBtn.disabled = false;
    }
  });

  // ---- only show what's relevant to the selected mode
  const sectionsHost = h('div', { class: 'claude-mode-sections' });
  function renderSections() {
    const mode = modeGroup.querySelector('input:checked').value;
    if (mode === 'off') {
      sectionsHost.replaceChildren(); // the card header already explains what Claude help does
    } else if (mode === 'api') {
      sectionsHost.replaceChildren(
        h('div', { class: 'settings-fields' }, keyField, modelField),
        h('div', { class: 'btn-group' }, testBtn),
        testResult);
    } else {
      sectionsHost.replaceChildren(
        cliSteps,
        cliStatus,
        modelField,
        h('div', { class: 'btn-group' }, testBtn),
        testResult);
    }
  }
  renderSections();

  return h('section', { class: 'card', 'aria-labelledby': 'claude-title' },
    h('div', { class: 'card-head' },
      h('h2', { id: 'claude-title', text: 'Claude help' }),
      h('p', { text: 'Debugging hints, explanations and code review from Claude on the Attempt editor.' })),
    modeGroup,
    sectionsHost);
}

// ================================================================ router
const ROUTES = [
  { re: /^\/today$/, nav: 'today', title: 'Today', view: (main, ctx) => viewToday(main, { ...ctx, deck: 'main' }) },
  { re: /^\/add(?:\?(.*))?$/, nav: 'add', title: 'Add problem', view: viewAdd },
  { re: /^\/library$/, nav: 'library', title: 'Library', view: viewLibrary },
  { re: /^\/problem\/(\d+)$/, nav: 'library', title: 'Problem', view: viewProblem },
  { re: /^\/neetcode$/, nav: 'neetcode', title: 'NeetCode 150', view: viewNeetcode },
  { re: /^\/neetcode\/review$/, nav: 'neetcode', title: 'NeetCode review',
    view: (main, ctx) => viewToday(main, { ...ctx, deck: 'neetcode' }) },
  { re: /^\/settings$/, nav: 'settings', title: 'Settings', view: viewSettings },
];

function router() {
  let path = location.hash.replace(/^#/, '');
  if (!path || path === '/') {
    history.replaceState(null, '', '#/today');
    path = '/today';
  }
  let route = null;
  let match = null;
  for (const r of ROUTES) {
    match = path.match(r.re);
    if (match) {
      route = r;
      break;
    }
  }
  if (!route) {
    location.replace('#/today');
    return;
  }

  for (const fn of state.cleanups.splice(0)) {
    try {
      fn();
    } catch {
      /* ignore cleanup errors */
    }
  }
  state.activeReview = null;
  state.seq += 1;
  const seq = state.seq;
  // A confirmation left open (e.g. after the browser Back button) must not float over another page.
  for (const dlg of document.querySelectorAll('dialog[open]')) dlg.close('cancel');

  for (const a of document.querySelectorAll('[data-nav]')) {
    if (a.dataset.nav === route.nav && route.view !== viewProblem) a.setAttribute('aria-current', 'page');
    else a.removeAttribute('aria-current');
  }
  document.title = `${route.title} · DSA Review`;

  const main = document.getElementById('main');
  main.replaceChildren();
  const moveFocus = !state.firstRender;
  state.firstRender = false;

  const retry = () => router();
  route.view(main, { seq, params: match.slice(1) }).catch((err) => {
    if (!isCurrent(seq)) return;
    main.replaceChildren(errorState(err, retry));
  });

  if (moveFocus) {
    window.scrollTo(0, 0);
    const heading = main.querySelector('h1');
    (heading || main).focus({ preventScroll: true });
  }
}

document.getElementById('skip-link').addEventListener('click', () => {
  const main = document.getElementById('main');
  const heading = main.querySelector('h1');
  (heading || main).focus();
});

window.addEventListener('hashchange', router);
router();
// Whichever page loaded first refreshes its own deck's summary; make sure both nav
// badges (Today's and NeetCode's) are populated regardless of which page that was.
refreshSummaries();
