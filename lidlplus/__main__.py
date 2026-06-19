#!/usr/bin/env python3
"""Lidl Plus command line tool."""

from __future__ import annotations

import argparse
import http.server
import json
import logging
import os
import socketserver
import sys
import webbrowser
from datetime import datetime, timezone
from getpass import getpass
from pathlib import Path

from lidlplus import LidlPlusApi
from lidlplus.archive.site import write_site
from lidlplus.backup.sync import default_data_dir, sync
from lidlplus.exceptions import LegalTermsException, LoginError, MissingLogin, WebBrowserException


def get_arguments():
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        prog="lidl-plus",
        description="Lidl Plus API, backup, and local archive",
        formatter_class=lambda prog: argparse.HelpFormatter(prog, max_help_position=28),
    )
    parser.add_argument("-c", "--country", metavar="CC", help="country (DE, BE, NL, AT, …)")
    parser.add_argument("-l", "--language", metavar="LANG", help="language (de, en, fr, it, …)")
    parser.add_argument("-u", "--user", help="Lidl Plus login username")
    parser.add_argument("-p", "--password", metavar="XXX", help="Lidl Plus login password")
    parser.add_argument(
        "--2fa",
        choices=["phone", "email"],
        default="phone",
        help="two-factor method for selenium login",
    )
    parser.add_argument("-r", "--refresh-token", metavar="TOKEN", help="refresh token")
    parser.add_argument("-a", "--access-token", metavar="TOKEN", help="access token")
    parser.add_argument("--skip-verify", action="store_true", help="skip SSL verification (selenium)")
    parser.add_argument(
        "--not-accept-legal-terms",
        action="store_true",
        help="do not auto-accept legal terms (selenium)",
    )
    parser.add_argument("-d", "--debug", action="store_true", help="debug logging")
    subparser = parser.add_subparsers(title="commands", metavar="command", required=True, dest="command")

    browser_auth = subparser.add_parser(
        "browser-auth",
        help="log in via browser + paste callback (supported)",
    )
    browser_auth.add_argument(
        "--open",
        action="store_true",
        help="open the login URL in your default browser",
    )
    browser_auth.set_defaults(command_action="browser-auth")

    auth = subparser.add_parser(
        "auth",
        help="broken — selenium login does not work; use browser-auth",
    )
    auth.set_defaults(command_action="auth")

    loyalty_id = subparser.add_parser("id", help="show loyalty ID")
    loyalty_id.set_defaults(command_action="id")

    receipt = subparser.add_parser("receipt", help="download receipts to out/")
    receipt.add_argument("-n", "--count", type=int, help="number of receipts to download")
    receipt.set_defaults(command_action="receipt")

    coupon = subparser.add_parser("coupon", help="list or activate coupons")
    coupon.add_argument("--all", action="store_true", help="activate all available coupons")
    coupon.set_defaults(command_action="coupon")

    backup = subparser.add_parser("backup", help="incremental receipt archive")
    backup_sub = backup.add_subparsers(dest="backup_command", required=True)

    backup_sync = backup_sub.add_parser("sync", help="download new receipts and rebuild index")
    backup_sync.add_argument(
        "--data-dir",
        type=Path,
        default=default_data_dir(),
        help="backup directory (default: ./data, /data in Docker)",
    )
    backup_sync.add_argument("--full", action="store_true", help="re-download all receipts")
    backup_sync.add_argument("--no-coupons", action="store_true", help="skip coupon snapshot")
    backup_sync.set_defaults(command_action="backup-sync")

    backup_index = backup_sub.add_parser("index", help="rebuild search index from archive")
    backup_index.add_argument("--data-dir", type=Path, default=default_data_dir())
    backup_index.set_defaults(command_action="backup-index")

    backup_serve = backup_sub.add_parser("serve", help="serve archive over HTTP and open browser")
    backup_serve.add_argument("--data-dir", type=Path, default=default_data_dir())
    backup_serve.add_argument("--host", default="127.0.0.1", help="bind address (use 0.0.0.0 in Docker)")
    backup_serve.add_argument("--port", type=int, default=8765, help="HTTP port")
    backup_serve.add_argument(
        "--no-open",
        action="store_true",
        help="do not try to open a browser (use in headless/Docker)",
    )
    backup_serve.set_defaults(command_action="backup-serve")

    return parser.parse_args()


def check_oic():
    try:
        import oic  # noqa: F401
    except ImportError as error:
        raise ImportError(
            "OAuth login requires oic (installed by default with lidl-plus)."
        ) from error


def check_selenium():
    try:
        import getuseragent  # noqa: F401
        import seleniumwire  # noqa: F401
        import webdriver_manager  # noqa: F401
    except ImportError:
        print(
            "Selenium login requires optional dependencies:\n"
            "  uv sync --extra selenium\n"
            "Prefer manual login: lidl-plus browser-auth"
        )
        sys.exit(1)


def resolve_locale(args):
    language = args.language or os.environ.get("LIDL_LANGUAGE") or input("Language (de, en, …): ")
    country = args.country or os.environ.get("LIDL_COUNTRY") or input("Country (DE, AT, …): ")
    return language, country


def api_from_args(args):
    language = args.language or os.environ.get("LIDL_LANGUAGE")
    country = args.country or os.environ.get("LIDL_COUNTRY")
    refresh_token = args.refresh_token or os.environ.get("LIDL_REFRESH_TOKEN")
    if args.access_token:
        if not language or not country:
            language, country = resolve_locale(args)
        api = LidlPlusApi(language, country)
        api._token = args.access_token
        return api
    if refresh_token:
        if not language or not country:
            language, country = resolve_locale(args)
        return LidlPlusApi(language, country, refresh_token)
    return None


def lidl_plus_login(args):
    """Selenium-based login (legacy)."""
    api = api_from_args(args)
    if api:
        return api
    check_selenium()
    if args.skip_verify:
        os.environ["WDM_SSL_VERIFY"] = "0"
        os.environ["CURL_CA_BUNDLE"] = ""
    language, country = resolve_locale(args)
    login_method = input("Login with email or phone? ([e]mail / [p]hone): ")
    if login_method.lower() not in ["e", "p"]:
        sys.exit(1)
    if login_method == "e":
        username = args.user or input("Email: ")
    else:
        username = args.user or input("Phone number: ")
    password = args.password or getpass("Password: ")
    lidl_plus = LidlPlusApi(language, country)
    try:
        text = f"Verification code ({getattr(args, '2fa', 'phone')}): "
        lidl_plus.login(
            username,
            password,
            login_method,
            verify_token_func=lambda: input(text),
            verify_mode=getattr(args, "2fa", "phone"),
            headless=not args.debug,
            accept_legal_terms=not args.not_accept_legal_terms,
        )
    except WebBrowserException:
        print("No supported browser found. Install Chrome, Chromium, or Firefox.")
        sys.exit(101)
    except LoginError as error:
        print(f"Login failed: {error}")
        sys.exit(102)
    except LegalTermsException as error:
        print(f"Legal terms not accepted: {error}")
        sys.exit(103)
    return lidl_plus


def print_refresh_token(lidl_plus):
    token = lidl_plus.refresh_token
    pad = max(0, len(token) - len("refresh token"))
    print(f"{'-' * (pad // 2)} refresh token {'-' * (pad // 2 - 1)}\n{token}\n{'-' * len(token)}")
    print(
        "\nExport for backup:\n"
        f"  export LIDL_LANGUAGE={lidl_plus._language} LIDL_COUNTRY={lidl_plus._country} "
        f"LIDL_REFRESH_TOKEN={token}\n"
        "  lidl-plus backup sync"
    )


def run_browser_auth(args):
    check_oic()
    language, country = resolve_locale(args)
    lidl_plus = LidlPlusApi(language, country)
    try:
        lidl_plus.browser_auth(open_browser=args.open)
    except KeyboardInterrupt:
        print("Aborted.")
        sys.exit(130)
    except ImportError as error:
        print(error)
        sys.exit(1)
    except (LoginError, WebBrowserException) as error:
        print(error)
        sys.exit(1)
    print_refresh_token(lidl_plus)


def print_loyalty_id(args):
    print(lidl_plus_login(args).loyalty_id())


def save_tickets(args):
    lidl_plus = lidl_plus_login(args)
    total_tickets = args.count or int(input("Number of receipts to download: "))
    tickets = lidl_plus.tickets()
    downloaded = []
    os.makedirs("out/", exist_ok=True)
    for index in range(total_tickets):
        try:
            ticket = lidl_plus.ticket(tickets[index]["id"])
            downloaded.append(ticket)
            with open(f"out/{tickets[index]['id']}.html", "w", encoding="utf-8") as handle:
                handle.write(ticket["htmlPrintedReceipt"])
        except Exception as error:
            print(f"Failed {tickets[index]['id']}: {error}")
    with open("out/summary.json", "w", encoding="utf-8") as handle:
        json.dump(downloaded, handle)
    print("Saved receipts to out/")


def activate_coupons(args):
    lidl_plus = lidl_plus_login(args)
    coupons = lidl_plus.coupons()
    if not args.all:
        print(json.dumps(coupons, indent=2))
        return
    activated = 0
    for section in coupons.get("sections", {}):
        for coupon in section.get("promotions", {}):
            if coupon["isActivated"]:
                continue
            start = datetime.fromisoformat(coupon["validity"]["start"])
            end = datetime.fromisoformat(coupon["validity"]["end"])
            now = datetime.now(timezone.utc)
            if start > now or end < now:
                continue
            print("activating:", coupon["title"])
            lidl_plus.activate_coupon(coupon["id"])
            activated += 1
    print(f"Activated {activated} coupons")


def run_backup_sync(args):
    language = args.language or os.environ.get("LIDL_LANGUAGE")
    country = args.country or os.environ.get("LIDL_COUNTRY")
    refresh_token = args.refresh_token or os.environ.get("LIDL_REFRESH_TOKEN")
    missing = [
        name
        for name, value in (
            ("language (-l / LIDL_LANGUAGE)", language),
            ("country (-c / LIDL_COUNTRY)", country),
            ("refresh token (-r / LIDL_REFRESH_TOKEN)", refresh_token),
        )
        if not value
    ]
    if missing:
        print("Missing " + ", ".join(missing) + ".", file=sys.stderr)
        sys.exit(2)
    api = LidlPlusApi(language, country, refresh_token)
    counts = sync(
        api,
        args.data_dir,
        full=args.full,
        with_coupons=not args.no_coupons,
    )
    www = args.data_dir / "www" / "index.html"
    if counts.get("interrupted"):
        print(
            f"Interrupted: {counts['downloaded']} new, "
            f"{counts['skipped'] + counts['downloaded']}/{counts['listed']} archived, "
            f"{counts['failed']} failed."
        )
        print(f"Partial archive: {www.resolve()}")
        print(f"Browse: lidl-plus backup serve --data-dir {args.data_dir}")
        sys.exit(130)
    print(
        f"Sync complete: {counts['downloaded']} new, "
        f"{counts['skipped'] + counts['downloaded']}/{counts['listed']} archived, "
        f"{counts['failed']} failed."
    )
    print(f"Archive: {www.resolve()}")
    print(f"Browse: lidl-plus backup serve --data-dir {args.data_dir}")
    sys.exit(1 if counts["failed"] else 0)


def run_backup_index(args):
    count = write_site(args.data_dir)
    www = args.data_dir / "www" / "index.html"
    print(f"Indexed {count} receipts → {www.resolve()}")
    print(f"Browse: lidl-plus backup serve --data-dir {args.data_dir}")


def run_backup_serve(args):
    root = (args.data_dir / "www").resolve()
    if not (root / "index.html").is_file():
        print(f"Archive not found: {root}\nRun backup sync or backup index first.", file=sys.stderr)
        sys.exit(1)

    url = f"http://{args.host}:{args.port}/"

    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *handler_args, **kwargs):
            super().__init__(*handler_args, directory=str(root), **kwargs)

    print(f"Serving {root}")
    print(f"Open: {url}")

    if not args.no_open:
        try:
            webbrowser.open(url, new=2)
        except OSError:
            pass

    with socketserver.TCPServer((args.host, args.port), Handler) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nStopped.")
            sys.exit(130)


def main():
    args = get_arguments()
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(levelname)s: %(message)s",
    )
    action = args.command_action
    try:
        if action == "browser-auth":
            run_browser_auth(args)
        elif action == "auth":
            print_refresh_token(lidl_plus_login(args))
        elif action == "id":
            print_loyalty_id(args)
        elif action == "receipt":
            save_tickets(args)
        elif action == "coupon":
            activate_coupons(args)
        elif action == "backup-sync":
            run_backup_sync(args)
        elif action == "backup-index":
            run_backup_index(args)
        elif action == "backup-serve":
            run_backup_serve(args)
    except MissingLogin as error:
        print(error, file=sys.stderr)
        sys.exit(2)


def start():
    try:
        main()
    except KeyboardInterrupt:
        print("Aborted.")


if __name__ == "__main__":
    start()
