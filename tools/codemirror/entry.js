// Entry point for the vendored CodeMirror 6 bundle used by DSA Review's
// in-app code editor (the "Attempt" panel and review-card "Code it here").
//
// This file is bundled with esbuild into app/static/vendor/codemirror.bundle.js
// (see build.md in this folder for the exact command). app.js imports the
// single named export `createEditor` from that bundle - nothing else is
// exposed, so the bundle stays a plain ES module with no globals.

import { EditorState, Compartment } from '@codemirror/state';
import {
  EditorView, keymap, lineNumbers, highlightActiveLine, highlightActiveLineGutter,
  drawSelection, dropCursor, rectangularSelection, crosshairCursor,
} from '@codemirror/view';
import {
  defaultKeymap, history, historyKeymap, indentWithTab,
} from '@codemirror/commands';
import {
  indentOnInput, indentUnit, bracketMatching, foldGutter, foldKeymap,
  syntaxHighlighting, HighlightStyle, defaultHighlightStyle,
} from '@codemirror/language';
import {
  closeBrackets, closeBracketsKeymap, autocompletion, completionKeymap,
} from '@codemirror/autocomplete';
import { searchKeymap, highlightSelectionMatches } from '@codemirror/search';
import { python } from '@codemirror/lang-python';
import { tags as t } from '@lezer/highlight';

// The theme reads the same CSS custom properties the rest of the app uses
// (see styles.css :root / prefers-color-scheme), so the editor follows
// light/dark automatically without a separate dark theme to keep in sync.
const appTheme = EditorView.theme({
  '&': {
    color: 'var(--text)',
    backgroundColor: 'var(--surface-2)',
    borderRadius: 'var(--radius-sm)',
    border: '1px solid var(--border)',
  },
  '&.cm-focused': {
    outline: '2px solid var(--focus)',
    outlineOffset: '1px',
  },
  '.cm-content': {
    caretColor: 'var(--text)',
    fontFamily: 'var(--mono)',
    fontSize: '13.5px',
    padding: '10px 0',
  },
  '.cm-cursor, .cm-dropCursor': { borderLeftColor: 'var(--text)' },
  '&.cm-editor .cm-selectionBackground, ::selection': { backgroundColor: 'var(--accent-soft)' },
  '.cm-gutters': {
    backgroundColor: 'var(--surface-2)',
    color: 'var(--muted)',
    border: 'none',
    borderRight: '1px solid var(--border)',
  },
  '.cm-activeLine': { backgroundColor: 'var(--surface-3)' },
  '.cm-activeLineGutter': { backgroundColor: 'var(--surface-3)', color: 'var(--text-2)' },
  '.cm-matchingBracket, .cm-nonmatchingBracket': {
    backgroundColor: 'var(--accent-soft)',
    outline: '1px solid var(--accent)',
  },
  '.cm-tooltip': {
    backgroundColor: 'var(--surface)',
    border: '1px solid var(--border)',
    borderRadius: 'var(--radius-sm)',
    color: 'var(--text)',
  },
  '.cm-tooltip.cm-tooltip-autocomplete > ul > li[aria-selected]': {
    backgroundColor: 'var(--accent-soft)',
    color: 'var(--accent-soft-text)',
  },
  '.cm-scroller': { overflow: 'auto', fontFamily: 'var(--mono)' },
  '.cm-searchMatch': { backgroundColor: 'var(--hard-soft)', outline: '1px solid var(--hard-border)' },
  '.cm-searchMatch-selected': { backgroundColor: 'var(--accent-soft)' },
}, { dark: false });

// Syntax colors as CSS variables too, so both themes stay consistent with the
// rest of the app's palette instead of a fixed set of hex colors.
const appHighlight = HighlightStyle.define([
  { tag: t.keyword, color: 'var(--accent)' },
  { tag: [t.name, t.propertyName], color: 'var(--text)' },
  { tag: [t.function(t.variableName), t.function(t.propertyName)], color: 'var(--easy)' },
  { tag: t.definition(t.variableName), color: 'var(--text)' },
  { tag: [t.string, t.special(t.string)], color: 'var(--good)' },
  { tag: [t.number, t.bool, t.null], color: 'var(--violet)' },
  { tag: t.comment, color: 'var(--muted)', fontStyle: 'italic' },
  { tag: t.operator, color: 'var(--text-2)' },
  { tag: t.className, color: 'var(--hard)' },
  { tag: t.invalid, color: 'var(--again)' },
], { themeType: undefined });

/**
 * Create a CodeMirror 6 editor bound to `parent`.
 *
 * options:
 *   parent    - DOM element to mount into (required)
 *   doc       - initial text
 *   onChange  - (text) => void, called after any document change
 *   onRun     - () => void, bound to Ctrl/Cmd+Enter (optional)
 *   ariaLabel - accessible label for the editable region (optional)
 *   readOnly  - boolean (optional)
 *
 * Returns { view, getValue(), setValue(text), focus(), destroy() }.
 */
export function createEditor({ parent, doc = '', onChange, onRun, ariaLabel, readOnly = false }) {
  const readOnlyCompartment = new Compartment();

  const runKeymap = onRun
    ? [{ key: 'Mod-Enter', run: () => { onRun(); return true; }, preventDefault: true }]
    : [];

  const extensions = [
    lineNumbers(),
    highlightActiveLineGutter(),
    highlightActiveLine(),
    history(),
    drawSelection(),
    dropCursor(),
    EditorState.allowMultipleSelections.of(true),
    indentOnInput(),
    indentUnit.of('    '), // 4 spaces, Python style
    syntaxHighlighting(appHighlight, { fallback: true }),
    syntaxHighlighting(defaultHighlightStyle, { fallback: true }),
    bracketMatching(),
    closeBrackets(),
    autocompletion(),
    rectangularSelection(),
    crosshairCursor(),
    highlightSelectionMatches(),
    foldGutter(),
    python(),
    appTheme,
    EditorView.lineWrapping,
    readOnlyCompartment.of(EditorState.readOnly.of(readOnly)),
    keymap.of([
      ...runKeymap,
      ...closeBracketsKeymap,
      ...defaultKeymap,
      ...searchKeymap,
      ...historyKeymap,
      ...foldKeymap,
      ...completionKeymap,
      indentWithTab, // Tab indents/dedents; Escape first, then Tab, moves focus out
    ]),
    EditorView.updateListener.of((update) => {
      if (update.docChanged && onChange) onChange(update.state.doc.toString());
    }),
    EditorView.contentAttributes.of({ 'aria-label': ariaLabel || 'Code editor' }),
  ];

  const state = EditorState.create({ doc, extensions });
  const view = new EditorView({ state, parent });

  return {
    view,
    getValue: () => view.state.doc.toString(),
    setValue(text) {
      view.dispatch({ changes: { from: 0, to: view.state.doc.length, insert: text || '' } });
    },
    setReadOnly(value) {
      view.dispatch({ effects: readOnlyCompartment.reconfigure(EditorState.readOnly.of(value)) });
    },
    focus: () => view.focus(),
    destroy: () => view.destroy(),
  };
}
