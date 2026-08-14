# src/infrastructure/db/migrations/migration_base.py

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pymongo.database import Database

@dataclass(frozen=True)
class MigrationMeta:
    migration_id: str
    from_version: int
    to_version: int
    description: str

class Migration(ABC):
    meta: MigrationMeta

    @abstractmethod
    def up(self, db: Database) -> None:
        """Aplica migração (idempotente)."""
        raise NotImplementedError