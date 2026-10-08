'use strict';

const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('chaplinShell', {
  onStatus: (handler) => {
    const listener = (_event, payload) => handler(payload);
    ipcRenderer.on('status', listener);
    return () => ipcRenderer.removeListener('status', listener);
  },
  onToggleRecord: (handler) => {
    const listener = () => handler();
    ipcRenderer.on('shell:toggle-record', listener);
    return () => ipcRenderer.removeListener('shell:toggle-record', listener);
  },
  minimize: () => ipcRenderer.send('shell:minimize'),
  copy: (text) => ipcRenderer.invoke('shell:copy', text),

  // HUD (both windows share this preload).
  hudState: (payload) => ipcRenderer.send('shell:hud-state', payload),
  onHudState: (handler) => {
    const listener = (_event, payload) => handler(payload);
    ipcRenderer.on('hud:state', listener);
    return () => ipcRenderer.removeListener('hud:state', listener);
  },
  onBubble: (handler) => {
    const listener = (_event, payload) => handler(payload);
    ipcRenderer.on('bubble:state', listener);
    return () => ipcRenderer.removeListener('bubble:state', listener);
  },
  transcript: (text) => ipcRenderer.send('bubble:transcript', text),
  paste: () => ipcRenderer.send('shell:paste'),
  onBubbleMode: (handler) => {
    const listener = (_event, mode) => handler(mode);
    ipcRenderer.on('bubble:mode', listener);
    return () => ipcRenderer.removeListener('bubble:mode', listener);
  },
  getBubbleMode: () => ipcRenderer.invoke('shell:get-bubble-mode'),
  getLiveTranscript: () => ipcRenderer.invoke('shell:get-live-transcript'),
  getHotkey: () => ipcRenderer.invoke('shell:get-hotkey'),
  setHotkey: (accel) => ipcRenderer.invoke('shell:set-hotkey', accel),
  onRules: (handler) => {
    const listener = (_event, rules) => handler(rules);
    ipcRenderer.on('bubble:rules', listener);
    return () => ipcRenderer.removeListener('bubble:rules', listener);
  },
  getReplacements: () => ipcRenderer.invoke('shell:get-replacements'),
  setReplacements: (list) => ipcRenderer.send('shell:set-replacements', list),
  onResult: (handler) => {
    const listener = (_event, payload) => handler(payload);
    ipcRenderer.on('result:show', listener);
    return () => ipcRenderer.removeListener('result:show', listener);
  },
  resultCopy: (text) => ipcRenderer.invoke('result:copy', text),
  resultClose: () => ipcRenderer.send('result:close'),
  setBubbleMode: (mode) => ipcRenderer.send('shell:bubble-mode', mode),
  hudAction: (action) => ipcRenderer.send('hud:action', action),
  onHudAction: (handler) => {
    const listener = (_event, action) => handler(action);
    ipcRenderer.on('shell:hud-action', listener);
    return () => ipcRenderer.removeListener('shell:hud-action', listener);
  },
});
