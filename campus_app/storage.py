"""Campus state and credentials are initialized in one SQLite transaction."""
from copy import deepcopy
import hashlib
import json

from duty_app.storage import Repository as BaseRepository, RevisionConflict
from .engine import RuleError, empty_state, identifier


class Repository(BaseRepository):
    def __init__(self, path):
        super().__init__(path, initial_state=empty_state)
        with self.connection() as db:
            db.execute("CREATE TABLE IF NOT EXISTS requests (id TEXT PRIMARY KEY, digest TEXT NOT NULL)")
        if self.read()[1].get("schema_version") != 2:
            raise RuleError("请选择校园版独立数据目录，此目录不是校园版数据库")

    def initialize(self, state, password):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                revision, payload = db.execute("SELECT revision,payload FROM state WHERE singleton=1").fetchone()
                if revision or json.loads(payload)["configs"] or db.execute("SELECT 1 FROM metadata WHERE key='password'").fetchone():
                    raise RuleError("初始化已完成，请登录管理端")
                db.execute("UPDATE state SET revision=1,payload=? WHERE singleton=1", (json.dumps(state, ensure_ascii=False),))
                db.execute("INSERT INTO metadata VALUES('password',?)", (json.dumps(password),))
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise

    def mutate_request(self, operation, body, transform):
        request_id = identifier(body.get("request_id"))
        digest = hashlib.sha256(json.dumps([operation, {k: v for k, v in body.items() if k not in ("revision", "request_id")}],
                                           ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                revision, payload = db.execute("SELECT revision,payload FROM state WHERE singleton=1").fetchone()
                previous = db.execute("SELECT digest FROM requests WHERE id=?", (request_id,)).fetchone()
                if previous:
                    if previous[0] != digest:
                        raise RuleError("请求标识已被其他操作使用")
                    db.execute("COMMIT")
                    return revision, json.loads(payload)
                expected = body.get("revision")
                if isinstance(expected, bool) or not isinstance(expected, int) or expected != revision:
                    raise RevisionConflict("其他终端已更新，请刷新后重试；当前表单已保留")
                state = json.loads(payload)
                before = deepcopy(state)
                updated = transform(state)
                if updated != before:
                    revision += 1
                    db.execute("UPDATE state SET revision=?,payload=? WHERE singleton=1",
                               (revision, json.dumps(updated, ensure_ascii=False)))
                db.execute("INSERT INTO requests VALUES(?,?)", (request_id, digest))
                db.execute("COMMIT")
                return revision, updated
            except BaseException:
                db.execute("ROLLBACK")
                raise

    def set_meta(self, key, value):
        with self.connection() as db:
            db.execute("INSERT OR REPLACE INTO metadata VALUES(?,?)", (key, json.dumps(value, ensure_ascii=False)))
