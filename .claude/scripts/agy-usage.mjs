// agy reports usage as a running total for the whole conversation, so one turn's cost is the
// difference from the last total seen in that conversation.
export function turnUsage(meta, result) {
  const previous = meta.conversationTokens ?? 0;
  const reported = result?.usage?.total_tokens;
  if (reported === undefined) return { turnTokens: 0, conversationTokens: previous };
  const sameConversation = result.conversation_id === meta.conversationId;
  return { turnTokens: Math.max(reported - (sameConversation ? previous : 0), 0), conversationTokens: reported };
}
