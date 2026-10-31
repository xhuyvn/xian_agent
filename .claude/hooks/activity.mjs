#!/usr/bin/env node
// Records what the lead and subagents do in .secretary/activity.jsonl. It only observes: it always
// exits 0 and prints nothing, so it cannot block or change a tool call.
import { logEvent, clip } from '../scripts/activity-log.mjs';

const GIT_ACTIONS = /\b(git\s+(-C\s+\S+\s+)?(commit|merge|push)|gh\s+pr\s+create)\b/;

function report(agent, message) {
  const text = String(message ?? '');
  return { type: 'subagent_end', agent, verdict: text.match(/VERDICT:\s*(PASS|FAIL)/)?.[1] ?? null, text: clip(text, 300) };
}

function eventFor(p) {
  const input = p.tool_input || {};
  switch (p.hook_event_name) {
    case 'UserPromptSubmit':
      return String(p.prompt || '').startsWith('<') ? null : { type: 'owner_message', text: clip(p.prompt) };
    case 'SubagentStart':
      return { type: 'subagent_start', agent: p.agent_type };
    case 'SubagentStop':
      return report(p.agent_type, p.last_assistant_message);
    case 'Stop':
      return { type: 'lead_stopped', text: clip(p.last_assistant_message, 300) };
    case 'PostToolUse':
      if (p.tool_name === 'SubagentHandback') return report(p.agent_type, input.report ?? input.message ?? input.result);
      if (/(^|[\/\\])docs[\/\\]GOALS\.md$/.test(String(input.file_path || ''))) {
        const added = String(input.new_string ?? input.content ?? '').split(/\r?\n/).filter(line => line.trim()).pop();
        return { type: 'goals_edit', agent: p.agent_type, text: clip(added || 'file updated') };
      }
      if (p.tool_name === 'Bash' && GIT_ACTIONS.test(String(input.command || ''))) {
        return { type: 'git', agent: p.agent_type, text: clip(input.command, 160) };
      }
      return null;
    default:
      return null;
  }
}

let raw = '';
process.stdin.setEncoding('utf8');
process.stdin.on('data', d => { raw += d; });
process.stdin.on('end', () => {
  try {
    const payload = JSON.parse(raw);
    const event = eventFor(payload);
    if (event) logEvent(process.env.CLAUDE_PROJECT_DIR || payload.cwd, event);
  } catch {
    // Observing only: a bad payload is not worth failing a tool call.
  }
  process.exit(0);
});
