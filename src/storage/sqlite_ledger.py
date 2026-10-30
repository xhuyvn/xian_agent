import sqlite3
import os
from typing import List, Optional
from src.config import settings
from src.schemas.lifecycle import EntityLifecycleRecord

class SQLiteLedger:
    """Sổ cái thời gian lưu vết biến cố thực thể"""
    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or settings.sqlite_db_path
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.create_tables()

    def create_tables(self):
        cursor = self.conn.cursor()
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS entity_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chapter_id INTEGER NOT NULL,
            entity_name TEXT NOT NULL,
            entity_type TEXT NOT NULL,
            event_type TEXT NOT NULL,
            target_or_holder TEXT,
            power_tier TEXT,
            context_details TEXT NOT NULL
        )
        """)
        cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_entity_name ON entity_events (entity_name, chapter_id);
        """)
        self.conn.commit()

    def record_event(self, event: EntityLifecycleRecord):
        cursor = self.conn.cursor()
        cursor.execute("""
        INSERT INTO entity_events (chapter_id, entity_name, entity_type, event_type, target_or_holder, power_tier, context_details)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            event.chapter_id,
            event.entity_name,
            event.entity_type,
            event.event_type.value if hasattr(event.event_type, 'value') else event.event_type,
            event.target_or_holder,
            event.power_tier,
            event.context_details
        ))
        self.conn.commit()

    def get_timeline_for_entity(self, entity_name: str) -> List[EntityLifecycleRecord]:
        cursor = self.conn.cursor()
        cursor.execute("""
        SELECT id, chapter_id, entity_name, entity_type, event_type, target_or_holder, power_tier, context_details
        FROM entity_events
        WHERE entity_name LIKE ?
        ORDER BY chapter_id ASC
        """, (f"%{entity_name}%",))
        rows = cursor.fetchall()
        return [
            EntityLifecycleRecord(
                id=r[0],
                chapter_id=r[1],
                entity_name=r[2],
                entity_type=r[3],
                event_type=r[4],
                target_or_holder=r[5],
                power_tier=r[6],
                context_details=r[7]
            ) for r in rows
        ]
