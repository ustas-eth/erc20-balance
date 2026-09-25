# erc20-balance

Check ERC-20 and native coin balances from your terminal. Pick a chain, choose
an RPC, select several tokens, and enter a wallet address. No wallet connection,
private keys, or signing.

- Common chains and tokens appear first; type to filter and **Tab** to select.
- Choose a listed RPC or enter your own. Providers claiming no logging appear
  first, with the source of each privacy label shown.
- Refresh balances or change tokens, wallet, RPC, or chain without restarting.
- Exact decimals, a shared block number, and explicit errors instead of false zeroes.

![Selecting tokens in the terminal](docs/screenshots/tokens.png)

## Install

Requires **Python 3.9+** and **[fzf](https://github.com/junegunn/fzf#installation)**
on Linux or macOS. Install `fzf` with your package manager, then:

```sh
uv tool install git+https://github.com/ustas-eth/erc20-balance.git
erc20-balance
```

Use `pipx install` instead of `uv tool install` if you prefer. Installing a private
repository requires GitHub access. From a checkout, use `uv tool install .`.

**Enter** confirms and **Esc** goes back. Run
`erc20-balance --help` for options, or `erc20-balance --version` for the version.

![Balances from a demo session](docs/screenshots/balances.png)

*Screenshots capture the real CLI with fixture metadata and demo balances.*
[Chain picker](docs/screenshots/chains.png) · [RPC picker](docs/screenshots/rpc.png)

## Privacy

The chosen remote RPC sees the queried addresses. No-logging labels are provider
claims, not guaranteed zero retention. There is no automatic provider switching,
wallet history, balance log, or saved custom RPC URL. Public lists are cached;
terminal output and provider logs are separate concerns.

```sh
erc20-balance --no-cache                  # Skip metadata-cache reads and writes
erc20-balance --local-only               # Your loopback node; no list downloads
erc20-balance --privacy                  # Full data-handling explanation
```

Read [privacy and correctness limits](docs/privacy.md).
[Contributing](CONTRIBUTING.md) · [MIT license](LICENSE)
