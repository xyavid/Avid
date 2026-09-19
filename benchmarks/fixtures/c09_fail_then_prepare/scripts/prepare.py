"""生成 analyze 需要的缓存。"""
import pathlib

cache = pathlib.Path("cache/index.txt")
cache.parent.mkdir(parents=True, exist_ok=True)
cache.write_text("RS-3312\n", encoding="utf-8")
print("已生成 cache/index.txt")
