from pymongo import MongoClient
from dotenv import load_dotenv
import os
from mongostore import MongoStore  # 👈 Import the class
import certifi

load_dotenv()

MONGO_URI = os.getenv("MONGO_URI")
MONGO_DB = os.getenv("MONGO_DB")

# Old client usage for direct collection access
client = MongoClient(MONGO_URI,   tlsCAFile=certifi.where())
db = client[MONGO_DB]

# Direct collection access if needed
users_collection = db["users"]
scheduled_messages_collection = db["scheduled_messages"]

# Initialize reusable store instance
store = MongoStore(MONGO_URI, MONGO_DB)  # 👈 Create MongoStore instance
