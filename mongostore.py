from pymongo import MongoClient, ASCENDING
from datetime import datetime
import threading
import logging
import certifi

logger = logging.getLogger(__name__)

class MongoStore:
    _lock = threading.RLock()

    def __init__(self, mongo_uri: str, db_name: str):
        self.client = MongoClient(mongo_uri,  tlsCAFile=certifi.where())
        self.db = self.client[db_name]
        self.contacts = self.db.contacts
        self.rules = self.db.rules
        self.message_log = self.db.message_log
        self.scheduled_messages = self.db.scheduled_messages
        self.held_messages = self.db.held_messages
        self.twilio_accounts = self.db.twilio_accounts
        self._ensure_indexes()

    def _ensure_indexes(self):
        self.contacts.create_index("alias", unique=True)
        self.contacts.create_index("phone_number", unique=True)
        self.twilio_accounts.create_index("account_sid", unique=True)

    def add_contact(self, alias: str, number: str, label: str = 'other'):
        with self._lock:
            self.contacts.replace_one(
                {"alias": alias.lower()},
                {"alias": alias.lower(), "phone_number": number, "label": label.lower()},
                upsert=True
            )

    def delete_contact(self, phone: str) -> bool:
        with self._lock:
            result = self.contacts.delete_one({"phone_number": phone})
            return result.deleted_count > 0

    def resolve(self, alias: str) -> str | None:
        alias = alias.strip().lower()
        result = self.contacts.find_one({"alias": alias})
        return result['phone_number'] if result else None

    def update_contact(self, alias: str, new_number: str, new_label: str = 'other') -> bool:
        with self._lock:
            result = self.contacts.update_one(
                {"alias": alias.lower()},
                {"$set": {"phone_number": new_number, "label": new_label.lower()}}
            )
            return result.modified_count > 0

    def list_contacts(self) -> list[dict]:
        try:
            cursor = self.contacts.find({}, {"_id": 0}).sort("alias", ASCENDING)
            return list(cursor)
        except Exception as e:
            logger.error(f"Failed to list contacts: {str(e)}")
            return []
