// agy reports usage as a running total for the whole conversation, so one turn's cost is the
// difference from the last total seen in that conversation.
export function turnUsage(meta, result) {
  const previous = meta.conversationTokens ?? 0;
  const reported = result?.usage?.total_tokens;
  if (reported === undefined) return { turnTokens: 0, conversationTokens: previous };
  const sameConversation = result.conversation_id === meta.conversationId;
  return { turnTokens: Math.max(reported - (sameConversation ? previous : 0), 0), conversationTokens: reported };
}

// agy can stop on an error after finishing or part-way through its edits. Work it left behind is
// committed either way, so that case is 'interrupted', not 'failed': the verifier decides.
export function outcomeFor(agyRun, filesChanged, committed) {
  const status = agyRun.result?.status;
  if (!agyRun.result || agyRun.timedOut || (status && status !== 'SUCCESS')) return committed ? 'interrupted' : 'failed';
  if (filesChanged.length) return 'changes';
  return agyRun.result.denied_actions?.length ? 'blocked' : 'no_changes';
}
