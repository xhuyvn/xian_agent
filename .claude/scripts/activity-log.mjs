// Appends one event to .secretary/activity.jsonl, which the monitor reads.
import { spawnSync } from 'node:child_process';
import { appendFileSync, mkdirSync } from 'node:fs';
import { basename, dirname, join } from 'node:path';

// The main checkout holds .secretary, so every worktree of a repo shares one log and one set of
// run records, and they outlive a session's own worktree. Returns null outside a git repo.
export function mainCheckout(cwd) {
  const revParse = args => spawnSync('git', ['rev-parse', ...args], { cwd, encoding: 'utf8' });
  const common = revParse(['--path-format=absolute', '--git-common-dir']);
  if (common.status !== 0) return null;
  const dir = common.stdout.trim();
  return basename(dir) === '.git' ? dirname(dir) : revParse(['--show-toplevel']).stdout.trim();
}

export function logEvent(repo, event) {
  try {
    const dir = join(repo, '.secretary');
    mkdirSync(dir, { recursive: true });
    appendFileSync(join(dir, 'activity.jsonl'), JSON.stringify({ ts: new Date().toISOString(), ...event }) + '\n');
  } catch {
    // Logging must never break the work it records.
  }
}

export function clip(text, max = 200) {
  return String(text ?? '').replace(/\s+/g, ' ').trim().slice(0, max);
}
