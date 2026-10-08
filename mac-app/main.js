'use strict';

/**
 * Chaplin for macOS.
 *
 * Double-clicking this app:
 *   1. starts the original Chaplin FastAPI server (the same one web_chaplin.py runs)
 *   2. waits until it answers /healthz
 *   3. opens a native window showing the real Practice / Demo / Review interface
 *
 * Everything the browser UI does (camera, mic, screen capture, file upload)
 * works unchanged, because it is the same web app — just hosted in its own
 * window instead of a browser tab.
 */

const { app, BrowserWindow, Menu, shell, dialog, ipcMain, globalShortcut, session, screen, Tray, nativeImage, clipboard } = require('electron');
const { spawn, execFile } = require('child_process');
const fs = require('fs');
const http = require('http');
const net = require('net');
const os = require('os');
const path = require('path');

// Summon hotkey: ⌃⌥R (Control+Option+R) brings the window up and starts a take.
// Deliberately avoids ⌘Space / ⌥⌘Space (Spotlight + Finder search) and
// ⌃⌥Space (macOS input-source switching, also claimed by mac-electron).
const SUMMON_HOTKEY = 'Control+Alt+R';
const SUMMON_FALLBACKS = ['Control+Alt+Command+R', 'Control+Shift+Alt+R'];

let summonHotkey = null;
let savedHotkey = SUMMON_HOTKEY;   // user-configurable; persisted in ui.json
let isQuitting = false;
let appPageReady = false;
let pendingToggle = false;
let hud = null;
let hudHideTimer = null;
let bubble = null;
let bubbleVisible = false;
let resultWin = null;
let tray = null;
let lastHudState = 'idle';
let liveTranscript = '';   // latest Doubao live transcript from the bubble
let autoPaste = true;      // paste the finished text into the focused app
// Custom dictionary applied to the live transcript: [{from, to}]. Fixes the
// odd mis-recognition and lets a spoken keyword stand in for a saved field.
let replacements = [];

// Camera bubble appearance. Layout: corner | meeting. Shape (corner only):
// wide (4:3) | square | portrait (9:16) | circle.
let bubbleMode = { layout: 'corner', shape: 'wide' };
const BUBBLE_WIDTH = { wide: 248, square: 232, portrait: 200, circle: 232 };
const BUBBLE_RATIO = { wide: 0.75, square: 1, portrait: 16 / 9, circle: 1 };
const BUBBLE_CAPS = 84;

function uiStorePath() {
  return path.join(app.getPath('userData'), 'ui.json');
}

function loadBubbleMode() {
  try {
    const raw = JSON.parse(fs.readFileSync(uiStorePath(), 'utf8'));
    if (raw && raw.bubbleMode) bubbleMode = { ...bubbleMode, ...raw.bubbleMode };
    if (raw && typeof raw.hotkey === 'string' && raw.hotkey) savedHotkey = raw.hotkey;
    if (raw && Array.isArray(raw.replacements)) replacements = raw.replacements;
  } catch { /* defaults */ }
}

function saveBubbleMode() {
  try {
    fs.writeFileSync(uiStorePath(), JSON.stringify({
      bubbleMode,
      hotkey: summonHotkey || savedHotkey,
      replacements,
    }));
  } catch { /* ignore */ }
}

/** Push the current dictionary to the bubble so it can rewrite captions. */
function sendRules() {
  if (bubble && !bubble.isDestroyed()) bubble.webContents.send('bubble:rules', replacements);
}

function bubbleGeometry() {
  const { workArea } = screen.getPrimaryDisplay();
  if (bubbleMode.layout === 'meeting') {
    const width = Math.round(workArea.width * 0.5);
    const height = Math.round(workArea.height - 48);
    return {
      width,
      height,
      x: Math.round(workArea.x + (workArea.width - width) / 2),
      y: Math.round(workArea.y + 24),
    };
  }
  const shape = BUBBLE_WIDTH[bubbleMode.shape] ? bubbleMode.shape : 'wide';
  const width = BUBBLE_WIDTH[shape];
  const height = Math.round(width * BUBBLE_RATIO[shape]) + BUBBLE_CAPS;
  return {
    width,
    height,
    x: Math.round(workArea.x + workArea.width - width - 20),
    y: Math.round(workArea.y + workArea.height - height - 20),
  };
}

// ---------------------------------------------------------------------------
// Where is the Python project?
// ---------------------------------------------------------------------------

/**
 * Resolve the Chaplin source tree. In development this is the repo root
 * (one level up); inside a packaged .app it is bundled under Resources.
 */
function resolveProjectDir() {
  const candidates = [
    process.env.CHAPLIN_PROJECT_DIR,
    path.join(__dirname, '..'),                                  // dev: mac-app/..
    path.join(process.resourcesPath || '', 'chaplin-src'),       // packaged
  ].filter(Boolean);

  for (const dir of candidates) {
    if (dir && fs.existsSync(path.join(dir, 'web_chaplin.py'))) return dir;
  }
  return null;
}

// ---------------------------------------------------------------------------
// Python discovery
// ---------------------------------------------------------------------------

/**
 * Find a Python that can actually import the app's dependencies.
 *
 * A packaged .app is launched by launchd and does NOT inherit the user's shell
 * PATH, so `python3` may resolve to /usr/bin/python3 (which has no FastAPI).
 * We therefore probe a list of known locations AND every pyenv/conda/brew
 * python we can discover, and remember the first one that works.
 */
function findPython(projectDir) {
  const home = os.homedir();
  const candidates = [
    process.env.CHAPLIN_PYTHON,
    path.join(projectDir, '.venv', 'bin', 'python'),
    path.join(projectDir, 'venv', 'bin', 'python'),
    '/opt/homebrew/bin/python3',
    '/usr/local/bin/python3',
    '/opt/anaconda3/bin/python3',
    path.join(home, 'miniconda3', 'bin', 'python'),
    path.join(home, 'anaconda3', 'bin', 'python'),
    path.join(home, '.local', 'bin', 'python3'),
    path.join(home, 'opt', 'anaconda3', 'bin', 'python3'),
    '/usr/bin/python3',
  ].filter(Boolean);

  // Also pick up anything conda/pyenv have installed, newest-looking first.
  const globs = [
    path.join(home, 'opt', '*', 'bin', 'python3'),
    path.join(home, 'miniforge3', 'bin', 'python3'),
    path.join(home, '.pyenv', 'versions', '*', 'bin', 'python3'),
    '/opt/homebrew/opt/python@*/bin/python3',
  ];
  for (const pattern of globs) {
    try {
      const { execFileSync } = require('child_process');
      const matches = execFileSync('/bin/sh', ['-c', `ls -d ${pattern} 2>/dev/null`])
        .toString()
        .split('\n')
        .filter(Boolean);
      candidates.push(...matches);
    } catch { /* no matches */ }
  }

  const probe = 'import fastapi, uvicorn; print("ok")';
  const seen = new Set();
  for (const py of candidates) {
    if (!py || seen.has(py)) continue;
    seen.add(py);
    if (!fs.existsSync(py)) continue;
    try {
      const { execFileSync } = require('child_process');
      const out = execFileSync(py, ['-c', probe], {
        timeout: 30000,
        env: Object.assign({}, process.env, {
          PATH: `/opt/homebrew/bin:/usr/local/bin:${process.env.PATH || ''}`,
        }),
      }).toString();
      if (out.includes('ok')) {
        console.log(`[chaplin] using python: ${py}`);
        return py;
      }
    } catch {
      /* try the next one */
    }
  }
  return null;
}

// ---------------------------------------------------------------------------
// Ports
// ---------------------------------------------------------------------------

function findFreePort(preferred, maxTries = 25) {
  return new Promise((resolve, reject) => {
    let port = preferred;
    let tries = 0;
    const attempt = () => {
      const server = net.createServer();
      server.unref();
      server.on('error', (err) => {
        if (err.code === 'EADDRINUSE' && tries < maxTries) {
          tries += 1;
          port += 1;
          attempt();
        } else {
          reject(err);
        }
      });
      server.listen(port, '127.0.0.1', () => {
        server.close(() => resolve(port));
      });
    };
    attempt();
  });
}

function waitForServer(port, timeoutMs = 180000) {
  const started = Date.now();
  return new Promise((resolve) => {
    const poll = () => {
      if (Date.now() - started > timeoutMs) return resolve(false);
      const req = http.get(
        { host: '127.0.0.1', port, path: '/healthz', timeout: 2000 },
        (res) => {
          res.resume();
          if (res.statusCode === 200) return resolve(true);
          setTimeout(poll, 400);
        }
      );
      req.on('error', () => setTimeout(poll, 400));
      req.on('timeout', () => {
        req.destroy();
        setTimeout(poll, 400);
      });
    };
    poll();
  });
}

// ---------------------------------------------------------------------------
// Server process
// ---------------------------------------------------------------------------

let serverProcess = null;
let serverPort = null;
let serverLog = [];
let mainWindow = null;

function rememberLog(chunk) {
  const text = chunk.toString();
  serverLog.push(text);
  if (serverLog.length > 200) serverLog = serverLog.slice(-200);
  process.stdout.write(`[server] ${text}`);
}

async function startServer(projectDir) {
  const python = findPython(projectDir);
  if (!python) {
    throw new Error(
      'No Python with FastAPI + uvicorn was found.\n\n' +
        'Install them, or set CHAPLIN_PYTHON to a suitable interpreter:\n' +
        '  pip install fastapi "uvicorn[standard]"'
    );
  }

  serverPort = await findFreePort(Number(process.env.CHAPLIN_PORT) || 8765);

  const env = Object.assign({}, process.env, {
    CHAPLIN_HOST: '127.0.0.1',
    CHAPLIN_PORT: String(serverPort),
    PYTHONUNBUFFERED: '1',
    // launchd gives us a minimal PATH; make sure ffmpeg/ffprobe and the usual
    // tool locations are reachable for audio extraction.
    PATH: [
      '/opt/homebrew/bin',
      '/usr/local/bin',
      '/opt/anaconda3/bin',
      path.dirname(python),
      process.env.PATH || '/usr/bin:/bin:/usr/sbin:/sbin',
    ].join(':'),
  });

  // The lip-reading model needs torch + torchaudio, which are frequently
  // mismatched on user machines. Detect that up front so the app still opens
  // (speech-to-text and Demo Studio work fine without it) instead of dying.
  if (process.env.CHAPLIN_FORCE_VSR !== '1') {
    env.CHAPLIN_SKIP_VSR = '1';
  }

  serverProcess = spawn(python, ['web_chaplin.py'], {
    cwd: projectDir,
    env,
    stdio: ['ignore', 'pipe', 'pipe'],
  });

  serverProcess.stdout.on('data', rememberLog);
  serverProcess.stderr.on('data', rememberLog);
  serverProcess.on('exit', (code, signal) => {
    console.log(`[server] exited code=${code} signal=${signal}`);
    serverProcess = null;
    if (mainWindow && !mainWindow.isDestroyed() && code !== 0 && code !== null) {
      showError(
        `The Chaplin server stopped unexpectedly (exit ${code}).\n\n` +
          serverLog.slice(-25).join('')
      );
    }
  });

  const ok = await waitForServer(serverPort);
  if (!ok) {
    throw new Error(
      'The Chaplin server did not start in time.\n\n' + serverLog.slice(-30).join('')
    );
  }
  return { python, port: serverPort };
}

function stopServer() {
  if (serverProcess && !serverProcess.killed) {
    try {
      serverProcess.kill('SIGTERM');
    } catch { /* already gone */ }
    serverProcess = null;
  }
}

// ---------------------------------------------------------------------------
// Windows
// ---------------------------------------------------------------------------

function createWindow(port) {
  mainWindow = new BrowserWindow({
    width: 1320,
    height: 900,
    minWidth: 900,
    minHeight: 640,
    title: 'Chaplin',
    backgroundColor: '#0b0d12',
    show: false,
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      preload: path.join(__dirname, 'preload.js'),
      // Keep recording/serving while the window is hidden behind the bubble.
      backgroundThrottling: false,
    },
  });

  mainWindow.once('ready-to-show', () => mainWindow.show());

  // Hide instead of quitting so the summon hotkey keeps working in the
  // background. Real quit goes through the app menu / Dock (⌘Q).
  mainWindow.on('close', (event) => {
    if (!isQuitting) {
      event.preventDefault();
      mainWindow.hide();
    }
  });

  // Anything trying to open a new window goes to the real browser instead.
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url);
    return { action: 'deny' };
  });

  mainWindow.loadFile(path.join(__dirname, 'loading.html'));
  return mainWindow;
}

/**
 * Bottom-of-screen status pill: ● recording, ■ stop, ✕ cancel, ✓ done.
 * Frameless, always on top, and non-focusable so it never steals typing focus.
 */
function hudGeometry(tall) {
  const { workArea } = screen.getPrimaryDisplay();
  const width = tall ? 480 : 340;
  const height = tall ? 168 : 56;
  return {
    width,
    height,
    x: Math.round(workArea.x + (workArea.width - width) / 2),
    y: Math.round(workArea.y + workArea.height - height - 16),
  };
}

function createHud() {
  const g = hudGeometry(false);

  hud = new BrowserWindow({
    width: g.width,
    height: g.height,
    x: g.x,
    y: g.y,
    frame: false,
    transparent: true,
    resizable: false,
    movable: false,
    minimizable: false,
    maximizable: false,
    fullscreenable: false,
    skipTaskbar: true,
    show: false,
    hasShadow: false,
    focusable: false,
    acceptFirstMouse: true,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  hud.loadFile(path.join(__dirname, 'hud.html'));
  // One notch above 'screen-saver' so the bar floats over other always-on-top
  // windows (e.g. a pinned workbench app).
  hud.setAlwaysOnTop(true, 'screen-saver', 1);
  hud.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true });
  hud.setAlwaysOnTop(true, 'screen-saver', 1);
  hud.on('closed', () => { hud = null; });
}

/**
 * Small floating self-view: mirrored camera + live (Doubao) captions. Pops up
 * with the HUD while recording, hidden the moment the take ends.
 */
function createBubble() {
  const g = bubbleGeometry();

  bubble = new BrowserWindow({
    width: g.width,
    height: g.height,
    x: g.x,
    y: g.y,
    frame: false,
    transparent: true,
    resizable: false,
    movable: true,
    minimizable: false,
    maximizable: false,
    fullscreenable: false,
    skipTaskbar: true,
    show: false,
    hasShadow: false,
    focusable: false,
    acceptFirstMouse: true,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  bubble.loadFile(path.join(__dirname, 'bubble.html'));
  bubble.setAlwaysOnTop(true, 'screen-saver', 1);
  bubble.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true });
  bubble.setAlwaysOnTop(true, 'screen-saver', 1);
  bubble.on('closed', () => { bubble = null; bubbleVisible = false; });
  bubble.webContents.on('did-finish-load', () => {
    bubble.webContents.send('bubble:mode', bubbleMode);
    sendRules();
    raiseBubble();
  });
}

/** Re-assert the top-most level (over other always-on-top windows). */
function raiseBubble() {
  if (!bubble || bubble.isDestroyed()) return;
  bubble.setAlwaysOnTop(true, 'screen-saver', 1);
  if (bubbleVisible) bubble.showInactive();
}

function applyBubbleMode() {
  if (!bubble || bubble.isDestroyed()) return;
  const g = bubbleGeometry();
  bubble.setBounds({ x: g.x, y: g.y, width: g.width, height: g.height });
  bubble.webContents.send('bubble:mode', bubbleMode);
  raiseBubble();
}

function showBubble() {
  if (!bubble) createBubble();
  bubble.webContents.send('bubble:mode', bubbleMode);
  bubble.webContents.send('bubble:state', { state: 'start', port: serverPort });
  bubble.showInactive();
  bubbleVisible = true;
  raiseBubble();
}

function hideBubble() {
  if (!bubble) return;
  bubble.webContents.send('bubble:state', { state: 'stop' });
  if (bubbleVisible) {
    bubble.hide();
    bubbleVisible = false;
  }
}

/**
 * Editable refined-text card. Stays until closed; the text is already on the
 * clipboard, and Copy puts an edited version back.
 */
function createResult() {
  const { workArea } = screen.getPrimaryDisplay();
  const width = 560;
  const height = 190;
  resultWin = new BrowserWindow({
    width,
    height,
    x: Math.round(workArea.x + (workArea.width - width) / 2),
    y: Math.round(workArea.y + workArea.height - height - 96),
    frame: false,
    transparent: true,
    resizable: true,
    movable: true,
    minimizable: false,
    maximizable: false,
    fullscreenable: false,
    skipTaskbar: true,
    show: false,
    hasShadow: false,
    focusable: true,
    acceptFirstMouse: true,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });
  resultWin.loadFile(path.join(__dirname, 'result.html'));
  resultWin.setAlwaysOnTop(true, 'screen-saver', 2);
  resultWin.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true });
  resultWin.on('closed', () => { resultWin = null; });
}

function showResult(text) {
  if (!resultWin) createResult();
  resultWin.webContents.send('result:show', { text: text || '' });
  resultWin.showInactive();
}

function hideResult() {
  if (resultWin && !resultWin.isDestroyed()) resultWin.hide();
}

function updateHud(payload) {
  clearTimeout(hudHideTimer);
  const state = typeof payload === 'string' ? payload : (payload && payload.state) || 'idle';
  if (!hud) createHud();
  // The moment recording stops: put the live transcript in the clipboard AND
  // drop it straight into the focused app, so words appear as fast as possible.
  if (state === 'processing' && liveTranscript.trim()) {
    clipboard.writeText(liveTranscript.trim());
    pasteClipboard();
  }
  // Errors get the taller card; the refined text lives in its own editable card.
  hud.setBounds(hudGeometry(state === 'error'));
  hud.webContents.send('hud:state', payload);
  if (state !== lastHudState) {
    lastHudState = state;
    rebuildTray();
  }
  // Take is over (or cancelled): stop floating above other apps.
  if ((state === 'done' || state === 'error' || state === 'idle') &&
      mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.setAlwaysOnTop(false);
  }
  // Camera bubble follows the take (stays up while paused).
  if (state === 'recording' || state === 'paused') showBubble();
  else hideBubble();

  // Editable refined card: appears when the AI lands, dismissed by user or a new take.
  if (state === 'done' && payload && payload.text) showResult(payload.text);
  else if (state === 'recording' || state === 'idle') hideResult();

  if (state === 'idle') {
    hud.hide();
    return;
  }
  hud.showInactive();
  if (state === 'done' || state === 'error') {
    hudHideTimer = setTimeout(() => { if (hud) hud.hide(); }, 9000);
  }
}

// ---------------------------------------------------------------------------
// Menu bar (tray) icon
// ---------------------------------------------------------------------------

// Chaplin mark (22pt @2x) as a PNG template image. nativeImage cannot decode
// SVG, so the bitmap is embedded here rather than built from markup.
const TRAY_PNG_B64 =
  'iVBORw0KGgoAAAANSUhEUgAAACwAAAAsCAYAAAAehFoBAAABA0lEQVR42u2ZyQ6DMAxEMxb//8vTO2IxeGycKtxQS/068c4Yk10osEGlbTiNQAQZFg5OYwjAQqn4FTBO7p+CStWGyOe4+x6zYsvEQaWAvTyVrSAjQBSYEoV5c6wIfM43wCjI76hSWFkQ3G5jY8xVga1piWc2sBcinPJMrAgcoCHft+adIZ4WDib5JU/+AKIKo1v66JrWFvAC/lvgrRnP7fBrjWGn6dZQ0a2VdGorSyzgBTxZ4cAXz9rNj0UbeH7hEsgyngXMl4qdDabIAEbQTTzzW2rQ0aHeUafF3Qo2DI6koMGL9ySufUXW1MyRNH1bwd5Mmq/bvM5yrr1kR956+fLp9QPjpjlaUNJCbQAAAABJRU5ErkJggg==';

function trayImage() {
  const image = nativeImage.createFromBuffer(Buffer.from(TRAY_PNG_B64, 'base64'), { scaleFactor: 2 });
  image.setTemplateImage(true);
  return image;
}

/** Paste whatever is on the clipboard into the frontmost app (⌘V). */
function pasteClipboard() {
  if (!autoPaste) return;
  execFile('osascript',
    ['-e', 'tell application "System Events" to keystroke "v" using command down'],
    (err) => { if (err) console.error('[chaplin] auto-paste failed:', String(err.message).split('\n')[0]); });
}

function showMainWindow() {
  if (mainWindow && !mainWindow.isDestroyed()) {
    if (mainWindow.isMinimized()) mainWindow.restore();
    mainWindow.show();
    if (process.platform === 'darwin') app.focus({ steal: true });
    mainWindow.focus();
  }
}

function rebuildTray() {
  if (!tray) return;
  const recording = lastHudState === 'recording';
  const busy = lastHudState === 'processing';
  if (process.platform === 'darwin' && tray.setTitle) tray.setTitle(recording ? '●' : '');
  tray.setToolTip(`Chaplin — ${recording ? 'Recording…' : busy ? 'Transcribing…' : 'Ready'}`);

  tray.setContextMenu(Menu.buildFromTemplate([
    { label: recording ? '● Recording…' : busy ? 'Transcribing…' : 'Chaplin — Ready', enabled: false },
    { type: 'separator' },
    { label: `Record / stop (${hotkeyDisplay()})`, click: () => onSummonHotkey() },
    { label: 'Show Chaplin', click: () => showMainWindow() },
    { label: 'Open in Browser', click: () => { if (serverPort) shell.openExternal(`http://127.0.0.1:${serverPort}/index.html`); } },
    { type: 'separator' },
    { type: 'separator' },
    {
      label: 'Auto-paste into focused app',
      type: 'checkbox',
      checked: autoPaste,
      click: (item) => { autoPaste = item.checked; rebuildTray(); },
    },
    { type: 'separator' },
    { label: 'Quit Chaplin', accelerator: 'Command+Q', click: () => app.quit() },
  ]));
}

function createTray() {
  const image = trayImage();
  console.log(`[chaplin] tray icon ${JSON.stringify(image.getSize())} empty=${image.isEmpty()}`);
  tray = new Tray(image);
  // Reliquary-style fallback: a glyph title always renders, even if the bitmap
  // fails to decode on some macOS/Electron combination.
  if (process.platform === 'darwin' && tray.setTitle && image.isEmpty()) {
    tray.setTitle('◆');
  }
  rebuildTray();
}

function loadApp(port) {
  if (!mainWindow || mainWindow.isDestroyed()) return;
  appPageReady = false;
  mainWindow.loadURL(`http://127.0.0.1:${port}/index.html`)
    .then(() => {
      appPageReady = true;
      if (pendingToggle) {
        pendingToggle = false;
        mainWindow.webContents.send('shell:toggle-record');
      }
    })
    .catch(() => {});
}

/**
 * Hotkey toggle: 1st press brings the window up and starts a take (camera +
 * record); 2nd press stops it and minimizes the window. The renderer decides
 * which based on whether it is currently recording.
 */
function onSummonHotkey() {
  if (!mainWindow || mainWindow.isDestroyed()) return;
  // Deliberately does NOT raise the main window: the take is driven from the
  // background so only the HUD + camera bubble appear. Open the app itself via
  // the Dock icon or the menu-bar (tray) → "Show Chaplin".
  if (appPageReady && mainWindow.webContents.getURL().includes('/index.html')) {
    mainWindow.webContents.send('shell:toggle-record');
  } else if (serverPort) {
    pendingToggle = true;
    loadApp(serverPort);
  }
}

function registerSummonHotkey(preferred) {
  const wanted = preferred || savedHotkey || SUMMON_HOTKEY;
  globalShortcut.unregisterAll();
  const candidates = [wanted, ...SUMMON_FALLBACKS.filter((c) => c !== wanted)];
  for (const combo of candidates) {
    if (globalShortcut.register(combo, onSummonHotkey)) {
      summonHotkey = combo;
      savedHotkey = combo;
      saveBubbleMode();
      rebuildTray();
      console.log(`[chaplin] summon + record hotkey: ${combo}`);
      return combo;
    }
    console.warn(`[chaplin] hotkey ${combo} is unavailable, trying the next one`);
  }
  console.error('[chaplin] could not register a summon + record hotkey');
  return null;
}

function hotkeyDisplay() {
  if (!summonHotkey) return 'unavailable';
  return summonHotkey
    .replace('Control', '⌃')
    .replace('Alt', '⌥')
    .replace('Command', '⌘')
    .replace('Shift', '⇧')
    .replace(/\+/g, '');
}

function sendStatus(payload) {
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.webContents.send('status', payload);
  }
}

function showError(message) {
  if (mainWindow && !mainWindow.isDestroyed()) {
    sendStatus({ state: 'error', message });
  } else {
    dialog.showErrorBox('Chaplin', message);
  }
}

// ---------------------------------------------------------------------------
// Boot
// ---------------------------------------------------------------------------

async function boot() {
  const projectDir = resolveProjectDir();
  if (!projectDir) {
    dialog.showErrorBox(
      'Chaplin',
      'Could not find the Chaplin project (web_chaplin.py).\n\n' +
        'Set CHAPLIN_PROJECT_DIR to the folder that contains it.'
    );
    app.quit();
    return;
  }
  console.log(`[chaplin] project: ${projectDir}`);

  createWindow();

  try {
    sendStatus({ state: 'starting', message: 'Starting the Chaplin server…' });
    const { python, port } = await startServer(projectDir);
    console.log(`[chaplin] server ready on port ${port} using ${python}`);
    sendStatus({ state: 'ready', port });
    loadApp(port);
  } catch (err) {
    console.error('[chaplin] boot failed:', err.message);
    showError(err.message);
  }
}

// ---------------------------------------------------------------------------
// Menu + lifecycle
// ---------------------------------------------------------------------------

function buildMenu() {
  const template = [
    {
      label: 'Chaplin',
      submenu: [
        { role: 'about' },
        { type: 'separator' },
        {
          label: `Record / stop (${hotkeyDisplay()})`,
          click: () => onSummonHotkey(),
        },
        {
          label: 'Show Chaplin',
          click: () => {
            if (mainWindow && !mainWindow.isDestroyed()) {
              mainWindow.show();
              mainWindow.focus();
            }
          },
        },
        { type: 'separator' },
        {
          label: 'Open in Browser',
          click: () => {
            if (serverPort) shell.openExternal(`http://127.0.0.1:${serverPort}/index.html`);
          },
        },
        {
          label: 'Show Server Log',
          click: () => {
            dialog.showMessageBox({
              title: 'Chaplin server log',
              message: 'Last server output',
              detail: serverLog.slice(-40).join('') || '(no output yet)',
            });
          },
        },
        { type: 'separator' },
        { role: 'reload' },
        { role: 'toggleDevTools' },
        { type: 'separator' },
        { role: 'quit' },
      ],
    },
    { role: 'editMenu' },
    {
      label: 'Go',
      submenu: [
        {
          label: 'Practice',
          accelerator: 'CmdOrCtrl+1',
          click: () => mainWindow && mainWindow.loadURL(`http://127.0.0.1:${serverPort}/index.html`),
        },
        {
          label: 'Demo Studio',
          accelerator: 'CmdOrCtrl+2',
          click: () => mainWindow && mainWindow.loadURL(`http://127.0.0.1:${serverPort}/demo`),
        },
        {
          label: 'Review',
          accelerator: 'CmdOrCtrl+3',
          click: () => mainWindow && mainWindow.loadURL(`http://127.0.0.1:${serverPort}/review.html`),
        },
      ],
    },
    { role: 'viewMenu' },
    { role: 'windowMenu' },
  ];
  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}

app.whenReady().then(() => {
  // Make sure Chaplin is a normal app (Dock icon + app menu) so it can always
  // be found again after the window is closed.
  if (process.platform === 'darwin' && app.dock) app.dock.show().catch(() => {});

  registerSummonHotkey();
  buildMenu();

  // Static assets change whenever the app is rebuilt; never trust the disk cache.
  session.defaultSession.clearCache().catch(() => {});

  loadBubbleMode();
  createHud();
  createBubble();
  createResult();
  createTray();

  // Renderer asks us to get out of the way after it stops a take.
  ipcMain.on('shell:minimize', () => {
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.setAlwaysOnTop(false);
      mainWindow.minimize();
    }
  });

  // Copy from the main process: works even when the window is hidden/minimized,
  // where navigator.clipboard.writeText() rejects with "Document is not focused".
  ipcMain.handle('shell:copy', (_event, text) => {
    clipboard.writeText(String(text == null ? '' : text));
    return true;
  });

  // Refined card: re-copy edited text / close.
  ipcMain.handle('result:copy', (_event, text) => {
    clipboard.writeText(String(text == null ? '' : text));
    return true;
  });
  ipcMain.on('result:close', () => hideResult());

  // Latest Doubao live transcript, kept so a stop can seed the clipboard with it.
  ipcMain.on('bubble:transcript', (_event, text) => {
    liveTranscript = typeof text === 'string' ? text : '';
  });

  // Let the app page hand the live transcript to the server for the refine.
  ipcMain.handle('shell:get-live-transcript', () => liveTranscript);

  // Paste the finished text into whatever app the user is typing in.
  ipcMain.on('shell:paste', () => pasteClipboard());

  // Camera-bubble appearance chosen in the app UI.
  ipcMain.handle('shell:get-bubble-mode', () => bubbleMode);
  ipcMain.on('shell:bubble-mode', (_event, mode) => {
    if (!mode) return;
    bubbleMode = {
      layout: mode.layout || bubbleMode.layout,
      shape: mode.shape || bubbleMode.shape,
    };
    saveBubbleMode();
    applyBubbleMode();
  });

  // User-configurable summon shortcut.
  ipcMain.handle('shell:get-hotkey', () => ({ hotkey: summonHotkey, display: hotkeyDisplay() }));
  ipcMain.handle('shell:set-hotkey', (_event, accel) => {
    if (typeof accel !== 'string' || !accel.trim()) {
      return { ok: false, hotkey: summonHotkey, display: hotkeyDisplay() };
    }
    const prev = summonHotkey;
    const got = registerSummonHotkey(accel.trim());
    if (!got) {
      if (prev) registerSummonHotkey(prev);
      return { ok: false, hotkey: summonHotkey, display: hotkeyDisplay() };
    }
    return { ok: true, hotkey: got, display: hotkeyDisplay() };
  });

  // Custom replacements: rewrite "heard" text in the live transcript.
  ipcMain.handle('shell:get-replacements', () => replacements);
  ipcMain.on('shell:set-replacements', (_event, list) => {
    replacements = Array.isArray(list)
      ? list
          .filter((r) => r && String(r.from || '').trim())
          .map((r) => ({ from: String(r.from).trim(), to: String(r.to == null ? '' : r.to) }))
      : [];
    saveBubbleMode();
    sendRules();
  });

  // Renderer reports take state so the HUD can mirror it.
  ipcMain.on('shell:hud-state', (_event, payload) => updateHud(payload));

  // HUD buttons -> app renderer (stop / cancel) or dismiss (confirm).
  ipcMain.on('hud:action', (_event, action) => {
    if (action === 'confirm') {
      if (hud) hud.hide();
      return;
    }
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.webContents.send('shell:hud-action', action);
    }
  });

  boot();

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) boot();
    else if (mainWindow && !mainWindow.isDestroyed()) mainWindow.show();
  });
});

// Stay alive when the window is hidden so the summon hotkey keeps working.
// Quitting is explicit: ⌘Q / Dock → Quit.
app.on('window-all-closed', () => {});

app.on('before-quit', () => {
  isQuitting = true;
});

app.on('will-quit', () => {
  globalShortcut.unregisterAll();
  stopServer();
});

process.on('exit', stopServer);
process.on('SIGINT', () => {
  stopServer();
  process.exit(0);
});
