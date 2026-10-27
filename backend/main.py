import base64
import hashlib
import hmac
import io
import json
import mimetypes
import os
import secrets
import string
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import jwt
from dotenv import load_dotenv

load_dotenv()

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field
from bson import Binary, ObjectId
from pymongo.errors import DuplicateKeyError
from cryptography.fernet import Fernet
import pypdfium2 as pdfium
from starlette.datastructures import UploadFile as FormUploadFile
try:
    from pypdf import PdfReader
except ImportError:
    PdfReader = None

from db import assignments, classes, ocr_results, subjects, submissions, users
from feedback import grade_image, grade_text

app = FastAPI(title="Student Feedback System")
UPLOAD_DIR = Path(__file__).parent / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)
JWT_SECRET = os.getenv("JWT_SECRET", "local-development-secret-change-me")
JWT_ALGORITHM = "HS256"
TOKEN_TTL_SECONDS = 60 * 60 * 24
bearer = HTTPBearer()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

ocr_engine = None


def get_ocr_engine():
    global ocr_engine
    if ocr_engine is None:
        try:
            from paddleocr import PaddleOCR

            ocr_engine = PaddleOCR(
                lang="en",
                enable_mkldnn=False,
                use_doc_orientation_classify=False,
                use_doc_unwarping=False,
                use_textline_orientation=False,
            )
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail=f"OCR is unavailable. Check the PaddleOCR installation and Windows application-control policy: {exc}",
            ) from exc
    return ocr_engine


class SignupRequest(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    email: str = Field(min_length=5, max_length=160)
    password: str = Field(min_length=8, max_length=128)
    grade: str = Field(default="", max_length=20)
    class_section: str = Field(default="", max_length=20)
    class_id: str = Field(default="", max_length=40)
    enrollment_key: str = Field(default="", max_length=40)
    subject: str = Field(default="", max_length=40)


class LoginRequest(BaseModel):
    email: str
    password: str


class ClassRequest(BaseModel):
    grade: str = Field(min_length=1, max_length=20)
    section: str = Field(min_length=1, max_length=20)


class SubjectRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)


class TeacherUploadRequest(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    text: str = Field(default="", max_length=20_000)
    feedback: str = Field(default="", max_length=5_000)
    is_correct: bool | None = None


class AssignmentRequest(BaseModel):
    title: str = Field(min_length=2, max_length=120)
    instructions: str = Field(default="", max_length=5_000)
    assignment_content: str = Field(default="", max_length=20_000)
    subject_id: str = Field(min_length=1)
    grade: str = Field(min_length=1, max_length=20)
    class_section: str = Field(min_length=1, max_length=20)
    due_date: str = Field(min_length=1, max_length=40)
    published: bool = True
    assignment_scope: str = Field(default="entire_class", pattern="^(entire_class|selected_students)$")
    assigned_student_ids: list[str] = Field(default_factory=list, max_length=500)


class ReviewRequest(BaseModel):
    teacher_feedback: str = Field(default="", max_length=5_000)
    feedback_status: str = Field(pattern="^(PENDING_REVIEW|REVIEWED|DELETED)$")


def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 210_000)
    return f"{salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    salt_hex, digest_hex = stored.split("$", 1)
    expected = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), 210_000)
    return hmac.compare_digest(expected.hex(), digest_hex)


def create_token(user: dict) -> str:
    now = datetime.now(timezone.utc)
    payload = {"sub": str(user["_id"]), "role": user["role"], "name": user["name"], "iat": now, "exp": now.timestamp() + TOKEN_TTL_SECONDS}
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


async def current_user(credentials: HTTPAuthorizationCredentials = Depends(bearer)):
    try:
        payload = jwt.decode(credentials.credentials, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        user = await users.find_one({"_id": ObjectId(payload["sub"])})
    except (jwt.PyJWTError, KeyError, ValueError):
        user = None
    if not user:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    return user


async def signup(data: SignupRequest, role: str):
    email = data.email.strip().lower()
    if await users.find_one({"email": email}):
        raise HTTPException(status_code=409, detail="An account with this email already exists")
    grade = data.grade.strip()
    section = data.class_section.strip().upper()
    class_record = None
    if role == "student":
        class_record = await classes.find_one({"_id": object_id(data.class_id, "class")}) if data.class_id else None
        enrollment_key = data.enrollment_key.strip().upper()
        if not class_record or class_record.get("grade") != grade or class_record.get("section") != section:
            raise HTTPException(status_code=400, detail="Choose a Grade and Section created by a teacher")
        if not enrollment_key or not hmac.compare_digest(
            class_record.get("enrollment_key_hash", ""),
            enrollment_key_hash(enrollment_key),
        ):
            raise HTTPException(status_code=400, detail="Invalid enrollment key for the selected class")
    user = {"name": data.name.strip(), "email": email, "password_hash": hash_password(data.password), "role": role, "grade": grade if role == "student" else "", "class_section": section if role == "student" else "", "class_id": class_record["_id"] if class_record else None, "teacher_id": class_record["teacher_id"] if class_record else None, "subject": data.subject.strip(), "created_at": datetime.now(timezone.utc)}
    try:
        result = await users.insert_one(user)
    except DuplicateKeyError as exc:
        raise HTTPException(status_code=409, detail="An account with this email already exists") from exc
    user["_id"] = result.inserted_id
    return {"message": "Account created successfully. Please log in.", "user": public_user(user)}


async def login(data: LoginRequest, role: str):
    user = await users.find_one({"email": data.email.strip().lower(), "role": role})
    if not user or not verify_password(data.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid email, password, or account type")
    return {"token": create_token(user), "user": public_user(user)}


def public_user(user: dict) -> dict:
    return {"id": str(user["_id"]), "name": user["name"], "email": user["email"], "role": user["role"], "grade": user.get("grade", ""), "class_section": user.get("class_section", ""), "subject": user.get("subject", "")}


def object_id(value: str, label: str = "id") -> ObjectId:
    try:
        return ObjectId(value)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid {label}") from exc


def normalized_subject_name(value: str) -> str:
    return " ".join(value.split()).casefold()


def clean_subject_name(value: str) -> str:
    return " ".join(value.split())


def subject_view(item: dict) -> dict:
    return {"id": str(item["_id"]), "name": item["name"]}


@app.on_event("startup")
async def create_indexes():
    await subjects.create_index([("teacher_id", 1), ("normalized_name", 1)], unique=True, name="teacher_subject_name_unique")
    await users.create_index("email", unique=True, name="user_email_unique")
    await classes.create_index([("teacher_id", 1), ("grade", 1), ("section", 1)], unique=True, name="teacher_class_unique")
    await classes.create_index("enrollment_key_hash", unique=True, sparse=True, name="enrollment_key_unique")
    await assignments.create_index([("teacher_id", 1), ("grade", 1), ("class_section", 1), ("created_at", -1)], name="teacher_assignment_filters")
    await submissions.create_index([("assignment_id", 1), ("student_id", 1)], name="assignment_student_submission")
    await users.create_index([("role", 1), ("grade", 1), ("class_section", 1), ("name", 1)], name="class_student_lookup")
    async for item in classes.find({"enrollment_key_hash": {"$exists": False}}):
        await save_enrollment_key(item["_id"])
    async for student in users.find({"role": "student", "class_id": {"$exists": False}}):
        matches = await classes.find({"grade": student.get("grade", ""), "section": student.get("class_section", "")}).to_list(length=2)
        if len(matches) == 1:
            await users.update_one({"_id": student["_id"]}, {"$set": {"class_id": matches[0]["_id"], "teacher_id": matches[0]["teacher_id"]}})
    async for assignment in assignments.find({"class_id": {"$exists": False}, "teacher_id": {"$exists": True}}):
        class_record = await classes.find_one({"teacher_id": assignment["teacher_id"], "grade": assignment.get("grade"), "section": assignment.get("class_section")})
        if class_record:
            await assignments.update_one({"_id": assignment["_id"]}, {"$set": {"class_id": class_record["_id"]}})
    async for assignment in assignments.find({"subject_id": {"$exists": False}, "subject": {"$type": "string"}}):
        if not assignment.get("teacher_id"):
            continue
        name = clean_subject_name(assignment["subject"])
        if not name:
            continue
        subject = await subjects.find_one({"teacher_id": assignment["teacher_id"], "normalized_name": normalized_subject_name(name)})
        if not subject:
            subject_document = {"name": name, "normalized_name": normalized_subject_name(name), "teacher_id": assignment["teacher_id"], "created_at": datetime.now(timezone.utc)}
            try:
                result = await subjects.insert_one(subject_document)
                subject_document["_id"] = result.inserted_id
                subject = subject_document
            except DuplicateKeyError:
                subject = await subjects.find_one({"teacher_id": assignment["teacher_id"], "normalized_name": normalized_subject_name(name)})
        if subject:
            await assignments.update_one({"_id": assignment["_id"]}, {"$set": {"subject_id": subject["_id"]}, "$unset": {"subject": ""}})


def iso(value):
    return value.isoformat() if value else None


def assignment_visibility_query(user: dict) -> dict:
    class_visibility = []
    if user.get("class_id"):
        class_visibility.append({"class_id": user["class_id"]})
    if user.get("teacher_id"):
        class_visibility.append({"class_id": {"$exists": False}, "teacher_id": user["teacher_id"]})
    if not class_visibility:
        return {"_id": {"$in": []}}
    return {
        "grade": user.get("grade", ""),
        "class_section": user.get("class_section", ""),
        "published": True,
        "$and": [
            {
                "$or": class_visibility
            },
            {
                "$or": [
                    {"assignment_scope": "entire_class"},
                    {"assignment_scope": {"$exists": False}},
                    {"assigned_student_ids": user["_id"]},
                ]
            },
        ],
    }


def class_view(item: dict) -> dict:
    return {"id": str(item["_id"]), "grade": item["grade"], "section": item["section"], "teacher_id": str(item["teacher_id"]) if item.get("teacher_id") else None}


def enrollment_cipher() -> Fernet:
    key = base64.urlsafe_b64encode(hashlib.sha256(JWT_SECRET.encode()).digest())
    return Fernet(key)


def enrollment_key_hash(value: str) -> str:
    return hmac.new(JWT_SECRET.encode(), value.encode(), hashlib.sha256).hexdigest()


def new_enrollment_key() -> str:
    alphabet = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(12))


def teacher_class_view(item: dict) -> dict:
    return {
        **class_view(item),
        "enrollment_key": enrollment_cipher().decrypt(item["enrollment_key_encrypted"]).decode(),
    }


async def save_enrollment_key(class_id: ObjectId) -> None:
    while True:
        key = new_enrollment_key()
        try:
            result = await classes.update_one(
                {"_id": class_id, "enrollment_key_hash": {"$exists": False}},
                {"$set": {
                    "enrollment_key_hash": enrollment_key_hash(key),
                    "enrollment_key_encrypted": enrollment_cipher().encrypt(key.encode()).decode(),
                }},
            )
            if result.modified_count:
                return
            return
        except DuplicateKeyError:
            continue


async def owned_class_ids(user: dict, grade: str = "", section: str = "") -> list[ObjectId]:
    query = {"teacher_id": user["_id"]}
    if grade:
        query["grade"] = grade
    if section:
        query["section"] = section.upper()
    return [item["_id"] async for item in classes.find(query, {"_id": 1})]


def teacher_students_query(user: dict, class_ids: list[ObjectId]) -> dict:
    if not class_ids:
        return {"_id": {"$in": []}}
    return {
        "role": "student",
        "$or": [
            {"class_id": {"$in": class_ids}},
            {"teacher_id": user["_id"], "class_id": {"$exists": False}},
        ],
    }


async def teacher_student_id(student_id: str, user: dict, class_id: ObjectId | None = None) -> ObjectId:
    student_object_id = object_id(student_id, "student")
    class_ids = [class_id] if class_id else await owned_class_ids(user)
    query = teacher_students_query(user, class_ids)
    query["_id"] = student_object_id
    if not await users.find_one(query, {"_id": 1}):
        raise HTTPException(status_code=404, detail="Student not found")
    return student_object_id


async def assignment_student_query(assignment: dict, user: dict) -> dict:
    class_ids = [assignment["class_id"]] if assignment.get("class_id") else await owned_class_ids(
        user, assignment.get("grade", ""), assignment.get("class_section", ""),
    )
    query = teacher_students_query(user, class_ids)
    query.update({"grade": assignment.get("grade", ""), "class_section": assignment.get("class_section", "")})
    return query


async def assignment_student_allowed(assignment: dict, student_id: ObjectId, user: dict) -> bool:
    if assignment.get("assignment_scope") == "selected_students" and student_id not in assignment.get("assigned_student_ids", []):
        return False
    query = await assignment_student_query(assignment, user)
    query["_id"] = student_id
    return bool(await users.find_one(query, {"_id": 1}))


async def teacher_class(grade: str, section: str, user: dict):
    item = await classes.find_one({"grade": grade.strip(), "section": section.strip().upper(), "teacher_id": user["_id"]})
    if not item:
        raise HTTPException(status_code=400, detail="Create this Grade and Section before using it")
    return item


async def assignment_document(data: AssignmentRequest, user: dict) -> dict:
    class_record = await teacher_class(data.grade, data.class_section, user)
    subject = await subjects.find_one({"_id": object_id(data.subject_id, "subject"), "teacher_id": user["_id"]})
    if not subject:
        raise HTTPException(status_code=400, detail="Choose a subject created by you")
    payload = data.model_dump()
    assigned_student_ids = []
    if data.assignment_scope == "selected_students":
        if not data.assigned_student_ids:
            raise HTTPException(status_code=400, detail="Select at least one student")
        assigned_student_ids = [object_id(student_id, "student id") for student_id in data.assigned_student_ids]
        valid_count = await users.count_documents({
            "_id": {"$in": assigned_student_ids},
            "role": "student",
            "grade": data.grade,
            "class_section": data.class_section.upper(),
            "$or": [{"class_id": class_record["_id"]}, {"teacher_id": user["_id"]}],
        })
        if valid_count != len(set(assigned_student_ids)):
            raise HTTPException(status_code=400, detail="Selected students must belong to the assignment class")
    payload["assigned_student_ids"] = assigned_student_ids
    payload["subject_id"] = subject["_id"]
    payload["class_section"] = data.class_section.upper()
    return {**payload, "class_id": class_record["_id"], "teacher_id": user["_id"], "created_at": datetime.now(timezone.utc)}


async def assignment_form(request: Request, user: dict) -> tuple[AssignmentRequest, UploadFile | None]:
    form = await request.form()
    selected_ids = form.get("assigned_student_ids", "[]")
    try:
        selected_ids = json.loads(selected_ids) if isinstance(selected_ids, str) else []
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid selected student list") from exc
    data = AssignmentRequest(
        title=str(form.get("title", "")).strip(),
        instructions=str(form.get("instructions", "")).strip(),
        assignment_content=str(form.get("assignment_content", "")).strip(),
        subject_id=str(form.get("subject_id", "")).strip(),
        grade=str(form.get("grade", "")).strip(),
        class_section=str(form.get("class_section", "")).strip(),
        due_date=str(form.get("due_date", "")).strip(),
        published=str(form.get("published", "true")).lower() == "true",
        assignment_scope=str(form.get("assignment_scope", "entire_class")),
        assigned_student_ids=selected_ids,
    )
    assignment_file = form.get("assignment_file")
    if assignment_file and not isinstance(assignment_file, FormUploadFile):
        assignment_file = None
    if not data.assignment_content and not data.instructions and not assignment_file:
        raise HTTPException(status_code=422, detail="Add assignment content or upload a file")
    document = await assignment_document(data, user)
    if assignment_file:
        if assignment_file.content_type not in {"application/pdf"} and not (assignment_file.content_type or "").startswith("image/"):
            raise HTTPException(status_code=415, detail="Assignment files must be an image or PDF")
        contents = await assignment_file.read()
        if not contents:
            raise HTTPException(status_code=422, detail="The assignment file is empty")
        if len(contents) > 10 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="Assignment files must be 10MB or smaller")
        document["assignment_file"] = {
            "filename": Path(assignment_file.filename or "assignment").name,
            "content_type": assignment_file.content_type,
            "data": Binary(contents),
        }
    return data, document


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/classes")
async def available_classes():
    result = []
    async for item in classes.find({}).sort([("grade", 1), ("section", 1)]):
        teacher = await users.find_one({"_id": item.get("teacher_id")}, {"name": 1})
        result.append({
            "id": str(item["_id"]),
            "grade": item["grade"],
            "section": item["section"],
            "teacher_name": teacher.get("name", "Teacher") if teacher else "Teacher",
        })
    return {"classes": result}


@app.post("/api/auth/signup/{role}")
async def auth_signup(role: str, data: SignupRequest):
    if role not in {"student", "teacher"}:
        raise HTTPException(status_code=404, detail="Unknown account type")
    return await signup(data, role)


@app.post("/api/auth/login/{role}")
async def auth_login(role: str, data: LoginRequest):
    if role not in {"student", "teacher"}:
        raise HTTPException(status_code=404, detail="Unknown account type")
    return await login(data, role)


@app.get("/api/me")
async def me(user: dict = Depends(current_user)):
    return public_user(user)


@app.get("/api/teacher/classes")
async def teacher_classes(user: dict = Depends(current_user)):
    await require_role(user, "teacher")
    return {"classes": [teacher_class_view(item) async for item in classes.find({"teacher_id": user["_id"]}).sort([("grade", 1), ("section", 1)])]}


@app.post("/api/teacher/classes")
async def create_teacher_class(data: ClassRequest, user: dict = Depends(current_user)):
    await require_role(user, "teacher")
    grade = data.grade.strip()
    section = data.section.strip().upper()
    if grade not in {str(value) for value in range(1, 11)}:
        raise HTTPException(status_code=422, detail="Grade must be a number from 1 to 10")
    if section not in {"A", "B", "C"} or len(data.section.strip()) != 1:
        raise HTTPException(status_code=422, detail="Section must be A, B, or C")
    item = None
    while item is None:
        key = new_enrollment_key()
        candidate = {
            "grade": grade,
            "section": section,
            "teacher_id": user["_id"],
            "enrollment_key_hash": enrollment_key_hash(key),
            "enrollment_key_encrypted": enrollment_cipher().encrypt(key.encode()).decode(),
            "created_at": datetime.now(timezone.utc),
        }
        try:
            result = await classes.insert_one(candidate)
            item = candidate
            item["_id"] = result.inserted_id
            item["enrollment_key"] = key
        except DuplicateKeyError:
            if await classes.find_one({"teacher_id": user["_id"], "grade": grade, "section": section}):
                raise HTTPException(status_code=409, detail="This class already exists")
    item["_id"] = result.inserted_id
    return {**class_view(item), "enrollment_key": item["enrollment_key"]}


@app.delete("/api/teacher/classes/{class_id}")
async def delete_teacher_class(class_id: str, user: dict = Depends(current_user)):
    await require_role(user, "teacher")
    item = await classes.find_one({"_id": object_id(class_id, "class"), "teacher_id": user["_id"]})
    if not item:
        raise HTTPException(status_code=404, detail="Class not found")
    if await users.find_one({"role": "student", "$or": [
        {"class_id": item["_id"]},
        {"teacher_id": user["_id"], "grade": item["grade"], "class_section": item["section"]},
    ]}):
        raise HTTPException(status_code=409, detail="Move or remove students before deleting this class")
    if await assignments.find_one({"teacher_id": user["_id"], "$or": [
        {"class_id": item["_id"]},
        {"grade": item["grade"], "class_section": item["section"]},
    ]}):
        raise HTTPException(status_code=409, detail="Delete assignments for this class before deleting it")
    await classes.delete_one({"_id": item["_id"]})
    return {"id": class_id}


@app.get("/api/teacher/subjects")
async def teacher_subjects(user: dict = Depends(current_user)):
    await require_role(user, "teacher")
    return {"subjects": [subject_view(item) async for item in subjects.find({"teacher_id": user["_id"]}).sort("name", 1)]}


@app.post("/api/teacher/subjects")
async def create_teacher_subject(data: SubjectRequest, user: dict = Depends(current_user)):
    await require_role(user, "teacher")
    name = clean_subject_name(data.name)
    if not name:
        raise HTTPException(status_code=422, detail="Subject name cannot be blank")
    item = {"name": name, "normalized_name": normalized_subject_name(name), "teacher_id": user["_id"], "created_at": datetime.now(timezone.utc)}
    try:
        result = await subjects.insert_one(item)
    except DuplicateKeyError as exc:
        raise HTTPException(status_code=409, detail="This subject already exists") from exc
    item["_id"] = result.inserted_id
    return subject_view(item)


@app.patch("/api/teacher/subjects/{subject_id}")
async def update_teacher_subject(subject_id: str, data: SubjectRequest, user: dict = Depends(current_user)):
    await require_role(user, "teacher")
    subject_object_id = object_id(subject_id, "subject")
    item = await subjects.find_one({"_id": subject_object_id, "teacher_id": user["_id"]})
    if not item:
        raise HTTPException(status_code=404, detail="Subject not found")
    name = clean_subject_name(data.name)
    if not name:
        raise HTTPException(status_code=422, detail="Subject name cannot be blank")
    try:
        await subjects.update_one({"_id": subject_object_id}, {"$set": {"name": name, "normalized_name": normalized_subject_name(name)}})
    except DuplicateKeyError as exc:
        raise HTTPException(status_code=409, detail="This subject already exists") from exc
    item.update({"name": name, "normalized_name": normalized_subject_name(name)})
    return subject_view(item)


@app.delete("/api/teacher/subjects/{subject_id}")
async def delete_teacher_subject(subject_id: str, user: dict = Depends(current_user)):
    await require_role(user, "teacher")
    subject_object_id = object_id(subject_id, "subject")
    item = await subjects.find_one({"_id": subject_object_id, "teacher_id": user["_id"]})
    if not item:
        raise HTTPException(status_code=404, detail="Subject not found")
    if await assignments.find_one({"teacher_id": user["_id"], "subject_id": subject_object_id}):
        raise HTTPException(status_code=409, detail="Delete assignments for this subject before deleting it")
    await subjects.delete_one({"_id": subject_object_id})
    return {"id": subject_id}


@app.get("/api/student/uploads")
async def student_uploads(user: dict = Depends(current_user)):
    if user["role"] != "student":
        raise HTTPException(status_code=403, detail="Student access required")
    uploads = []
    cursor = ocr_results.find({"user_id": user["_id"]}).sort("created_at", -1)
    async for item in cursor:
        grading = item.get("grading", {})
        uploads.append({
            "id": str(item["_id"]),
            "filename": item.get("filename", "Untitled upload"),
            "uploaded_file": f"/api/files/{Path(item['uploaded_file']).name}" if item.get("uploaded_file") else None,
            "text": item.get("text", ""),
            "is_correct": grading.get("is_correct"),
            "question": grading.get("question", "Question unavailable"),
            "answer": grading.get("answer", "Answer unavailable"),
            "feedback": grading.get("feedback", "Feedback unavailable"),
            "created_at": item.get("created_at").isoformat() if item.get("created_at") else None,
        })
    return {"uploads": uploads}


@app.get("/api/teacher/dashboard")
async def teacher_dashboard(user: dict = Depends(current_user)):
    await require_role(user, "teacher")
    class_ids = await owned_class_ids(user)
    student_query = teacher_students_query(user, class_ids)
    student_ids = [student["_id"] async for student in users.find(student_query, {"_id": 1}).sort("name", 1)]
    students = len(student_ids)
    uploads = await ocr_results.count_documents({"user_id": {"$in": student_ids}}) if student_ids else 0
    correct = await ocr_results.count_documents({"user_id": {"$in": student_ids}, "grading.is_correct": True}) if student_ids else 0
    student_list = []
    student_cursor = users.find(student_query).sort("name", 1)
    async for student in student_cursor:
        student_uploads = await ocr_results.count_documents({"user_id": student["_id"]})
        student_list.append({"id": str(student["_id"]), "name": student["name"], "email": student["email"], "uploads": student_uploads})
    return {"stats": {"students": students, "uploads": uploads, "images": uploads, "feedback": uploads, "average_score": round((correct / uploads) * 100) if uploads else 0}, "students": student_list}


@app.get("/api/teacher/students/{student_id}")
async def teacher_student_detail(student_id: str, user: dict = Depends(current_user)):
    await require_role(user, "teacher")
    student_object_id = await teacher_student_id(student_id, user)
    student = await users.find_one({"_id": student_object_id, "role": "student"})
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")
    uploads = []
    cursor = ocr_results.find({"user_id": student_object_id}).sort("created_at", -1)
    async for item in cursor:
        grading = item.get("grading", {})
        uploads.append({
            "id": str(item["_id"]),
            "filename": item.get("filename", "Untitled upload"),
            "text": item.get("text", ""),
            "uploaded_file": f"/api/files/{Path(item['uploaded_file']).name}" if item.get("uploaded_file") else None,
            "is_correct": grading.get("is_correct"),
            "score": grading.get("score", 100 if grading.get("is_correct") else 0),
            "feedback": grading.get("feedback", "No feedback yet"),
            "created_at": item.get("created_at").isoformat() if item.get("created_at") else None,
        })
    return {"student": {"id": str(student["_id"]), "name": student["name"], "email": student["email"]}, "uploads": uploads}


def upload_response(upload_id, item):
    grading = item.get("grading", {})
    return {
        "id": str(upload_id),
        "filename": item.get("filename", "Untitled upload"),
        "text": item.get("text", ""),
        "uploaded_file": f"/api/files/{Path(item['uploaded_file']).name}" if item.get("uploaded_file") else None,
        "score": 100 if grading.get("is_correct") else 0,
        "feedback": grading.get("feedback", "No feedback yet"),
        "created_at": item.get("created_at").isoformat() if item.get("created_at") else None,
    }


@app.post("/api/teacher/students/{student_id}/uploads")
async def teacher_add_upload(student_id: str, data: TeacherUploadRequest, user: dict = Depends(current_user)):
    await require_role(user, "teacher")
    student_object_id = await teacher_student_id(student_id, user)
    item = {
        "user_id": student_object_id,
        "filename": data.filename.strip(),
        "text": data.text,
        "grading": {"is_correct": data.is_correct, "feedback": data.feedback},
        "created_at": datetime.now(timezone.utc),
    }
    result = await ocr_results.insert_one(item)
    return upload_response(result.inserted_id, item)


@app.patch("/api/teacher/uploads/{upload_id}")
async def teacher_edit_upload(upload_id: str, data: TeacherUploadRequest, user: dict = Depends(current_user)):
    await require_role(user, "teacher")
    try:
        upload_object_id = ObjectId(upload_id)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid upload id") from exc
    item = await ocr_results.find_one({"_id": upload_object_id})
    if not item:
        raise HTTPException(status_code=404, detail="Upload not found")
    await teacher_student_id(str(item["user_id"]), user)
    await ocr_results.update_one({"_id": upload_object_id}, {"$set": {
        "filename": data.filename.strip(),
        "text": data.text,
        "grading": {"is_correct": data.is_correct, "feedback": data.feedback},
    }})
    item.update({"filename": data.filename.strip(), "text": data.text, "grading": {"is_correct": data.is_correct, "feedback": data.feedback}})
    return upload_response(upload_object_id, item)


@app.delete("/api/teacher/uploads/{upload_id}")
async def teacher_delete_upload(upload_id: str, user: dict = Depends(current_user)):
    await require_role(user, "teacher")
    try:
        upload_object_id = ObjectId(upload_id)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid upload id") from exc
    item = await ocr_results.find_one({"_id": upload_object_id})
    if not item:
        raise HTTPException(status_code=404, detail="Upload not found")
    await teacher_student_id(str(item["user_id"]), user)
    await ocr_results.delete_one({"_id": upload_object_id})
    return {"id": upload_id}


@app.post("/api/upload")
async def upload(file: UploadFile = File(...), user: dict = Depends(current_user)):
    if user["role"] != "student":
        raise HTTPException(status_code=403, detail="Only student accounts can upload work")
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(status_code=415, detail="Please upload an image file")
    contents = await file.read()
    if len(contents) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Images must be 10MB or smaller")
    suffix = Path(file.filename).suffix

    with tempfile.TemporaryDirectory() as temp_dir:
        image_path = Path(temp_dir) / f"upload{suffix}"
        image_path.write_bytes(contents)
        results = get_ocr_engine().predict(str(image_path))
        rec_texts = []
        for res in results:
            rec_texts.extend(res.json.get("res", {}).get("rec_texts", []))

    text = "\n".join(rec_texts)
    grading = await grade_image(contents, file.content_type or "image/png")
    stored_name = f"{secrets.token_hex(16)}{Path(file.filename or 'work').suffix.lower()}"
    (UPLOAD_DIR / stored_name).write_bytes(contents)

    doc = {
        "user_id": user["_id"],
        "filename": file.filename,
        "uploaded_file": f"/files/{stored_name}",
        "content_type": file.content_type or "image/png",
        "text": text,
        "grading": grading,
        "created_at": datetime.now(timezone.utc),
    }
    result = await ocr_results.insert_one(doc)

    return {
        "id": str(result.inserted_id),
        "filename": file.filename,
        "uploaded_file": f"/api/files/{stored_name}",
        "text": text,
        "grading": grading,
    }


@app.get("/api/files/{filename}")
async def uploaded_file(filename: str, user: dict = Depends(current_user)):
    if Path(filename).name != filename:
        raise HTTPException(status_code=404, detail="File not found")
    stored_path = f"/files/{filename}"
    submission = await submissions.find_one({
        "$or": [
            {"uploaded_file": stored_path},
            {"uploaded_files.path": stored_path},
        ],
    })
    record = None
    if submission:
        if user["role"] == "student":
            allowed = submission["student_id"] == user["_id"]
        elif user["role"] == "teacher":
            assignment = await assignments.find_one({"_id": submission["assignment_id"], "teacher_id": user["_id"]})
            allowed = bool(assignment and await assignment_student_allowed(assignment, submission["student_id"], user))
        else:
            allowed = False
        if not allowed:
            raise HTTPException(status_code=404, detail="File not found")
        record = next((item for item in submission.get("uploaded_files", []) if item.get("path") == stored_path), None)
        record = record or {"filename": submission.get("filename", filename), "content_type": mimetypes.guess_type(filename)[0]}
    else:
        legacy_upload = await ocr_results.find_one({"uploaded_file": stored_path})
        if not legacy_upload:
            raise HTTPException(status_code=404, detail="File not found")
        if user["role"] == "student":
            allowed = legacy_upload["user_id"] == user["_id"]
        elif user["role"] == "teacher":
            try:
                await teacher_student_id(str(legacy_upload["user_id"]), user)
                allowed = True
            except HTTPException as exc:
                if exc.status_code != 404:
                    raise
                allowed = False
        else:
            allowed = False
        if not allowed:
            raise HTTPException(status_code=404, detail="File not found")
        record = legacy_upload
    file_path = UPLOAD_DIR / filename
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    content_type = (record or {}).get("content_type") or mimetypes.guess_type((record or {}).get("filename", filename))[0] or "application/octet-stream"
    return Response(
        content=file_path.read_bytes(),
        media_type=content_type,
        headers={"Content-Disposition": f'inline; filename="{Path((record or {}).get("filename", filename)).name}"'},
    )


async def assignment_view(item: dict, submission: dict | None = None, teacher_name: str = "") -> dict:
    subject_name = item.get("subject", "Uncategorised")
    if item.get("subject_id"):
        subject = await subjects.find_one({"_id": item["subject_id"]})
        subject_name = subject["name"] if subject else "Uncategorised"
    view = {
        "id": str(item["_id"]), "title": item["title"], "instructions": item.get("instructions", ""), "assignment_content": item.get("assignment_content", ""),
        "subject_id": str(item["subject_id"]) if item.get("subject_id") else None, "subject": subject_name, "grade": item["grade"], "class_section": item["class_section"],
        "teacher_name": teacher_name, "due_date": item["due_date"], "published": item.get("published", True), "created_at": iso(item.get("created_at")),
        "assignment_scope": item.get("assignment_scope", "entire_class"),
        "assigned_student_ids": [str(student_id) for student_id in item.get("assigned_student_ids", [])],
        "feedback_status": item.get("_feedback_status", "NOT_STARTED"),
    }
    if item.get("assignment_file"):
        view["assignment_file"] = {"filename": item["assignment_file"].get("filename"), "content_type": item["assignment_file"].get("content_type"), "url": f"/api/assignments/{item['_id']}/file"}
    if submission:
        view["submission"] = submission_view(submission)
    return view


def submission_view(item: dict) -> dict:
    uploaded_files = item.get("uploaded_files")
    if uploaded_files is None and item.get("uploaded_file"):
        uploaded_files = [{
            "path": item["uploaded_file"],
            "filename": item.get("filename", "Submitted work"),
            "content_type": mimetypes.guess_type(item.get("filename", ""))[0] or "application/octet-stream",
        }]
    return {
        "id": str(item["_id"]), "assignment_id": str(item["assignment_id"]), "student_id": str(item["student_id"]),
        "uploaded_file": item.get("uploaded_file"), "filename": item.get("filename"), "submitted_at": iso(item.get("submitted_at")),
        "status": item.get("status", "SUBMITTED"), "ocr_text": item.get("ocr_text", ""), "ai_score": item.get("ai_score"),
        "ai_feedback": item.get("ai_feedback", ""), "teacher_feedback": item.get("teacher_feedback", ""),
        "submission_text": item.get("submission_text", ""),
        "submission_method": item.get("submission_method", "upload" if uploaded_files else "typed"),
        "uploaded_files": [
            {
                "filename": uploaded_file.get("filename", "Submitted work"),
                "content_type": uploaded_file.get("content_type", "application/octet-stream"),
                "url": f"/api/files/{Path(uploaded_file['path']).name}" if uploaded_file.get("path") else None,
            }
            for uploaded_file in uploaded_files or []
        ],
        "grading": item.get("grading", {}),
        "feedback_status": item.get("feedback_status", "PENDING_REVIEW"),
    }


async def require_role(user: dict, role: str):
    if user["role"] != role:
        raise HTTPException(status_code=403, detail=f"{role.title()} access required")


async def teacher_assignment(assignment_id: str, user: dict):
    item = await assignments.find_one({"_id": object_id(assignment_id, "assignment"), "teacher_id": user["_id"]})
    if not item:
        raise HTTPException(status_code=404, detail="Assignment not found")
    return item


@app.get("/api/student/dashboard")
async def student_dashboard(user: dict = Depends(current_user)):
    await require_role(user, "student")
    query = assignment_visibility_query(user)
    total = await assignments.count_documents(query)
    visible_assignments = await assignments.find(query).to_list(length=None)
    visible_ids = [item["_id"] for item in visible_assignments]
    student_submissions = await submissions.find({"student_id": user["_id"], "assignment_id": {"$in": visible_ids}}).to_list(length=None) if visible_ids else []
    submitted_ids = {item["assignment_id"] for item in student_submissions}
    by_subject = {}
    for item in visible_assignments:
        subject = item.get("subject", "Uncategorised")
        if item.get("subject_id"):
            subject_record = await subjects.find_one({"_id": item["subject_id"]})
            subject = subject_record["name"] if subject_record else "Uncategorised"
        summary = by_subject.setdefault(subject, {"subject": subject, "completed": 0, "total": 0})
        summary["total"] += 1
        if item["_id"] in submitted_ids:
            summary["completed"] += 1
    scores = [item.get("ai_score") for item in student_submissions if isinstance(item.get("ai_score"), (int, float))]
    recent = sorted(student_submissions, key=lambda item: item.get("submitted_at") or datetime.min.replace(tzinfo=timezone.utc), reverse=True)[:3]
    completed = len(submitted_ids)
    return {"stats": {"total_assignments": total, "pending_assignments": max(total - completed, 0), "completed_assignments": completed, "average_score": round(sum(scores) / len(scores)) if scores else 0}, "subjects": list(by_subject.values()), "recent_feedback": [submission_view(item) for item in recent]}


@app.get("/api/student/assignments")
async def student_assignments(user: dict = Depends(current_user)):
    await require_role(user, "student")
    query = assignment_visibility_query(user)
    result = []
    async for item in assignments.find(query).sort("due_date", 1):
        submission = await submissions.find_one({"assignment_id": item["_id"], "student_id": user["_id"]})
        teacher = await users.find_one({"_id": item["teacher_id"]})
        result.append(await assignment_view(item, submission, teacher.get("name", "Teacher") if teacher else "Teacher"))
    return {"assignments": result}


@app.get("/api/student/submissions")
async def student_submissions(user: dict = Depends(current_user)):
    await require_role(user, "student")
    visible_assignments = await assignments.find(assignment_visibility_query(user), {"_id": 1}).to_list(length=None)
    visible_ids = [item["_id"] for item in visible_assignments]
    result = []
    query = {"student_id": user["_id"], "assignment_id": {"$in": visible_ids}}
    async for item in submissions.find(query).sort("submitted_at", -1):
        assignment = await assignments.find_one({"_id": item["assignment_id"]})
        submission = submission_view(item)
        if assignment:
            assignment_view_data = await assignment_view(assignment)
            submission["assignment"] = {"title": assignment_view_data["title"], "subject": assignment_view_data["subject"], "instructions": assignment_view_data["instructions"], "assignment_content": assignment_view_data["assignment_content"]}
        else:
            submission["assignment"] = None
        if item.get("feedback_status") != "REVIEWED":
            submission["teacher_feedback"] = ""
        result.append(submission)
    return {"submissions": result}


@app.get("/api/assignments/{assignment_id}/file")
async def assignment_file(assignment_id: str, user: dict = Depends(current_user)):
    assignment_object_id = object_id(assignment_id, "assignment")
    if user["role"] == "teacher":
        assignment = await assignments.find_one({"_id": assignment_object_id, "teacher_id": user["_id"]})
    else:
        await require_role(user, "student")
        assignment = await assignments.find_one({"_id": assignment_object_id, **assignment_visibility_query(user)})
    if not assignment or not assignment.get("assignment_file"):
        raise HTTPException(status_code=404, detail="Assignment file not found")
    stored_file = assignment["assignment_file"]
    return Response(content=bytes(stored_file["data"]), media_type=stored_file.get("content_type", "application/octet-stream"), headers={"Content-Disposition": f'inline; filename="{stored_file.get("filename", "assignment")}"'})


@app.post("/api/student/assignments/{assignment_id}/submit")
async def student_submit(
    assignment_id: str,
    file: UploadFile | None = File(default=None),
    files: list[UploadFile] = File(default=[]),
    answer_text: str = Form(default="", max_length=20_000),
    user: dict = Depends(current_user),
):
    await require_role(user, "student")
    query = assignment_visibility_query(user)
    query["_id"] = object_id(assignment_id, "assignment")
    assignment = await assignments.find_one(query)
    if not assignment:
        raise HTTPException(status_code=404, detail="Assignment not found for your class")
    submitted_files = ([file] if file else []) + files
    if len(submitted_files) > 10:
        raise HTTPException(status_code=413, detail="You can submit up to 10 files at a time")
    answer_text = answer_text.strip()
    if not answer_text and not submitted_files:
        raise HTTPException(status_code=422, detail="Type an answer or upload at least one image or PDF")
    assignment_content = assignment.get("assignment_content", "") or assignment.get("instructions", "")
    ocr_parts = []
    image_grading_input = None
    stored_files = []
    for uploaded_file in submitted_files:
        content_type = uploaded_file.content_type or ""
        if not (content_type.startswith("image/") or content_type == "application/pdf"):
            raise HTTPException(status_code=415, detail="Please upload image or PDF files only")
        contents = await uploaded_file.read()
        if not contents:
            raise HTTPException(status_code=422, detail=f"{uploaded_file.filename or 'A file'} is empty")
        if len(contents) > 10 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="Files must be 10MB or smaller")
        original_name = Path(uploaded_file.filename or "work").name
        stored_name = f"{secrets.token_hex(16)}{Path(original_name).suffix.lower()}"
        stored_files.append({"stored_name": stored_name, "filename": original_name, "content_type": content_type, "data": contents})
        if content_type.startswith("image/"):
            with tempfile.TemporaryDirectory() as temp_dir:
                image_path = Path(temp_dir) / stored_name
                image_path.write_bytes(contents)
                results = get_ocr_engine().predict(str(image_path))
                for result in results:
                    ocr_parts.extend(result.json.get("res", {}).get("rec_texts", []))
            if len(submitted_files) == 1 and not answer_text:
                image_grading_input = (contents, content_type)
        elif PdfReader:
            reader = PdfReader(io.BytesIO(contents))
            if len(reader.pages) > 30:
                raise HTTPException(status_code=413, detail="PDFs must contain 30 pages or fewer")
            with pdfium.PdfDocument(contents) as pdf_document:
                for page_index, page in enumerate(reader.pages):
                    page_text = page.extract_text() or ""
                    if page_text.strip():
                        ocr_parts.append(page_text)
                        continue
                    rendered_page = pdf_document[page_index].render(scale=2).to_pil()
                    with tempfile.TemporaryDirectory() as temp_dir:
                        page_path = Path(temp_dir) / f"page-{page_index}.png"
                        rendered_page.save(page_path, format="PNG")
                        results = get_ocr_engine().predict(str(page_path))
                        for result in results:
                            ocr_parts.extend(result.json.get("res", {}).get("rec_texts", []))
    ocr_text = "\n".join(part for part in ocr_parts if part).strip()
    feedback_text = "\n\n".join(part for part in (answer_text, ocr_text) if part)
    if image_grading_input:
        grading = await grade_image(image_grading_input[0], image_grading_input[1], assignment_content)
        score = grading.get("score", 100 if grading.get("is_correct") else 0)
        feedback = grading.get("feedback", "")
    else:
        grading = await grade_text(feedback_text, assignment_content)
        score, feedback = grading["score"], grading["feedback"]
    for uploaded_file in stored_files:
        (UPLOAD_DIR / uploaded_file["stored_name"]).write_bytes(uploaded_file["data"])
    existing = await submissions.find_one({"assignment_id": assignment["_id"], "student_id": user["_id"]})
    file_records = [
        {"path": f"/files/{uploaded_file['stored_name']}", "filename": uploaded_file["filename"], "content_type": uploaded_file["content_type"]}
        for uploaded_file in stored_files
    ]
    document = {
        "assignment_id": assignment["_id"], "student_id": user["_id"],
        "uploaded_file": file_records[0]["path"] if file_records else None,
        "uploaded_files": file_records,
        "filename": ", ".join(item["filename"] for item in file_records) or "Typed answer",
        "submission_text": answer_text, "submission_method": "typed_and_upload" if answer_text and file_records else "typed" if answer_text else "upload",
        "submitted_at": datetime.now(timezone.utc), "status": "SUBMITTED", "ocr_text": ocr_text,
        "ai_score": score, "ai_feedback": feedback, "grading": grading, "teacher_feedback": "", "feedback_status": "PENDING_REVIEW",
    }
    if existing:
        await submissions.replace_one({"_id": existing["_id"]}, document)
        document["_id"] = existing["_id"]
    else:
        result = await submissions.insert_one(document)
        document["_id"] = result.inserted_id
    return submission_view(document)


@app.get("/api/teacher/assignments")
async def teacher_assignments(
    search: str = "",
    subject: str = "",
    grade: str = "",
    section: str = "",
    student_id: str = "",
    feedback_status: str = "",
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=50),
    user: dict = Depends(current_user),
):
    await require_role(user, "teacher")
    pending_submissions = {
        "$filter": {
            "input": "$assignment_submissions",
            "as": "submission",
            "cond": {"$ne": ["$$submission.feedback_status", "REVIEWED"]},
        }
    }
    feedback_status_expr = {
        "$cond": [
            {"$eq": [{"$size": "$assignment_submissions"}, 0]},
            "NOT_STARTED",
            {"$cond": [
                {"$gt": [{"$size": pending_submissions}, 0]},
                "PENDING_REVIEW",
                "REVIEWED",
            ]},
        ]
    }
    match = {"teacher_id": user["_id"]}
    if grade:
        match["grade"] = grade
    if section:
        match["class_section"] = section.upper()
    if student_id:
        student_object_id = object_id(student_id, "student")
        class_ids = await owned_class_ids(user, grade, section)
        if not class_ids:
            raise HTTPException(status_code=404, detail="Student not found")
        student_query = teacher_students_query(user, class_ids)
        student_query["_id"] = student_object_id
        selected_student = await users.find_one(student_query, {"_id": 1, "class_id": 1, "grade": 1, "class_section": 1})
        if not selected_student:
            raise HTTPException(status_code=404, detail="Student not found")
        match["grade"] = selected_student["grade"]
        match["class_section"] = selected_student["class_section"]
        if selected_student.get("class_id"):
            match["class_id"] = selected_student["class_id"]
        match["$or"] = [
            {"assignment_scope": "entire_class"},
            {"assignment_scope": {"$exists": False}},
            {"assigned_student_ids": student_object_id},
        ]
    pipeline = [
        {"$match": match},
        {"$lookup": {"from": "subjects", "localField": "subject_id", "foreignField": "_id", "as": "assignment_subject"}},
        {"$addFields": {"_subject_name": {"$ifNull": [{"$arrayElemAt": ["$assignment_subject.name", 0]}, "$subject"]}}},
        {"$lookup": {
            "from": "users",
            "let": {
                "assignment_class_id": "$class_id",
                "assignment_teacher_id": "$teacher_id",
                "assignment_grade": "$grade",
                "assignment_section": "$class_section",
                "assignment_scope": "$assignment_scope",
                "assigned_student_ids": "$assigned_student_ids",
            },
            "pipeline": [
                {"$match": {"$expr": {"$and": [
                    {"$eq": ["$role", "student"]},
                    {"$or": [
                        {"$and": [{"$ne": ["$$assignment_class_id", None]}, {"$eq": ["$class_id", "$$assignment_class_id"]}]},
                        {"$and": [
                            {"$eq": ["$teacher_id", "$$assignment_teacher_id"]},
                            {"$eq": ["$grade", "$$assignment_grade"]},
                            {"$eq": ["$class_section", "$$assignment_section"]},
                            {"$eq": [{"$ifNull": ["$class_id", None]}, None]},
                        ]},
                    ]},
                    {"$or": [
                        {"$ne": ["$$assignment_scope", "selected_students"]},
                        {"$in": ["$_id", {"$ifNull": ["$$assigned_student_ids", []]}]},
                    ]},
                ]}}},
                {"$project": {"_id": 1}},
            ],
            "as": "eligible_students",
        }},
        {"$lookup": {
            "from": "submissions",
            "let": {"assignment_id": "$_id", "student_ids": "$eligible_students._id"},
            "pipeline": [{"$match": {"$expr": {"$and": [
                {"$eq": ["$assignment_id", "$$assignment_id"]},
                {"$in": ["$student_id", "$$student_ids"]},
            ]}}}],
            "as": "assignment_submissions",
        }},
        {"$project": {"eligible_students": 0}},
        {"$addFields": {"_feedback_status": feedback_status_expr}},
    ]
    if subject:
        pipeline.append({"$match": {"_subject_name": subject}})
    if feedback_status:
        pipeline.append({"$match": {"_feedback_status": feedback_status.upper()}})
    if search.strip():
        search_regex = {"$regex": search.strip(), "$options": "i"}
        pipeline.append({"$match": {"$or": [{"title": search_regex}, {"grade": search_regex}, {"class_section": search_regex}, {"_subject_name": search_regex}]}})
    pipeline.extend([
        {"$sort": {"created_at": -1, "_id": -1}},
        {"$facet": {
            "items": [{"$skip": (page - 1) * page_size}, {"$limit": page_size}],
            "total": [{"$count": "count"}],
        }},
    ])
    aggregate_result = await assignments.aggregate(pipeline).to_list(length=1)
    facet = aggregate_result[0] if aggregate_result else {"items": [], "total": []}
    result = []
    for item in facet.get("items", []):
        item.pop("assignment_subject", None)
        item.pop("assignment_submissions", None)
        item.pop("_subject_name", None)
        result.append(await assignment_view(item))
    total_rows = facet.get("total", [])
    total = total_rows[0].get("count", 0) if total_rows else 0
    return {"assignments": result, "total": total, "page": page, "page_size": page_size, "page_count": max(1, (total + page_size - 1) // page_size)}


@app.get("/api/teacher/assignment-students")
async def teacher_assignment_students(
    grade: str,
    section: str,
    assignment_ids: str = "",
    user: dict = Depends(current_user),
):
    await require_role(user, "teacher")
    ids = [object_id(value, "assignment") for value in assignment_ids.split(",") if value]
    if not ids:
        return {"students": {}}
    class_ids = await owned_class_ids(user, grade, section)
    if not class_ids:
        return {"students": {}}
    owned_assignments = await assignments.find({"_id": {"$in": ids}, "teacher_id": user["_id"], "class_id": {"$in": class_ids}, "grade": grade, "class_section": section.upper()}, {"_id": 1, "class_id": 1, "assignment_scope": 1, "assigned_student_ids": 1}).to_list(length=len(ids))
    if not owned_assignments:
        return {"students": {}}
    student_query = teacher_students_query(user, class_ids)
    student_query.update({"grade": grade, "class_section": section.upper()})
    class_students = await users.find(student_query, {"password_hash": 0}).sort("name", 1).to_list(length=None)
    submissions_list = await submissions.find({"assignment_id": {"$in": [item["_id"] for item in owned_assignments]}, "student_id": {"$in": [student["_id"] for student in class_students]}}).to_list(length=None)
    submission_by_key = {(item["assignment_id"], item["student_id"]): submission_view(item) for item in submissions_list}
    result = {}
    for assignment in owned_assignments:
        assignment_students = class_students
        if assignment.get("assignment_scope") == "selected_students":
            assignment_students = [student for student in class_students if student["_id"] in assignment.get("assigned_student_ids", [])]
        result[str(assignment["_id"])] = [{"student": public_user(student), "submission": submission_by_key.get((assignment["_id"], student["_id"]))} for student in assignment_students]
    return {"students": result}


@app.get("/api/teacher/students")
async def teacher_class_students(grade: str = "", class_section: str = "", subject: str = "", user: dict = Depends(current_user)):
    await require_role(user, "teacher")
    if not grade.strip() or not class_section.strip():
        return {"students": []}
    class_ids = await owned_class_ids(user, grade.strip(), class_section.strip())
    if not class_ids:
        return {"students": []}
    student_query = teacher_students_query(user, class_ids)
    student_query.update({"grade": grade.strip(), "class_section": class_section.strip().upper()})
    if subject.strip():
        subject_record = await subjects.find_one({"teacher_id": user["_id"], "name": subject.strip()})
        if not subject_record:
            return {"students": []}
        subject_assignments = await assignments.find(
            {"teacher_id": user["_id"], "grade": grade.strip(), "class_section": class_section.strip().upper(), "subject_id": subject_record["_id"]},
            {"assignment_scope": 1, "assigned_student_ids": 1},
        ).to_list(length=None)
        if not any(item.get("assignment_scope") != "selected_students" for item in subject_assignments):
            student_ids = {student_id for item in subject_assignments for student_id in item.get("assigned_student_ids", [])}
            student_query["_id"] = {"$in": list(student_ids)}
    students = users.find(student_query).sort("name", 1)
    return {"students": [public_user(student) async for student in students]}


@app.post("/api/teacher/assignments")
async def create_assignment(request: Request, user: dict = Depends(current_user)):
    await require_role(user, "teacher")
    _, document = await assignment_form(request, user)
    result = await assignments.insert_one(document)
    document["_id"] = result.inserted_id
    return await assignment_view(document, teacher_name=user["name"])


@app.patch("/api/teacher/assignments/{assignment_id}")
async def update_assignment(assignment_id: str, request: Request, user: dict = Depends(current_user)):
    item = await teacher_assignment(assignment_id, user)
    _, document = await assignment_form(request, user)
    fields = {key: value for key, value in document.items() if key not in {"teacher_id", "created_at"}}
    await assignments.update_one({"_id": item["_id"]}, {"$set": fields})
    item.update(fields)
    return await assignment_view(item, teacher_name=user["name"])


@app.delete("/api/teacher/assignments/{assignment_id}")
async def delete_assignment(assignment_id: str, user: dict = Depends(current_user)):
    item = await teacher_assignment(assignment_id, user)
    await assignments.delete_one({"_id": item["_id"]})
    await submissions.delete_many({"assignment_id": item["_id"]})
    return {"id": assignment_id}


@app.get("/api/teacher/assignments/{assignment_id}/tracking")
async def assignment_tracking(assignment_id: str, user: dict = Depends(current_user)):
    item = await teacher_assignment(assignment_id, user)
    students = []
    student_query = await assignment_student_query(item, user)
    if item.get("assignment_scope") == "selected_students":
        student_query["_id"] = {"$in": item.get("assigned_student_ids", [])}
    async for student in users.find(student_query).sort("name", 1):
        submission = await submissions.find_one({"assignment_id": item["_id"], "student_id": student["_id"]})
        students.append({"student": public_user(student), "submission": submission_view(submission) if submission else None})
    return {"assignment": await assignment_view(item, teacher_name=user["name"]), "students": students}


@app.patch("/api/teacher/submissions/{submission_id}/review")
async def review_submission(submission_id: str, data: ReviewRequest, user: dict = Depends(current_user)):
    await require_role(user, "teacher")
    item = await submissions.find_one({"_id": object_id(submission_id, "submission")})
    if not item:
        raise HTTPException(status_code=404, detail="Submission not found")
    assignment = await teacher_assignment(str(item["assignment_id"]), user)
    if not await assignment_student_allowed(assignment, item["student_id"], user):
        raise HTTPException(status_code=404, detail="Submission not found")
    await submissions.update_one({"_id": item["_id"]}, {"$set": {"teacher_feedback": data.teacher_feedback, "feedback_status": data.feedback_status, "status": "REVIEWED" if data.feedback_status == "REVIEWED" else "SUBMITTED"}})
    item.update({"teacher_feedback": data.teacher_feedback, "feedback_status": data.feedback_status, "status": "REVIEWED" if data.feedback_status == "REVIEWED" else "SUBMITTED"})
    return submission_view(item)


@app.get("/api/teacher/dashboard/classroom")
async def teacher_classroom_dashboard(user: dict = Depends(current_user)):
    await require_role(user, "teacher")
    owned = {"teacher_id": user["_id"]}
    total_assignments = await assignments.count_documents(owned)
    assignment_list = await assignments.find(owned).to_list(length=None)
    assignment_ids = [item["_id"] for item in assignment_list]
    class_ids = await owned_class_ids(user)
    students = await users.count_documents(teacher_students_query(user, class_ids))
    eligible_count = 0
    submitted = 0
    scores = []
    for assignment in assignment_list:
        student_query = await assignment_student_query(assignment, user)
        if assignment.get("assignment_scope") == "selected_students":
            student_query["_id"] = {"$in": assignment.get("assigned_student_ids", [])}
        student_ids = [student["_id"] async for student in users.find(student_query, {"_id": 1})]
        eligible_count += len(student_ids)
        if not student_ids:
            continue
        assignment_submissions = await submissions.find(
            {"assignment_id": assignment["_id"], "student_id": {"$in": student_ids}},
            {"ai_score": 1},
        ).to_list(length=None)
        submitted += len(assignment_submissions)
        scores.extend(item["ai_score"] for item in assignment_submissions if isinstance(item.get("ai_score"), (int, float)))
    return {"stats": {"students": students, "assignments": total_assignments, "submitted": submitted, "not_submitted": max(eligible_count - submitted, 0), "average_score": round(sum(scores) / len(scores)) if scores else 0}}
