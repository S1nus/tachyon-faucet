"""Bounded, cancellable subprocess calls; devtool owns all cryptography and keys."""

import asyncio
import json
import os
import signal


class CliError(Exception):
    def __init__(self, command, code, stderr):
        super().__init__(f"Devtool {command} failed (exit {code})")
        self.command = command
        self.code = code
        self.stderr = stderr  # For classification only; never log receipt-bearing output.


async def read_bounded(stream, maximum):
    chunks, size = [], 0
    while chunk := await stream.read(65536):
        size += len(chunk)
        if size > maximum:
            raise RuntimeError("Devtool output exceeded its size limit")
        chunks.append(chunk)
    return b"".join(chunks)


class Devtool:
    def __init__(self, config):
        self.config = config

    async def call(self, *args):
        process = await asyncio.create_subprocess_exec(
            str(self.config.devtool), "tachyon", "--wallet-dir", str(self.config.wallet_dir),
            "--quiet", *map(str, args), stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, start_new_session=True)
        readers = [asyncio.create_task(read_bounded(process.stdout, 8 * 1024 * 1024)),
                   asyncio.create_task(read_bounded(process.stderr, 128 * 1024))]
        try:
            async with asyncio.timeout(self.config.command_timeout):
                stdout, stderr = await asyncio.gather(*readers)
                await process.wait()
            if process.returncode:
                raise CliError(args[0], process.returncode, stderr.decode(errors="replace"))
            result = json.loads(stdout)
            if not isinstance(result, dict):
                raise ValueError("Devtool returned a non-object result")
            return result
        finally:
            for reader in readers:
                reader.cancel()
            await asyncio.gather(*readers, return_exceptions=True)
            if process.returncode is None:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    await asyncio.wait_for(process.wait(), 5)
                except TimeoutError:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    await process.wait()

    async def lookup(self, operation_id):
        try:
            return await self.call("operation", operation_id)
        except CliError as error:
            if "unknown operation ID" in error.stderr:
                return None
            raise
