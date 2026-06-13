/**
 * Truncate ``text`` to ``max`` characters, appending an ellipsis when cut.
 * Used to produce short content snippets in search result cards.
 */
export function excerpt(text: string, max = 160): string {
  return text.length > max ? text.slice(0, max) + '…' : text;
}
