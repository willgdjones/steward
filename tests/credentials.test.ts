import { describe, it, expect } from 'vitest';
import {
  isOpReference,
  checkCredentialScopes,
  type CredentialResolver,
  type CredentialScope,
} from '../src/credentials.js';

describe('isOpReference', () => {
  it('recognises op:// prefixed strings', () => {
    expect(isOpReference('op://vault/item/field')).toBe(true);
  });

  it('rejects non-op strings', () => {
    expect(isOpReference('https://example.com')).toBe(false);
    expect(isOpReference('plaintext-token')).toBe(false);
  });
});

describe('checkCredentialScopes', () => {
  const unlockedResolver: CredentialResolver = {
    resolve: (ref: string) => `resolved-${ref}`,
    isUnlocked: () => true,
  };

  const lockedResolver: CredentialResolver = {
    resolve: () => { throw new Error('vault locked'); },
    isUnlocked: () => false,
  };

  const failingResolver: CredentialResolver = {
    resolve: (ref: string) => { throw new Error(`cannot resolve ${ref}`); },
    isUnlocked: () => true,
  };

  const scopes: CredentialScope[] = [
    { action: 'send_draft', refs: ['op://vault/gmail/refresh_token'] },
  ];

  it('allows actions with no credential scope declared', () => {
    const result = checkCredentialScopes('archive', scopes, unlockedResolver);
    expect(result.allowed).toBe(true);
  });

  it('allows when vault is unlocked and refs resolve', () => {
    const result = checkCredentialScopes('send_draft', scopes, unlockedResolver);
    expect(result.allowed).toBe(true);
  });

  it('refuses when vault is locked', () => {
    const result = checkCredentialScopes('send_draft', scopes, lockedResolver);
    expect(result.allowed).toBe(false);
    expect((result as { reason: string }).reason).toContain('locked');
  });

  it('refuses when a ref fails to resolve', () => {
    const result = checkCredentialScopes('send_draft', scopes, failingResolver);
    expect(result.allowed).toBe(false);
    expect((result as { reason: string }).reason).toContain('cannot resolve');
  });
});
