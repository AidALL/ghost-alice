#!/usr/bin/env bash
# Stop the selected brainstorm server and clean up. Requires: Node.js.
# Usage: stop-server.sh <session_dir>
# Persistent project mockups survive cleanup; only direct /tmp/brainstorm-* sessions are removed.

if [[ -z "$1" ]]; then
  echo '{"error": "Usage: stop-server.sh <session_dir>"}'
  exit 1
fi

exec node - "$1" <<'NODE'
const fs = require('fs');
const http = require('http');
const net = require('net');
const path = require('path');
const { setTimeout: delay } = require('timers/promises');

const sessionDir = path.resolve(process.argv[2]);
const stateDir = path.join(sessionDir, 'state');
const pidFile = path.join(stateDir, 'server.pid');

function processAlive(pid) {
  try {
    process.kill(pid, 0);
    return true;
  } catch (error) {
    if (error.code === 'ESRCH') return false;
    throw new Error('Cannot observe server process: ' + error.code);
  }
}

function requestShutdown(control) {
  return new Promise((resolve, reject) => {
    const hostname = control.host === '0.0.0.0' ? '127.0.0.1' : control.host === '::' ? '::1' : control.host;
    const request = http.request({
      hostname, port: control.port, method: 'POST', path: '/_brainstorm/stop',
      headers: {
        'X-Brainstorm-Token': control.token,
        'X-Brainstorm-Session': Buffer.from(control.session_dir).toString('base64url'),
        'Connection': 'close'
      }
    }, response => {
      response.resume();
      response.on('end', () => {
        if (response.statusCode === 202) resolve();
        else reject(new Error('Server refused shutdown identity: HTTP ' + response.statusCode));
      });
      response.on('error', reject);
    });
    request.setTimeout(2000, () => request.destroy(new Error('Server shutdown request timed out')));
    request.on('error', reject);
    request.end();
  });
}

async function main() {
  if (!fs.existsSync(pidFile)) {
    console.log(JSON.stringify({ status: 'not_running' }));
    return;
  }
  const pidText = fs.readFileSync(pidFile, 'utf8').trim();
  const pid = Number(pidText);
  if (!/^[1-9]\d*$/.test(pidText) || !Number.isSafeInteger(pid)) throw new Error('Invalid server PID');
  let control;
  try {
    control = JSON.parse(fs.readFileSync(path.join(stateDir, 'server-control'), 'utf8'));
  } catch (error) {
    throw new Error('Server session identity unavailable');
  }
  if (!control || control.pid !== pid || control.session_dir !== fs.realpathSync(sessionDir) ||
      typeof control.token !== 'string' || !/^[a-f0-9]{64}$/.test(control.token) ||
      !net.isIP(control.host) || !Number.isInteger(control.port) || control.port < 1 || control.port > 65535) {
    throw new Error('Server session identity mismatch');
  }

  if (processAlive(pid)) {
    await requestShutdown(control);
    // Observe process disappearance; a reused PID cannot identify this server
    // well enough to target a signal at it.
    for (let i = 0; i < 20 && processAlive(pid); i++) await delay(100);
    if (processAlive(pid)) throw new Error('Server process still present after shutdown request');
  }
  for (const name of ['server.pid', 'server.log', 'server-control', 'server-info']) {
    fs.rmSync(path.join(stateDir, name), { force: true });
  }
  if (path.dirname(sessionDir) === '/tmp' && path.basename(sessionDir).startsWith('brainstorm-')) {
    fs.rmSync(sessionDir, { recursive: true });
  }
  console.log(JSON.stringify({ status: 'stopped' }));
}

main().catch(error => {
  console.log(JSON.stringify({ status: 'failed', error: error.message }));
  process.exitCode = 1;
});
NODE
