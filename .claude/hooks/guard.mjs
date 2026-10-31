#!/usr/bin/env node
// PreToolUse guard for the AI Secretary kit.
// Reads the hook payload on stdin; exit 2 blocks the tool call and shows stderr to Claude.
// Edit the lists below to fit your project. Note: this guards Claude's own tools only —
// agy's tools are limited separately (headless agy cannot run shell commands).

import { appendFileSync, mkdirSync } from 'node:fs';
import { join, posix } from 'node:path';

const PROTECTED_BRANCHES = ['main', 'master'];

// Shell commands that are never allowed from an agent session.
const BLOCKED_COMMANDS = [
  [/--dangerously-skip-permissions|bypassPermissions|--permission-mode[= ]+bypass/i, 'permission bypass flags are not allowed'],
  [/\bgit\b.*\bpush\b.*(\s--force\b|\s-f\b|--force-with-lease|\s--mirror\b|\s--delete\b|\s-d\b)/, 'force/mirror/delete pushes are not allowed'],
  [/\bgit\b.*\breset\b.*--hard\b/, 'git reset --hard can destroy work; ask the owner'],
  [/\bgit\b.*\bclean\b.*\s-[a-z]*f/, 'git clean -f can destroy work; ask the owner'],
  [/\bgh\s+pr\s+merge\b/, 'merging PRs is the owner\'s decision'],
  [/\bgh\s+(workflow\s+run|release\s+create)\b/, 'triggering workflows/releases is the owner\'s decision'],
  [/\bnpm\s+publish\b|\bpnpm\s+publish\b|\byarn\s+publish\b/, 'publishing packages is not allowed'],
  [/\bvercel\b.*--prod\b|\bnetlify\s+deploy\b.*--prod\b|\bfirebase\s+deploy\b|\bfly\s+deploy\b|\bwrangler\s+deploy\b/, 'deploys run only through GitHub Actions'],
  [/\bkubectl\s+(apply|delete|replace|rollout)\b|\bterraform\s+(apply|destroy)\b|\bdocker\s+push\b/, 'infrastructure changes run only through GitHub Actions'],
];

// Secret-looking paths: .env and .env.* (except example/sample/template), SSH keys, credential files.
const SECRET_PATH = /(^|[\s\/\\'"=])\.env(\.(?!example\b|sample\b|template\b)[\w.-]+)?(?![\w.])|[\/\\]\.ssh[\/\\]|\bid_(rsa|ed25519|ecdsa)\b|\.pem\b|\bcredentials(\.json)?\b/i;

// Files the agents must not modify (their own guardrails and CI).
const PROTECTED_FILES = /(^|[\/\\])(\.claude[\/\\]|\.github[\/\\]workflows[\/\\]|CLAUDE\.md$)/i;

let attempted = '';

function block(reason) {
  try {
    // Inline on purpose: a logging problem must never stop a block from happening.
    if (process.env.CLAUDE_PROJECT_DIR) {
      const dir = join(process.env.CLAUDE_PROJECT_DIR, '.secretary');
      mkdirSync(dir, { recursive: true });
      appendFileSync(join(dir, 'activity.jsonl'), JSON.stringify({ ts: new Date().toISOString(), type: 'blocked', reason, text: attempted.slice(0, 160) }) + '\n');
    }
  } catch {}
  process.stderr.write(`Blocked by ai-secretary guard: ${reason}.\n`);
  process.exit(2);
}

// Judge a worktree's files by their path inside it: the worktree itself may live under .claude/.
function pathInRepo(path) {
  const clean = posix.normalize(`/${path.replace(/\\/g, '/')}`);
  return clean.match(/^(?:(?!\/\.claude\/).)*\/\.claude\/worktrees\/[^/]+\/(.+)$/i)?.[1] ?? clean;
}

function checkPush(cmd) {
  // Every `git ... push ...` segment must name a remote and a non-protected branch explicitly.
  for (const segment of cmd.split(/&&|\|\||;|\||\n/)) {
    const tokens = segment.trim().split(/\s+/).map(t => t.replace(/^["']|["']$/g, ''));
    const g = tokens.findIndex(t => /(^|[\/\\])git(\.exe)?$/.test(t));
    if (g < 0) continue;
    // Skip git's global options (`-C <path>`, `-c k=v`, `--git-dir=...`) to find the subcommand.
    let i = g + 1;
    while (i < tokens.length && tokens[i].startsWith('-')) i += (tokens[i] === '-C' || tokens[i] === '-c') ? 2 : 1;
    if (tokens[i] !== 'push') continue;
    const positional = tokens.slice(i + 1).filter(t => t && !t.startsWith('-'));
    if (positional.length < 2) block('push with an explicit remote and branch, e.g. `git push -u origin feature/x`');
    for (const ref of positional.slice(1)) {
      const target = ref.includes(':') ? ref.split(':').pop() : ref;
      const name = target.replace(/^\+/, '').replace(/^refs\/heads\//, '');
      if (PROTECTED_BRANCHES.includes(name) || name === 'HEAD') block(`pushing to ${name} is not allowed; open a PR instead`);
    }
  }
}

let raw = '';
process.stdin.setEncoding('utf8');
process.stdin.on('data', d => { raw += d; });
process.stdin.on('end', () => {
  let payload;
  try { payload = JSON.parse(raw); } catch { block('could not parse hook input'); }
  const tool = payload.tool_name || '';
  const input = payload.tool_input || {};
  attempted = `${tool} ${input.command || input.file_path || input.notebook_path || input.path || ''}`;

  if (tool === 'Bash' || tool === 'PowerShell') {
    const cmd = String(input.command || '');
    for (const [re, reason] of BLOCKED_COMMANDS) if (re.test(cmd)) block(reason);
    if (SECRET_PATH.test(cmd)) block('commands touching secret files (.env, keys, credentials) are not allowed');
    checkPush(cmd);
  } else {
    const path = String(input.file_path || input.notebook_path || input.path || '');
    if (path && SECRET_PATH.test(path)) block('secret files (.env, keys, credentials) are off limits');
    if (path && tool !== 'Read' && tool !== 'Grep' && tool !== 'Glob' && PROTECTED_FILES.test(pathInRepo(path))) {
      block('.claude/, .github/workflows/ and CLAUDE.md are owner-managed; ask the owner to change them');
    }
  }
  process.exit(0);
});
