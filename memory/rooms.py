import json
import re
from datetime import datetime
from pathlib import Path


BASE_DIR = Path.home() / ".local" / "share" / "eros" / "rooms"


def _safe_name(name: str) -> str:
    return re.sub(r"[^\w\-]", "_", name).strip("_") or "default"


def _room_path(name: str) -> Path:
    return BASE_DIR / f"{_safe_name(name)}.jsonl"


_SESSION_FILE = BASE_DIR.parent / "last_room"


def init() -> None:
    BASE_DIR.mkdir(parents=True, exist_ok=True)


def save_last_room(room: str) -> None:
    _SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
    _SESSION_FILE.write_text(room)


def load_last_room() -> str | None:
    try:
        if _SESSION_FILE.exists():
            name = _SESSION_FILE.read_text().strip()
            if name and _room_path(name).exists():
                return name
    except OSError:
        pass
    return None


def list_rooms() -> list[dict]:
    init()
    result = []
    for f in sorted(BASE_DIR.glob("*.jsonl"), key=lambda x: x.stat().st_mtime, reverse=True):
        lines = [l for l in f.read_text().splitlines() if l.strip()]
        last_msg = ""
        last_ts = ""
        if lines:
            try:
                entry = json.loads(lines[-1])
                last_msg = entry.get("user", "")
                raw_ts = entry.get("ts", "")
                if raw_ts:
                    last_ts = raw_ts[:16].replace("T", " ")
            except (json.JSONDecodeError, KeyError):
                pass
        result.append({
            "name": f.stem,
            "turns": len(lines),
            "mtime": f.stat().st_mtime,
            "last": datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d %H:%M"),
            "last_msg": last_msg,
            "last_ts": last_ts,
        })
    return result


def save_turn(room: str, model: str, user: str, assistant: str) -> None:
    init()
    p = _room_path(room)
    entry = {"ts": datetime.now().isoformat(), "model": model, "user": user, "assistant": assistant}
    with open(p, "a") as f:
        f.write(json.dumps(entry) + "\n")


def load_turns(room: str, max_turns: int = 10) -> list[dict]:
    p = _room_path(room)
    if not p.exists():
        return []
    lines = [l for l in p.read_text().splitlines() if l.strip()]
    turns = []
    for line in lines[-max_turns:]:
        try:
            turns.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return turns


def load_messages(room: str, max_turns: int = 50) -> list[dict]:
    p = _room_path(room)
    if not p.exists():
        return []
    messages = []
    lines = [l for l in p.read_text().splitlines() if l.strip()]
    for line in lines[-(max_turns * 2):]:
        try:
            e = json.loads(line)
            messages.append({"role": "user", "content": e["user"]})
            messages.append({"role": "assistant", "content": e["assistant"]})
        except (json.JSONDecodeError, KeyError):
            pass
    return messages


def rename_room(old: str, new: str) -> bool:
    src = _room_path(old)
    dst = _room_path(new)
    if not src.exists() or dst.exists():
        return False
    src.rename(dst)
    meta_src = src.with_suffix(".json")
    if meta_src.exists():
        meta_src.rename(dst.with_suffix(".json"))
    return True


def delete_room(room: str) -> bool:
    p = _room_path(room)
    if p.exists():
        p.unlink()
        p.with_suffix(".json").unlink(missing_ok=True)
        return True
    return False


def room_exists(room: str) -> bool:
    return _room_path(room).exists()


def search_rooms(query: str) -> list[dict]:
    init()
    query_lower = query.lower()
    results = []
    for f in sorted(BASE_DIR.glob("*.jsonl"), key=lambda x: x.stat().st_mtime, reverse=True):
        lines = [l for l in f.read_text().splitlines() if l.strip()]
        for line in lines:
            try:
                entry = json.loads(line)
                user = entry.get("user", "")
                asst = entry.get("assistant", "")
                if query_lower in user.lower() or query_lower in asst.lower():
                    results.append({
                        "room": f.stem,
                        "ts": entry.get("ts", "")[:16].replace("T", " "),
                        "user": user[:120],
                        "assistant": asst[:120],
                    })
            except json.JSONDecodeError:
                pass
    return results


def clear_room(room: str) -> None:
    p = _room_path(room)
    if p.exists():
        p.write_text("")


def save_meta(room: str, meta: dict) -> None:
    init()
    p = _room_path(room).with_suffix(".json")
    existing = load_meta(room)
    existing.update(meta)
    p.write_text(json.dumps(existing))


def load_meta(room: str) -> dict:
    p = _room_path(room).with_suffix(".json")
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return {}
