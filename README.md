# Tachyon faucet

A small Python 3.11+ service for the Tachyon Alpha Testnet. Tachyscan accepts a
payment key, this service calls `zcash-devtool`, and the visitor downloads the
recipient note JSON. Devtool retains all keys, signing, proof construction,
input selection, and chain validation. There is no trial decryption: the recipient
must import that JSON into the wallet that owns the payment key.

Defaults: **0.01 testnet ZEC per claim**, **one claim every five minutes globally**.
The cooldown starts when a claim is accepted, survives restarts, and is shown to
every visitor. A pending payment or wallet refill can keep the faucet unavailable
after the timer expires. This service uses the experimental mock-proof wallet and
must only hold testnet funds.

## Install and configure

```sh
cd ~/tachyon-faucet
python3 -m venv .venv
.venv/bin/python -m pip install .
cp config.example.toml config.local.toml
```

Edit `config.local.toml`. Set `devtool` to the binary built with Tachyon support,
`wallet_dir` to your **existing dedicated faucet wallet**, and `state_dir` to a
new private service-state directory. Paths can be absolute or relative to the
config file. The wallet's RPC URL and credentials come from its own initialization;
the HTTP service does not take arbitrary RPC URLs or devtool commands from clients.

To create a wallet only if you do not already have one:

```sh
umask 077
mkdir -p ~/tachyon-faucet-data
~/zcash-devtool/target/release/zcash-devtool tachyon \
  --wallet-dir ~/tachyon-faucet-data/wallet init \
  --rpc-url http://127.0.0.1:18232 --activation-height 8 --accept-mock-proofs
~/zcash-devtool/target/release/zcash-devtool tachyon \
  --wallet-dir ~/tachyon-faucet-data/wallet address
```

Fund the returned **`funding_address`**, or keep the workload's `[faucet].address`
pointing to that address. Reuse the wallet that owns your configured funding
address; creating a new wallet does not recover the old one's funds. The service
syncs automatically and shields transparent funds when it needs another spendable
Tachyon note. No manual receipt import is needed for incoming transparent funding.

Devtool selects one input per payment. Automatic refill needs a confirmed,
unreserved **non-coinbase UTXO** large enough for the refill plus its 50,000-zatoshi
fee; separate smaller UTXOs are not consolidated. The default refill is up to
1 ZEC. Set `shield_amount_zat` lower for smaller funding payments, but keep it at
least `amount_zat + 50000`. If funding directly from coinbases, shield those in
full with devtool before starting the service. Do not operate the same wallet
concurrently with another service or manual spending commands.

| Setting | Default | Purpose |
| --- | --- | --- |
| `devtool` | Required | Path to the executable |
| `wallet_dir` | Required | Dedicated initialized devtool wallet |
| `state_dir` | Required | Private durable claims and receipt storage |
| `host`, `port` | `127.0.0.1`, `4142` | Local HTTP listener |
| `allowed_origins` | `["https://tachyscan.cnode.phd"]` | Exact frontend origins, without trailing slashes |
| `amount_zat` | `1000000` | Recipient amount; fees are additional |
| `cooldown_seconds` | `300` | Global minimum interval between accepted claims |
| `auto_shield` | `true` | Replenish Tachyon funds from transparent funds |
| `shield_amount_zat` | `100000000` | Maximum refill amount |
| `poll_seconds` | `15` | Wallet sync/recovery interval |
| `command_timeout` | `1800` | Maximum seconds per devtool command |

Restart after configuration changes. Existing claims retain their original
amounts, recipients, and cooldown deadlines. Wallet and state directories must be
mode `0700`; the service creates its SQLite files with mode `0600`.

## Allow the workload miner to include faucet transactions

If `tachyon-workload` controls mining with `generate`, update it to the version
with this option and add it **at the top level** of its config, before any tables:

```toml
allow_external_transactions = true
```

Restart the workload service. Its default strict mode rejects transactions from
other wallets, including faucet refills and sends. With this option, its blocks
may contain user/faucet payments in addition to the sampled workload count.
Keep it as the sole miner, with node-side `tachyon_workload = false`.
Live workload mode with an internal miner already accepts outside traffic.

## Run with systemd

First run in the foreground to check the paths and wallet:

```sh
.venv/bin/python -m tachyon_faucet --config config.local.toml
# In a second terminal:
curl -s http://127.0.0.1:4142/api/faucet/status
```

Initial synchronization can take time. Status reports `syncing`, `funding`, or
`empty` until the wallet is ready. Stop the foreground process before installing
the service. Edit `deploy/tachyon-faucet.service` if your paths differ, including
`ReadWritePaths` for the actual wallet and service-state directories:

```sh
sudo install -m 644 deploy/tachyon-faucet.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now tachyon-faucet
journalctl -u tachyon-faucet -f -o cat
```

The service invokes one wallet command at a time. Timeouts and shutdown terminate
the child process; the next run recovers its saved operation before trying again.
Logs omit wallet output, note contents, claim tokens, and RPC credentials. On a
persistent devtool failure, stop the service and run `sync`/`balance` for this wallet
directly to inspect its error. Fix the cause and restart with the same state.

## Connect Tachyscan

Add the location in [deploy/nginx-faucet.conf](deploy/nginx-faucet.conf) **inside
the existing HTTPS server block** for `tachyscan.cnode.phd`. Keep Tachyscan's other
locations. The browser uses same-origin `/api/faucet/`; do not expose port 4142
publicly or route wallet/state files through nginx.

```sh
sudo nginx -t
sudo systemctl reload nginx
```

Build and restart Tachyscan using its normal deployment workflow. Its faucet page
now accepts a bare payment key or `tachyon-test:` address, polls shared status,
and downloads the exact note JSON that devtool's `receive --receipt` accepts.
The browser saves a private random claim token in session storage before sending.
Refreshing the same tab recovers its payment; a lost response can be retried
without creating a second payment. Download the note before closing the tab.

For frontend development, add `"http://localhost:5173"` to `allowed_origins`, run
this service on 4142, and run `bun run dev` in Tachyscan. Vite proxies the faucet
API separately from the explorer API. HTTP on localhost works; public deployment
needs HTTPS for the browser's secure random-number API.

## Recovery and storage

Keep **both the entire wallet directory and the entire faucet state directory**.
The journal binds itself to the wallet path and chain/address identity. A restart
does not reset the cooldown or create replacement payments. An expired payment is
marked expired, and its receipt is no longer offered; the visitor can explicitly
request a new one after the cooldown. Reorgs can return mined claims to pending.

The state directory holds private receipt JSON inside SQLite. Never commit or
publicly serve it. Receipts are retained for recovery; there is no automatic
retention policy in this first version. Do not erase either database to fix a
failed payment, or run multiple service instances against the same wallet. One
process lock protects a state directory; separate state directories cannot safely
share a wallet. After regenesis, use a new wallet and new state together.

## HTTP API

All responses use `Cache-Control: no-store`. Claim bodies are limited to 2 KiB.
Clients cannot choose payment amounts. Cross-origin browser requests are rejected.

| Request | Result |
| --- | --- |
| `GET /api/faucet/status` | Public amount, availability, server time, and `remaining_ms` |
| `POST /api/faucet/claims` | `{ "address": "tachyon-test:…", "token": "<32 random bytes as lowercase hex>" }`; returns claim and status |
| `GET /api/faucet/claim` | Private claim state; requires `Authorization: Bearer <token>` |
| `GET /api/faucet/claim/receipt` | Exact recipient note JSON attachment; same authorization |

A new claim returns 202; a global cooldown returns 429 with `Retry-After`; an
unready wallet returns 503. Reusing the same token and address returns the same
claim, including during cooldown. Never put the token in a URL. Status contains
no recipient keys or receipts. Global limiting is intentionally simple: a visitor
can occupy each available slot, so this does not guarantee fairness.

## Tests

```sh
.venv/bin/python -m unittest discover -v
```

Tests use synthetic wallets and temporary directories: concurrent admission,
cooldown persistence, ambiguous broadcasts, restart/reorg/expiry recovery,
refilling, receipt validation/privacy, and subprocess cancellation. They do not
connect to a live node or send funds.
