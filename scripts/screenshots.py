#!/usr/bin/env python3
"""Capture the actual CLI with fixture data; never contact public RPCs."""

import json
import os
import shutil
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pexpect
import pyte
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "docs" / "screenshots"
WALLET = "0x1111111111111111111111111111111111111111"
TOKENS = [
    ("USDC", "USD Coin", "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48", 6, 1234567000),
    ("USDT", "Tether USD", "0xdAC17F958D2ee523a2206206994597C13D831ec7", 6, 250000000),
    (
        "DAI",
        "Dai Stablecoin",
        "0x6B175474E89094C44Da98b954EedeAC495271d0F",
        18,
        42000000000000000000,
    ),
    (
        "WETH",
        "Wrapped Ether",
        "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
        18,
        1250000000000000000,
    ),
    ("WBTC", "Wrapped BTC", "0x2260FAC5E5542a773Aa44fBCfeDf7C193bc2C599", 8, 2500000),
    ("LINK", "Chainlink", "0x514910771AF9Ca656af840dff83E8264EcF986CA", 18, 50000000000000000000),
]
CHAINS = [
    (1, "Ethereum Mainnet", "ETH"),
    (8453, "Base", "ETH"),
    (42161, "Arbitrum One", "ETH"),
    (10, "OP Mainnet", "ETH"),
    (137, "Polygon Mainnet", "POL"),
    (56, "BNB Smart Chain Mainnet", "BNB"),
    (43114, "Avalanche C-Chain", "AVAX"),
    (130, "Unichain", "ETH"),
]
PALETTE = {
    "default": "#dce6f5",
    "black": "#151b26",
    "red": "#ef7d86",
    "green": "#9cce8a",
    "brown": "#eac58d",
    "blue": "#83b6f5",
    "magenta": "#bc9bec",
    "cyan": "#84d2dc",
    "white": "#dce6f5",
    "brightblack": "#6f829c",
    "brightred": "#ff8e99",
    "brightgreen": "#b0e899",
    "brightbrown": "#ffdb9f",
    "brightblue": "#a2cbff",
    "brightmagenta": "#d8b6ff",
    "brightcyan": "#a0edf6",
    "brightwhite": "#ffffff",
}
BACKGROUND = "#151b26"


class DemoRPC(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_CONNECT(self):
        # The capture environment routes accidental remote selections here.
        self.send_error(403, "Screenshot sessions cannot contact public RPCs")

    def do_POST(self):
        request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        method = request["method"]
        if method == "eth_chainId":
            result = "0x1"
        elif method == "eth_blockNumber":
            result = hex(21000000)
        elif method == "eth_getBalance":
            result = hex(5420000000000000000)
        elif method == "eth_call":
            address = request["params"][0]["to"].lower()
            value = next(t[4] for t in TOKENS if t[2].lower() == address)
            result = "0x" + format(value, "064x")
        else:
            raise ValueError(method)
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(
            json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result}).encode()
        )


def color(value, background=False):
    if value == "default" and background:
        return BACKGROUND
    return PALETTE.get(value, "#" + value if len(value) == 6 else PALETTE["default"])


def render(screen, name):
    font = ImageFont.truetype("DejaVuSansMono.ttf", 16)
    cell_width, cell_height = 10, 24
    used = [i for i, row in enumerate(screen.display) if row.strip()]
    first, last = (used[0], used[-1]) if used else (0, 0)
    width, height = screen.columns * cell_width + 48, (last - first + 1) * cell_height + 100
    image = Image.new("RGB", (width, height), BACKGROUND)
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, width, 44), fill="#202a3a")
    for x, fill in [(22, "#ed6a5e"), (44, "#f4bf50"), (66, "#61c454")]:
        draw.ellipse((x, 16, x + 11, 27), fill=fill)
    draw.text((96, 12), "erc20-balance", font=font, fill="#dce6f5")
    for y in range(first, last + 1):
        for x in range(screen.columns):
            cell = screen.buffer[y][x]
            fg, bg = color(cell.fg), color(cell.bg, True)
            if cell.reverse:
                fg, bg = bg, fg
            left, top = 24 + x * cell_width, 58 + (y - first) * cell_height
            if bg != BACKGROUND:
                draw.rectangle((left, top, left + cell_width, top + cell_height), fill=bg)
            if cell.data.strip():
                draw.text((left, top), cell.data, font=font, fill=fg)
    draw.text(
        (24, height - 30), "DEMO · fixture metadata and sample balances", font=font, fill="#8293ac"
    )
    OUTPUT.mkdir(parents=True, exist_ok=True)
    image.save(OUTPUT / name)
    print(OUTPUT / name)


class Capture:
    def __init__(self, child):
        self.screen = pyte.Screen(116, 30)
        self.screen.write_process_input = child.send
        self.stream = pyte.Stream(self.screen)
        self.tail = ""
        self.saved_balances = False

    def write(self, data):
        for character in data:
            self.stream.feed(character)
            self.tail = (self.tail + character)[-80:]
            if not self.saved_balances and self.tail.endswith("3/3 successful"):
                render(self.screen, "balances.png")
                self.saved_balances = True

    def flush(self):
        pass


def main():
    binary = shutil.which("erc20-balance")
    if binary is None:
        raise SystemExit("Run with uv run --group screenshots python scripts/screenshots.py")
    server = ThreadingHTTPServer(("127.0.0.1", 0), DemoRPC)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with tempfile.TemporaryDirectory(prefix="erc20-screenshots-") as directory:
            cache = Path(directory) / "erc20-balance"
            cache.mkdir()
            chains = [
                {
                    "chainId": cid,
                    "name": name,
                    "isTestnet": False,
                    "nativeCurrency": {"symbol": symbol, "name": symbol, "decimals": 18},
                    "rpc": [
                        {"url": "https://public.1rpc.io/eth", "tracking": "none"},
                        {"url": "https://ethereum-rpc.publicnode.com", "tracking": "none"},
                        {"url": "https://eth.drpc.org", "tracking": "none"},
                    ],
                }
                for cid, name, symbol in CHAINS
            ]
            tokens = [
                {
                    "chainId": 1,
                    "symbol": symbol,
                    "name": name,
                    "address": address,
                    "decimals": decimals,
                }
                for symbol, name, address, decimals, _ in TOKENS
            ]
            (cache / "chains.json").write_text(json.dumps(chains))
            (cache / "tokens.json").write_text(json.dumps({"tokens": tokens}))
            env = dict(os.environ, XDG_CACHE_HOME=directory, TERM="xterm-256color")
            proxy = f"http://127.0.0.1:{server.server_port}"
            env.update(
                http_proxy=proxy,
                https_proxy=proxy,
                HTTP_PROXY=proxy,
                HTTPS_PROXY=proxy,
                no_proxy="",
                NO_PROXY="",
            )
            # Never carry a user's configured RPC into a screenshot session.
            env.pop("ERC20_RPC_URL", None)
            child = pexpect.spawn(
                binary,
                ["--wallet", WALLET],
                env=env,
                encoding="utf-8",
                timeout=10,
                dimensions=(30, 116),
            )
            capture = Capture(child)
            child.logfile_read = capture

            def wait_for(pattern):
                child.expect(pattern)
                deadline = time.monotonic() + 0.3
                while time.monotonic() < deadline:
                    try:
                        child.read_nonblocking(65536, timeout=0.05)
                    except pexpect.TIMEOUT:
                        pass

            try:
                wait_for("Chain ›")
                render(capture.screen, "chains.png")
                child.send("\r")
                wait_for("RPC ›")
                render(capture.screen, "rpc.png")
                child.send("custom")
                time.sleep(0.15)
                child.send("\r")
                wait_for("hidden input; q back")
                child.sendline(f"http://127.0.0.1:{server.server_port}")
                wait_for("Tokens ›")
                child.send("\t\t\t")
                time.sleep(0.15)
                # Pull the actual redraw before capturing selected token rows.
                try:
                    child.read_nonblocking(65536, timeout=0.2)
                except pexpect.TIMEOUT:
                    pass
                render(capture.screen, "tokens.png")
                child.send("\r")
                wait_for("Next ›")
                child.send("\x1b")
                child.expect(pexpect.EOF)
                child.close()
                if child.exitstatus != 0 or not capture.saved_balances:
                    raise RuntimeError("CLI screenshot session did not complete")
            finally:
                child.close(force=True)
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


if __name__ == "__main__":
    main()
