#!/usr/bin/env node
// Live view of the secretary's work:  node .claude/scripts/monitor.mjs [--once]
// Shows the goals, then follows .secretary/activity.jsonl. Read-only.
import { existsSync, readFileSync, readdirSync, statSync } from 'node:fs';
import { basename, dirname, join } from 'node:path';
import { spawnSync } from 'node:child_process';

export function parseGoals(text) {
  const goals = [];
  for (const line of text.split(/\r?\n/)) {
    const heading = line.match(/^### (G-[\d.]+):\s*(.*?)\s*\((\w+)\)\s*$/);
    if (heading) goals.push({ id: heading[1], title: heading[2], status: heading[3], rounds: 0, last: null });
    else if (goals.length && /^- R-\d+/.test(line)) {
      const goal = goals[goals.length - 1];
      goal.rounds += 1;
      goal.last = line.slice(2).trim();
    }
  }
  return goals;
}

export function formatEvent(e) {
  const time = new Date(e.ts).toLocaleTimeString('en-GB');
  const who = e.agent ? `[${e.agent}] ` : '';
  const k = n => `${Math.round((n || 0) / 1000)}k`;
  switch (e.type) {
    case 'owner_message': return `${time} YOU      ${e.text}`;
    case 'lead_stopped': return `${time} LEAD     waiting: ${e.text}`;
    case 'subagent_start': return `${time} START    ${e.agent}`;
    case 'subagent_end': return `${time} END      ${who}${e.verdict ? `${e.verdict} ` : ''}${e.text}`;
    case 'agy_start': return `${time} AGY >    ${e.slug} turn ${e.turn}: ${e.text}`;
    case 'agy_end': return `${time} AGY <    ${e.outcome}, ${k(e.turnTokens)} tokens, ${e.turnSeconds}s, ${e.files} files, ${e.denied} denied (run total ${k(e.runTotalTokens)})`;
    case 'goals_edit': return `${time} GOALS    ${who}${e.text}`;
    case 'git': return `${time} GIT      ${who}${e.text}`;
    case 'blocked': return `${time} BLOCKED  ${e.reason}: ${e.text}`;
    default: return `${time} ${e.type}`;
  }
}

function goalsFile(repo) {
  // The lead logs rounds on the task branch, so the newest copy may be in a worktree.
  const worktrees = join(dirname(repo), `${basename(repo)}.worktrees`);
  const copies = [join(repo, 'docs/GOALS.md')];
  if (existsSync(worktrees)) for (const w of readdirSync(worktrees)) copies.push(join(worktrees, w, 'docs/GOALS.md'));
  return copies.filter(existsSync).sort((a, b) => statSync(b).mtimeMs - statSync(a).mtimeMs)[0];
}

function showGoals(repo) {
  const file = goalsFile(repo);
  const goals = file ? parseGoals(readFileSync(file, 'utf8')) : [];
  console.log(goals.length ? '--- Goals ---' : '--- No goals yet ---');
  for (const g of goals) console.log(`${g.id} [${g.status}] ${g.title}: ${g.rounds} rounds${g.last ? `, last ${g.last}` : ''}`);
  console.log('---');
}

function main() {
  const top = spawnSync('git', ['rev-parse', '--show-toplevel'], { encoding: 'utf8' });
  if (top.status !== 0) { console.error('run this inside the project'); process.exit(1); }
  const repo = top.stdout.trim();
  const log = join(repo, '.secretary', 'activity.jsonl');
  let offset = 0;
  let agyStartedAt = null;
  let lastBeat = 0;

  const drain = () => {
    if (!existsSync(log)) return;
    const data = readFileSync(log);
    const fresh = data.subarray(offset);
    const end = fresh.lastIndexOf(10) + 1;
    if (!end) return;
    offset += end;
    for (const line of fresh.subarray(0, end).toString('utf8').split('\n').filter(Boolean)) {
      let event;
      try { event = JSON.parse(line); } catch { continue; }
      console.log(formatEvent(event));
      if (event.type === 'agy_start') agyStartedAt = Date.parse(event.ts);
      if (event.type === 'agy_end') agyStartedAt = null;
      if (event.type === 'goals_edit') showGoals(repo);
    }
  };

  showGoals(repo);
  drain();
  if (process.argv.includes('--once')) return;
  console.log('Watching. Press Ctrl+C to stop.');
  setInterval(() => {
    drain();
    if (agyStartedAt && Date.now() - lastBeat >= 60000) {
      lastBeat = Date.now();
      console.log(`         agy still running, ${Math.round((Date.now() - agyStartedAt) / 60000)}m so far`);
    }
  }, 1000);
}

if (basename(process.argv[1] || '') === 'monitor.mjs') main();
