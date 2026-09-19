"""分析入口：需要 cache/index.txt，缺了就按提示先跑 prepare。"""
import pathlib
import sys

cache = pathlib.Path("cache/index.txt")
if not cache.is_file():
    print(
        "错误：缺少 cache/index.txt。请先运行 scripts/prepare.py 生成缓存。",
        file=sys.stderr,
    )
    sys.exit(1)
print("result=" + cache.read_text(encoding="utf-8").strip())
