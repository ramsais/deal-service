import fcntl
import json
import os
import tempfile
from pathlib import Path

import anyio
import anyio.to_thread

from app.exceptions import StorageException
from app.services.config import settings


def _read_deals_sync() -> list[dict]:
    file_path = settings.STORAGE_FILE_PATH
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            fcntl.flock(f, fcntl.LOCK_SH)
            try:
                return json.load(f)
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)
    except FileNotFoundError:
        return []
    except Exception as e:
        raise StorageException(message="Failed to read deals from storage", details={"error": str(e)})


def _write_deals_sync(deals: list[dict]) -> None:
    file_path = settings.STORAGE_FILE_PATH
    try:
        dir_path = Path(file_path).parent
        dir_path.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(dir=dir_path, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(deals, f, indent=2, default=str)
            os.replace(tmp_path, file_path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
    except StorageException:
        raise
    except Exception as e:
        raise StorageException(message="Failed to write deals to storage", details={"error": str(e)})


async def read_deals() -> list[dict]:
    return await anyio.to_thread.run_sync(_read_deals_sync)


async def write_deals(deals: list[dict]) -> None:
    await anyio.to_thread.run_sync(_write_deals_sync, deals)
