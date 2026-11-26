/**
 * Shared "is this M3U account displayable" predicate.
 *
 * Dispatcharr seeds a provider-internal pseudo-account named "custom" (any
 * casing) that must never surface to the operator. Every consumer goes
 * through these helpers so there is exactly one definition.
 */

/** Name of the hidden pseudo-account, compared case-insensitively. */
const HIDDEN_ACCOUNT_NAME = 'custom';

/** True when the account should appear in operator-facing lists and selects. */
export function isDisplayableM3UAccount(account: { name: string }): boolean {
  return account.name.toLowerCase() !== HIDDEN_ACCOUNT_NAME;
}

/** Displayable AND active: accounts that Refresh All and capacity badges act on. */
export function isActiveDisplayableM3UAccount(account: {
  name: string;
  is_active: boolean;
}): boolean {
  return account.is_active && isDisplayableM3UAccount(account);
}
