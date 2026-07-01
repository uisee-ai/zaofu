// Defense against a text block locking the tab in the markdown pipeline.
// A ~50KB unbroken base64 data URL (e.g. an image serialized into the text
// stream) both jams Shiki/KaTeX/rehype on the main thread AND forces one
// unbreakable line through layout. Either heuristic routes such a block to
// plain, break-anywhere rendering that bypasses markdown. Pure — unit tested
// in tests/markdownGuard.test.ts.

export const MAX_MARKDOWN_TEXT_LENGTH = 50_000;
export const MAX_UNBROKEN_TOKEN_LENGTH = 5_000;
export const MAX_PLAINTEXT_DISPLAY_LENGTH = 200_000;

/**
 * Longest run of consecutive non-whitespace characters. ASCII whitespace
 * (space, tab, CR, LF, FF, VT) resets the run — those are the break
 * opportunities the layout engine can use. O(n), single pass.
 */
export function longestUnbrokenRun(text: string): number {
  let max = 0;
  let current = 0;
  for (let i = 0; i < text.length; i += 1) {
    const code = text.charCodeAt(i);
    if (code === 32 || (code >= 9 && code <= 13)) {
      current = 0;
    } else {
      current += 1;
      if (current > max) max = current;
    }
  }
  return max;
}

/** Whether `text` should bypass markdown because rendering it risks locking the tab. */
export function isPathologicalText(text: string): boolean {
  return text.length > MAX_MARKDOWN_TEXT_LENGTH || longestUnbrokenRun(text) > MAX_UNBROKEN_TOKEN_LENGTH;
}

/** Clamp an over-long payload so the plain-text DOM node can't grow without bound. */
export function clampPlainText(text: string): { shown: string; hiddenCount: number } {
  if (text.length <= MAX_PLAINTEXT_DISPLAY_LENGTH) return { shown: text, hiddenCount: 0 };
  return { shown: text.slice(0, MAX_PLAINTEXT_DISPLAY_LENGTH), hiddenCount: text.length - MAX_PLAINTEXT_DISPLAY_LENGTH };
}
