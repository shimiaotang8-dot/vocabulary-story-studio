"""Local companion server for Vocabulary Story Studio."""
from __future__ import annotations

import json
import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import threading
import time
import uuid
from datetime import date
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
PORT = int(os.getenv("WORDSTORY_PORT", "8765"))
_ENV_LOADED = False
_ENV_LOCK = threading.Lock()
DB_PATH = ROOT / "wordstory.sqlite3"
SESSION_COOKIE = "wordstory_session"


def load_dotenv() -> None:
    """Load project environment values without requiring python-dotenv."""
    global _ENV_LOADED
    if _ENV_LOADED:
        return
    with _ENV_LOCK:
        if _ENV_LOADED:
            return
        _load_dotenv_file()
        _ENV_LOADED = True


def _load_dotenv_file() -> None:
    # User-specific credentials live in the project-root .env. Keep the older
    # RAG project's .env available as a backwards-compatible fallback only.
    for env_file in (ROOT / ".env", ROOT / "rag-learning" / ".env"):
        if not env_file.exists():
            continue
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"\''))


def make_prompt(payload: dict) -> str:
    words = [str(w).strip() for w in payload.get("words", []) if str(w).strip()]
    mode = payload.get("mode", "zh")
    background = payload.get("background") or {}
    custom = str(payload.get("customPrompt", "")).strip()
    word_lines = "\n".join(f"- {word}" for word in words)
    scene = "由你根据词义、词性和联想自行判断最合适的故事背景；从第一词到最后一词构成连贯的章节式叙事。"
    if background.get("description"):
        scene = f"使用用户指定背景：{background.get('name', '')}。{background['description']}"
    requirements = [
        f"请为英语学习者写一篇完整、自然、逻辑连贯、有起承转合、读起来像小说章节的短篇故事。",
        f"语言模式：{'中文叙事，英语目标词自然嵌入' if mode == 'zh' else '英语叙事，英语目标词后紧跟简短中文释义'}。",
        "必须逐字包含清单里的每一个目标词，大小写可以不同，但拼写不能改变；每个词至少出现一次，且要有语义地融入情节。不要把词汇表简单堆在一起。",
        "中文模式下，每个目标词的第一次出现格式为：target（简明中文释义）。释义简洁、准确。",
        "英文模式下，在目标词的第一次出现后立即附上中文释义，格式为：target（简明中文释义）。其余叙述使用自然、正确的英文。",
        "故事应是一章完整的小说叙事，而非例句或词语清单。情节、人物动机、因果关系和结尾都要合理。",
        scene,
        f"目标词清单：\n{word_lines}",
    ]
    if custom:
        requirements.append(f"用户的额外要求：{custom}")
    requirements.append('仅返回 JSON 对象，格式为 {"title":"故事标题","paragraphs":["段落一","段落二"]}。不要返回 Markdown 代码块。')
    return "\n\n".join(requirements)


def make_prompt(payload: dict) -> str:
    """Build the complete, structured story-generation prompt."""
    words = [str(w).strip() for w in payload.get("words", []) if str(w).strip()]
    mode = payload.get("mode", "zh")
    background = payload.get("background") or {}
    custom = str(payload.get("customPrompt", "")).strip()
    mode_spec = (
        "【中文故事 + 英语目标词】主体叙述使用自然、成熟的简体中文。目标词必须保留英文原拼写，并自然嵌入中文句子。每个目标词第一次出现时，紧跟全角括号中文释义，例如：他必须 obtrude（打断；打扰）她的疯狂想法。不要把整个句子翻译成英文。"
        if mode == "zh" else
        "【英文故事 + 英语目标词】主体叙述使用自然、语法正确、适合英语学习者阅读的英文。目标词保留英文原拼写，并自然嵌入情节。每个目标词第一次出现时，紧跟中文括号释义，例如：He knew he had to obtrude（打断；打扰） on her thoughts. 后续重复该词无需重复释义。"
    )
    if background.get("description"):
        background_spec = f"用户指定背景名称：{background.get('name', '自定义背景')}\n背景细节：{background['description']}\n遵守背景的时代、地点、氛围与设定；如与目标词冲突，优先创造合理的情节解释。"
    else:
        background_spec = "未指定背景。先从所有目标词的词义、词性、语域、情绪色彩和联想中提炼共同主题，再选择最合适的地点、时代、类型和氛围；将背景自然融入故事，不要向用户解释你的推理过程。"
    word_lines = "\n".join(f"{index}. {word}" for index, word in enumerate(words, 1))
    custom_spec = custom if custom else "无。请完全依据目标词与所选背景创作。"
    return f"""# 角色
你是一位兼具小说创作能力与英语教学经验的双语作家。你写的是有文学可读性的完整短篇小说章节，同时帮助读者通过上下文掌握目标词。

# 核心任务
根据用户给出的全部英语目标词，创作一篇逻辑连贯、人物可信、因果清楚、有起承转合且结尾完整的故事。目标词不是装饰或清单：每个词都必须在语义正确的语境中推动人物、冲突、线索、选择或结果。把它们编织成一条情节链，让读者读完能回忆起词义和故事。

# 语言模式
{mode_spec}

# 故事背景
{background_spec}

# 故事结构与质量标准
1. 写成一个有章节感的短篇故事，而不是词汇例句、提纲、对话拼盘、词义列表或学习说明。
2. 建立明确的主角、当下目标、阻碍或悬念。让事件按“起因 → 行动 → 阻力/发现 → 决定 → 结果”自然推进；避免突然转场、无因果反转和未解决的关键矛盾。
3. 目标词必须通过自然的句法和语义融入正文。不要为了覆盖词汇而把多个词硬塞进一句话，也不要在正文末尾额外罗列单词。
4. 必须包含下列每一个目标词的完整拼写，大小写可按句首语法调整；不能改成派生词、同义词、翻译或截断形式来代替。每个词至少出现一次。对于短词/常见词，也要确认出现的是完整独立词，不可仅作为另一个更长单词的字母子串。
5. 在目标词第一次出现处添加简短准确的中文释义，格式严格遵循所选语言模式；释义结合当前上下文选义项。目标词本身必须保持为英文。
6. 释义不确定时，依据词在句中的词性和语境给出常见、保守的中文解释；不要编造词义。若目标词有多个常见义项，选择故事实际使用的一个。
7. 情节内容要服务于记忆。可使用意象、对比、重复出现的物件/线索和人物选择，但避免说教、陈词滥调、过度堆砌形容词或为了戏剧性牺牲逻辑。
8. 正文建议 4–7 个自然段，中文约 450–800 个汉字，英文约 350–650 个英文词；目标词很多时可适当加长，但不缩减词汇覆盖。若词汇较少，也应保证故事有完整情节而非重复灌水。
9. 标题要简洁、有画面感，匹配故事气质。标题和 JSON 键名不计入目标词覆盖检查，目标词必须出现在 paragraphs 正文中。

# 用户额外要求
{custom_spec}
额外要求只在不违反目标词覆盖、选定语言模式和故事逻辑的前提下采纳。若额外要求与硬性规则冲突，遵守硬性规则。

# 目标词清单（必须逐项核对）
{word_lines}

# 输出格式
只输出一个有效 JSON 对象，不要 Markdown 代码围栏、前言、解释或附加字段。格式如下：
{{
  "title": "故事标题",
  "paragraphs": ["第一段正文", "第二段正文", "第三段正文"]
}}

# 输出前的静默自检
生成后先自行检查，但不要输出检查过程：
- paragraphs 中是否逐字包含清单内全部目标词（独立完整词，而非子串）？
- 每个目标词首次出现是否紧跟准确中文释义，且格式符合模式？
- 词是否被自然、有意义地嵌入情节？
- 人物动机、事件因果、转折和结尾是否连贯？
- 返回内容是否是可解析的 JSON，且仅有 title、paragraphs 两个字段？
发现遗漏或错误时先修订，再返回最终 JSON。"""


def ai_config() -> tuple[str, str, str, str]:
    provider = os.getenv("AI_PROVIDER", "DeepSeek").strip() or "AI provider"
    key = (os.getenv("AI_API_KEY") or os.getenv("DEEPSEEK_API_KEY", "")).strip()
    base_url = (os.getenv("AI_BASE_URL") or os.getenv("DEEPSEEK_BASE_URL") or "https://api.deepseek.com").rstrip("/")
    model = (os.getenv("AI_MODEL") or os.getenv("DEEPSEEK_MODEL") or "deepseek-chat").strip()
    return provider, key, base_url, model


def generate_with_ai(payload: dict) -> dict:
    provider, key, base_url, model = ai_config()
    if not key:
        raise RuntimeError("未配置 AI API Key：请在项目根目录 .env 文件中填写 AI_API_KEY")
    messages = [
        {"role": "system", "content": "你是严谨的双语小说作家与提示词规则执行器。将用户消息中的规则作为创作规范，并按其中的 JSON 结构返回。"},
        {"role": "user", "content": make_prompt(payload)},
    ]
    request_data = {
        "model": model,
        "messages": messages,
        "temperature": 0.8,
        "max_tokens": 3000,
        "response_format": {"type": "json_object"},
    }
    req = Request(
        f"{base_url}/chat/completions",
        data=json.dumps(request_data, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(req, timeout=90) as response:
        envelope = json.loads(response.read().decode("utf-8"))
    content = envelope["choices"][0]["message"]["content"]
    if isinstance(content, list):
        content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
    if isinstance(content, str):
        content = content.strip()
        content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content, flags=re.I)
        content = json.loads(content)
    if not isinstance(content, dict):
        raise RuntimeError("AI 返回格式无法识别")
    return content


def lookup_meanings(words: list[str]) -> dict[str, str]:
    """Cache concise Chinese glosses; make one low-token DeepSeek call for misses."""
    unique = list(dict.fromkeys(str(w).strip()[:100] for w in words if str(w).strip()))[:80]
    if not unique:
        return {}
    with db_connect() as db:
        rows = db.execute(f"SELECT word,meaning FROM meaning_cache WHERE word IN ({','.join('?' for _ in unique)})", unique).fetchall()
    cached = {r['word'].casefold(): r['meaning'] for r in rows}
    missing = [w for w in unique if w.casefold() not in cached]
    _provider, key, base_url, model = ai_config()
    if not missing or not key:
        return {w: cached[w.casefold()] for w in unique if w.casefold() in cached}
    prompt = ('你是英汉词典编辑。给英语学习者提供准确、简短的中文释义。优先给最常见义项；多词性用分号隔开，短语按整体解释；拼写错误或无法判断时给空字符串，不猜测。仅返回 JSON 对象，键必须是输入原词，值是中文释义。词表：' + json.dumps(missing, ensure_ascii=False))
    req_data = {'model': model, 'messages':[{'role':'system','content':'你是严谨的英汉词典，不编造释义，只输出 JSON。'},{'role':'user','content':prompt}], 'temperature':0, 'max_tokens':min(2000, 70*len(missing)+250), 'response_format':{'type':'json_object'}}
    url = base_url + '/chat/completions'
    req = Request(url, data=json.dumps(req_data,ensure_ascii=False).encode('utf-8'), headers={'Authorization':f'Bearer {key}','Content-Type':'application/json'},method='POST')
    with urlopen(req,timeout=45) as response:
        content=json.loads(response.read().decode('utf-8'))['choices'][0]['message']['content']
    if isinstance(content,str):
        content=json.loads(re.sub(r'^```(?:json)?\s*|\s*```$','',content.strip(),flags=re.I))
    if not isinstance(content,dict):
        return {w:cached[w.casefold()] for w in unique if w.casefold() in cached}
    resolved={}
    with db_connect() as db:
        for word in missing:
            value=next((v for k,v in content.items() if str(k).casefold()==word.casefold()),'')
            meaning=str(value).strip()[:80] if value else ''
            if meaning:
                db.execute('INSERT INTO meaning_cache(word,meaning,updated_at) VALUES(?,?,?) ON CONFLICT(word) DO UPDATE SET meaning=excluded.meaning,updated_at=excluded.updated_at',(word,meaning,int(time.time())))
                resolved[word.casefold()]=meaning
    return {w:(cached.get(w.casefold()) or resolved.get(w.casefold())) for w in unique if cached.get(w.casefold()) or resolved.get(w.casefold())}


def db_connect():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    with db_connect() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            username TEXT NOT NULL UNIQUE COLLATE NOCASE,
            password_hash TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            total_words_added INTEGER NOT NULL DEFAULT 0,
            stories_generated INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS sessions (
            token_hash TEXT PRIMARY KEY,
            user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            expires_at INTEGER NOT NULL,
            created_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS vocab (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            word TEXT NOT NULL COLLATE NOCASE,
            created_at INTEGER NOT NULL,
            added_date TEXT NOT NULL DEFAULT '',
            UNIQUE(user_id, word)
        );
        CREATE TABLE IF NOT EXISTS shelf (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            title TEXT NOT NULL,
            mode TEXT NOT NULL,
            paragraphs_json TEXT NOT NULL,
            words_json TEXT NOT NULL,
            background_name TEXT NOT NULL DEFAULT '',
            created_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS meaning_cache (
            word TEXT PRIMARY KEY COLLATE NOCASE,
            meaning TEXT NOT NULL,
            updated_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS generation_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            title TEXT NOT NULL,
            mode TEXT NOT NULL,
            word_count INTEGER NOT NULL,
            created_at INTEGER NOT NULL
        );
        """)
        user_columns = {row[1] for row in db.execute("PRAGMA table_info(users)")}
        if "total_words_added" not in user_columns:
            db.execute("ALTER TABLE users ADD COLUMN total_words_added INTEGER NOT NULL DEFAULT 0")
            db.execute("UPDATE users SET total_words_added=(SELECT COUNT(*) FROM vocab WHERE vocab.user_id=users.id)")
        if "stories_generated" not in user_columns:
            db.execute("ALTER TABLE users ADD COLUMN stories_generated INTEGER NOT NULL DEFAULT 0")
            db.execute("UPDATE users SET stories_generated=(SELECT COUNT(*) FROM shelf WHERE shelf.user_id=users.id)")
        columns = {row[1] for row in db.execute("PRAGMA table_info(vocab)")}
        if "added_date" not in columns:
            db.execute("ALTER TABLE vocab ADD COLUMN added_date TEXT NOT NULL DEFAULT ''")
        db.execute("UPDATE vocab SET added_date=substr(datetime(created_at,'unixepoch','localtime'),1,10) WHERE added_date='' ")


def password_hash(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1)
    return f"{salt.hex()}:{digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        salt_hex, expected = encoded.split(":", 1)
        candidate = password_hash(password, bytes.fromhex(salt_hex)).split(":", 1)[1]
        return hmac.compare_digest(candidate, expected)
    except (ValueError, TypeError):
        return False


def cookie_token(handler) -> str | None:
    cookie = handler.headers.get("Cookie", "")
    for part in cookie.split(";"):
        name, sep, value = part.strip().partition("=")
        if sep and name == SESSION_COOKIE:
            return value
    return None


def current_user(handler):
    token = cookie_token(handler)
    if not token:
        return None
    digest = hashlib.sha256(token.encode()).hexdigest()
    now = int(time.time())
    with db_connect() as db:
        row = db.execute("""SELECT users.id, users.username FROM sessions
                           JOIN users ON users.id=sessions.user_id
                           WHERE sessions.token_hash=? AND sessions.expires_at>?""", (digest, now)).fetchone()
        db.execute("DELETE FROM sessions WHERE expires_at<=?", (now,))
    return dict(row) if row else None


def issue_session(handler, user_id: str, days: int):
    token = secrets.token_urlsafe(32)
    now = int(time.time())
    expires = now + days * 86400
    digest = hashlib.sha256(token.encode()).hexdigest()
    with db_connect() as db:
        db.execute("INSERT INTO sessions(token_hash,user_id,expires_at,created_at) VALUES(?,?,?,?)", (digest, user_id, expires, now))
    handler.pending_cookie = f"{SESSION_COOKIE}={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age={days * 86400}"


def read_json(handler, limit=32_000):
    length = int(handler.headers.get("Content-Length", "0"))
    if length > limit:
        raise OverflowError("请求内容过长")
    return json.loads(handler.rfile.read(length).decode("utf-8"))


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def do_POST(self):
        try:
            load_dotenv()
            if self.path == "/api/auth/register":
                self._register(read_json(self, 5000))
            elif self.path == "/api/auth/login":
                self._login(read_json(self, 5000))
            elif self.path == "/api/auth/logout":
                self._logout()
            elif self.path == "/api/vocab":
                self._vocab(read_json(self, 12000))
            elif self.path == "/api/shelf":
                self._save_shelf(read_json(self, 64000))
            elif self.path.startswith("/api/shelf/delete/"):
                self._delete_shelf(self.path.rsplit("/", 1)[-1])
            elif self.path == "/api/story":
                payload = read_json(self)
                words = payload.get("words")
                if not isinstance(words, list) or not words or len(words) > 40:
                    self._json(400, {"error": "请提供 1 到 40 个目标词"})
                    return
                result = generate_with_ai(payload)
                self._json(200, result)
            elif self.path == "/api/meanings":
                payload = read_json(self, 16000)
                words = payload.get("words", [])
                if not isinstance(words, list):
                    self._json(400, {"error": "词汇列表格式无效"})
                    return
                self._json(200, {"meanings": lookup_meanings(words[:80])})
            elif self.path == "/api/usage/generation":
                self._record_generation(read_json(self, 5000))
            else:
                self.send_error(404)
        except OverflowError as exc:
            self._json(413, {"error": str(exc)})
        except sqlite3.IntegrityError:
            self._json(409, {"error": "用户名已存在，或数据重复"})
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            print(f"AI HTTP {exc.code}: {detail}")
            self._json(502, {"error": f"{ai_config()[0]} API 请求失败（HTTP {exc.code}）"})
        except (URLError, TimeoutError) as exc:
            print(f"AI network error: {exc}")
            self._json(502, {"error": f"无法连接 {ai_config()[0]} API，请检查网络"})
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._json(400, {"error": "请求不是有效 JSON"})
        except RuntimeError as exc:
            self._json(503, {"error": str(exc)})
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            print(f"AI response parse error: {exc}")
            self._json(400, {"error": "请求参数无效或 DeepSeek 返回内容解析失败"})

    def do_GET(self):
        if self.path == "/api/auth/me":
            self._json(200, {"user": current_user(self)})
        elif self.path == "/api/auth/stats":
            user = current_user(self)
            if not user:
                self._json(401, {"error": "请先登录"})
                return
            with db_connect() as db:
                row = db.execute("SELECT total_words_added,stories_generated,(SELECT COUNT(*) FROM vocab WHERE user_id=users.id) AS vocab_count,(SELECT COUNT(*) FROM shelf WHERE user_id=users.id) AS shelf_count FROM users WHERE id=?", (user['id'],)).fetchone()
                recent = db.execute("SELECT title,mode,word_count,created_at FROM generation_events WHERE user_id=? ORDER BY created_at DESC,id DESC LIMIT 6", (user['id'],)).fetchall()
            self._json(200, {"stats": {"wordsAdded": row['total_words_added'], "vocabCount": row['vocab_count'], "storiesGenerated": row['stories_generated'], "shelfCount": row['shelf_count']}, "recent": [{"title": r['title'], "mode": r['mode'], "wordCount": r['word_count'], "createdAt": r['created_at']} for r in recent]})
        elif self.path == "/api/vocab":
            user = current_user(self)
            if not user:
                self._json(401, {"error": "请先登录"})
                return
            with db_connect() as db:
                rows = db.execute("SELECT word,added_date FROM vocab WHERE user_id=? ORDER BY added_date DESC,word COLLATE NOCASE ASC", (user["id"],)).fetchall()
            self._json(200, {"words": [{"word": r["word"], "date": r["added_date"]} for r in rows]})
        elif self.path == "/api/shelf":
            user = current_user(self)
            if not user:
                self._json(401, {"error": "请先登录"})
                return
            with db_connect() as db:
                rows = db.execute("SELECT * FROM shelf WHERE user_id=? ORDER BY created_at DESC", (user["id"],)).fetchall()
            stories = [{"id": r["id"], "title": r["title"], "mode": r["mode"], "paragraphs": json.loads(r["paragraphs_json"]), "words": json.loads(r["words_json"]), "background": r["background_name"], "createdAt": r["created_at"]} for r in rows]
            self._json(200, {"stories": stories})
        else:
            super().do_GET()

    def _register(self, payload):
        username = str(payload.get("username", "")).strip()
        password = str(payload.get("password", ""))
        if not re.fullmatch(r"[\w\u3400-\u9fff.-]{3,24}", username, re.UNICODE):
            self._json(400, {"error": "用户名需为 3–24 位，可用中文、字母、数字、下划线、点或连字符"})
            return
        if len(password) < 8 or len(password) > 128:
            self._json(400, {"error": "密码长度需要为 8–128 位"})
            return
        user_id = str(uuid.uuid4())
        with db_connect() as db:
            db.execute("INSERT INTO users(id,username,password_hash,created_at) VALUES(?,?,?,?)", (user_id, username, password_hash(password), int(time.time())))
        days = 30 if payload.get("rememberDays") == 30 else 7
        issue_session(self, user_id, days)
        self._json(201, {"user": {"id": user_id, "username": username}, "rememberDays": days})

    def _login(self, payload):
        username = str(payload.get("username", "")).strip()
        password = str(payload.get("password", ""))
        with db_connect() as db:
            row = db.execute("SELECT id,username,password_hash FROM users WHERE username=? COLLATE NOCASE", (username,)).fetchone()
        if not row or not verify_password(password, row["password_hash"]):
            self._json(401, {"error": "用户名或密码不正确"})
            return
        days = 30 if payload.get("rememberDays") == 30 else 7
        issue_session(self, row["id"], days)
        self._json(200, {"user": {"id": row["id"], "username": row["username"]}, "rememberDays": days})

    def _logout(self):
        token = cookie_token(self)
        if token:
            digest = hashlib.sha256(token.encode()).hexdigest()
            with db_connect() as db:
                db.execute("DELETE FROM sessions WHERE token_hash=?", (digest,))
        self.pending_cookie = f"{SESSION_COOKIE}=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0"
        self._json(200, {"ok": True})

    def _vocab(self, payload):
        user = current_user(self)
        if not user:
            self._json(401, {"error": "请先登录"})
            return
        action = payload.get("action")
        with db_connect() as db:
            if action == "add":
                words = payload.get("words", [])
                if not isinstance(words, list):
                    self._json(400, {"error": "词汇格式无效"})
                    return
                for raw in words[:200]:
                    word = str(raw).strip()[:100]
                    if word:
                        cur = db.execute("INSERT OR IGNORE INTO vocab(user_id,word,created_at,added_date) VALUES(?,?,?,?)", (user["id"], word, int(time.time()), date.today().isoformat()))
                        if cur.rowcount:
                            db.execute("UPDATE users SET total_words_added=total_words_added+1 WHERE id=?", (user['id'],))
            elif action == "delete":
                db.execute("DELETE FROM vocab WHERE user_id=? AND word=? COLLATE NOCASE", (user["id"], str(payload.get("word", ""))))
            elif action == "delete_many":
                words = payload.get("words", [])
                if isinstance(words, list):
                    db.executemany("DELETE FROM vocab WHERE user_id=? AND word=? COLLATE NOCASE", [(user["id"], str(word)) for word in words[:500]])
            elif action == "clear":
                db.execute("DELETE FROM vocab WHERE user_id=?", (user["id"],))
        with db_connect() as db:
            rows = db.execute("SELECT word,added_date FROM vocab WHERE user_id=? ORDER BY added_date DESC,word COLLATE NOCASE ASC", (user["id"],)).fetchall()
        self._json(200, {"words": [{"word": r["word"], "date": r["added_date"]} for r in rows]})

    def _save_shelf(self, payload):
        user = current_user(self)
        if not user:
            self._json(401, {"error": "请先登录"})
            return
        title = str(payload.get("title", "")).strip()[:160]
        paragraphs, words = payload.get("paragraphs"), payload.get("words")
        if not title or not isinstance(paragraphs, list) or not isinstance(words, list):
            self._json(400, {"error": "故事内容不完整"})
            return
        story_id = str(uuid.uuid4())
        with db_connect() as db:
            db.execute("INSERT INTO shelf(id,user_id,title,mode,paragraphs_json,words_json,background_name,created_at) VALUES(?,?,?,?,?,?,?,?)", (story_id, user["id"], title, str(payload.get("mode", "zh")), json.dumps(paragraphs, ensure_ascii=False), json.dumps(words, ensure_ascii=False), str(payload.get("background", ""))[:120], int(time.time())))
        self._json(201, {"id": story_id})

    def _record_generation(self, payload):
        user = current_user(self)
        if not user:
            self._json(401, {"error": "请先登录"})
            return
        title = str(payload.get('title', '')).strip()[:160]
        mode = 'en' if payload.get('mode') == 'en' else 'zh'
        try:
            word_count = max(0, min(200, int(payload.get('wordCount', 0))))
        except (ValueError, TypeError):
            word_count = 0
        if not title:
            self._json(400, {"error": "缺少故事标题"})
            return
        now = int(time.time())
        with db_connect() as db:
            db.execute("INSERT INTO generation_events(user_id,title,mode,word_count,created_at) VALUES(?,?,?,?,?)", (user['id'], title, mode, word_count, now))
            db.execute("UPDATE users SET stories_generated=stories_generated+1 WHERE id=?", (user['id'],))
        self._json(201, {"ok": True})

    def _delete_shelf(self, story_id):
        user = current_user(self)
        if not user:
            self._json(401, {"error": "请先登录"})
            return
        with db_connect() as db:
            db.execute("DELETE FROM shelf WHERE id=? AND user_id=?", (story_id, user["id"]))
        self._json(200, {"ok": True})

    def _json(self, status: int, value: dict) -> None:
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if hasattr(self, "pending_cookie"):
            self.send_header("Set-Cookie", self.pending_cookie)
            del self.pending_cookie
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        print(f"[{self.log_date_time_string()}] {fmt % args}")

    def end_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        super().end_headers()


if __name__ == "__main__":
    load_dotenv()
    init_db()
    if not ai_config()[1]:
        print("提示：项目根目录 .env 未提供 AI_API_KEY，网页会使用本地备用故事。")
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"词间故事工作台运行于 http://127.0.0.1:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n服务器已停止。")
    finally:
        server.server_close()
