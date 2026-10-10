"""构建期预压缩文本资源，服务器无需重复压缩大文件。"""
import gzip
from pathlib import Path

TEXT_TYPES = {".html", ".json", ".svg", ".css", ".js"}


def compress_site(root: Path) -> None:
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in TEXT_TYPES:
            continue
        target = path.with_name(path.name + ".gz")
        stat = path.stat()
        if target.exists() and target.stat().st_mtime_ns >= stat.st_mtime_ns:
            continue
        body = path.read_bytes()
        compressed = gzip.compress(body, compresslevel=6, mtime=0)
        if len(body) >= 512 and len(compressed) < len(body) * 0.95:
            staging = target.with_name(target.name + ".tmp")
            staging.write_bytes(compressed)
            staging.replace(target)
        elif target.exists():
            target.unlink()
