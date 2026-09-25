import argparse
import getpass
import http.client
import ipaddress
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from . import __version__

# Display preferences, not live popularity rankings. Edit to suit your workflow.
COMMON_CHAINS = [
    1,
    8453,
    42161,
    10,
    137,
    56,
    43114,
    130,
    100,
    42220,
    324,
    59144,
    534352,
    5000,
    81457,
]
COMMON_TOKENS = [
    "USDC",
    "USDT",
    "DAI",
    "USDS",
    "WETH",
    "WBTC",
    "CBBTC",
    "WSTETH",
    "STETH",
    "RETH",
    "CBETH",
    "WEETH",
    "WBNB",
    "WAVAX",
    "WPOL",
    "LINK",
    "UNI",
    "AAVE",
    "ARB",
    "OP",
]
# Minimal offline fallback; cached lists add other chains and token contracts.
OFFLINE_CHAINS = [
    (1, "Ethereum Mainnet", "ETH"),
    (8453, "Base", "ETH"),
    (42161, "Arbitrum One", "ETH"),
    (10, "OP Mainnet", "ETH"),
    (137, "Polygon Mainnet", "POL"),
    (56, "BNB Smart Chain Mainnet", "BNB"),
    (43114, "Avalanche C-Chain", "AVAX"),
    (130, "Unichain", "ETH"),
    (100, "Gnosis", "XDAI"),
    (42220, "Celo", "CELO"),
    (324, "zkSync Mainnet", "ETH"),
    (59144, "Linea", "ETH"),
    (534352, "Scroll", "ETH"),
    (5000, "Mantle", "MNT"),
    (81457, "Blast", "ETH"),
]
TOKEN_URL = "https://tokens.uniswap.org/"
CHAIN_URL = "https://chainlist.org/rpcs.json"
ADDRESS = re.compile(r"0x[0-9a-fA-F]{40}\Z")
CACHE_TTL = 86400
MAX_JSON_BYTES = 32 * 1024 * 1024
LOCAL_ONLY = False
USE_CACHE = True
PRIVACY = """Privacy and RPCs

Remote RPC selection is the default. Providers claiming no request logging are
listed first; this ordering is not an independent privacy certification.
A remote RPC receives the queried wallet/contract addresses and request timing.
It normally sees your IP too. HTTPS protects transit, not against the operator.
Chainlist's tracking labels are reported policies, not audited ZDR guarantees.
1RPC claims metadata masking and no retained request data; this script does not
verify its TEE attestation. Upstream nodes still process the requested addresses.
  https://docs.1rpc.io/web3-relay/overview

For private reads, use your own fully synced node:
  erc20-balance --local-only --rpc http://127.0.0.1:8545
Local-only mode permits loopback RPCs only, bypasses environment proxies, blocks
redirects, and never downloads lists. It uses cached lists or a small built-in
chain list plus native/custom tokens. An SSH tunnel to your own node also works.
A local proxy that forwards to a public provider still exposes queries upstream.
  https://ethereum.org/developers/docs/nodes-and-clients

No automatic RPC failover or background endpoint probes. Only your chosen RPC
receives balance requests. No wallets, balances or custom RPC URLs are saved by
this program; results remain visible in terminal output. Shell arguments may be
recorded in shell history/process listings: enter wallet/RPC interactively if
that matters. Terminal recording, OS swap/crash dumps and provider logging are
outside this program's control; this is not a promise of forensic erasure.

Only public chain/token metadata is cached (owner-only files). --no-cache skips
reading/writing these cache files; it does not delete caches from earlier runs.
List downloads contact Chainlist and Uniswap, without wallet data. Remote RPCs
must use HTTPS; HTTP is allowed only for loopback. Configured HTTPS proxies are
honored for remote requests and are another party in the connection path.
TLS key logging via SSLKEYLOGFILE and fzf search history are disabled.
"""


class AppError(Exception):
    pass


class Cancelled(Exception):
    pass


def clean(value):
    return "".join(c if c.isprintable() else " " for c in str(value))


def say(message=""):
    print(clean(message), file=sys.stderr, flush=True)


def valid_decimals(value):
    return type(value) is int and 0 <= value <= 255


def valid_url(url):
    if not isinstance(url, str) or any(c.isspace() or not c.isprintable() for c in url):
        return False
    if any(c in url for c in ("${", "{", "}", "<", ">")):
        return False
    try:
        p = urllib.parse.urlsplit(url)
        return bool(
            p.scheme in ("https", "http")
            and p.hostname
            and p.port != 0
            and not p.fragment
            and p.username is None
            and p.password is None
            and (p.scheme == "https" or is_loopback(url))
        )
    except ValueError:
        return False


def is_loopback(url):
    host = urllib.parse.urlsplit(url).hostname
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise AppError("Endpoint redirect blocked; select the destination explicitly")


def http_json(url, payload=None, timeout=12):
    if not valid_url(url):
        raise AppError(
            "Use HTTPS for remote endpoints, or HTTP on loopback; URL userinfo is unsupported"
        )
    local = is_loopback(url)
    if LOCAL_ONLY and not local:
        raise AppError("Local-only mode blocks non-loopback connections")
    if local:
        # Avoid DNS resolution or environment proxy forwarding for localhost.
        parts = urllib.parse.urlsplit(url)
        if parts.hostname == "localhost":
            url = urllib.parse.urlunsplit(
                parts._replace(netloc=parts.netloc.replace("localhost", "127.0.0.1"))
            )
    body = None if payload is None else json.dumps(payload).encode()
    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "User-Agent": "erc20-balance/2.0",
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
    )
    handlers = [NoRedirect()]
    if local:
        handlers.append(urllib.request.ProxyHandler({}))
    try:
        with urllib.request.build_opener(*handlers).open(request, timeout=timeout) as response:
            data = response.read(MAX_JSON_BYTES + 1)
        if len(data) > MAX_JSON_BYTES:
            raise AppError("Response exceeds the 32 MiB size limit")
        return json.loads(data)
    except urllib.error.HTTPError as exc:
        raise AppError(f"HTTP {exc.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException) as exc:
        # Avoid echoing URLs or provider messages containing custom credentials.
        if "timed out" in str(getattr(exc, "reason", exc)).lower():
            raise AppError("Connection timed out") from None
        raise AppError("Connection failed (network, TLS, or endpoint unavailable)") from None
    except (ValueError, UnicodeError):
        raise AppError("Endpoint returned invalid JSON") from None


def validate_list(data, kind):
    if kind == "tokens":
        rows = data.get("tokens") if isinstance(data, dict) else None
        required = ("chainId", "address", "symbol", "name", "decimals")
    else:
        rows = data
        required = ("chainId", "name", "rpc", "nativeCurrency")
    if not isinstance(rows, list) or not rows:
        raise AppError(f"Invalid {kind} list")
    for row in rows:
        if not isinstance(row, dict) or not all(key in row for key in required):
            raise AppError(f"Invalid entry in {kind} list")
        if type(row["chainId"]) is not int or row["chainId"] <= 0:
            raise AppError(f"Invalid chain ID in {kind} list")
        if kind == "chains" and (
            not isinstance(row["rpc"], list) or not isinstance(row["nativeCurrency"], dict)
        ):
            raise AppError("Invalid chain metadata")
    if kind == "tokens":
        # Uniswap also includes non-EVM networks (e.g. Solana).
        usable = [
            row
            for row in rows
            if isinstance(row["address"], str)
            and ADDRESS.fullmatch(row["address"])
            and valid_decimals(row["decimals"])
        ]
        if not usable:
            raise AppError("Token list has no usable EVM contracts")
        return dict(data, tokens=usable)
    return data


def atomic_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temp = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as stream:
            temp = Path(stream.name)
            json.dump(data, stream)
        os.replace(temp, path)
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)


def cached_list(kind, url, cache_dir, refresh=False, timeout=12):
    path = cache_dir / f"{kind}.json"
    previous = None
    if USE_CACHE:
        try:
            previous = validate_list(json.loads(path.read_text()), kind)
            if LOCAL_ONLY or (not refresh and 0 <= time.time() - path.stat().st_mtime < CACHE_TTL):
                return previous
        except (OSError, ValueError, AppError):
            pass
    if LOCAL_ONLY:
        if kind == "tokens":
            say("No cached token list; use native coins or add a token contract.")
            return {"tokens": []}
        return [
            {
                "chainId": cid,
                "name": name,
                "rpc": [],
                "isTestnet": False,
                "nativeCurrency": {"symbol": symbol, "decimals": 18},
            }
            for cid, name, symbol in OFFLINE_CHAINS
        ]
    say(f"Loading {kind} list…")
    try:
        data = validate_list(http_json(url, timeout=timeout), kind)
    except AppError as exc:
        if previous is not None:
            say(f"Warning: {exc}; using the previous {kind} cache.")
            return previous
        raise AppError(f"Could not load {kind}: {exc}") from None
    if USE_CACHE:
        try:
            atomic_json(path, data)
        except OSError:
            say(f"Warning: could not save {kind} cache; using downloaded data.")
    return data


def pick(title, items, labels, multi=False, header=""):
    if not items:
        raise AppError(f"No choices available for {title}")
    rows = "\n".join(f"{i}\t{clean(label)}" for i, label in enumerate(labels))
    hints = "Type to search · Enter select · Esc back"
    if multi:
        hints = "Tab select · Ctrl-A all matches · Ctrl-D clear · Enter confirm · Esc back"
    command = [
        "fzf",
        "--height=80%",
        "--layout=reverse",
        "--border",
        "--no-sort",
        "--delimiter=\t",
        "--with-nth=2..",
        "--prompt",
        f"{title} › ",
        "--header",
        f"{header}\n{hints}".strip(),
        "--multi" if multi else "--no-multi",
    ]
    if multi:
        command.extend(["--bind", "ctrl-a:select-all,ctrl-d:deselect-all"])
    env = os.environ.copy()
    # User filters or command bindings must not change selection semantics.
    env.pop("FZF_DEFAULT_OPTS", None)
    env.pop("FZF_DEFAULT_OPTS_FILE", None)
    env.pop("ERC20_RPC_URL", None)
    env.pop("SSLKEYLOGFILE", None)
    result = subprocess.run(command, input=rows, text=True, stdout=subprocess.PIPE, env=env)
    if result.returncode in (1, 130):
        raise Cancelled()
    if result.returncode:
        raise AppError(f"fzf exited with status {result.returncode}")
    try:
        selected = [items[int(line.split("\t", 1)[0])] for line in result.stdout.splitlines()]
    except (ValueError, IndexError):
        raise AppError("Invalid menu selection") from None
    if not selected:
        raise Cancelled()
    return selected if multi else selected[0]


def prompt(label, default="", secret=False):
    suffix = f" [{default}]" if default else ""
    try:
        value = (
            getpass.getpass(f"{label}{suffix}: ") if secret else input(f"{label}{suffix}: ")
        ).strip()
    except EOFError:
        raise Cancelled() from None
    if value.lower() == "q":
        raise Cancelled()
    return value or default


def ask_address(label="Wallet address", default=""):
    while True:
        value = prompt(f"{label} (q back)", default)
        if ADDRESS.fullmatch(value):
            return value
        say("Enter a 0x address with exactly 40 hexadecimal characters.")


def chain_order(chain):
    cid = chain["chainId"]
    return (
        COMMON_CHAINS.index(cid) if cid in COMMON_CHAINS else len(COMMON_CHAINS),
        clean(chain["name"]).casefold(),
        cid,
    )


def choose_chain(chains, tokens, include_testnets=False):
    counts = {}
    for token in tokens:
        counts[token["chainId"]] = counts.get(token["chainId"], 0) + 1
    choices = sorted(
        (c for c in chains if include_testnets or not c.get("isTestnet", False)), key=chain_order
    )
    labels = [
        f"{'★' if c['chainId'] in COMMON_CHAINS else ' '}  {clean(c['name']):30}"
        f"  ID {c['chainId']:<10}  {counts.get(c['chainId'], 0):>4} tokens"
        f"{'  [testnet]' if c.get('isTestnet') else ''}"
        for c in choices
    ]
    return pick(
        "Chain", choices, labels, header="Common chains first · --testnets to include testnets"
    )


def rpc_candidates(chain):
    result, seen = [], set()
    for entry in chain["rpc"]:
        if isinstance(entry, str):
            entry = {"url": entry}
        if not isinstance(entry, dict):
            continue
        url = entry.get("url")
        if not valid_url(url) or url in seen:
            continue
        if re.search(r"(YOUR[_-]|API[_-]?KEY|INFURA_ID|ALCHEMY_ID)", url, re.I):
            continue
        seen.add(url)
        result.append({"url": url, "tracking": entry.get("tracking", "unspecified")})
    return sorted(
        result,
        key=lambda r: (
            not claims_no_logs(r["url"]),
            r["tracking"] != "none",
            urllib.parse.urlsplit(r["url"]).scheme != "https",
            urllib.parse.urlsplit(r["url"]).hostname or "",
        ),
    )


def claims_no_logs(url):
    # Provider statement, reviewed 2026-09-24; not an attestation check.
    # Source: https://docs.1rpc.io/web3-relay/overview
    return urllib.parse.urlsplit(url).hostname in ("1rpc.io", "public.1rpc.io")


def privacy_label(entry):
    if claims_no_logs(entry["url"]):
        return "1RPC claims no request logs; unverified here"
    return f"Chainlist reports tracking: {entry['tracking']}"


def rpc(url, method, params, timeout):
    data = http_json(url, {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}, timeout)
    if not isinstance(data, dict) or data.get("jsonrpc") != "2.0" or data.get("id") != 1:
        raise AppError("Invalid JSON-RPC response")
    if data.get("error") is not None:
        error = data["error"]
        code = error.get("code") if isinstance(error, dict) else None
        code = code if type(code) is int else "unknown"
        raise AppError(f"RPC error {code} for {method}")
    if not isinstance(data.get("result"), str):
        raise AppError(f"Missing or invalid result for {method}")
    return data["result"]


def hex_integer(value, word=False):
    pattern = r"0x[0-9a-fA-F]{64}" if word else r"0x[0-9a-fA-F]{1,64}"
    if not isinstance(value, str) or not re.fullmatch(pattern, value):
        raise AppError("Invalid ABI return data" if word else "Invalid hexadecimal RPC result")
    return int(value[2:], 16)


def check_rpc(url, chain_id, timeout):
    started = time.monotonic()
    actual = hex_integer(rpc(url, "eth_chainId", [], timeout))
    if actual != chain_id:
        raise AppError(f"Wrong network: endpoint reports chain {actual}, expected {chain_id}")
    block = hex_integer(rpc(url, "eth_blockNumber", [], timeout))
    return round((time.monotonic() - started) * 1000), block


def choose_rpc(chain, timeout, current=None, initial=None):
    pending = initial
    while True:
        if pending:
            choice = {"url": pending, "custom": True}
            pending = None
        else:
            choices, labels = [], []
            if current:
                choices.append(current)
                labels.append("↻ Current endpoint: " + current["label"])
            if not LOCAL_ONLY:
                for entry in rpc_candidates(chain):
                    if current and current["url"] == entry["url"]:
                        continue
                    choices.append(entry)
                    labels.append(f"{privacy_label(entry):52}  {entry['url']}")
            choices.extend(
                [{"url": "http://127.0.0.1:8545", "custom": True}, {"custom_prompt": True}]
            )
            labels.extend(["Local node · http://127.0.0.1:8545", "+ Enter custom RPC URL"])
            header = (
                f"{clean(chain['name'])} · Loopback only; no list downloads or public fallback"
                if LOCAL_ONLY
                else f"{clean(chain['name'])} · No-logging claims first, not guaranteed ZDR · Search 'custom' or 'local' for your node"
            )
            choice = pick("RPC", choices, labels, header=header)
            if choice.get("custom_prompt"):
                url = prompt(
                    "Custom HTTPS RPC (HTTP for loopback; hidden input; q back)", secret=True
                )
                if not valid_url(url):
                    say(
                        "Use HTTPS for remote RPCs or HTTP on loopback, without placeholders or URL userinfo."
                    )
                    continue
                choice = {"url": url, "custom": True}
        if not valid_url(choice["url"]):
            raise AppError("Invalid RPC URL")
        if LOCAL_ONLY and not is_loopback(choice["url"]):
            say(
                "Local-only mode requires a loopback URL. Tunnel to your own remote node if needed."
            )
            continue
        say("Checking endpoint and network…")
        try:
            elapsed, block = check_rpc(choice["url"], chain["chainId"], timeout)
        except AppError as exc:
            say(f"Endpoint unavailable: {exc}. Choose another RPC.")
            continue
        choice = dict(choice)
        choice["label"] = (
            f"custom ({urllib.parse.urlsplit(choice['url']).hostname})"
            if choice.get("custom")
            else choice["url"]
        )
        say(f"Connected: {choice['label']} · {elapsed} ms for 2 calls · block {block:,}")
        return choice


def token_order(token):
    symbol = str(token["symbol"]).upper()
    return (
        COMMON_TOKENS.index(symbol) if symbol in COMMON_TOKENS else len(COMMON_TOKENS),
        symbol,
        str(token["name"]).casefold(),
        token["address"].lower(),
    )


def native_token(chain):
    currency = chain["nativeCurrency"]
    if not valid_decimals(currency.get("decimals")):
        raise AppError("Chain has invalid native currency decimals")
    return {
        "symbol": currency.get("symbol", "NATIVE"),
        "name": currency.get("name", "Native coin"),
        "decimals": currency["decimals"],
        "address": None,
    }


def custom_token(endpoint, timeout):
    address = ask_address("Token contract address")
    data = rpc(
        endpoint["url"], "eth_call", [{"to": address, "data": "0x313ce567"}, "latest"], timeout
    )
    decimals = hex_integer(data, word=True)
    if not valid_decimals(decimals):
        raise AppError("Contract returned invalid decimals")
    symbol = prompt("Display symbol (q back)", address[:10])
    return {
        "address": address,
        "decimals": decimals,
        "symbol": clean(symbol),
        "name": "Custom token",
    }


def choose_tokens(chain, tokens, endpoint, timeout):
    seen = set()
    choices = [native_token(chain)]
    for token in sorted((t for t in tokens if t["chainId"] == chain["chainId"]), key=token_order):
        key = token["address"].lower()
        if key not in seen:
            seen.add(key)
            choices.append(token)
    labels = []
    for token in choices:
        symbol, name = clean(token["symbol"]), clean(token["name"])
        common = not token["address"] or symbol.upper() in COMMON_TOKENS
        labels.append(
            f"{'★' if common else ' '}  {symbol:10}  {name:30}  {token['address'] or '[native coin]'}"
        )
    choices.append({"custom_prompt": True})
    labels.append("+ Add a custom token contract…")
    while True:
        selected = pick(
            "Tokens",
            choices,
            labels,
            multi=True,
            header=f"{clean(chain['name'])} · Native + common symbols first · Check contract addresses",
        )
        if any(t.get("custom_prompt") for t in selected):
            try:
                added = custom_token(endpoint, timeout)
            except (AppError, Cancelled) as exc:
                if isinstance(exc, AppError):
                    say(f"Could not add token: {exc}")
                continue
            selected = [t for t in selected if not t.get("custom_prompt")]
            if not any(
                t["address"] and t["address"].lower() == added["address"].lower() for t in selected
            ):
                selected.append(added)
        return selected


def format_balance(value, decimals):
    # Preserve the complete uint256 and every decimal without floating point.
    if not valid_decimals(decimals) or type(value) is not int or value < 0:
        raise AppError("Invalid balance or decimals")
    if decimals == 0:
        return str(value)
    digits = str(value).zfill(decimals + 1)
    fraction = digits[-decimals:].rstrip("0")
    return digits[:-decimals] + ("." + fraction if fraction else "")


def get_balance(endpoint, token, wallet, block, timeout):
    if token["address"] is None:
        amount = hex_integer(rpc(endpoint, "eth_getBalance", [wallet, block], timeout))
    else:
        calldata = "0x70a08231" + wallet[2:].lower().zfill(64)
        raw = rpc(
            endpoint, "eth_call", [{"to": token["address"], "data": calldata}, block], timeout
        )
        amount = hex_integer(raw, word=True)
    return format_balance(amount, token["decimals"])


def show_balances(chain, endpoint, tokens, wallet, timeout):
    say("Fetching balances…")
    try:
        block = hex_integer(rpc(endpoint["url"], "eth_blockNumber", [], timeout))
    except AppError as exc:
        say(f"Cannot read current block: {exc}. Use “Change RPC” or retry.")
        return
    results = {}
    # One block for all tokens, even if the head advances during the query.
    pool = ThreadPoolExecutor(max_workers=4)
    try:
        futures = {
            pool.submit(get_balance, endpoint["url"], token, wallet, hex(block), timeout): i
            for i, token in enumerate(tokens)
        }
        for future in as_completed(futures):
            i = futures[future]
            try:
                results[i] = (future.result(), None)
            except AppError as exc:
                results[i] = ("ERROR", str(exc))
            if sys.stderr.isatty():
                print(
                    f"\r  {len(results)}/{len(tokens)} balances checked",
                    end="",
                    file=sys.stderr,
                    flush=True,
                )
    finally:
        # Ctrl+C must not drain the entire queue and keep sending wallet queries.
        # Already-running requests are allowed to finish or time out.
        pool.shutdown(wait=True, cancel_futures=True)
    if sys.stderr.isatty():
        say()
    print(f"\n{clean(chain['name'])} · chain {chain['chainId']} · block {block:,}")
    print(f"Wallet  {wallet}")
    print(f"RPC     {clean(endpoint['label'])}\n")
    width = max(8, *(len(clean(t["symbol"])) for t in tokens))
    print(f"{'TOKEN':<{width}}  {'BALANCE':>24}  CONTRACT")
    print("─" * (width + 72))
    failed = 0
    for i, token in enumerate(tokens):
        value, error = results[i]
        print(f"{clean(token['symbol']):<{width}}  {value:>24}  {token['address'] or '[native]'}")
        if error:
            failed += 1
            print(f"  ↳ {clean(error)}")
    print(
        f"\n{len(tokens) - failed}/{len(tokens)} successful"
        + (" · Change RPC or retry failed queries." if failed else "")
        + "\n",
        flush=True,
    )


def parse_args():
    parser = argparse.ArgumentParser(
        prog="erc20-balance",
        description="Interactive ERC-20 and native balances. Remote RPC picker by default. Requires Python 3.9+ and fzf.",
        epilog="Tab marks tokens; Enter selects; Esc goes back. Lists are cached for 24h in "
        "$XDG_CACHE_HOME/erc20-balance (default ~/.cache/erc20-balance). "
        "Use --privacy for data handling and local node instructions.",
    )
    parser.add_argument("--version", action="version", version=f"erc20-balance {__version__}")
    parser.add_argument("--wallet", metavar="0xADDRESS", help="prefill wallet address")
    parser.add_argument("--chain", type=int, metavar="ID", help="start on this chain ID")
    parser.add_argument("--rpc", metavar="URL", help="start with this RPC (or set ERC20_RPC_URL)")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--local-only", action="store_true", help="only loopback RPCs; no list downloads"
    )
    mode.add_argument(
        "--remote",
        action="store_true",
        help="allow list downloads and remote RPC selection (default)",
    )
    parser.add_argument("--privacy", action="store_true", help="explain privacy modes and exit")
    parser.add_argument("--testnets", action="store_true", help="include testnets in chain menu")
    parser.add_argument(
        "--refresh", action="store_true", help="refresh cached lists (requires remote mode)"
    )
    parser.add_argument(
        "--no-cache", action="store_true", help="keep lists in memory; skip cache reads and writes"
    )
    parser.add_argument(
        "--timeout", type=float, default=12, metavar="SECONDS", help="network timeout (default: 12)"
    )
    args = parser.parse_args()
    if args.privacy:
        print(PRIVACY)
        parser.exit()
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("--timeout must be a finite positive number")
    if args.wallet and not ADDRESS.fullmatch(args.wallet):
        parser.error("--wallet must be a 0x address with 40 hexadecimal characters")
    environment_rpc = os.environ.pop("ERC20_RPC_URL", None)
    args.rpc = args.rpc or environment_rpc
    if args.rpc and not valid_url(args.rpc):
        parser.error(
            "--rpc / ERC20_RPC_URL requires HTTPS (HTTP on loopback); URL userinfo is unsupported"
        )
    if args.local_only and args.rpc and not is_loopback(args.rpc):
        parser.error("--local-only requires a loopback RPC URL")
    if args.local_only and args.refresh:
        parser.error("--refresh cannot be used with --local-only")
    return args


def main():
    global LOCAL_ONLY, USE_CACHE
    args = parse_args()
    if not shutil.which("fzf"):
        raise AppError("fzf is required; install it with your system package manager.")
    if not sys.stdin.isatty() or not sys.stderr.isatty():
        raise AppError("Run this interactive browser in a terminal. Use --help for options.")
    LOCAL_ONLY = args.local_only
    USE_CACHE = not args.no_cache
    cache_dir = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "erc20-balance"
    say("Balance browser · ERC-20 + native coins")
    say(
        "Local-only mode"
        if LOCAL_ONLY
        else "Remote mode · the chosen provider can see queried addresses · --privacy for details"
    )
    chains = cached_list("chains", CHAIN_URL, cache_dir, args.refresh, args.timeout)
    tokens = cached_list("tokens", TOKEN_URL, cache_dir, args.refresh, args.timeout)["tokens"]
    chain = None
    if args.chain is not None:
        chain = next((c for c in chains if c["chainId"] == args.chain), None)
        if chain is None:
            raise AppError(f"Chain ID {args.chain} is not in the available chain list")
    wallet, endpoint, selected = args.wallet, None, None
    initial_rpc = args.rpc
    while True:
        if chain is None:
            try:
                chain = choose_chain(chains, tokens, args.testnets)
            except Cancelled:
                return
        if endpoint is None:
            try:
                first_rpc, initial_rpc = initial_rpc, None
                endpoint = choose_rpc(chain, args.timeout, initial=first_rpc)
            except Cancelled:
                chain = None
                continue
        if selected is None:
            try:
                selected = choose_tokens(chain, tokens, endpoint, args.timeout)
            except Cancelled:
                endpoint = None
                continue
        if wallet is None:
            try:
                wallet = ask_address()
            except Cancelled:
                selected = None
                continue
        show_balances(chain, endpoint, selected, wallet, args.timeout)
        while True:
            try:
                action = pick(
                    "Next",
                    ["refresh", "tokens", "rpc", "wallet", "chain", "quit"],
                    [
                        "Refresh balances",
                        "Change tokens",
                        "Change RPC",
                        "Change wallet",
                        "Change chain",
                        "Quit",
                    ],
                    header=f"{clean(chain['name'])} · {len(selected)} selected tokens",
                )
            except Cancelled:
                return
            try:
                if action == "quit":
                    return
                if action == "tokens":
                    selected = choose_tokens(chain, tokens, endpoint, args.timeout)
                elif action == "rpc":
                    endpoint = choose_rpc(chain, args.timeout, current=endpoint)
                elif action == "wallet":
                    wallet = ask_address(default=wallet)
                elif action == "chain":
                    new_chain = choose_chain(chains, tokens, args.testnets)
                    if new_chain["chainId"] != chain["chainId"]:
                        chain, endpoint, selected = new_chain, None, None
                break
            except Cancelled:
                continue


def run():
    try:
        main()
        return 0
    except (KeyboardInterrupt, Cancelled):
        say("\nCancelled.")
        return 130
    except (AppError, OSError) as exc:
        say(f"Error: {exc}")
        return 1
