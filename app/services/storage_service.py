import sys

import anyio.to_thread
from app.exceptions.custom import StorageException

from app.services.config import settings

# Cross-platform file locking
if sys.platform == "win32":

    def lock_file(f, shared=False):
        """Lock file on Windows using msvcrt - no-op for simplicity"""
        # Windows file locking with msvcrt can be problematic
        # For local development, we'll skip locking
        pass


    def unlock_file(f):
        """Unlock file on Windows using msvcrt - no-op for simplicity"""
        pass
else:
    import fcntl


    def lock_file(f, shared=False):
        """Lock file on Unix using fcntl"""
        lock_type = fcntl.LOCK_SH if shared else fcntl.LOCK_EX
        fcntl.flock(f, lock_type)


    def unlock_file(f):
        """Unlock file on Unix using fcntl"""
        fcntl.flock(f, fcntl.LOCK_UN)


class DealStorage:
    def __init__(self, file_path: str = settings.STORAGE_FILE_PATH):
        self.file_path = file_path
        import logging
        logging.getLogger(__name__).info(f"DealStorage initialized with file_path={file_path}")

    async def _read_all(self) -> list[dict]:
        import anyio
        return await anyio.to_thread.run_sync(self._read_sync)

    def _read_sync(self) -> list[dict]:
        from pathlib import Path
        import json
        import logging
        logger = logging.getLogger(__name__)
        try:
            Path(self.file_path).parent.mkdir(parents=True, exist_ok=True)
            if not Path(self.file_path).exists():
                return []
            with open(self.file_path, "r", encoding="utf-8") as f:
                try:
                    lock_file(f, shared=True)
                except Exception:
                    pass
                try:
                    data = json.load(f)
                    return data
                finally:
                    try:
                        unlock_file(f)
                    except Exception:
                        pass
        except (json.JSONDecodeError, ValueError) as e:
            logger.error(f"JSON decode error reading {self.file_path}: {e}", exc_info=True)
            return []
        except Exception as e:
            from app.exceptions import StorageException
            raise StorageException(message="Failed to read deals from storage", details={"error": str(e)})

    async def _write_all(self, deals: list[dict]) -> None:
        import anyio
        await anyio.to_thread.run_sync(self._write_sync, deals)

    def _write_sync(self, deals: list[dict]) -> None:
        import os, json, tempfile, logging
        from pathlib import Path
        from datetime import datetime
        logger = logging.getLogger(__name__)

        def datetime_serializer(obj):
            if isinstance(obj, datetime):
                return obj.isoformat()
            raise TypeError(f"Type {type(obj)} not serializable")

        dir_path = Path(self.file_path).parent
        dir_path.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(dir=dir_path, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(deals, f, indent=2, default=datetime_serializer)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, self.file_path)
        except Exception as e:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            from app.exceptions import StorageException
            logger.error(f"Error writing to storage: {e}", exc_info=True)
            raise StorageException(message="Failed to write deals to storage", details={"error": str(e)})

    async def list_deals(self) -> list[dict]:
        return await self._read_all()

    async def get_deal(self, deal_id: str) -> dict | None:
        try:
            deals = await self._read_all()
            for d in deals:
                if d.get("id") == deal_id:
                    return d
            return None
        except Exception as e:
            from app.exceptions import StorageException
            raise StorageException(message=f"Failed to get deal {deal_id}", details={"error": str(e)})

    async def create_deal(self, deal: dict) -> dict:
        try:
            deals = await self._read_all()
            deals.append(deal)
            await self._write_all(deals)
            return deal
        except Exception as e:
            from app.exceptions import StorageException
            raise StorageException(message="Failed to create deal", details={"error": str(e)})

    async def update_deal(self, deal_id: str, deal_data: dict) -> dict | None:
        try:
            deals = await self._read_all()
            for i, d in enumerate(deals):
                if d.get("id") == deal_id:
                    deals[i].update(deal_data)
                    updated = deals[i]
                    await self._write_all(deals)
                    return updated
            return None
        except Exception as e:
            from app.exceptions import StorageException
            raise StorageException(message=f"Failed to update deal {deal_id}", details={"error": str(e)})

    async def delete_deal(self, deal_id: str) -> bool:
        try:
            from datetime import datetime, timezone
            deals = await self._read_all()
            for i, d in enumerate(deals):
                if d.get("id") == deal_id:
                    deals[i]["is_active"] = False
                    deals[i]["updated_at"] = datetime.now(timezone.utc).isoformat()
                    await self._write_all(deals)
                    return True
            return False
        except Exception as e:
            from app.exceptions import StorageException
            raise StorageException(message=f"Failed to delete deal {deal_id}", details={"error": str(e)})
