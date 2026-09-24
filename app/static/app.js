/* DSA Review - browser front end.
 * Vanilla JS, no build step, no external resources.
 * All user-provided text is rendered with textContent / createElement (never innerHTML).
 */

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
  done: ['M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18z', 'M8 12.5l2.8 2.8L16 10'],
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

/** Call the backend. Non-GET requests always send JSON. Throws ApiError with the server's message. */
async function api(method, path, body) {
  const opts = { method, headers: { Accept: 'application/json' } };
  if (method !== 'GET') {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(body ?? {});
  }
  let res;
  try {
    res = await fetch(path, opts);
  } catch {
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

// ================================================================ formatting
function startOfLocalDay(d) {
  return new Date(d.getFullYear(), d.getMonth(), d.getDate());
}

/** Hours after midnight when a new study day begins (Settings → "New day starts at"). */
function dayStartMs() {
  const h = state.summary && state.summary.settings ? Number(state.summary.settings.day_starts_at) : 4;
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

// ================================================================ app state
const state = {
  summary: null,
  tags: [],
  session: null, // { key, tag, order: [id], done }
  todayTag: '',
  library: { q: '', tag: '', status: '', sortKey: 'next', sortDir: 1 },
  cleanups: [],
  seq: 0,
  firstRender: true,
  activeReview: null, // review card controller that receives keyboard shortcuts
  today: null, // Today view controller while mounted
  detail: null, // Problem view controller while mounted
};

function onCleanup(fn) {
  state.cleanups.push(fn);
}

function isCurrent(seq) {
  return seq === state.seq;
}

async function refreshSummary() {
  const s = await api('GET', '/api/summary');
  state.summary = s;
  updateBadge();
  return s;
}

function updateBadge() {
  const badge = document.getElementById('due-badge');
  const n = state.summary ? state.summary.counts.due : 0;
  badge.hidden = n === 0;
  badge.replaceChildren(String(n), srOnly(' due'));
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
      }
    }
  }

  // ---------- footer
  const hint = (keys, label) => h('span', null, keys, ' ', label);
  const foot = h('div', { class: 'rc-foot' },
    h('div', { class: 'btn-group' },
      mode === 'session'
        ? [
          h('button', { type: 'button', class: 'btn btn-sm', 'aria-keyshortcuts': 'S', onclick: () => onSkip() }, 'Skip for now'),
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
    h('div', { class: 'rc-body' }, prompt, instruction, timerBox, notesBtn, notesPanel, rateSection),
    foot);

  return {
    el,
    id: p.id,
    rate,
    toggleTimer,
    toggleNotes,
    skip: mode === 'session' ? () => { if (!busy && !destroyed) onSkip(); } : null,
    focusTitle: () => title.focus({ preventScroll: true }),
    destroy() {
      destroyed = true;
      clearInterval(tick);
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
    card.toggleNotes();
  } else if ((key === 's' || key === 'S') && card.skip) {
    e.preventDefault();
    card.skip();
  }
});
// A focused button would otherwise also "click" on Space keyup after we handled it on keydown.
document.addEventListener('keyup', (e) => {
  if (!state.activeReview || (e.key !== ' ' && e.key !== 'Spacebar')) return;
  if (isTypingTarget(e.target) || document.querySelector('dialog[open]')) return;
  e.preventDefault();
});

function showReviewToast(updated, context) {
  // The review's id goes along with Undo, so a pop-up whose review was already undone
  // (e.g. from the problem page) can't remove an older review by mistake.
  const reviewId = updated.history && updated.history.length ? updated.history[0].id : undefined;
  toast(`“${updated.title}”: next review ${nextReviewPhrase(updated.due)}`, {
    type: 'success',
    actionLabel: 'Undo',
    onAction: () => undoReview(updated.id, context, reviewId),
  });
}

async function undoReview(pid, context, reviewId) {
  try {
    const p = await api('POST', `/api/problems/${pid}/undo`, reviewId === undefined ? {} : { review_id: reviewId });
    toast(`Review undone for “${p.title}”`);
    if (context === 'session' && state.session) {
      const s = state.session;
      s.order = [pid, ...s.order.filter((x) => x !== pid)];
      s.done = Math.max(0, s.done - 1);
    }
    await refreshSummary();
    if (state.today) {
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

// ================================================================ Today view
async function viewToday(main, { seq }) {
  const statsEl = h('section', { class: 'stats is-loading', 'aria-label': 'Your progress' });
  renderStats(statsEl, null, null);
  const sessionEl = h('section', { class: 'session', 'aria-labelledby': 'session-title' }, loadingEl('Loading your queue'));
  const forecastEl = h('section', { class: 'card forecast', 'aria-labelledby': 'forecast-title' });
  main.append(
    pageHead('Today', studyNow().toLocaleDateString(undefined, { weekday: 'long', month: 'long', day: 'numeric' })),
    statsEl, sessionEl, forecastEl,
  );
  forecastEl.hidden = true;

  const ctrl = { seq, statsEl, sessionEl, forecastEl, queue: null, card: null, syncToken: 0, cardToken: 0 };
  state.today = ctrl;
  onCleanup(() => {
    if (ctrl.card) ctrl.card.destroy();
    if (state.today === ctrl) state.today = null;
  });

  const [, tags] = await Promise.all([refreshSummary(), loadTags()]);
  if (!isCurrent(seq)) return;
  if (state.todayTag && !tags.some((t) => t.tag === state.todayTag)) state.todayTag = '';
  await syncQueue(ctrl);
  if (!isCurrent(seq)) return;
  renderToday(ctrl);
}

/** Fetch the queue (and optionally the summary) and merge it into the in-memory session. */
async function syncQueue(ctrl, { withSummary = false } = {}) {
  const token = ++ctrl.syncToken;
  const tag = state.todayTag;
  const [queue] = await Promise.all([
    api('GET', `/api/queue${tag ? `?tag=${encodeURIComponent(tag)}` : ''}`),
    withSummary ? refreshSummary() : null,
  ]);
  if (token !== ctrl.syncToken) return false;
  ctrl.queue = queue;
  const items = [...queue.due, ...queue.new];
  const ids = new Set(items.map((p) => p.id));
  const key = `${tag}|${state.summary ? state.summary.day_start : ''}`;
  let s = state.session;
  if (!s || s.key !== key) {
    s = { key, tag, order: [], done: 0 };
    state.session = s;
  }
  s.order = s.order.filter((id) => ids.has(id));
  for (const p of items) if (!s.order.includes(p.id)) s.order.push(p.id);
  return true;
}

function renderToday(ctrl, { focusCard = false } = {}) {
  if (!isCurrent(ctrl.seq)) return;
  ctrl.statsEl.hidden = state.summary.counts.total === 0;
  renderStats(ctrl.statsEl, state.summary, ctrl.queue);
  renderSession(ctrl, { focusCard });
  renderForecast(ctrl.forecastEl, state.summary);
}

function renderStats(el, summary, queue) {
  el.classList.toggle('is-loading', !summary);
  const tile = (label, value, unit, sub, primary) => h('div', { class: `stat${primary ? ' is-primary' : ''}` },
    h('div', { class: 'stat-label', text: label }),
    h('div', { class: 'stat-value' }, String(value), unit ? h('span', { class: 'unit', text: unit }) : null),
    h('div', { class: 'stat-sub', text: sub || ' ' }));
  if (!summary || !queue) {
    el.replaceChildren(...['Due today', 'New available', 'Reviewed today', 'Streak', '30-day recall']
      .map((l) => tile(l, '–', '', '')));
    return;
  }
  const c = summary.counts;
  const newAvail = Math.min(queue.new_left_today, c.new);
  let newSub = c.new ? `${c.new} waiting` : 'None waiting';
  if (c.new && queue.new_left_today === 0) newSub = `Limit reached · ${c.new} waiting`;
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
  const s = state.session;
  const summary = state.summary;
  if (ctrl.card) {
    ctrl.card.destroy();
    ctrl.card = null;
  }
  state.activeReview = null;

  if (summary.counts.total === 0) {
    el.replaceChildren(welcomeState());
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
  select.value = state.todayTag;
  select.addEventListener('change', async () => {
    state.todayTag = select.value;
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

async function showCurrent(ctrl, holder, focusCard) {
  const s = state.session;
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
    } else {
      holder.replaceChildren(errorState(err, () => renderSession(ctrl)));
    }
    return;
  }
  if (token !== ctrl.cardToken || !isCurrent(ctrl.seq) || s.order[0] !== id) return;

  const card = createReviewCard(p, {
    mode: 'session',
    onRated: async (updated) => {
      s.order = s.order.filter((x) => x !== updated.id);
      s.done += 1;
      showReviewToast(updated, 'session');
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
      renderSession(ctrl, { focusCard: true });
    },
  });
  holder.replaceWith(card.el);
  ctrl.card = card;
  state.activeReview = card;
  if (focusCard) {
    revealTop(ctrl.sessionEl);
    const active = document.activeElement;
    if (!active || active === document.body || !active.isConnected) card.focusTitle();
  }
}

function caughtUpState(ctrl) {
  const summary = state.summary;
  const q = ctrl.queue;
  const tag = state.todayTag;
  const s = state.session;
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
    lines.push(`${plural(q.new_waiting, 'new problem')} ${q.new_waiting === 1 ? 'is' : 'are'} waiting, but today’s limit of ${summary.settings.new_per_day} is used up. You can raise it in Settings.`);
  }

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
            state.todayTag = '';
            try {
              if (await syncQueue(ctrl)) renderToday(ctrl);
            } catch (err) {
              toastError(err);
            }
          },
        }, 'Show all tags')
        : null,
      h('a', { class: `btn${tag ? '' : ' btn-primary'}`, href: '#/add' }, icon('plus'), 'Add a problem'),
      h('a', { class: 'btn', href: '#/library' }, 'Browse library')));
}

function welcomeState() {
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

  const formError = h('div', { class: 'form-error', role: 'alert', hidden: true });
  const submitBtn = h('button', { type: 'submit', class: 'btn btn-primary btn-lg' }, submitText);
  const anotherBtn = anotherText ? h('button', { type: 'button', class: 'btn btn-lg', onclick: () => submit(true) }, anotherText) : null;
  const cancelBtn = onCancel ? h('button', { type: 'button', class: 'btn btn-ghost btn-lg', onclick: () => onCancel() }, 'Cancel') : null;

  const form = h('form', { class: 'form', novalidate: true, 'aria-label': withRating ? 'Add a problem' : 'Edit problem' },
    formError,
    field('title', 'Title', title, { required: true }),
    h('div', { class: 'form-grid-3' },
      field('url', 'Link', url, { optional: true }),
      h('div', null, field('source', 'Source', source, { optional: true }), sourceList),
      field('difficulty', 'Difficulty', difficulty)),
    field('tags', 'Tags', tagsInput, {
      hint: 'Separate with commas. Click a tag below to add or remove it.',
      extra: chipBox,
    }),
    field('prompt', 'Prompt', prompt, { hint: 'Enough detail that you could solve it again without the original page.' }),
    field('insight', 'Key insight', insight, { hint: 'The one idea that unlocks the problem.' }),
    field('notes', 'Notes', notes, { optional: true }),
    field('solution', 'Solution', solution, {
      optional: true,
      hint: 'Tab inserts 4 spaces. To leave the field with the keyboard, press Esc and then Tab.',
    }),
    h('div', { class: 'form-grid' }, h('div', null, field('language', 'Language', language), langList)),
    withRating ? [h('hr', { class: 'divider' }), ratingGroup, durationWrap] : null,
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
    language.value = 'python';
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
async function viewAdd(main, { seq }) {
  main.append(pageHead('Add a problem', 'Save a problem you want to keep sharp. Include enough of the prompt to solve it again cold.'));
  const holder = h('div', { class: 'card' }, loadingEl());
  main.append(holder);
  let tags = [];
  try {
    tags = await loadTags();
  } catch (err) {
    toastError(err);
  }
  if (!isCurrent(seq)) return;

  const form = problemForm({
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
      refreshSummary().catch(() => {});
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

// ================================================================ Library view
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
  main.append(filters, meta, listHost);

  let problems = [];
  let fetchToken = 0;

  function setSort(key) {
    if (L.sortKey === key) L.sortDir = -L.sortDir;
    else {
      L.sortKey = key;
      L.sortDir = key === 'reps' || key === 'lapses' ? -1 : 1;
    }
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

    const filtered = Boolean(L.q || L.tag || L.status);
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
      const noneAtAll = !filtered && state.summary && state.summary.counts.total === 0;
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
    load();
    search.focus();
  }
  clearBtn.addEventListener('click', clearFilters);

  const onSearch = debounce(() => {
    L.q = search.value.trim();
    load();
  }, 250);
  onCleanup(onSearch.cancel);
  search.addEventListener('input', onSearch);
  tagSel.addEventListener('change', () => { L.tag = tagSel.value; load(); });
  statusSel.addEventListener('change', () => { L.status = statusSel.value; load(); });
  sortSel.addEventListener('change', () => {
    const [key, dir] = sortSel.value.split(':');
    L.sortKey = key;
    L.sortDir = Number(dir);
    render();
  });

  const [tags] = await Promise.all([
    loadTags().catch(() => []),
    refreshSummary().catch(() => null),
  ]);
  if (!isCurrent(seq)) return;
  tagSel.append(...tags.map((t) => h('option', { value: t.tag }, `${t.tag} (${t.count})`)));
  if (L.tag && !tags.some((t) => t.tag === L.tag)) L.tag = '';
  tagSel.value = L.tag;
  await load();
}

// ================================================================ Problem view
async function viewProblem(main, { seq, params }) {
  const id = Number(params[0]);
  const ctrl = { id, seq, main, mode: 'view', p: null, card: null };
  state.detail = ctrl;
  onCleanup(() => {
    if (ctrl.card) ctrl.card.destroy();
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
  state.activeReview = null;
  document.title = `${p.title} · DSA Review`;

  let focusTarget;
  if (ctrl.mode === 'review') {
    const heading = h('h1', { tabindex: '-1', text: 'Review now' });
    const card = createReviewCard(p, {
      mode: 'single',
      onRated: async (updated) => {
        showReviewToast(updated, 'single');
        ctrl.p = updated;
        ctrl.mode = 'view';
        refreshSummary().catch(() => {});
        renderDetail(ctrl, { focus: true });
      },
      onClose: () => {
        ctrl.mode = 'view';
        renderDetail(ctrl, { focus: true });
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
    main.replaceChildren(backLink(), detailHeader(ctrl, heading), detailBody(p));
    focusTarget = heading;
  }
  if (focus || hadHeadingFocus) {
    window.scrollTo(0, 0);
    focusTarget.focus({ preventScroll: true });
  }
}

function detailHeader(ctrl, heading) {
  const p = ctrl.p;
  const busyWrap = async (btn, fn) => {
    btn.disabled = true;
    try {
      await fn();
    } catch (err) {
      toastError(err);
      if (btn.isConnected) btn.disabled = false;
    }
  };
  const reviewBtn = h('button', { type: 'button', class: 'btn btn-primary' }, 'Review now');
  reviewBtn.addEventListener('click', () => {
    ctrl.mode = 'review';
    renderDetail(ctrl, { focus: true });
  });
  const editBtn = h('button', { type: 'button', class: 'btn' }, 'Edit');
  editBtn.addEventListener('click', () => {
    ctrl.mode = 'edit';
    renderDetail(ctrl, { focus: true });
  });
  const suspendBtn = h('button', { type: 'button', class: 'btn', 'aria-pressed': String(p.suspended) }, p.suspended ? 'Unsuspend' : 'Suspend');
  suspendBtn.addEventListener('click', () => busyWrap(suspendBtn, async () => {
    const updated = await api('PATCH', `/api/problems/${p.id}`, { suspended: !p.suspended });
    toast(updated.suspended ? 'Suspended. It won’t show up in Today until you unsuspend it.' : 'Unsuspended. It’s back on your schedule.', { type: 'success' });
    ctrl.p = updated;
    refreshSummary().catch(() => {});
    renderDetail(ctrl);
    const again = [...ctrl.main.querySelectorAll('.detail-actions button')].find((b) => b.hasAttribute('aria-pressed'));
    if (again) again.focus();
  }));
  const undoBtn = h('button', { type: 'button', class: 'btn', disabled: !p.history.length }, 'Undo last review');
  undoBtn.addEventListener('click', () => busyWrap(undoBtn, async () => {
    const updated = await api('POST', `/api/problems/${p.id}/undo`, {});
    toast('Last review undone. The schedule is back to how it was.', { type: 'success' });
    ctrl.p = updated;
    refreshSummary().catch(() => {});
    renderDetail(ctrl, { focus: true });
  }));
  const deleteBtn = h('button', { type: 'button', class: 'btn btn-danger' }, 'Delete');
  deleteBtn.addEventListener('click', async () => {
    const n = p.history.length;
    const ok = await confirmDialog({
      title: 'Delete this problem?',
      body: `“${p.title}”${n ? ` and its ${plural(n, 'review')}` : ''} will be permanently deleted. This can’t be undone.`,
      confirmLabel: 'Delete problem',
    });
    if (!ok) return;
    await busyWrap(deleteBtn, async () => {
      await api('DELETE', `/api/problems/${p.id}`, {});
      if (state.session) state.session.order = state.session.order.filter((x) => x !== p.id);
      toast(`Deleted “${p.title}”`, { type: 'success' });
      refreshSummary().catch(() => {});
      location.hash = '#/library';
    });
  });

  return h('header', { class: 'detail-head' },
    heading,
    h('div', { class: 'meta-row' },
      statusPill(p),
      diffBadge(p.difficulty),
      p.source ? h('span', { class: 'source', text: p.source }) : null,
      tagChips(p.tags)),
    h('div', { class: 'btn-group detail-actions' }, reviewBtn, editBtn, suspendBtn, undoBtn, deleteBtn));
}

function detailBody(p) {
  const isNew = p.status === 'new';
  const row = (label, value, sub) => h('div', null,
    h('dt', { text: label }),
    h('dd', null, value, sub ? h('span', { class: 'small muted', text: sub }) : null));

  let note = null;
  if (p.suspended) note = 'Suspended. It won’t appear in Today until you unsuspend it.';
  else if (isNew) note = 'Not practiced yet. It’ll show up in Today as a new problem.';

  const recallRow = row('Predicted recall now', pct(p.retrievability));
  if (!isNew) {
    const value = Math.round((p.retrievability || 0) * 100);
    const bar = h('div');
    bar.style.width = `${value}%`;
    recallRow.append(h('div', {
      class: 'recall-meter', 'aria-hidden': 'true',
    }, bar));
  }
  const memory = h('aside', { class: 'card memory', 'aria-labelledby': 'memory-title' },
    h('h2', { id: 'memory-title', text: 'Memory' }),
    note ? h('p', { class: 'memory-note', text: note }) : null,
    h('dl', null,
      row('Status', statusPill(p)),
      row('Next review', isNew ? 'Not scheduled' : fmtDate(p.due), isNew ? null : relShort(p.due)),
      recallRow,
      row('Memory strength', fmtStrength(p.stability)),
      row('Difficulty for you', p.fsrs_difficulty === null ? '—' : `${p.fsrs_difficulty.toFixed(1)} / 10`),
      row('Reviews', String(p.reps)),
      row('Lapses', String(p.lapses)),
      row('Added', fmtDate(p.created_at, { weekday: false }))));

  const section = (label, content) => h('section', { class: 'content-section' }, h('h3', { text: label }), content);
  const emptyNote = (t) => h('p', { class: 'empty-note', text: t });
  const content = h('div', { class: 'card' },
    h('h2', { class: 'sr-only', text: 'Problem content' }),
    section('Prompt', p.prompt.trim() ? h('div', { class: 'prompt', text: p.prompt }) : emptyNote('No prompt saved.')),
    section('Key insight', p.insight
      ? h('div', { class: 'insight' }, h('p', { text: p.insight }))
      : emptyNote('No key insight saved.')),
    section('Notes', p.notes.trim() ? h('div', { class: 'prewrap', text: p.notes }) : emptyNote('No notes saved.')),
    section('Solution', p.solution.trim() ? codeBlock(p.solution, p.language) : emptyNote('No solution saved.')));

  const history = h('section', { class: 'card', 'aria-labelledby': 'history-title' },
    h('div', { class: 'card-head' },
      h('h2', { id: 'history-title', text: 'Review history' }),
      h('p', { text: p.history.length ? plural(p.history.length, 'review') : '' })),
    p.history.length
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
      : h('p', { class: 'empty-note', text: 'No reviews yet.' }));

  return h('div', { class: 'detail-grid' },
    h('div', { class: 'detail-main' }, content, history),
    memory);
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
  const newPerDay = h('input', { type: 'number', min: '0', max: '100', step: '1', inputmode: 'numeric', value: String(s.new_per_day) });
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
      state.session = null;
      refreshSummary().catch(() => {});
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
      numField('set-newper', 'New problems per day', newPerDay, 'Unsolved problems introduced in Today each day.'),
      numField('set-daystart', 'New day starts at', dayStart, 'Reviews after midnight still count for the previous day until this hour.')),
    h('label', { class: 'check', for: 'set-again' },
      againNext,
      h('span', null,
        h('span', { class: 'choice-title', text: 'Again brings it back tomorrow' }),
        h('span', { class: 'choice-desc', text: 'When you rate a problem Again, it is due the next day no matter what the algorithm suggests.' }))),
    paramsRow,
    h('div', { class: 'form-actions' }, saveBtn));

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const mi = intIn(maxInt, 1, 3650);
    const npd = intIn(newPerDay, 0, 100);
    setErr('set-maxint', mi === null ? 'Enter a whole number of days from 1 to 3650.' : '');
    setErr('set-newper', npd === null ? 'Enter a whole number from 0 to 100.' : '');
    if (mi === null || npd === null) {
      (mi === null ? maxInt : newPerDay).focus();
      return;
    }
    saveBtn.disabled = true;
    try {
      // Deliberately no fsrs_parameters here, so saving can't overwrite personalized parameters.
      const r = await api('PATCH', '/api/settings', {
        desired_retention: Number(retention.value),
        maximum_interval: mi,
        again_next_day: againNext.checked,
        new_per_day: npd,
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
      state.session = null;
      refreshSummary().catch(() => {});
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
      refreshSummary().catch(() => {});
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

  main.append(h('div', { class: 'settings-grid' }, schedCard, guideCard, backupCard));
}

// ================================================================ router
const ROUTES = [
  { re: /^\/today$/, nav: 'today', title: 'Today', view: viewToday },
  { re: /^\/add$/, nav: 'add', title: 'Add problem', view: viewAdd },
  { re: /^\/library$/, nav: 'library', title: 'Library', view: viewLibrary },
  { re: /^\/problem\/(\d+)$/, nav: 'library', title: 'Problem', view: viewProblem },
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
if (!/^#\/today/.test(location.hash)) refreshSummary().catch(() => {});
