"""Run the HTTP API and its single wallet worker."""

import argparse
import logging
import os

from aiohttp import web

from .config import load_config
from .devtool import Devtool
from .http import create_app
from .service import Faucet
from .store import Store, private_dir


def main():
    parser = argparse.ArgumentParser(description="Tachyon Alpha Testnet faucet")
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    os.umask(0o077)
    config = load_config(args.config)
    if not config.devtool.is_file() or not os.access(config.devtool, os.X_OK):
        parser.error("devtool must point to an executable zcash-devtool binary")
    if not (config.wallet_dir / "tachyon.sqlite").is_file():
        parser.error("Initialize the dedicated devtool wallet first; see README.md")
    private_dir(config.wallet_dir)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    store = Store(config.state_dir)
    try:
        faucet = Faucet(config, store, Devtool(config))
        web.run_app(create_app(faucet), host=config.host, port=config.port,
                    access_log=None, print=None, shutdown_timeout=15)
    finally:
        store.close()


if __name__ == "__main__":
    main()
