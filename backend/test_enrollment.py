import unittest
from types import SimpleNamespace

from bson import ObjectId
from fastapi import HTTPException
from pymongo.errors import DuplicateKeyError

import main


class FakeClasses:
    def __init__(self):
        self.items = []

    async def find_one(self, query):
        for item in self.items:
            if all(item.get(key) == value for key, value in query.items()):
                return item
        return None

    async def insert_one(self, item):
        if any(
            existing.get("teacher_id") == item.get("teacher_id")
            and existing.get("grade") == item.get("grade")
            and existing.get("section") == item.get("section")
            for existing in self.items
        ):
            raise DuplicateKeyError("duplicate class")
        if any(
            existing.get("enrollment_key_hash") == item.get("enrollment_key_hash")
            for existing in self.items
        ):
            raise DuplicateKeyError("duplicate enrollment key")
        item = dict(item)
        item["_id"] = ObjectId()
        self.items.append(item)
        return SimpleNamespace(inserted_id=item["_id"])


class FakeUsers:
    def __init__(self):
        self.items = []

    async def find_one(self, query):
        return next(
            (
                item
                for item in self.items
                if all(item.get(key) == value for key, value in query.items())
            ),
            None,
        )

    async def insert_one(self, item):
        if any(existing["email"] == item["email"] for existing in self.items):
            raise DuplicateKeyError("duplicate email")
        item = dict(item)
        item["_id"] = ObjectId()
        self.items.append(item)
        return SimpleNamespace(inserted_id=item["_id"])


class EnrollmentTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.original_classes = main.classes
        self.original_users = main.users
        main.classes = FakeClasses()
        main.users = FakeUsers()
        self.teacher = {"_id": ObjectId(), "role": "teacher", "name": "Teacher"}

    async def asyncTearDown(self):
        main.classes = self.original_classes
        main.users = self.original_users

    async def create_class(self, grade="1", section="A", teacher=None):
        return await main.create_teacher_class(
            main.ClassRequest(grade=grade, section=section),
            teacher or self.teacher,
        )

    def student_request(self, email, class_record, enrollment_key, grade="1", section="A"):
        return main.SignupRequest(
            name="Student One",
            email=email,
            password="password123",
            grade=grade,
            class_section=section,
            class_id=class_record["id"],
            enrollment_key=enrollment_key,
        )

    async def test_validates_class_values_and_normalizes_sections(self):
        created = await self.create_class(section="a")
        self.assertEqual(created["section"], "A")

        for grade, section in (
            ("11", "A"),
            ("Grade 1", "A"),
            ("1", "AA"),
            ("1", "Section A"),
            ("1", "1"),
            ("1", "@"),
        ):
            with self.subTest(grade=grade, section=section):
                with self.assertRaises(HTTPException) as raised:
                    await self.create_class(grade, section)
                self.assertEqual(raised.exception.status_code, 422)

    async def test_duplicate_class_is_rejected_for_same_teacher(self):
        await self.create_class()
        with self.assertRaises(HTTPException) as raised:
            await self.create_class(section="a")
        self.assertEqual(raised.exception.status_code, 409)

    async def test_key_is_random_stored_safely_and_teacher_only(self):
        created = await self.create_class()
        other_teacher = {"_id": ObjectId(), "role": "teacher", "name": "Other"}
        other_class = await self.create_class(teacher=other_teacher)

        self.assertEqual(len(created["enrollment_key"]), 12)
        self.assertNotEqual(created["enrollment_key"], other_class["enrollment_key"])
        stored = main.classes.items[0]
        self.assertNotEqual(stored["enrollment_key_hash"], created["enrollment_key"])
        self.assertEqual(
            main.teacher_class_view(stored)["enrollment_key"],
            created["enrollment_key"],
        )
        self.assertNotIn("enrollment_key", main.class_view(stored))

    async def test_signup_requires_matching_key_and_links_student_to_class_teacher(self):
        created = await self.create_class()
        other_teacher = {"_id": ObjectId(), "role": "teacher", "name": "Other"}
        other_class = await self.create_class(teacher=other_teacher)

        for request in (
            self.student_request("missing@example.org", created, ""),
            self.student_request("wrong@example.org", created, other_class["enrollment_key"]),
            self.student_request("mismatch@example.org", other_class, created["enrollment_key"]),
        ):
            with self.subTest(email=request.email):
                with self.assertRaises(HTTPException) as raised:
                    await main.signup(request, "student")
                self.assertEqual(raised.exception.status_code, 400)

        await main.signup(
            self.student_request(
                "valid@example.org",
                created,
                created["enrollment_key"].lower(),
            ),
            "student",
        )
        student = main.users.items[0]
        self.assertEqual(student["class_id"], main.classes.items[0]["_id"])
        self.assertEqual(student["teacher_id"], self.teacher["_id"])
        self.assertEqual(student["grade"], "1")
        self.assertEqual(student["class_section"], "A")
        self.assertNotIn("token", main.public_user(student))

    async def test_duplicate_student_registration_is_rejected(self):
        created = await self.create_class()
        request = self.student_request(
            "duplicate@example.org",
            created,
            created["enrollment_key"],
        )
        await main.signup(request, "student")
        with self.assertRaises(HTTPException) as raised:
            await main.signup(request, "student")
        self.assertEqual(raised.exception.status_code, 409)


if __name__ == "__main__":
    unittest.main()
