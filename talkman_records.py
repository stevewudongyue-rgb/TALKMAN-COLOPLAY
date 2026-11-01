"""本机投递历史：成功填入的快照；不代表目标应用已发送消息。"""
from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
import tempfile
import uuid


class DeliveryHistory:
    def __init__(self, directory):
        self.path = Path(directory) / "delivery-history.jsonl"
        self.items = self.load()

    def load(self):
        try:
            lines = self.path.read_bytes().splitlines()
        except FileNotFoundError:
            return []
        records = {}
        for line in lines:
            try:
                item = json.loads(line)
                if not isinstance(item, dict):
                    continue
                identifier = item.get("id")
                if not isinstance(identifier, str):
                    continue
                if item.get("deleted") is True:
                    records.pop(identifier, None)
                elif all(isinstance(item.get(key), str) for key in ("time", "text", "app", "title")):
                    records[identifier] = item
            except ValueError:
                # 断电留下的未完成行不影响之前的记录。
                continue
        return list(reversed(records.values()))

    def _append(self, item):
        payload = json.dumps(item, ensure_ascii=False) + "\n"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # 追加前补分隔符，避免上次被中断的末行吞掉新记录。
        with self.path.open("ab+") as stream:
            stream.seek(0, os.SEEK_END)
            if stream.tell():
                stream.seek(-1, os.SEEK_END)
                if stream.read(1) != b"\n":
                    stream.write(b"\n")
            stream.write(payload.encode("utf-8"))
            stream.flush()
            os.fsync(stream.fileno())

    def add(self, text, app, title, when=None, status='已填入'):
        item = dict(id=uuid.uuid4().hex, text=text, app=app, title=title,
                    time=when or datetime.now().astimezone().isoformat(timespec="seconds"),
                    status=status if status in ('已填入', '已触发下发') else '已填入')
        self._append(item)
        self.items.insert(0, item)
        return item

    def delete(self, identifier):
        items = [item for item in self.items if item["id"] != identifier]
        self._replace(items)
        self.items = items

    def _replace(self, items):
        # 删除较少发生，原子重写以实际移除文本；失败时保留原文件与内存。
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.path.parent,
                                         prefix="delivery-", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            try:
                for item in items:
                    stream.write(json.dumps(item, ensure_ascii=False) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            except BaseException:
                stream.close()
                temporary.unlink(missing_ok=True)
                raise
        try:
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)


def delivery_destination(item):
    return f"{item['app'] or '未知应用'} · {item['title'] or '未识别对话'}"


def validate_layout_only(before, after):
    """仅排版时允许增减空白，禁止模型偷改正文或返回空结果。"""
    if not isinstance(after, str) or "".join(before.split()) != "".join(after.split()):
        raise ValueError("排版结果改变了正文，已保留原文")
    return after
