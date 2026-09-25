# AGENTS

`erc20-balance` is an interactive Unix command for ERC-20 and native balances.
Keep it narrow, predictable, explicit about failures, and easy to install.

## Engineering posture

Prefer simple primitives over frameworks. The Python standard library handles
networking and arithmetic; `fzf` owns interactive selection. Add dependencies
only for a concrete behavioral need, not for uniformity.

Let interfaces, exit status, and tests carry semantics. Use judgment for routine
choices. Add durable rules only for non-obvious invariants or recurring failures
that cannot be designed away.

## Boundaries that matter

- RPC reads never sign, send transactions, or request private keys.
- Only the chosen RPC receives wallet queries. Never add automatic failover,
  background endpoint probes, analytics, or wallet/credential persistence.
- Remote requests require HTTPS. Local-only mode permits loopback RPCs, blocks
  redirects, bypasses proxies, and does not download metadata.
- Provider privacy labels are attributed claims, not verified ZDR guarantees.
- Balances use integer arithmetic and a shared block number. A failed or empty
  RPC response is an error, never a zero balance.
- The launcher restarts Python in isolated mode before loading the application;
  preserve TLS-key-log suppression and the absence of menu search history.
- Public metadata caches, terminal output, and provider-visible requests are
  distinct surfaces. Do not imply stronger privacy or correctness than provided.

## Documentation and verification

Keep this file durable workspace context. README explains value and installation;
command help owns syntax; `docs/privacy.md` owns privacy limits. Avoid repeating
rules across those documents.

Follow `CONTRIBUTING.md` for verification, commit conventions, and publishing.
Test observable behavior with local fixtures; do not use personal wallets,
credentials, or live RPCs in tests or screenshots. Screenshots must show the real
CLI and identify fixture balances as demo data.

Adapted from ustas-eth/ferrumctl's engineering and context conventions.
