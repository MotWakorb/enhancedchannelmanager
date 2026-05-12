/**
 * SMTP alert recipient parsing helpers.
 *
 * Extracted from SettingsTab.tsx so the parsing/validation/dedup/normalization
 * rules are unit-testable in isolation. Behavior must match what shipped in
 * PR #163 — this module is purely a relocation, not a behavior change.
 */

/**
 * Result of parsing a raw recipient string.
 *
 * - `recipients` — deduplicated, validated email addresses in input order
 * - `normalized` — recipients joined as `", "` (or the raw input on the invalid path)
 * - `invalid` — the first token that failed validation (undefined when all valid)
 * - `dedupedCount` — number of duplicate tokens removed (case-insensitive)
 */
export interface ParsedSmtpRecipients {
  recipients: string[];
  normalized: string;
  invalid?: string;
  dedupedCount: number;
}

/**
 * Match the HTML Standard's "valid email address" production (a pragmatic
 * subset of RFC 5322). The local part rejects CR/LF and other control
 * characters by construction, which guards against header-injection style
 * payloads such as `alice@x.co\r\nBcc: x`.
 *
 * See: WHATWG HTML — valid-email-address production.
 */
export function isValidHtml5EmailAddress(value: string): boolean {
  const re =
    /^[a-zA-Z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)*$/;
  return re.test(value);
}

/**
 * Parse a comma-separated recipient string into a deduplicated, validated list.
 *
 * On the first invalid token the function short-circuits and returns
 * `{ recipients: [], normalized: raw, invalid, dedupedCount: 0 }` so the
 * caller can surface a precise validation error.
 */
export function parseSmtpRecipients(raw: string): ParsedSmtpRecipients {
  const parts = raw
    .split(',')
    .map((s) => s.trim())
    .filter(Boolean);

  for (const token of parts) {
    if (!isValidHtml5EmailAddress(token)) {
      return { recipients: [], normalized: raw, invalid: token, dedupedCount: 0 };
    }
  }

  const seen = new Set<string>();
  const deduped: string[] = [];
  let dedupedCount = 0;
  for (const token of parts) {
    const key = token.toLowerCase();
    if (seen.has(key)) {
      dedupedCount += 1;
      continue;
    }
    seen.add(key);
    deduped.push(token);
  }

  return { recipients: deduped, normalized: deduped.join(', '), dedupedCount };
}

/**
 * Normalize a clipboard payload that uses `;`, `\n`, or `\r` as separators
 * into the comma-separated form `parseSmtpRecipients` understands. Returns
 * the input unchanged when no separators of interest are present so callers
 * can defer to the input's default paste behavior.
 *
 * Returns:
 *   - `needsRewrite: true` when the input contains `;`, `\n`, or `\r`
 *   - `normalized` — the rewritten string (only meaningful when `needsRewrite`)
 */
export function normalizeSmtpRecipientsPaste(text: string): {
  needsRewrite: boolean;
  normalized: string;
} {
  if (!/[;\n\r]/.test(text)) {
    return { needsRewrite: false, normalized: text };
  }
  return { needsRewrite: true, normalized: text.replace(/[;\n\r]+/g, ', ') };
}
