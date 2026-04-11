import { execFileSync } from 'node:child_process';

/**
 * An op:// reference to a 1Password item field.
 * Format: op://<vault>/<item>/<field>
 */
export interface OpReference {
  ref: string;
}

export function isOpReference(value: string): boolean {
  return value.startsWith('op://');
}

/**
 * Credential scope declared in principles.md.
 * Maps an action to the op:// references it requires.
 */
export interface CredentialScope {
  action: string;
  refs: string[];
}

/**
 * Resolve an op:// reference via the 1Password CLI.
 * Returns the resolved value. Throws if the vault is locked or the reference is invalid.
 *
 * IMPORTANT: The resolved value must NEVER be logged, written to disk, or included
 * in journal entries. It is used only at the moment of dispatch.
 */
export function resolveOpReference(ref: string): string {
  if (!isOpReference(ref)) {
    throw new Error(`not an op:// reference: ${ref}`);
  }
  try {
    const result = execFileSync('op', ['read', ref], {
      encoding: 'utf8',
      timeout: 10_000,
      stdio: ['pipe', 'pipe', 'pipe'],
    });
    return result.trim();
  } catch (err) {
    const message = (err as Error).message ?? '';
    if (message.includes('not signed in') || message.includes('session expired') || message.includes('locked')) {
      throw new Error(`vault locked or not signed in — cannot resolve ${ref}`);
    }
    throw new Error(`failed to resolve ${ref}: ${message}`);
  }
}

/**
 * Check whether the 1Password vault is currently unlocked by attempting
 * a lightweight operation. Returns true if unlocked, false if locked.
 */
export function isVaultUnlocked(): boolean {
  try {
    execFileSync('op', ['whoami'], {
      encoding: 'utf8',
      timeout: 5_000,
      stdio: ['pipe', 'pipe', 'pipe'],
    });
    return true;
  } catch {
    return false;
  }
}

/**
 * Injectable credential resolver interface for testing.
 * Production uses resolveOpReference; tests inject a fake.
 */
export interface CredentialResolver {
  resolve(ref: string): string;
  isUnlocked(): boolean;
}

/** Production credential resolver backed by 1Password CLI. */
export function createOpResolver(): CredentialResolver {
  return {
    resolve: resolveOpReference,
    isUnlocked: isVaultUnlocked,
  };
}

/**
 * Check that all required credential scopes for an action are resolvable.
 * Returns { allowed: true } or { allowed: false, reason: string }.
 */
export function checkCredentialScopes(
  action: string,
  scopes: CredentialScope[],
  resolver: CredentialResolver,
): { allowed: true } | { allowed: false; reason: string } {
  const scope = scopes.find((s) => s.action === action);
  if (!scope) {
    // No credential scope declared — action doesn't require credentials
    return { allowed: true };
  }

  if (!resolver.isUnlocked()) {
    return { allowed: false, reason: 'vault is locked — cannot resolve credentials' };
  }

  for (const ref of scope.refs) {
    try {
      resolver.resolve(ref);
    } catch (err) {
      return { allowed: false, reason: (err as Error).message };
    }
  }

  return { allowed: true };
}
