'use strict';

/* Editable refined-text card.
 *
 * Shown when the AI finishes. The text is already on the clipboard, but the
 * user can tweak it here and hit Copy to put the edited version back.
 * Main drives it with `result:show` {text}; the buttons call back into main.
 */

const ta = document.getElementById('text');
const hint = document.getElementById('hint');

function flash(text) {
  hint.textContent = text;
  setTimeout(() => { if (hint.textContent === text) hint.textContent = ''; }, 1500);
}

function api(name) {
  return window.chaplinShell && window.chaplinShell[name] ? window.chaplinShell[name] : null;
}

document.getElementById('copy').onclick = async () => {
  const copy = api('resultCopy');
  if (!copy) return;
  await copy(ta.value);
  flash('copied ✓');
};

document.getElementById('close').onclick = () => {
  const close = api('resultClose');
  if (close) close();
};

window.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') {
    const close = api('resultClose');
    if (close) close();
  }
});

if (window.chaplinShell && window.chaplinShell.onResult) {
  window.chaplinShell.onResult((payload) => {
    if (!payload) return;
    ta.value = payload.text || '';
    hint.textContent = '';
  });
}
