#!/usr/bin/env python3
"""下载、校验并合并完整交付包；仅使用 Python 3 标准库，不自动解压。"""
import argparse
import hashlib
import http.client
import json
import os
from pathlib import Path
import shutil
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

CHUNK = 4 * 1024 * 1024


def valid(path, size, digest):
    if not path.is_file() or path.stat().st_size != size:
        return False
    sha = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(CHUNK), b""):
            sha.update(block)
    return sha.hexdigest() == digest.lower()


def checked_existing(path, size, digest):
    if not path.exists():
        return False
    if not valid(path, size, digest):
        raise ValueError(f"已有文件与清单不匹配，已保留且不会覆盖：{path}\n请先移走该文件后重试。")
    print(f"已校验并复用：{path.name}", flush=True)
    return True


def publish(partial, final, size, digest):
    # 以不覆盖的方式发布；正常文件系统的硬链接无需再复制数 GB 数据。
    if checked_existing(final, size, digest):
        partial.unlink(missing_ok=True)
        return
    try:
        os.link(partial, final)
    except FileExistsError:
        checked_existing(final, size, digest)
    except OSError:
        # 不支持硬链接的文件系统使用独占创建，同样不会覆盖已有文件。
        with final.open("xb") as target, partial.open("rb") as source:
            shutil.copyfileobj(source, target, CHUNK)
    partial.unlink()


def download(base_url, part, output):
    final = output / part["name"]
    size, digest = part["bytes"], part["sha256"]
    if checked_existing(final, size, digest):
        return final
    partial = final.with_name(final.name + ".partial")
    for attempt in range(1, 5):
        try:
            offset = partial.stat().st_size if partial.exists() else 0
            if offset >= size:
                if valid(partial, size, digest):
                    publish(partial, final, size, digest)
                    return final
                partial.unlink()
                offset = 0
            headers = {"User-Agent": "MathModelSkill-Fudan-delivery/1.0"}
            if offset:
                headers["Range"] = f"bytes={offset}-"
            request = urllib.request.Request(base_url + urllib.parse.quote(part["name"]), headers=headers)
            print(f"下载 {part['name']}，从 {offset / 1048576:.1f} MiB 继续（尝试 {attempt}/4）", flush=True)
            with urllib.request.urlopen(request, timeout=90) as response:
                status = response.status
                if status == 206:
                    expected = f"bytes {offset}-"
                    if not response.headers.get("Content-Range", "").startswith(expected):
                        raise ValueError("服务器返回的续传位置不匹配")
                elif status == 200:
                    offset = 0  # 服务器不支持续传时，从头写入临时文件。
                else:
                    raise ValueError(f"非预期 HTTP 状态：{status}")
                last = time.monotonic()
                with partial.open("ab" if offset else "wb") as stream:
                    while True:
                        block = response.read(CHUNK)
                        if not block:
                            break
                        stream.write(block)
                        offset += len(block)
                        if offset > size:
                            raise ValueError("下载内容超过清单大小")
                        if time.monotonic() - last >= 10:
                            print(f"  {offset / size:.1%} ({offset / 1048576:.1f} MiB)", flush=True)
                            last = time.monotonic()
            if not valid(partial, size, digest):
                # 未下载完整时保留进度；完整但校验失败时下次重新下载。
                if partial.stat().st_size >= size:
                    partial.unlink()
                raise ValueError("分卷长度或 SHA256 校验未通过")
            publish(partial, final, size, digest)
            print(f"下载并校验完成：{final.name}", flush=True)
            return final
        except (OSError, ValueError, http.client.HTTPException, urllib.error.URLError) as error:
            if attempt == 4:
                raise RuntimeError(f"下载失败：{part['name']}；可重新运行以续传。原因：{error}") from error
            print(f"  {error}；稍后重试。", flush=True)
            time.sleep(min(2 ** attempt, 8))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("complete_delivery"), help="下载和合并目录（默认：complete_delivery）")
    parser.add_argument("--merge-only", action="store_true", help="只校验、合并已手动下载的分卷，不联网")
    args = parser.parse_args()
    manifest = json.loads(Path(__file__).with_name("release_manifest.json").read_text(encoding="utf-8-sig"))
    # 清单中的资源名称必须为单个文件名，避免写入下载目录之外。
    for name in [manifest["archive"]] + [p["name"] for p in manifest["parts"]]:
        if not name or name in (".", "..") or any(c in name for c in "/\\:"):
            raise ValueError(f"清单文件名不合法：{name}")
    args.output.mkdir(parents=True, exist_ok=True)
    archive = args.output / manifest["archive"]
    size, digest = manifest["archive_bytes"], manifest["archive_sha256"]
    if not checked_existing(archive, size, digest):
        base_url = f"https://github.com/{manifest['repository']}/releases/download/{manifest['tag']}/"
        parts = []
        for part in manifest["parts"]:
            path = args.output / part["name"]
            if args.merge_only:
                if not checked_existing(path, part["bytes"], part["sha256"]):
                    raise FileNotFoundError(f"缺少分卷：{path}")
            else:
                path = download(base_url, part, args.output)
            parts.append(path)
        partial = archive.with_name(archive.name + ".partial")
        print("分卷均已校验，正在按清单顺序合并完整 ZIP……", flush=True)
        sha = hashlib.sha256()
        written = 0
        with partial.open("wb") as target:
            for path in parts:
                with path.open("rb") as source:
                    for block in iter(lambda: source.read(CHUNK), b""):
                        target.write(block)
                        sha.update(block)
                        written += len(block)
        if written != size or sha.hexdigest() != digest.lower():
            raise ValueError(f"完整 ZIP 校验失败，临时文件保留在：{partial}")
        publish(partial, archive, size, digest)
    print(f"\n完成：{archive.resolve()}\n请使用 7-Zip 解压完整 .zip 文件，建议使用较短的解压路径，例如 D:\\B34。")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError) as error:
        print(f"错误：{error}", file=sys.stderr)
        sys.exit(1)
