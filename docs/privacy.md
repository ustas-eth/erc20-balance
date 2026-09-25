# Privacy and correctness

Remote mode is the default. You choose the RPC before any wallet query is sent.
The script checks that endpoint's chain ID and current block; it never probes
other providers in the background or silently switches after an error.

## What each party can see

- The chosen RPC receives the wallet and contract addresses, timing, and normally
  your IP. HTTPS protects requests in transit, not from the provider.
- Chainlist and Uniswap receive metadata-list downloads, without wallet data.
  Lists are cached for 24 hours; failed refreshes may use a validated older cache.
- Environment-configured HTTPS proxies are honored for remote requests. They are
  another party on the connection path. Local RPCs bypass these proxies.

1RPC appears first where available based on its
[no-logging and metadata-masking claims](https://docs.1rpc.io/web3-relay/overview),
reviewed September 24, 2026. Other endpoints are ordered using Chainlist's
reported tracking labels. These are attributed claims, not audited ZDR
certifications. The program does not verify TEE attestations, and upstream nodes
still process the requested addresses. Policies may change.

## Local traces

The application writes only public chain/token metadata caches under
`$XDG_CACHE_HOME/erc20-balance` (default `~/.cache/erc20-balance`), using owner-only
files. It does not persist wallets, balances, custom RPC URLs, or search history.
`--no-cache` skips cache reads and writes; it does not erase earlier caches.

Results remain in terminal output. Shell history and process listings may expose
`--wallet` or `--rpc` arguments; enter these interactively when that matters.
Custom RPC entry is hidden and its path/query credentials are omitted from
output. TLS key logging via `SSLKEYLOGFILE` is disabled. Menu processes receive
neither that variable nor `ERC20_RPC_URL`.

Terminal recording, OS swap/crash dumps, installer caches, and provider logs are
outside the application's control. There is no promise of forensic erasure.
The installed command restarts its Python worker in isolated mode with bytecode
writes disabled before loading the application.

## Your own node

```sh
erc20-balance --local-only --rpc http://127.0.0.1:8545
```

Local-only mode permits loopback RPCs, bypasses proxies, blocks redirects, and
never downloads lists. With no cache it provides common chains and native coins;
add ERC-20 contracts manually. An SSH tunnel to your own fully synced node works
too. A local proxy forwarding to a public provider still exposes queries upstream.

## Limits of the result

Balances are read at the same block number using exact integer arithmetic.
Missing or malformed responses are errors, not zeroes. The program still trusts
the RPC and metadata list: it does not verify state proofs, independently check
node freshness, or make block-number queries immune to reorgs. Common-symbol
ordering is a display preference, not token verification; check contract addresses.

Ctrl+C cancels queued requests; already-running requests may finish. Network
limits are socket timeouts, not a deadline for an entire multi-token session.
No private key, signing, transaction submission, or trading is involved.
