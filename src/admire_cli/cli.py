"""admire: command-line client for the Admire MCP server."""

import argparse
import json
import os
import sys
import time

from . import __version__, oauth
from .http import HTTPError
from .mcp import Client, MCPError
from .store import Store, config_dir

DEFAULT_SERVER = "https://app.ca.admire.ai/mcp"
CACHE_TTL = 24 * 3600
COMMANDS = {"login", "logout", "status", "tools", "describe", "call", "config"}


# --- settings + tool cache -------------------------------------------------

def _settings_path():
    return config_dir() / "settings.json"


def load_settings():
    try:
        return json.loads(_settings_path().read_text())
    except FileNotFoundError:
        return {}


def save_settings(settings):
    Store(_settings_path()).save(settings)


def get_tools(client, refresh=False):
    cache = config_dir() / "tools.json"
    if not refresh:
        try:
            data = json.loads(cache.read_text())
            if time.time() - data["fetched_at"] < CACHE_TTL and data["server_url"] == client.server_url:
                return data["tools"]
        except (FileNotFoundError, KeyError, ValueError):
            pass
    client.initialize()
    tools = client.list_tools()
    Store(cache).save({"fetched_at": int(time.time()), "server_url": client.server_url, "tools": tools})
    return tools


def find_tool(tools, name):
    for tool in tools:
        if tool["name"] == name:
            return tool
    close = [t["name"] for t in tools if name.lower() in t["name"].lower()]
    hint = f" Did you mean: {', '.join(close[:5])}?" if close else ""
    raise SystemExit(f"Unknown tool: {name}.{hint} Run `admire tools` to list them.")


# --- argument parsing for tool calls ---------------------------------------

def parse_pairs(pairs):
    """key=value pairs; values are parsed as JSON when valid, else kept as strings."""
    args = {}
    for pair in pairs:
        if "=" not in pair:
            raise SystemExit(f"Expected key=value, got: {pair}")
        key, raw = pair.split("=", 1)
        try:
            args[key] = json.loads(raw)
        except ValueError:
            args[key] = raw
    return args


def build_arguments(tool, pairs, json_arg):
    args = {}
    if json_arg:
        text = sys.stdin.read() if json_arg == "-" else json_arg
        args.update(json.loads(text))
    args.update(parse_pairs(pairs))
    props = (tool.get("inputSchema") or {}).get("properties", {})
    subdomain = os.environ.get("ADMIRE_SUBDOMAIN") or load_settings().get("subdomain")
    if "subdomain" in props and "subdomain" not in args and subdomain:
        args["subdomain"] = subdomain
    return args


# --- output ----------------------------------------------------------------

def print_result(result, raw=False):
    if raw or not isinstance(result, dict):
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return
    if "structuredContent" in result:
        print(json.dumps(result["structuredContent"], indent=2, ensure_ascii=False))
        return
    for block in result.get("content", []):
        if block.get("type") == "text":
            text = block["text"]
            try:
                print(json.dumps(json.loads(text), indent=2, ensure_ascii=False))
            except ValueError:
                print(text)
        else:
            print(json.dumps(block, indent=2, ensure_ascii=False))


def summarize(description, width=100):
    line = (description or "").strip().split("\n", 1)[0]
    return line if len(line) <= width else line[: width - 1] + "…"


# --- commands --------------------------------------------------------------

def cmd_login(args, store):
    oauth.login(store, args.server, open_browser=not args.no_browser)
    print("Logged in.")


def cmd_logout(args, store):
    oauth.logout(store)
    (config_dir() / "tools.json").unlink(missing_ok=True)
    print("Logged out.")


def cmd_status(args, store):
    creds = store.load()
    if not creds.get("access_token"):
        print("Not logged in.")
        return 1
    oauth.access_token(store)
    creds = store.load()
    out = {"server_url": creds["server_url"], "credentials": str(store.path)}
    if creds.get("expires_at"):
        out["access_token_expires"] = time.strftime("%Y-%m-%d %H:%M %Z", time.localtime(creds["expires_at"]))
    claims = oauth.decode_jwt_claims(creds["access_token"])
    for key in ("sub", "email", "name"):
        if key in claims:
            out[key] = claims[key]
    subdomain = os.environ.get("ADMIRE_SUBDOMAIN") or load_settings().get("subdomain")
    if subdomain:
        out["default_subdomain"] = subdomain
    print(json.dumps(out, indent=2))


def cmd_tools(args, store):
    tools = get_tools(Client(store), refresh=args.refresh)
    if args.filter:
        tools = [t for t in tools if args.filter.lower() in t["name"].lower()
                 or args.filter.lower() in (t.get("description") or "").lower()]
    if args.json:
        print(json.dumps(tools, indent=2, ensure_ascii=False))
        return
    width = max((len(t["name"]) for t in tools), default=0)
    for tool in sorted(tools, key=lambda t: t["name"]):
        hints = tool.get("annotations") or {}
        flag = "r" if hints.get("readOnlyHint") else ("!" if hints.get("destructiveHint") else " ")
        print(f"{flag} {tool['name']:<{width}}  {summarize(tool.get('description'))}")


def cmd_describe(args, store):
    tool = find_tool(get_tools(Client(store)), args.tool)
    if args.json:
        print(json.dumps(tool, indent=2, ensure_ascii=False))
        return
    print(tool["name"])
    print()
    print((tool.get("description") or "").strip())
    schema = tool.get("inputSchema") or {}
    required = set(schema.get("required", []))
    props = schema.get("properties", {})
    if props:
        print("\nArguments:")
        for name, spec in props.items():
            kind = spec.get("type", "any")
            if "enum" in spec:
                kind = "|".join(map(str, spec["enum"]))
            mark = "*" if name in required else " "
            print(f"  {mark} {name} ({kind})  {summarize(spec.get('description'), 80)}")
        print("\n  * required")


def cmd_call(args, store):
    client = Client(store)
    tool = find_tool(get_tools(client), args.tool)
    arguments = build_arguments(tool, args.pairs, args.json)
    client.initialize()
    result = client.call_tool(tool["name"], arguments)
    print_result(result, raw=args.raw)
    return 1 if isinstance(result, dict) and result.get("isError") else 0


def cmd_config(args, store):
    settings = load_settings()
    if args.action == "get":
        print(json.dumps(settings if not args.key else settings.get(args.key), indent=2))
    elif args.action == "set":
        settings[args.key] = args.value
        save_settings(settings)
    elif args.action == "unset":
        settings.pop(args.key, None)
        save_settings(settings)


def build_parser():
    p = argparse.ArgumentParser(
        prog="admire",
        description="Command-line client for the Admire MCP server.",
        epilog="Shorthand: `admire <tool> key=value ...` is the same as `admire call <tool> ...`.",
    )
    p.add_argument("--version", action="version", version=f"admire-cli {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("login", help="log in through the browser (OAuth)")
    s.add_argument("--server", default=os.environ.get("ADMIRE_SERVER_URL", DEFAULT_SERVER))
    s.add_argument("--no-browser", action="store_true", help="print the URL without opening it")
    s.set_defaults(func=cmd_login)

    s = sub.add_parser("logout", help="revoke the token and delete local credentials")
    s.set_defaults(func=cmd_logout)

    s = sub.add_parser("status", help="show login state")
    s.set_defaults(func=cmd_status)

    s = sub.add_parser("tools", help="list available tools (r = read-only, ! = destructive)")
    s.add_argument("filter", nargs="?", help="substring to match in name or description")
    s.add_argument("--json", action="store_true", help="print full tool definitions")
    s.add_argument("--refresh", action="store_true", help="ignore the 24h tool cache")
    s.set_defaults(func=cmd_tools)

    s = sub.add_parser("describe", help="show a tool's description and arguments")
    s.add_argument("tool")
    s.add_argument("--json", action="store_true", help="print the raw tool definition")
    s.set_defaults(func=cmd_describe)

    s = sub.add_parser("call", help="call a tool")
    s.add_argument("tool")
    s.add_argument("pairs", nargs="*", metavar="key=value",
                   help="arguments; values are parsed as JSON when valid")
    s.add_argument("--json", metavar="JSON", help="arguments as a JSON object, or - for stdin")
    s.add_argument("--raw", action="store_true", help="print the full MCP result")
    s.set_defaults(func=cmd_call)

    s = sub.add_parser("config", help="get or set defaults (e.g. subdomain)")
    s.add_argument("action", choices=["get", "set", "unset"])
    s.add_argument("key", nargs="?")
    s.add_argument("value", nargs="?")
    s.set_defaults(func=cmd_config)
    return p


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and not argv[0].startswith("-") and argv[0] not in COMMANDS:
        argv.insert(0, "call")
    args = build_parser().parse_args(argv)
    if args.command == "config" and args.action in ("set", "unset") and not args.key:
        raise SystemExit("config set/unset needs a key")
    if args.command == "config" and args.action == "set" and args.value is None:
        raise SystemExit("config set needs a value")
    try:
        return args.func(args, Store()) or 0
    except (MCPError, HTTPError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
