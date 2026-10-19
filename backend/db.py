import os

from motor.motor_asyncio import AsyncIOMotorClient

MONGODB_URI = os.getenv("MONGODB_URI", "mongodb://localhost:27017/ocr")

client = AsyncIOMotorClient(MONGODB_URI)
db = client.get_default_database()
ocr_results = db["ocr_results"]
users = db["users"]
assignments = db["assignments"]
submissions = db["submissions"]
classes = db["classes"]
subjects = db["subjects"]
