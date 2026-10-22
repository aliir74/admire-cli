# admire-cli

Unofficial command-line client for the [Admire](https://admire.ai) MCP server.

Admire exposes its features to AI tools over MCP. This CLI talks to the same hosted endpoint (`https://app.ca.admire.ai/mcp`), so scripts and coding agents can use Admire without an MCP connection. It builds its commands from the server's tool list, so new Admire tools work without a CLI update.

No dependencies beyond the Python 3.10+ standard library.

## Install

```sh
pipx install git+https://github.com/aliir74/admire-cli
```

## Log in

```sh
admire login
```

This opens your browser for Admire's OAuth login. The CLI registers itself as an OAuth client (dynamic client registration, PKCE, no client secret) and stores the tokens in `~/.config/admire-cli/credentials.json` with `0600` permissions. Access tokens refresh automatically.

Other commands: `admire status`, `admire logout` (revokes the refresh token and deletes local credentials).

## Use

```sh
admire tools                      # list tools (r = read-only, ! = destructive)
admire tools staff                # filter by name or description
admire describe list_staff        # description and arguments of one tool
admire my_profile                 # call a tool (shorthand for `admire call my_profile`)
admire list_staff subdomain=acme  # arguments as key=value
admire upsert_dashboard --json '{"subdomain": "acme", "title": "Q4"}'
echo '{"subdomain": "acme"}' | admire list_teams --json -
```

Values in `key=value` pairs are parsed as JSON when valid (`limit=10`, `active=true`, `ids='["a","b"]'`), otherwise they stay strings.

Most tools need your organization's `subdomain`. Set it once and the CLI adds it to every tool that takes one:

```sh
admire config set subdomain acme
```

Output is JSON on stdout (the tool's structured content, or its text content). `--raw` prints the full MCP result. A tool error exits with status 1.

## Configuration

| Variable | Purpose |
|----------|---------|
| `ADMIRE_SERVER_URL` | MCP endpoint used by `admire login` (default `https://app.ca.admire.ai/mcp`) |
| `ADMIRE_SUBDOMAIN` | Default organization subdomain; overrides `admire config set subdomain` |
| `ADMIRE_CONFIG_DIR` | Where credentials, settings and the 24-hour tool cache live |

## License

MIT
