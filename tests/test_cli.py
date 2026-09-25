#!/usr/bin/env python3
"""Offline regression and terminal integration tests for the installed CLI."""

import contextlib
import io
import json
import os
import shutil
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from erc20_balance import cli as app

SCRIPT = Path(os.environ.get("ERC20_BALANCE_TEST_BINARY") or shutil.which("erc20-balance") or "")

WALLET = "0x" + "12" * 20
TOKEN = {
    "chainId": 1,
    "symbol": "USDC",
    "name": "USD Coin",
    "decimals": 6,
    "address": "0x" + "34" * 20,
}
CHAIN = {
    "chainId": 1,
    "name": "Ethereum Mainnet",
    "rpc": [],
    "isTestnet": False,
    "nativeCurrency": {"symbol": "ETH", "name": "Ether", "decimals": 18},
}


class Handler(BaseHTTPRequestHandler):
    requests = []

    def log_message(self, *args):
        pass

    def do_GET(self):
        self.requests.append(("GET", self.path))
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", self.server.url + "/destination")
            self.end_headers()
            return
        if self.path == "/bad-json":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"not-json")
            return
        self.send_response(503)
        self.end_headers()

    def do_POST(self):
        request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.requests.append(request)
        method = request["method"]
        if self.path == "/wrong-chain":
            result = "0x89"
        elif self.path == "/empty":
            result = "0x"
        elif method == "eth_chainId":
            result = "0x1"
        elif method == "eth_blockNumber":
            result = "0xabc"
        elif method == "eth_getBalance":
            result = hex(1234567890123456789)
        elif method == "eth_call" and request["params"]:
            result = "0x" + format(
                6 if request["params"][0]["data"] == "0x313ce567" else 1234567, "064x"
            )
        else:
            result = None
        response = {"jsonrpc": "2.0", "id": 1, "result": result}
        if self.path == "/error":
            response = {
                "jsonrpc": "2.0",
                "id": 1,
                "error": {"code": -32000, "message": "secret-url"},
            }
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(response).encode())


class Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.url = f"http://127.0.0.1:{cls.server.server_port}"
        cls.server.url = cls.url
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def setUp(self):
        app.LOCAL_ONLY = False
        app.USE_CACHE = True
        Handler.requests.clear()

    def test_uint256_full_precision(self):
        n = 2**256 - 1
        text = app.format_balance(n, 18)
        self.assertEqual(int(text.replace(".", "")), n)
        self.assertEqual(len(text.split(".")[1]), 18)

    def test_decimal_precision(self):
        for n, d, expected in [
            (0, 18, "0"),
            (1, 24, "0." + "0" * 23 + "1"),
            (1230000, 6, "1.23"),
            (42, 0, "42"),
            (1, 255, "0." + "0" * 254 + "1"),
        ]:
            self.assertEqual(app.format_balance(n, d), expected)

    def test_invalid_abi_never_zero(self):
        for value in [None, "", "0x", "0x0", "null", "0xzz", "0x" + "0" * 65]:
            with self.subTest(value=value), self.assertRaises(app.AppError):
                app.hex_integer(value, word=True)
        self.assertEqual(app.hex_integer("0x" + "0" * 64, word=True), 0)

    def test_rpc_envelope_validation(self):
        for value in [
            None,
            [],
            {},
            {"jsonrpc": "2.0", "id": 2, "result": "0x0"},
            {"jsonrpc": "2.0", "id": 1},
            {"jsonrpc": "2.0", "id": 1, "result": None},
        ]:
            with (
                patch.object(app, "http_json", return_value=value),
                self.assertRaises(app.AppError),
            ):
                app.rpc(self.url, "eth_call", [], 1)

    def test_remote_http_and_url_userinfo_rejected(self):
        for url in [
            "http://example.com",
            "https://user:password@example.com",
            "http://user:password@localhost:8545",
        ]:
            with patch.object(app.urllib.request, "build_opener") as opener:
                with self.assertRaises(app.AppError):
                    app.http_json(url)
                opener.assert_not_called()
        self.assertTrue(app.valid_url("https://example.com/?key=example"))

    def test_no_cache_uses_memory_only(self):
        app.USE_CACHE = False
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory) / "should-not-exist"
            with (
                patch.object(app, "http_json", return_value={"tokens": [TOKEN]}),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                self.assertEqual(app.cached_list("tokens", app.TOKEN_URL, cache)["tokens"], [TOKEN])
            self.assertFalse(cache.exists())
            cache.mkdir()
            previous = cache / "tokens.json"
            previous.write_text("previous cache must not be read or changed")
            with (
                patch.object(app, "http_json", return_value={"tokens": [TOKEN]}),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                self.assertEqual(app.cached_list("tokens", app.TOKEN_URL, cache)["tokens"], [TOKEN])
            self.assertEqual(previous.read_text(), "previous cache must not be read or changed")

    def test_cache_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cache" / "tokens.json"
            app.atomic_json(path, {"tokens": [TOKEN]})
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)

    def test_menu_does_not_inherit_secrets_or_history_configuration(self):
        with patch.dict(
            os.environ,
            {
                "ERC20_RPC_URL": "https://example.com/secret",
                "SSLKEYLOGFILE": "/tmp/unused",
                "FZF_DEFAULT_OPTS": "--history=/tmp/unused",
                "FZF_DEFAULT_OPTS_FILE": "/tmp/unused",
            },
        ):
            with patch.object(
                app.subprocess,
                "run",
                return_value=types.SimpleNamespace(returncode=0, stdout="0\tone"),
            ) as run:
                app.pick("test", [1], ["one"])
            for name in [
                "ERC20_RPC_URL",
                "SSLKEYLOGFILE",
                "FZF_DEFAULT_OPTS",
                "FZF_DEFAULT_OPTS_FILE",
            ]:
                self.assertNotIn(name, run.call_args.kwargs["env"])

    def test_interruption_cancels_queued_queries(self):
        ready, release = threading.Event(), threading.Event()
        lock = threading.Lock()
        started = []

        def balance(*args):
            with lock:
                started.append(1)
                if len(started) == 4:
                    ready.set()
            release.wait(3)
            return "0"

        def interrupt(futures):
            if not ready.wait(2):
                raise AssertionError("Workers did not start")
            threading.Timer(0.1, release.set).start()
            raise KeyboardInterrupt()

        try:
            with (
                patch.object(app, "get_balance", side_effect=balance),
                patch.object(app, "as_completed", side_effect=interrupt),
            ):
                with (
                    self.assertRaises(KeyboardInterrupt),
                    contextlib.redirect_stderr(io.StringIO()),
                ):
                    app.show_balances(
                        CHAIN, {"url": self.url, "label": "mock"}, [TOKEN] * 20, WALLET, 1
                    )
            self.assertEqual(len(started), 4)
        finally:
            release.set()

    def test_json_error_and_http_error(self):
        for path, message in [("/bad-json", "invalid JSON"), ("/503", "HTTP 503")]:
            with self.assertRaisesRegex(app.AppError, message):
                app.http_json(self.url + path)

    def test_rpc_errors_redact_provider_text(self):
        with self.assertRaisesRegex(app.AppError, "-32000") as result:
            app.rpc(self.url + "/error", "eth_call", [], 1)
        self.assertNotIn("secret", str(result.exception))

    def test_chain_verification(self):
        _, block = app.check_rpc(self.url, 1, 1)
        self.assertEqual(block, 0xABC)
        with self.assertRaisesRegex(app.AppError, "Wrong network"):
            app.check_rpc(self.url + "/wrong-chain", 1, 1)

    def test_native_and_token_at_same_block(self):
        self.assertEqual(app.get_balance(self.url, TOKEN, WALLET, "0xabc", 1), "1.234567")
        self.assertEqual(
            app.get_balance(self.url, app.native_token(CHAIN), WALLET, "0xabc", 1),
            "1.234567890123456789",
        )
        for req in Handler.requests:
            self.assertEqual(req["params"][-1], "0xabc")
        self.assertEqual(
            Handler.requests[0]["params"][0]["data"], "0x70a08231" + "0" * 24 + WALLET[2:]
        )

    def test_empty_contract_response_is_error(self):
        with self.assertRaises(app.AppError):
            app.get_balance(self.url + "/empty", TOKEN, WALLET, "0xabc", 1)

    def test_selected_rpc_only_and_errors_visible(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            app.show_balances(
                CHAIN, {"url": self.url + "/empty", "label": "test"}, [TOKEN], WALLET, 1
            )
        # Empty block number aborts before querying the wallet.
        self.assertEqual([r["method"] for r in Handler.requests], ["eth_blockNumber"])
        with patch.object(app, "get_balance", side_effect=app.AppError("unavailable")):
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                app.show_balances(CHAIN, {"url": self.url, "label": "test"}, [TOKEN], WALLET, 1)
        self.assertIn("ERROR", out.getvalue())
        self.assertIn("0/1 successful", out.getvalue())

    def test_local_mode_blocks_remote_before_network(self):
        app.LOCAL_ONLY = True
        for url in [
            "https://example.com",
            "http://localhost.example.com",
            "http://192.168.1.2",
            "http://127.0.0.1@example.com",
            "http://2130706433",
        ]:
            with patch.object(app.urllib.request, "build_opener") as opener:
                with self.assertRaises(app.AppError):
                    app.http_json(url)
                opener.assert_not_called()

    def test_loopback_classification(self):
        for url in ["http://127.0.0.1:8545", "http://localhost:8545", "http://[::1]:8545"]:
            self.assertTrue(app.is_loopback(url))

    def test_local_proxy_bypass(self):
        app.LOCAL_ONLY = True
        with patch.dict(
            os.environ,
            {
                "http_proxy": "http://127.0.0.1:1",
                "HTTP_PROXY": "http://127.0.0.1:1",
                "no_proxy": "",
                "NO_PROXY": "",
            },
        ):
            self.assertEqual(app.rpc(self.url, "eth_chainId", [], 1), "0x1")

    def test_redirect_blocked(self):
        app.LOCAL_ONLY = True
        with self.assertRaisesRegex(app.AppError, "redirect blocked"):
            app.http_json(self.url + "/redirect")
        self.assertEqual(Handler.requests, [("GET", "/redirect")])

    def test_offline_fallback_no_requests(self):
        app.LOCAL_ONLY = True
        with tempfile.TemporaryDirectory() as directory, patch.object(app, "http_json") as network:
            with contextlib.redirect_stderr(io.StringIO()):
                chains = app.cached_list("chains", app.CHAIN_URL, Path(directory))
                tokens = app.cached_list("tokens", app.TOKEN_URL, Path(directory))
            network.assert_not_called()
            self.assertEqual(chains[0]["chainId"], 1)
            self.assertEqual(tokens, {"tokens": []})

    def test_cached_data_does_not_get_refetched(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(app, "http_json") as network:
            path = Path(directory) / "tokens.json"
            app.atomic_json(path, {"tokens": [TOKEN]})
            result = app.cached_list("tokens", app.TOKEN_URL, Path(directory))
            self.assertEqual(result["tokens"], [TOKEN])
            network.assert_not_called()

    def test_failed_refresh_preserves_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tokens.json"
            app.atomic_json(path, {"tokens": [TOKEN]})
            before = path.read_bytes()
            with patch.object(app, "http_json", side_effect=app.AppError("offline")):
                with contextlib.redirect_stderr(io.StringIO()):
                    result = app.cached_list("tokens", app.TOKEN_URL, Path(directory), refresh=True)
            self.assertEqual(result["tokens"], [TOKEN])
            self.assertEqual(path.read_bytes(), before)

    def test_bad_download_does_not_replace_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tokens.json"
            app.atomic_json(path, {"tokens": [TOKEN]})
            before = path.read_bytes()
            with patch.object(app, "http_json", return_value={"error": "unavailable"}):
                with contextlib.redirect_stderr(io.StringIO()):
                    app.cached_list("tokens", app.TOKEN_URL, Path(directory), refresh=True)
            self.assertEqual(path.read_bytes(), before)

    def test_non_evm_tokens_filtered(self):
        non_evm = dict(TOKEN, address="someSolanaAddress", chainId=501000101)
        data = app.validate_list({"tokens": [non_evm, TOKEN]}, "tokens")
        self.assertEqual(data["tokens"], [TOKEN])

    def test_priority_and_contract_dedup(self):
        other = dict(TOKEN, symbol="ZZZ", address="0x" + "56" * 20)
        captured = {}

        def pick(title, choices, labels, **kwargs):
            captured["choices"] = choices
            return [choices[1]]

        with patch.object(app, "pick", side_effect=pick):
            app.choose_tokens(CHAIN, [other, TOKEN, dict(TOKEN)], {}, 1)
        self.assertIsNone(captured["choices"][0]["address"])
        self.assertEqual(captured["choices"][1]["symbol"], "USDC")
        self.assertEqual(len(captured["choices"]), 4)  # Native, USDC, ZZZ, custom
        self.assertLess(app.chain_order(CHAIN), app.chain_order(dict(CHAIN, chainId=9999)))

    def test_rpc_candidates_filter_and_rank(self):
        chain = dict(
            CHAIN,
            rpc=[
                "wss://example.com",
                "https://example.com/${KEY}",
                {"url": "https://tracked.example", "tracking": "yes"},
                {"url": "https://untracked.example", "tracking": "none"},
                "https://untracked.example",
            ],
        )
        result = app.rpc_candidates(chain)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["tracking"], "none")

    def test_no_logging_provider_claim_is_prioritized(self):
        chain = dict(
            CHAIN,
            rpc=[
                {"url": "https://aaa.example", "tracking": "none"},
                {"url": "https://public.1rpc.io/eth", "tracking": "none"},
            ],
        )
        result = app.rpc_candidates(chain)
        self.assertEqual(result[0]["url"], "https://public.1rpc.io/eth")
        self.assertIn("claims", app.privacy_label(result[0]))
        self.assertIn("unverified", app.privacy_label(result[0]))
        self.assertFalse(app.claims_no_logs("https://public.1rpc.io.example/eth"))

    def test_remote_is_default(self):
        with patch.object(sys, "argv", ["erc20-balance"]):
            self.assertFalse(app.parse_args().local_only)

    def test_rpc_picker_tracks_choice(self):
        with (
            patch.object(app, "pick", return_value={"url": self.url, "custom": True}),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            result = app.choose_rpc(CHAIN, 1)
        self.assertEqual(result["url"], self.url)

    def test_cancel_menu(self):
        with patch.object(
            app.subprocess, "run", return_value=types.SimpleNamespace(returncode=130)
        ):
            with self.assertRaises(app.Cancelled):
                app.pick("test", [1], ["one"])

    def test_custom_token_decimals(self):
        with (
            patch.object(app, "ask_address", return_value=TOKEN["address"]),
            patch.object(app, "prompt", return_value="TEST"),
        ):
            token = app.custom_token({"url": self.url}, 1)
        self.assertEqual(token["decimals"], 6)
        self.assertEqual(token["symbol"], "TEST")

    def test_cli_help_and_validation(self):
        for arg in ["--help", "--privacy"]:
            result = subprocess.run([str(SCRIPT), arg], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
        for args in [
            ["--timeout", "nan"],
            ["--wallet", "bad"],
            ["--local-only", "--rpc", "https://example.com"],
            ["--local-only", "--refresh"],
        ]:
            result = subprocess.run([str(SCRIPT)] + args, capture_output=True, text=True)
            self.assertEqual(result.returncode, 2, result.stderr)

    def test_real_terminal_flow(self):
        try:
            import pexpect
        except ImportError:
            self.skipTest("pexpect needed for terminal integration test")
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory) / "erc20-balance"
            app.atomic_json(cache / "chains.json", [CHAIN])
            app.atomic_json(cache / "tokens.json", {"tokens": [TOKEN]})
            env = dict(os.environ, XDG_CACHE_HOME=directory, TERM="xterm-256color")
            child = pexpect.spawn(
                str(SCRIPT),
                ["--local-only", "--chain", "1", "--rpc", self.url, "--wallet", WALLET],
                env=env,
                encoding="utf-8",
                timeout=8,
                dimensions=(35, 140),
            )

            def expect_terminal(pattern):
                while child.expect([pattern, r"\x1b\[6n"]) == 1:
                    child.send("\x1b[10;1R")

            try:
                expect_terminal("Tokens")
                child.send("USDC")
                expect_terminal("1/3")
                child.send("\t")
                expect_terminal(r"\(1\)")
                child.send("\x15")
                expect_terminal("3/3")
                child.send("native")
                expect_terminal("1/3")
                child.send("\t")
                expect_terminal(r"\(2\)")
                child.send("\r")
                expect_terminal("2/2 successful")
                expect_terminal("Next")
                time.sleep(0.15)
                child.send("\x1b")
                child.expect(pexpect.EOF)
                child.close()
                self.assertEqual(child.exitstatus, 0)
            finally:
                child.close(force=True)
        methods = [r["method"] for r in Handler.requests]
        self.assertIn("eth_call", methods)
        self.assertIn("eth_getBalance", methods)
        for req in Handler.requests:
            if req["method"] in ("eth_call", "eth_getBalance"):
                self.assertEqual(req["params"][-1], "0xabc")

    def test_https_terminal_session_leaves_no_logs_or_cache(self):
        try:
            import pexpect
        except ImportError:
            self.skipTest("pexpect needed for terminal integration test")
        if not shutil.which("openssl"):
            self.skipTest("openssl needed for local HTTPS fixture")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cert, key = root / "cert.pem", root / "key.pem"
            subprocess.run(
                [
                    "openssl",
                    "req",
                    "-x509",
                    "-newkey",
                    "rsa:2048",
                    "-nodes",
                    "-keyout",
                    str(key),
                    "-out",
                    str(cert),
                    "-days",
                    "1",
                    "-subj",
                    "/CN=127.0.0.1",
                    "-addext",
                    "subjectAltName=IP:127.0.0.1",
                    "-addext",
                    "basicConstraints=critical,CA:TRUE",
                ],
                check=True,
                capture_output=True,
            )
            server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(cert, key)
            server.socket = context.wrap_socket(server.socket, server_side=True)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            url = f"https://127.0.0.1:{server.server_port}"
            history, keylog, opts = root / "fzf-history", root / "tls-keys", root / "fzf-options"
            opts.write_text(f"--history={history}")
            env = dict(
                os.environ,
                XDG_CACHE_HOME=str(root / "cache"),
                TERM="xterm-256color",
                SSL_CERT_FILE=str(cert),
                SSLKEYLOGFILE=str(keylog),
                FZF_DEFAULT_OPTS=f"--history={history}",
                FZF_DEFAULT_OPTS_FILE=str(opts),
            )
            child = pexpect.spawn(
                str(SCRIPT),
                ["--local-only", "--no-cache", "--chain", "1", "--rpc", url, "--wallet", WALLET],
                env=env,
                encoding="utf-8",
                timeout=8,
                dimensions=(35, 140),
            )

            def expect_terminal(pattern):
                while child.expect([pattern, r"\x1b\[6n"]) == 1:
                    child.send("\x1b[10;1R")

            try:
                expect_terminal("Tokens ›")
                child.send("\r")
                expect_terminal("1/1 successful")
                expect_terminal("Next ›")
                child.send("\x1b")
                child.expect(pexpect.EOF)
                child.close()
                self.assertEqual(child.exitstatus, 0)
                self.assertFalse(keylog.exists())
                self.assertFalse(history.exists())
                self.assertFalse((root / "cache").exists())
            finally:
                child.close(force=True)
                server.shutdown()
                server.server_close()
                thread.join()

    def test_remote_menus_and_back_navigation(self):
        try:
            import pexpect
        except ImportError:
            self.skipTest("pexpect needed for terminal integration test")
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory) / "erc20-balance"
            app.atomic_json(
                cache / "chains.json", [dict(CHAIN, rpc=[{"url": self.url, "tracking": "none"}])]
            )
            app.atomic_json(cache / "tokens.json", {"tokens": [TOKEN]})
            env = dict(os.environ, XDG_CACHE_HOME=directory, TERM="xterm-256color")
            child = pexpect.spawn(
                str(SCRIPT),
                ["--wallet", WALLET],
                env=env,
                encoding="utf-8",
                timeout=8,
                dimensions=(35, 140),
            )

            def expect_terminal(pattern):
                while child.expect([pattern, r"\x1b\[6n"]) == 1:
                    child.send("\x1b[10;1R")

            try:
                expect_terminal("Chain ›")
                child.send("\r")
                expect_terminal("RPC ›")
                child.send("\r")
                expect_terminal("Tokens ›")
                child.send("\r")
                expect_terminal("1/1 successful")
                expect_terminal("Next ›")
                child.send("Change RPC")
                # Older fzf redraws can fill the PTY buffer. Drain output until
                # filtering finishes before accepting the highlighted result.
                expect_terminal("1/6")
                child.send("\r")
                expect_terminal("RPC ›")
                child.send("\x1b")
                expect_terminal("Next ›")
                child.send("\x1b")
                child.expect(pexpect.EOF)
                child.close()
                self.assertEqual(child.exitstatus, 0)
            finally:
                child.close(force=True)
        self.assertEqual([r["method"] for r in Handler.requests].count("eth_getBalance"), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
