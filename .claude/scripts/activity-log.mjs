// Appends one event to .secretary/activity.jsonl, which the monitor reads.
import { appendFileSync, mkdirSync } from 'node:fs';
import { join } from 'node:path';

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
