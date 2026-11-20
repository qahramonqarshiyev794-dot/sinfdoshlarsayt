
import hashlib
import hmac
import ipaddress
import json
import os
import re
import secrets
import threading
import time
import psycopg

from psycopg.types.json import Jsonb
from http import HTTPStatus
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parent
COOKIE_NAME = "sinfdoshlar_session"
SESSION_SECONDS = 60 * 60 * 24 * 7
MAX_BODY_SIZE = 64 * 1024
PBKDF2_ITERATIONS = 310_000

ACCESS_REQUESTS = {}
LOGIN_ATTEMPTS = {}
SESSIONS = {}
STORE_LOCK = threading.RLock()


def load_env():
    env_file = ROOT / ".env"
    if not env_file.exists():
        return

    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()

        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


load_env()

DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()

ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")

COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "0") == "1"

TRUSTED_PROXY_IPS = {
    address.strip()
    for address in os.environ.get(
        "TRUSTED_PROXY_IPS", "127.0.0.1,::1"
    ).split(",")
    if address.strip()
}

INITIAL_VIDEO_URL = os.environ.get("INITIAL_VIDEO_URL", "")
INITIAL_VIDEO_TITLE = os.environ.get(
    "INITIAL_VIDEO_TITLE", "Sinfdoshlar uchun video"
)


# ============================================================
# NEON POSTGRESQL BAZASI
# ============================================================

def initialize_database():
    """
    Neon PostgreSQL bazasiga ulanadi.
    Kerakli jadvalni yaratadi.
    Birinchi ishga tushishda boshlang'ich ma'lumotlarni kiritadi.
    """

    if not DATABASE_URL:
        raise RuntimeError(
            "DATABASE_URL sozlanmagan. "
            "Render Environment bo'limini tekshiring."
        )

    initial_videos = []

    if video_source(INITIAL_VIDEO_URL):
        initial_videos.append({
            "id": secrets.token_hex(12),
            "title": INITIAL_VIDEO_TITLE,
            "url": INITIAL_VIDEO_URL,
            "category": "Umumiy",
            "createdAt": int(time.time()),
        })

    initial_store = {
        "requests": [],
        "videos": initial_videos,
        "users": [],
    }

    try:
        with psycopg.connect(
            DATABASE_URL,
            connect_timeout=10
        ) as conn:

            with conn.cursor() as cur:

                cur.execute("""
                    CREATE TABLE IF NOT EXISTS sinfdoshlar_site_store (
                        id SMALLINT PRIMARY KEY CHECK (id = 1),
                        data JSONB NOT NULL,
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                """)

                cur.execute("""
                    INSERT INTO sinfdoshlar_site_store (id, data)
                    VALUES (1, %s)
                    ON CONFLICT (id) DO NOTHING
                """, (Jsonb(initial_store),))

    except Exception as error:
        raise RuntimeError(
            "Neon PostgreSQL bazasiga ulanish yoki jadval yaratishda "
            f"xatolik yuz berdi: {error}"
        ) from error


def read_store():
    """
    Sayt ma'lumotlarini PostgreSQL bazasidan o'qiydi.
    """

    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL sozlanmagan.")

    with STORE_LOCK:
        try:
            with psycopg.connect(
                DATABASE_URL,
                connect_timeout=10
            ) as conn:

                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT data
                        FROM sinfdoshlar_site_store
                        WHERE id = 1
                    """)

                    row = cur.fetchone()

            if not row:
                raise RuntimeError(
                    "PostgreSQL bazasida sayt ma'lumotlari topilmadi."
                )

            store = row[0]

            if (
                not isinstance(store, dict)
                or not isinstance(store.get("requests"), list)
                or not isinstance(store.get("videos"), list)
            ):
                raise RuntimeError(
                    "PostgreSQL bazasidagi ma'lumotlar formati noto'g'ri."
                )

            store.setdefault("users", [])

            if not isinstance(store["users"], list):
                raise RuntimeError(
                    "PostgreSQL bazasidagi foydalanuvchilar "
                    "ro'yxati noto'g'ri."
                )

            return store

        except Exception as error:
            if isinstance(error, RuntimeError):
                raise

            raise RuntimeError(
                f"PostgreSQL bazasidan ma'lumot o'qishda xatolik: {error}"
            ) from error


def write_store(store):
    """
    Sayt ma'lumotlarini PostgreSQL bazasiga yozadi.
    """

    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL sozlanmagan.")

    with STORE_LOCK:
        try:
            with psycopg.connect(
                DATABASE_URL,
                connect_timeout=10
            ) as conn:

                with conn.cursor() as cur:
                    cur.execute("""
                        UPDATE sinfdoshlar_site_store
                        SET data = %s,
                            updated_at = NOW()
                        WHERE id = 1
                    """, (Jsonb(store),))

                    if cur.rowcount != 1:
                        raise RuntimeError(
                            "PostgreSQL bazasida sayt ma'lumotlari "
                            "qatori topilmadi."
                        )

        except Exception as error:
            if isinstance(error, RuntimeError):
                raise

            raise RuntimeError(
                f"PostgreSQL bazasiga ma'lumot yozishda xatolik: {error}"
            ) from error


# ============================================================
# VIDEO MANZILLARINI TEKSHIRISH
# ============================================================

def video_source(value):
    if not isinstance(value, str) or len(value) > 2048:
        return None

    try:
        parsed = urlparse(value.strip())
        host = parsed.hostname.lower() if parsed.hostname else ""
    except ValueError:
        return None

    if (
        parsed.scheme != "https"
        or not host
        or parsed.username
        or parsed.password
    ):
        return None

    if host in {
        "mega.nz",
        "www.mega.nz",
        "mega.co.nz",
        "www.mega.co.nz"
    }:
        match = re.fullmatch(
            r"/(file|embed)/([A-Za-z0-9_-]+)",
            parsed.path
        )

        if not match or not re.fullmatch(
            r"[A-Za-z0-9_-]+", parsed.fragment
        ):
            return None

        return {
            "type": "embed",
            "url": f"https://mega.nz/embed/{match.group(2)}#{parsed.fragment}",
        }

    if host == "youtu.be":
        video_id = parsed.path.strip("/").split("/", 1)[0]

    elif host in {
        "youtube.com",
        "www.youtube.com",
        "m.youtube.com",
        "youtube-nocookie.com",
        "www.youtube-nocookie.com",
    }:
        video_id = ""

        if parsed.path == "/watch":
            from urllib.parse import parse_qs
            video_id = parse_qs(parsed.query).get("v", [""])[0]

        else:
            match = re.match(
                r"^/(?:embed|shorts|live)/([A-Za-z0-9_-]+)",
                parsed.path
            )

            if match:
                video_id = match.group(1)

    else:
        video_id = ""

    if video_id and re.fullmatch(
        r"[A-Za-z0-9_-]{6,20}", video_id
    ):
        return {
            "type": "embed",
            "url": (
                "https://www.youtube.com/embed/"
                f"{video_id}"
                "?controls=0&rel=0&fs=1&playsinline=1&enablejsapi=1"
            ),
        }

    if host in {
        "vimeo.com",
        "www.vimeo.com",
        "player.vimeo.com"
    }:
        match = re.search(
            r"/(?:video/)?([0-9]+)(?:/|$)",
            parsed.path
        )

        if match:
            return {
                "type": "embed",
                "url": (
                    "https://player.vimeo.com/video/"
                    f"{match.group(1)}"
                ),
            }

    if Path(parsed.path).suffix.lower() in {
        ".mp4",
        ".webm",
        ".ogg",
        ".ogv",
        ".m4v",
        ".mov",
        ".mpeg",
        ".mpg",
    }:
        return {
            "type": "file",
            "url": value.strip()
        }

    return {
        "type": "embed",
        "url": value.strip()
    }


# ============================================================
# YORDAMCHI FUNKSIYALAR
# ============================================================

def valid_email(value):
    return (
        isinstance(value, str)
        and len(value) <= 254
        and re.fullmatch(
            r"[^@\s]+@[^@\s]+\.[^@\s]+",
            value
        ) is not None
    )


def hash_password(password):
    salt = secrets.token_bytes(16)

    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        PBKDF2_ITERATIONS
    )

    return (
        f"pbkdf2_sha256${PBKDF2_ITERATIONS}"
        f"${salt.hex()}${digest.hex()}"
    )


def verify_password(password, stored_hash):
    try:
        algorithm, iterations, salt_hex, digest_hex = (
            stored_hash.split("$", 3)
        )

        if algorithm != "pbkdf2_sha256":
            return False

        iterations = int(iterations)

        if not 100_000 <= iterations <= 1_000_000:
            return False

        digest = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            bytes.fromhex(salt_hex),
            iterations
        )

        return hmac.compare_digest(
            digest.hex(),
            digest_hex
        )

    except (AttributeError, ValueError):
        return False


def key_hash(value):
    return hashlib.sha256(
        value.encode("utf-8")
    ).hexdigest()


def public_request(request):
    return {
        "id": request["id"],
        "name": request["name"],
        "email": request.get("email", ""),
        "status": request["status"],
        "createdAt": request["createdAt"],
    }


def set_session(handler, role, user_id=None):
    token = secrets.token_urlsafe(32)

    SESSIONS[key_hash(token)] = {
        "role": role,
        "userId": user_id,
        "expires": time.time() + SESSION_SECONDS,
    }

    secure = "; Secure" if COOKIE_SECURE else ""

    handler.send_header(
        "Set-Cookie",
        (
            f"{COOKIE_NAME}={token}; Path=/; HttpOnly; "
            f"SameSite=Strict; Max-Age={SESSION_SECONDS}{secure}"
        ),
    )


# ============================================================
# ASOSIY HTTP SERVER
# ============================================================

class SiteHandler(BaseHTTPRequestHandler):

    server_version = "SinfdoshlarSite/1.0"

    def log_message(self, _format, *_args):
        return

    def send_json(self, status, payload, extra_headers=None):
        body = json.dumps(
            payload,
            ensure_ascii=False
        ).encode("utf-8")

        self.send_response(status)
        self.send_header(
            "Content-Type",
            "application/json; charset=utf-8"
        )
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")

        self.send_header(
            "Content-Security-Policy",
            (
                "default-src 'self'; "
                "style-src 'self'; "
                "script-src 'self' https://www.youtube.com https://s.ytimg.com; "
                "frame-src https:; "
                "media-src 'self' https: blob:; "
                "base-uri 'none'; "
                "frame-ancestors 'none'"
            )
        )

        if extra_headers:
            for name, value in extra_headers:
                self.send_header(name, value)

        self.end_headers()
        self.wfile.write(body)

    def read_json(self):
        try:
            length = int(
                self.headers.get("Content-Length", "0")
            )
        except ValueError:
            return None

        if length < 1 or length > MAX_BODY_SIZE:
            return None

        if self.headers.get_content_type() != "application/json":
            return None

        try:
            value = json.loads(self.rfile.read(length))

            return value if isinstance(value, dict) else None

        except (json.JSONDecodeError, UnicodeDecodeError):
            return None

    def current_session(self):
        cookie = SimpleCookie()

        try:
            cookie.load(self.headers.get("Cookie", ""))
        except CookieError:
            return None

        morsel = cookie.get(COOKIE_NAME)

        if not morsel:
            return None

        session_key = key_hash(morsel.value)
        session = SESSIONS.get(session_key)

        if session and session["expires"] > time.time():

            if session["role"] == "user":
                user = next(
                    (
                        item
                        for item in read_store().get("users", [])
                        if item.get("email") == session["userId"]
                    ),
                    None,
                )

                if not user or user.get("blocked"):
                    SESSIONS.pop(session_key, None)
                    return None

            return session

        SESSIONS.pop(session_key, None)
        return None

    def require_role(self, *roles):
        session = self.current_session()

        if not session or session["role"] not in roles:
            self.send_json(
                HTTPStatus.UNAUTHORIZED,
                {"error": "Avval tizimga kiring."}
            )
            return None

        return session

    def client_ip(self):
        if os.environ.get("RENDER_EXTERNAL_HOSTNAME"):
            forwarded_ip = self.headers.get(
                "CF-Connecting-IP", ""
            ).strip()

            try:
                return str(ipaddress.ip_address(forwarded_ip))
            except ValueError:
                pass

        remote_ip = self.client_address[0]

        if remote_ip not in TRUSTED_PROXY_IPS:
            return remote_ip

        forwarded_ip = self.headers.get(
            "X-Real-IP", ""
        ).strip()

        try:
            return str(ipaddress.ip_address(forwarded_ip))
        except ValueError:
            return remote_ip

    # --------------------------------------------------------
    # GET SO'ROVLARI
    # --------------------------------------------------------

    def do_GET(self):
        path = urlparse(self.path).path

        if path == "/":
            self.serve_static(
                "index.html",
                "text/html; charset=utf-8"
            )
            return

        if path == "/app.js":
            self.serve_static(
                "app.js",
                "text/javascript; charset=utf-8"
            )
            return

        if path == "/styles.css":
            self.serve_static(
                "styles.css",
                "text/css; charset=utf-8"
            )
            return

        if path == "/favicon.svg":
            self.serve_static(
                "favicon.svg",
                "image/svg+xml"
            )
            return

        if path == "/api/me":
            session = self.current_session()

            if not session:
                self.send_json(
                    HTTPStatus.OK,
                    {"role": None}
                )

            elif session["role"] == "admin":
                self.send_json(
                    HTTPStatus.OK,
                    {"role": "admin"}
                )

            else:
                user = next(
                    (
                        item
                        for item in read_store().get("users", [])
                        if item["email"] == session["userId"]
                    ),
                    None,
                )

                if user:
                    self.send_json(
                        HTTPStatus.OK,
                        {
                            "role": "user",
                            "name": user["name"]
                        }
                    )
                else:
                    self.send_json(
                        HTTPStatus.OK,
                        {"role": None}
                    )

            return

        if path == "/api/access/status":
            self.get_access_status()
            return

        if path == "/api/videos":
            session = self.require_role("user", "admin")

            if session:
                is_admin = session["role"] == "admin"
                videos = []

                for video in read_store()["videos"]:
                    source = video_source(
                        video.get("url", "")
                    )

                    item = {
                        "id": video["id"],
                        "title": video["title"],
                        "category": video.get("category") or "Umumiy",
                        "type": source["type"] if source else None,
                        "playUrl": source["url"] if source else None,
                    }

                    if is_admin:
                        item["url"] = video.get("url", "")

                    videos.append(item)

                categories = sorted(
                    {item["category"] for item in videos},
                    key=str.casefold
                )

                self.send_json(
                    HTTPStatus.OK,
                    {
                        "videos": videos,
                        "categories": categories
                    }
                )

            return

        if path == "/api/admin/requests":
            if self.require_role("admin"):
                requests = sorted(
                    read_store()["requests"],
                    key=lambda item: item["createdAt"],
                    reverse=True
                )

                self.send_json(
                    HTTPStatus.OK,
                    {
                        "requests": [
                            public_request(item)
                            for item in requests
                        ]
                    }
                )

            return

        if path == "/api/admin/users":
            if self.require_role("admin"):
                users = sorted(
                    read_store().get("users", []),
                    key=lambda item: str(
                        item.get("name") or ""
                    ).casefold()
                )

                self.send_json(
                    HTTPStatus.OK,
                    {
                        "users": [
                            {
                                "email": item["email"],
                                "name": item.get("name", ""),
                                "createdAt": item.get("createdAt", 0),
                                "blocked": bool(
                                    item.get("blocked", False)
                                ),
                            }
                            for item in users
                        ]
                    }
                )

            return

        self.send_json(
            HTTPStatus.NOT_FOUND,
            {"error": "Sahifa topilmadi."}
        )

    # --------------------------------------------------------
    # STATIK FAYLLAR
    # --------------------------------------------------------

    def serve_static(self, filename, content_type):
        file_path = ROOT / "public" / filename

        try:
            body = file_path.read_bytes()
        except OSError:
            self.send_json(
                HTTPStatus.NOT_FOUND,
                {"error": "Fayl topilmadi."}
            )
            return

        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Referrer-Policy", "no-referrer")

        self.send_header(
            "Content-Security-Policy",
            (
                "default-src 'self'; "
                "style-src 'self'; "
                "script-src 'self' https://www.youtube.com https://s.ytimg.com; "
                "frame-src https:; "
                "media-src 'self' https: blob:; "
                "base-uri 'none'; "
                "frame-ancestors 'none'"
            )
        )

        self.end_headers()
        self.wfile.write(body)

    # --------------------------------------------------------
    # RO'YXATDAN O'TISH HOLATINI TEKSHIRISH
    # --------------------------------------------------------

    def get_access_status(self):
        query = urlparse(self.path).query

        from urllib.parse import parse_qs

        key = parse_qs(query).get("key", [""])[0]

        request = next(
            (
                item
                for item in read_store()["requests"]
                if item.get("keyHash") == key_hash(key)
            ),
            None,
        )

        if not key or not request:
            self.send_json(
                HTTPStatus.NOT_FOUND,
                {"error": "So‘rov topilmadi."}
            )
            return

        self.send_json(
            HTTPStatus.OK,
            {"status": request["status"]}
        )

    # --------------------------------------------------------
    # POST SO'ROVLARI
    # --------------------------------------------------------

    def do_POST(self):
        origin = self.headers.get("Origin")

        if origin:
            render_host = os.environ.get(
                "RENDER_EXTERNAL_HOSTNAME", ""
            ).strip().lower()

            is_render = bool(
                render_host
                or os.environ.get("RENDER_EXTERNAL_URL")
            )

            expected_scheme = (
                "https" if COOKIE_SECURE or is_render else "http"
            )

            parsed_origin = urlparse(origin)

            allowed_hosts = {
                self.headers.get("Host", "").strip().lower()
            }

            if is_render:
                if render_host:
                    allowed_hosts.add(render_host)

                forwarded_host = self.headers.get(
                    "X-Forwarded-Host", ""
                ).strip().lower()

                if forwarded_host:
                    allowed_hosts.add(forwarded_host)

            if (
                parsed_origin.scheme != expected_scheme
                or parsed_origin.netloc.lower() not in allowed_hosts
                or parsed_origin.username
                or parsed_origin.password
                or parsed_origin.path not in ("", "/")
                or parsed_origin.query
                or parsed_origin.fragment
            ):
                self.send_json(
                    HTTPStatus.FORBIDDEN,
                    {"error": "So‘rov manbasi rad etildi."}
                )
                return

        path = urlparse(self.path).path

        if path in {
            "/api/access-requests",
            "/api/register"
        }:
            self.register_user()
            return

        if path == "/api/user/login":
            self.user_login()
            return

        if path == "/api/admin/login":
            self.admin_login()
            return

        if path == "/api/admin/change-password":
            self.change_admin_password()
            return

        if (
            path.startswith("/api/admin/requests/")
            and path.endswith("/approve")
        ):
            self.update_request(path, "approved")
            return

        if (
            path.startswith("/api/admin/requests/")
            and path.endswith("/reject")
        ):
            self.update_request(path, "rejected")
            return

        if path == "/api/admin/users/status":
            self.update_user_block_status()
            return

        if path == "/api/videos":
            self.add_video()
            return

        if path.startswith("/api/videos/"):
            self.delete_video(path)
            return

        if path == "/api/logout":
            self.logout()
            return

        self.send_json(
            HTTPStatus.NOT_FOUND,
            {"error": "Manzil topilmadi."}
        )

    # --------------------------------------------------------
    # FOYDALANUVCHINI RO'YXATDAN O'TKAZISH
    # --------------------------------------------------------

    def register_user(self):
        body = self.read_json()

        name = (
            body.get("name", "").strip()
            if body and isinstance(body.get("name"), str)
            else ""
        )

        email = (
            body.get("email", "").strip().lower()
            if body and isinstance(body.get("email"), str)
            else ""
        )

        password = body.get("password") if body else None

        if not 1 <= len(name) <= 80:
            self.send_json(
                HTTPStatus.BAD_REQUEST,
                {"error": "Ism 1–80 ta belgidan iborat bo‘lsin."}
            )
            return

        if not valid_email(email):
            self.send_json(
                HTTPStatus.BAD_REQUEST,
                {"error": "To‘g‘ri elektron pochta manzilini kiriting."}
            )
            return

        if not isinstance(password, str) or not 8 <= len(password) <= 128:
            self.send_json(
                HTTPStatus.BAD_REQUEST,
                {"error": "Parol 8–128 ta belgidan iborat bo‘lsin."}
            )
            return

        now = time.time()
        remote = self.client_ip()

        previous = ACCESS_REQUESTS.get(remote, 0)

        if now - previous < 30:
            self.send_json(
                HTTPStatus.TOO_MANY_REQUESTS,
                {
                    "error": (
                        "Yangi so‘rov yuborishdan oldin "
                        "biroz kuting."
                    )
                }
            )
            return

        ACCESS_REQUESTS[remote] = now

        key = secrets.token_urlsafe(32)

        with STORE_LOCK:
            store = read_store()

            if any(
                user.get("email") == email
                for user in store.get("users", [])
            ):
                self.send_json(
                    HTTPStatus.CONFLICT,
                    {
                        "error": (
                            "Bu elektron pochta manzili tasdiqlangan. "
                            "Mavjud hisobingizga kiring."
                        )
                    }
                )
                return

            if any(
                item.get("email") == email
                and item.get("status") == "pending"
                for item in store["requests"]
            ):
                self.send_json(
                    HTTPStatus.CONFLICT,
                    {
                        "error": (
                            "Bu elektron pochta manzili uchun "
                            "tasdiqlash so‘rovi allaqachon yuborilgan."
                        )
                    }
                )
                return

            request = {
                "id": secrets.token_hex(12),
                "name": name,
                "email": email,
                "passwordHash": hash_password(password),
                "status": "pending",
                "keyHash": key_hash(key),
                "createdAt": int(now),
            }

            store["requests"].append(request)
            write_store(store)

        self.send_json(
            HTTPStatus.CREATED,
            {
                "key": key,
                "status": "pending",
                "email": email
            }
        )

    # --------------------------------------------------------
    # FOYDALANUVCHI LOGIN
    # --------------------------------------------------------

    def user_login(self):
        body = self.read_json()

        email = (
            body.get("email", "").strip().lower()
            if body and isinstance(body.get("email"), str)
            else ""
        )

        password = body.get("password") if body else None

        if (
            not valid_email(email)
            or not isinstance(password, str)
            or len(password) > 128
        ):
            self.send_json(
                HTTPStatus.UNAUTHORIZED,
                {
                    "error": (
                        "Elektron pochta manzili yoki "
                        "parol noto‘g‘ri."
                    )
                }
            )
            return

        remote = self.client_ip()
        now = time.time()

        recent = [
            stamp
            for stamp in LOGIN_ATTEMPTS.get(remote, [])
            if now - stamp < 900
        ]

        if len(recent) >= 5:
            self.send_json(
                HTTPStatus.TOO_MANY_REQUESTS,
                {
                    "error": (
                        "Ko‘p urinish. 15 daqiqadan so‘ng "
                        "qayta urinib ko‘ring."
                    )
                }
            )
            return

        user = next(
            (
                item
                for item in read_store().get("users", [])
                if item.get("email") == email
            ),
            None,
        )

        if (
            not user
            or not verify_password(
                password,
                user.get("passwordHash")
            )
        ):
            recent.append(now)
            LOGIN_ATTEMPTS[remote] = recent

            self.send_json(
                HTTPStatus.UNAUTHORIZED,
                {
                    "error": (
                        "Elektron pochta manzili yoki "
                        "parol noto‘g‘ri."
                    )
                }
            )
            return

        if user.get("blocked"):
            self.send_json(
                HTTPStatus.FORBIDDEN,
                {
                    "error": (
                        "Bu hisob boshqaruvchi tomonidan "
                        "bloklangan."
                    )
                }
            )
            return

        with STORE_LOCK:
            latest_user = next(
                (
                    item
                    for item in read_store().get("users", [])
                    if item.get("email") == email
                ),
                None,
            )

            if not latest_user or latest_user.get("blocked"):
                self.send_json(
                    HTTPStatus.FORBIDDEN,
                    {
                        "error": (
                            "Bu hisob boshqaruvchi tomonidan "
                            "bloklangan."
                        )
                    }
                )
                return

            LOGIN_ATTEMPTS.pop(remote, None)

            self.send_response(HTTPStatus.OK)
            self.send_header(
                "Content-Type",
                "application/json; charset=utf-8"
            )
            self.send_header("Cache-Control", "no-store")
            self.send_header(
                "X-Content-Type-Options",
                "nosniff"
            )

            set_session(self, "user", email)

            self.end_headers()
            self.wfile.write(b'{"role":"user"}')

    # --------------------------------------------------------
    # ADMIN LOGIN
    # --------------------------------------------------------

    def admin_login(self):
        body = self.read_json()

        if not body or not ADMIN_USERNAME or not ADMIN_PASSWORD:
            self.send_json(
                HTTPStatus.SERVICE_UNAVAILABLE,
                {"error": "Boshqaruvchi hisobi sozlanmagan."}
            )
            return

        remote = self.client_ip()
        now = time.time()

        recent = [
            stamp
            for stamp in LOGIN_ATTEMPTS.get(remote, [])
            if now - stamp < 900
        ]

        if len(recent) >= 5:
            self.send_json(
                HTTPStatus.TOO_MANY_REQUESTS,
                {
                    "error": (
                        "Ko‘p urinish. 15 daqiqadan so‘ng "
                        "qayta urinib ko‘ring."
                    )
                }
            )
            return

        username = body.get("username", "")
        password = body.get("password", "")

        stored_hash = read_store().get("adminPasswordHash")

        password_matches = (
            verify_password(password, stored_hash)
            if isinstance(password, str) and stored_hash
            else (
                isinstance(password, str)
                and hmac.compare_digest(
                    password.encode("utf-8"),
                    ADMIN_PASSWORD.encode("utf-8")
                )
            )
        )

        valid = (
            isinstance(username, str)
            and isinstance(password, str)
            and hmac.compare_digest(
                username.encode("utf-8"),
                ADMIN_USERNAME.encode("utf-8")
            )
            and password_matches
        )

        if not valid:
            recent.append(now)
            LOGIN_ATTEMPTS[remote] = recent

            self.send_json(
                HTTPStatus.UNAUTHORIZED,
                {
                    "error": (
                        "Foydalanuvchi nomi yoki "
                        "parol noto‘g‘ri."
                    )
                }
            )
            return

        LOGIN_ATTEMPTS.pop(remote, None)

        self.send_response(HTTPStatus.OK)
        self.send_header(
            "Content-Type",
            "application/json; charset=utf-8"
        )
        self.send_header("Cache-Control", "no-store")
        self.send_header(
            "X-Content-Type-Options",
            "nosniff"
        )

        set_session(self, "admin")

        self.end_headers()
        self.wfile.write(b'{"role":"admin"}')

    # --------------------------------------------------------
    # ADMIN PAROLINI O'ZGARTIRISH
    # --------------------------------------------------------

    def change_admin_password(self):
        if not self.require_role("admin"):
            return

        body = self.read_json()

        current_password = (
            body.get("currentPassword") if body else None
        )

        new_password = (
            body.get("newPassword") if body else None
        )

        confirm_password = (
            body.get("confirmPassword") if body else None
        )

        if not all(
            isinstance(value, str)
            for value in (
                current_password,
                new_password,
                confirm_password
            )
        ):
            self.send_json(
                HTTPStatus.BAD_REQUEST,
                {
                    "error": (
                        "Barcha parol maydonlarini "
                        "to‘ldiring."
                    )
                }
            )
            return

        if not 8 <= len(new_password) <= 128:
            self.send_json(
                HTTPStatus.BAD_REQUEST,
                {
                    "error": (
                        "Yangi parol 8–128 ta belgidan "
                        "iborat bo‘lsin."
                    )
                }
            )
            return

        if new_password != confirm_password:
            self.send_json(
                HTTPStatus.BAD_REQUEST,
                {"error": "Yangi parollar mos kelmadi."}
            )
            return

        with STORE_LOCK:
            store = read_store()
            stored_hash = store.get("adminPasswordHash")

            if stored_hash:
                current_is_valid = verify_password(
                    current_password,
                    stored_hash
                )
            else:
                current_is_valid = hmac.compare_digest(
                    current_password.encode("utf-8"),
                    ADMIN_PASSWORD.encode("utf-8")
                )

            if not current_is_valid:
                self.send_json(
                    HTTPStatus.UNAUTHORIZED,
                    {"error": "Joriy parol noto‘g‘ri."}
                )
                return

            store["adminPasswordHash"] = hash_password(
                new_password
            )

            write_store(store)

        self.send_json(
            HTTPStatus.OK,
            {
                "ok": True,
                "message": (
                    "Boshqaruvchi paroli muvaffaqiyatli "
                    "o‘zgartirildi."
                )
            }
        )

    # --------------------------------------------------------
    # FOYDALANUVCHINI BLOKLASH / BLOKDAN CHIQARISH
    # --------------------------------------------------------

    def update_user_block_status(self):
        if not self.require_role("admin"):
            return

        body = self.read_json()

        email = (
            body.get("email", "").strip().lower()
            if body and isinstance(body.get("email"), str)
            else ""
        )

        blocked = body.get("blocked") if body else None

        if not valid_email(email) or not isinstance(blocked, bool):
            self.send_json(
                HTTPStatus.BAD_REQUEST,
                {
                    "error": (
                        "Foydalanuvchi va blok holatini "
                        "to‘g‘ri ko‘rsating."
                    )
                }
            )
            return

        with STORE_LOCK:
            store = read_store()

            user = next(
                (
                    item
                    for item in store.get("users", [])
                    if item.get("email") == email
                ),
                None,
            )

            if not user:
                self.send_json(
                    HTTPStatus.NOT_FOUND,
                    {
                        "error": (
                            "Tasdiqlangan foydalanuvchi "
                            "topilmadi."
                        )
                    }
                )
                return

            user["blocked"] = blocked

            write_store(store)

            if blocked:
                for session_key, session in list(SESSIONS.items()):
                    if (
                        session.get("role") == "user"
                        and session.get("userId") == email
                    ):
                        SESSIONS.pop(session_key, None)

        self.send_json(
            HTTPStatus.OK,
            {
                "email": email,
                "blocked": blocked
            }
        )

    # --------------------------------------------------------
    # RO'YXATDAN O'TISH SO'ROVINI TASDIQLASH / RAD ETISH
    # --------------------------------------------------------

    def update_request(self, path, status):
        if not self.require_role("admin"):
            return

        action = (
            "approve" if status == "approved" else "reject"
        )

        match = re.fullmatch(
            rf"/api/admin/requests/([a-f0-9]+)/{action}",
            path
        )

        if not match:
            self.send_json(
                HTTPStatus.BAD_REQUEST,
                {"error": "So‘rov manzili noto‘g‘ri."}
            )
            return

        with STORE_LOCK:
            store = read_store()

            request = next(
                (
                    item
                    for item in store["requests"]
                    if item["id"] == match.group(1)
                ),
                None,
            )

            if not request:
                self.send_json(
                    HTTPStatus.NOT_FOUND,
                    {"error": "So‘rov topilmadi."}
                )
                return

            if (
                status == "approved"
                and request.get("email")
                and request.get("passwordHash")
            ):
                if any(
                    user.get("email") == request["email"]
                    for user in store.get("users", [])
                ):
                    self.send_json(
                        HTTPStatus.CONFLICT,
                        {
                            "error": (
                                "Bu elektron pochta manzili "
                                "uchun hisob allaqachon mavjud."
                            )
                        }
                    )
                    return

                store.setdefault("users", []).append({
                    "email": request["email"],
                    "name": request["name"],
                    "passwordHash": request["passwordHash"],
                    "createdAt": request["createdAt"],
                    "approvedAt": int(time.time()),
                })

            request.pop("passwordHash", None)
            request["status"] = status

            write_store(store)

        self.send_json(
            HTTPStatus.OK,
            {"status": status}
        )

    # --------------------------------------------------------
    # VIDEO QO'SHISH
    # --------------------------------------------------------

    def add_video(self):
        if not self.require_role("admin"):
            return

        body = self.read_json()

        raw_title = body.get("title") if body else None
        raw_url = body.get("url") if body else None
        raw_category = body.get("category") if body else None

        if (
            not isinstance(raw_title, str)
            or not isinstance(raw_url, str)
            or not isinstance(raw_category, str)
        ):
            self.send_json(
                HTTPStatus.BAD_REQUEST,
                {
                    "error": (
                        "Video nomi, bo‘limi va HTTPS "
                        "manzilini kiriting."
                    )
                }
            )
            return

        title = raw_title.strip()
        url = raw_url.strip()
        category = raw_category.strip()

        if (
            not 1 <= len(title) <= 120
            or not 1 <= len(category) <= 60
            or not video_source(url)
        ):
            self.send_json(
                HTTPStatus.BAD_REQUEST,
                {
                    "error": (
                        "Video nomi, bo‘limi va HTTPS "
                        "manzilini to‘g‘ri kiriting."
                    )
                }
            )
            return

        video = {
            "id": secrets.token_hex(12),
            "title": title,
            "url": url,
            "category": category,
            "createdAt": int(time.time()),
        }

        with STORE_LOCK:
            store = read_store()
            store["videos"].append(video)
            write_store(store)

        self.send_json(
            HTTPStatus.CREATED,
            {"video": video}
        )

    # --------------------------------------------------------
    # VIDEO O'CHIRISH
    # --------------------------------------------------------

    def delete_video(self, path):
        if not self.require_role("admin"):
            return

        match = re.fullmatch(
            r"/api/videos/([a-f0-9]+)",
            path
        )

        if not match:
            self.send_json(
                HTTPStatus.BAD_REQUEST,
                {"error": "Video manzili noto‘g‘ri."}
            )
            return

        with STORE_LOCK:
            store = read_store()
            videos = store["videos"]

            remaining = [
                video
                for video in videos
                if video["id"] != match.group(1)
            ]

            if len(remaining) == len(videos):
                self.send_json(
                    HTTPStatus.NOT_FOUND,
                    {"error": "Video topilmadi."}
                )
                return

            store["videos"] = remaining
            write_store(store)

        self.send_json(
            HTTPStatus.OK,
            {"ok": True}
        )

    # --------------------------------------------------------
    # TIZIMDAN CHIQISH
    # --------------------------------------------------------

    def logout(self):
        cookie = SimpleCookie()

        try:
            cookie.load(self.headers.get("Cookie", ""))
        except CookieError:
            pass

        morsel = cookie.get(COOKIE_NAME)

        if morsel:
            SESSIONS.pop(
                key_hash(morsel.value),
                None
            )

        self.send_response(HTTPStatus.OK)

        self.send_header(
            "Content-Type",
            "application/json; charset=utf-8"
        )

        self.send_header(
            "Set-Cookie",
            (
                f"{COOKIE_NAME}=; Path=/; HttpOnly; "
                "SameSite=Strict; Max-Age=0"
            )
        )

        self.end_headers()
        self.wfile.write(b'{"ok":true}')


# ============================================================
# SERVERNI ISHGA TUSHIRISH
# ============================================================

if __name__ == "__main__":

    if not ADMIN_USERNAME or not ADMIN_PASSWORD:
        raise SystemExit(
            "Boshqaruvchi hisobi sozlanmagan. "
            ".env faylida ADMIN_USERNAME va "
            "ADMIN_PASSWORD qiymatlarini kiriting."
        )

    # Neon PostgreSQL bazasini tayyorlash
    initialize_database()

    # Bazadan ma'lumot o'qilishini tekshirish
    read_store()

    host = os.environ.get(
        "HOST",
        (
            "0.0.0.0"
            if os.environ.get("RENDER_EXTERNAL_HOSTNAME")
            else "127.0.0.1"
        )
    )

    port = int(
        os.environ.get(
            "PORT",
            (
                "10000"
                if os.environ.get("RENDER_EXTERNAL_HOSTNAME")
                else "8000"
            )
        )
    )

    address = os.environ.get(
        "RENDER_EXTERNAL_URL",
        f"http://{host}:{port}"
    )

    print(f"Sinfdoshlar sayti: {address}")
    print("Neon PostgreSQL bazasi bilan aloqa o'rnatildi.")

    ThreadingHTTPServer(
        (host, port),
        SiteHandler
    ).serve_forever()
