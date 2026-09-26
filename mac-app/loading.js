'use strict';

const wrap = document.getElementById('wrap');
const sub = document.getElementById('sub');
const detail = document.getElementById('detail');

if (window.chaplinShell) {
  window.chaplinShell.onStatus((payload) => {
    if (!payload) return;
    if (payload.state === 'starting') {
      sub.textContent = payload.message || 'Starting the Chaplin server…';
    } else if (payload.state === 'ready') {
      sub.textContent = 'Ready — loading the interface…';
    } else if (payload.state === 'error') {
      wrap.classList.add('error');
      sub.textContent = 'Chaplin could not start.';
      detail.textContent = payload.message || '';
    }
  });
}
