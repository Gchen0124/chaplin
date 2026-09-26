'use strict';

/* Bottom-of-screen recording HUD.
 *
 * States pushed from the main process: recording | processing | done | error.
 * Buttons call back into main, which forwards the action to the app renderer.
 */

const views = {
  recording: document.getElementById('recording'),
  processing: document.getElementById('processing'),
  done: document.getElementById('done'),
  error: document.getElementById('error'),
};

const timerEl = document.getElementById('timer');
const doneLabel = document.getElementById('doneLabel');
const errorMsg = document.getElementById('errorMsg');

let tick = null;
let startedAt = 0;
let accumulated = 0;
let last = 'idle';

function show(state) {
  Object.entries(views).forEach(([name, el]) => el.classList.toggle('on', name === state));
  views.recording.classList.toggle('paused', state === 'paused');
}

function stopTimer() {
  if (tick) clearInterval(tick);
  tick = null;
}

function fmt(ms) {
  const total = Math.floor(ms / 1000);
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, '0')}`;
}

function renderClock() {
  const live = startedAt ? Date.now() - startedAt : 0;
  timerEl.textContent = fmt(accumulated + live);
}

function runTimer(reset) {
  if (reset) { accumulated = 0; }
  stopTimer();
  startedAt = Date.now();
  renderClock();
  tick = setInterval(renderClock, 250);
}

function freezeTimer() {
  if (startedAt) accumulated += Date.now() - startedAt;
  startedAt = 0;
  stopTimer();
  renderClock();
}

function apply(payload) {
  const state = typeof payload === 'string' ? payload : (payload && payload.state) || 'idle';
  if (state === 'recording') {
    show('recording');
    runTimer(last !== 'paused');
    last = state;
    return;
  }
  if (state === 'paused') {
    show('paused');
    freezeTimer();
    last = state;
    return;
  }
  stopTimer();
  last = state;
  if (state === 'processing') return show('processing');
  if (state === 'done') {
    doneLabel.textContent = (payload && payload.text) || 'Copied to clipboard';
    return show('done');
  }
  if (state === 'error') {
    errorMsg.textContent = (payload && payload.text) || 'Something went wrong';
    return show('error');
  }
  show('none');
}

function act(action) {
  window.chaplinShell.hudAction(action);
}

document.getElementById('pause').onclick = () => act('pause');
document.getElementById('stop').onclick = () => act('stop');
document.getElementById('cancel').onclick = () => act('cancel');
document.getElementById('dismiss').onclick = () => act('confirm');
document.getElementById('confirm').onclick = () => act('confirm');
document.getElementById('errorDismiss').onclick = () => act('confirm');

if (window.chaplinShell && window.chaplinShell.onHudState) {
  window.chaplinShell.onHudState(apply);
}
