#!/usr/bin/env node
'use strict';

// Cross-platform LogGraph launcher.
// Inspired by CodeGraph's npm shim: this file is the stable entry point. It
// locates the vendored LogGraph source, creates/repairs a local venv when
// needed, then execs the Python CLI from that environment.

const childProcess = require('child_process');
const fs = require('fs');
const os = require('os');
const path = require('path');

const isWindows = process.platform === 'win32';
const here = __dirname;
const loggraphRoot = path.resolve(here);
const venvDir = process.env.LOGGRAPH_VENV || path.join(os.homedir(), '.loggraph', 'venv');
const venvBin = isWindows ? path.join(venvDir, 'Scripts') : path.join(venvDir, 'bin');
const loggraphExe = path.join(venvBin, isWindows ? 'loggraph.exe' : 'loggraph');

main();

function main() {
  try {
    if (!fs.existsSync(loggraphExe)) ensureVenv();
    const result = childProcess.spawnSync(loggraphExe, process.argv.slice(2), {
      stdio: 'inherit',
      windowsHide: true,
    });
    if (result.error) throw result.error;
    process.exit(result.status === null ? 1 : result.status);
  } catch (error) {
    process.stderr.write(`loggraph: ${error && error.message ? error.message : String(error)}\n`);
    process.exit(1);
  }
}

function ensureVenv() {
  fs.rmSync(venvDir, { recursive: true, force: true });
  const python = findPython();
  if (python && run(python.command, python.args.concat(['-m', 'venv', venvDir])).status === 0) {
    const venvPython = path.join(venvBin, isWindows ? 'python.exe' : 'python');
    runChecked(venvPython, ['-m', 'pip', 'install', '--upgrade', 'pip']);
    runChecked(venvPython, ['-m', 'pip', 'install', '-e', loggraphRoot]);
    return;
  }

  const uv = findCommand('uv');
  if (uv) {
    fs.rmSync(venvDir, { recursive: true, force: true });
    runChecked(uv, ['venv', venvDir]);
    const venvPython = path.join(venvBin, isWindows ? 'python.exe' : 'python');
    runChecked(uv, ['pip', 'install', '--python', venvPython, '-e', loggraphRoot]);
    return;
  }

  throw new Error(
    'could not create LogGraph venv. Install Python venv support (python3-venv on Debian/Ubuntu) or install uv.'
  );
}

function findPython() {
  const candidates = isWindows
    ? [
        { command: 'py', args: ['-3'] },
        { command: 'python', args: [] },
        { command: 'python3', args: [] },
      ]
    : [
        { command: 'python3', args: [] },
        { command: 'python', args: [] },
      ];
  for (const candidate of candidates) {
    const result = run(candidate.command, candidate.args.concat(['--version']));
    if (result.status === 0) return candidate;
  }
  return null;
}

function findCommand(command) {
  const result = run(command, ['--version']);
  return result.status === 0 ? command : null;
}

function runChecked(command, args) {
  const result = run(command, args, { stdio: 'inherit' });
  if (result.error) throw result.error;
  if (result.status !== 0) throw new Error(`${command} ${args.join(' ')} failed with exit code ${result.status}`);
}

function run(command, args, options = {}) {
  return childProcess.spawnSync(command, args, {
    stdio: options.stdio || 'ignore',
    windowsHide: true,
  });
}
