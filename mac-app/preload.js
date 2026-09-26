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
  hudAction: (action) => ipcRenderer.send('hud:action', action),
  onHudAction: (handler) => {
    const listener = (_event, action) => handler(action);
    ipcRenderer.on('shell:hud-action', listener);
    return () => ipcRenderer.removeListener('shell:hud-action', listener);
  },
});
