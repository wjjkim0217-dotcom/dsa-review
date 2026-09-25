# Rebuilding the vendored CodeMirror bundle

DSA Review's in-app code editor (the "Attempt" panel on a problem page, and
"Code it here" on a review card) uses [CodeMirror 6](https://codemirror.net/),
vendored as a single pre-built ES module so the app keeps working offline
with no build step at run time and no external network requests (the strict
Content-Security-Policy only allows scripts from `'self'`).

The published bundle lives at `app/static/vendor/codemirror.bundle.js` and is
imported directly by `app/static/app.js`:

```js
import { createEditor } from './vendor/codemirror.bundle.js';
```

You only need to rebuild it if you change `entry.js` in this folder (for
example, to add a language or keymap feature) - normal app development never
touches this file.

## Rebuild steps

Requires Node.js and npm (not needed to *run* the app - only to rebuild this
one file).

```sh
cd tools/codemirror
npm install
npm run build
```

That installs the CodeMirror packages listed in `package.json` and runs
esbuild:

```sh
esbuild entry.js --bundle --format=esm --minify --target=es2020 \
  --outfile=../../app/static/vendor/codemirror.bundle.js
```

`entry.js` exports one function, `createEditor({ parent, doc, onChange, onRun,
ariaLabel, readOnly })`, which is everything app.js needs - nothing else from
CodeMirror is exposed, so the bundle stays a self-contained module with no
globals. It sets up:

- line numbers, active-line highlight, gutter
- Python syntax highlighting (`@codemirror/lang-python`)
- bracket matching + auto-closing brackets
- undo/redo history
- search (Ctrl/Cmd+F)
- autocomplete
- 4-space indentation; Tab indents/dedents the selection, and (CodeMirror's
  standard behavior) pressing Escape then Tab moves focus out of the editor,
  so keyboard users are never trapped in it
- Ctrl/Cmd+Enter fires the `onRun` callback (wired to the Run button)
- a theme built from this app's own CSS custom properties (`var(--text)`,
  `var(--surface-2)`, etc. - see `styles.css`), so the editor follows the
  app's light/dark mode automatically instead of shipping a separate
  hardcoded dark theme

After rebuilding, reload the app in a browser and check the DevTools console
for CSP violations - none are expected, since CodeMirror only injects
`<style>` elements (allowed by `style-src 'self' 'unsafe-inline'`) and never
uses `eval`.

`app/static/vendor/CODEMIRROR-LICENSE.txt` is CodeMirror's MIT license text,
copied from `node_modules/codemirror/LICENSE` after `npm install`.
