# /// script
# requires-python = ">=3.12"
# dependencies = ["pefile==2024.8.26", "zstandard==0.23.0"]
# ///
"""仅供 GitHub Actions 使用：组装官方 Cygwin 二进制，不在本地编译。"""

import hashlib
import json
import lzma
import re
import shutil
import subprocess
import tarfile
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath

import pefile
import zstandard


ROOT = Path(__file__).resolve().parent.parent
WORK = ROOT / "debug" / "tmpfile-AI-package"
OUT = ROOT / "dist"
MIRROR = "https://mirrors.kernel.org/sourceware/cygwin/"
FINGERPRINT = "56405CF6FCC81574682A5D561A698DE9E2E56300"


def download(url, target):
    """只从公开上游读取文件，不读取凭据或本地配置。"""
    target.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url, timeout=120) as response:
        with target.open("wb") as output:
            shutil.copyfileobj(response, output)


def digest(path, algorithm="sha256"):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, algorithm).hexdigest()


def signed_index():
    """先验证索引签名，再信任其中的包路径和 SHA512。"""
    index = WORK / "setup.xz"
    signature = WORK / "setup.xz.sig"
    key = WORK / "cygwin-pubring.asc"
    download(MIRROR + "x86_64/setup.xz", index)
    download(MIRROR + "x86_64/setup.xz.sig", signature)
    download("https://cygwin.com/key/pubring.asc", key)
    home = WORK / "gnupg"
    home.mkdir(mode=0o700)
    base = ["gpg", "--batch", "--homedir", str(home)]
    subprocess.run(base + ["--import", str(key)], check=True, capture_output=True)
    result = subprocess.run(
        base + ["--status-fd", "1", "--verify", str(signature), str(index)],
        check=True, capture_output=True, text=True,
    )
    valid = [line.split() for line in result.stdout.splitlines()
             if line.startswith("[GNUPG:] VALIDSIG ")]
    if not any(FINGERPRINT in (fields[2], fields[-1]) for fields in valid):
        raise RuntimeError("索引签名不属于预期的 Cygwin 官方公钥")
    return lzma.decompress(index.read_bytes()).decode("utf-8")


def parse_index(text):
    """只取默认稳定版本，忽略 prev/test 版本段落。"""
    packages = {}
    current = None
    for line in text.splitlines():
        if line.startswith("@ "):
            current = {}
            packages[line[2:]] = current
        elif line.startswith("["):
            current = None
        elif current is not None:
            match = re.match(r"^([a-z0-9_-]+): (.*)$", line)
            if match:
                current[match[1]] = match[2]
    return packages


def dependency_packages(packages):
    pending = ["rsync", "openssh", "cygwin"]
    chosen = set()
    while pending:
        name = pending.pop()
        # _windows 是由系统提供的虚拟包，本项目只面向 Windows 10/11 x64。
        if name == "_windows" or name in chosen:
            continue
        if name not in packages:
            raise RuntimeError(f"上游依赖无法解析：{name}")
        chosen.add(name)
        fields = packages[name]
        dependencies = re.sub(r"\([^)]*\)", "", fields.get("depends2", ""))
        if "|" in dependencies:
            raise RuntimeError(f"上游出现尚未支持的可选依赖：{name}")
        pending.extend(filter(None, re.split(r"[,\s]+", dependencies)))
        pending.extend(fields.get("requires", "").split())
    return sorted(chosen)


def fetch_archive(record):
    path, size, expected = record.split()
    relative = PurePosixPath(path)
    if relative.is_absolute() or ".." in relative.parts:
        raise RuntimeError("包路径不合法")
    if not re.fullmatch(r"[0-9a-fA-F]{128}", expected):
        raise RuntimeError("上游未提供预期的 SHA512 校验值")
    target = WORK / "downloads" / relative
    if not target.exists():
        download(MIRROR + path, target)
    if target.stat().st_size != int(size) or digest(target, "sha512") != expected.lower():
        raise RuntimeError(f"包校验失败：{path}")
    return target


def archive_members(path):
    """读取归档，不直接 extractall，避免路径穿越和符号链接。"""
    with path.open("rb") as raw:
        stream = zstandard.ZstdDecompressor().stream_reader(raw) if path.suffix == ".zst" else raw
        try:
            with tarfile.open(fileobj=stream, mode="r|*") as archive:
                for member in archive:
                    if member.isfile():
                        name = PurePosixPath(member.name)
                        if name.is_absolute() or ".." in name.parts:
                            raise RuntimeError("归档成员路径不合法")
                        yield name, archive.extractfile(member).read()
        finally:
            if stream is not raw:
                stream.close()


def zip_directory(directory, destination):
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(directory.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(directory.parent).as_posix())


def main():
    # 防止误将工作站文件或本机环境打包。
    import os
    if os.environ.get("GITHUB_ACTIONS") != "true":
        raise RuntimeError("请通过 GitHub Actions 手动触发，本脚本不支持本地构建")
    WORK.mkdir(parents=True, exist_ok=False)
    OUT.mkdir(exist_ok=False)
    index = signed_index()
    packages = parse_index(index)
    candidates = WORK / "candidates"
    candidates.mkdir()
    owners = {}
    docs = {}
    downloaded = {}
    for name in dependency_packages(packages):
        fields = packages[name]
        if "install" not in fields:
            continue
        print(f"获取组件：{name} {fields['version']}", flush=True)
        downloaded[name] = fields
        for path, content in archive_members(fetch_archive(fields["install"])):
            if path.parent.as_posix() in ("usr/bin", "bin") and (
                path.suffix.lower() == ".dll" or path.name in ("rsync.exe", "ssh.exe")
            ):
                key = path.name.lower()
                if key in owners:
                    raise RuntimeError(f"重复的二进制文件：{key}")
                (candidates / key).write_bytes(content)
                owners[key] = name
            elif path.as_posix().startswith(("usr/share/doc/", "usr/share/licenses/")):
                docs.setdefault(name, []).append((path, content))

    # 仅保留两个入口实际导入的 DLL，递归检查，系统 DLL 交给 Windows。
    portable = WORK / "rsync-portable"
    portable.mkdir()
    pending = ["rsync.exe", "ssh.exe"]
    included = set()
    used = set()
    while pending:
        name = pending.pop()
        if name in included:
            continue
        if name not in owners:
            raise RuntimeError(f"缺少运行依赖：{name}")
        included.add(name)
        used.add(owners[name])
        source = candidates / name
        shutil.copyfile(source, portable / name)
        with pefile.PE(str(source)) as binary:
            if binary.FILE_HEADER.Machine != 0x8664:
                raise RuntimeError(f"不是 x64 二进制：{name}")
            for field in ("DIRECTORY_ENTRY_IMPORT", "DIRECTORY_ENTRY_DELAY_IMPORT"):
                for entry in getattr(binary, field, []):
                    dependency = entry.dll.decode("ascii").lower()
                    if dependency in owners or dependency.startswith("cyg"):
                        pending.append(dependency)

    sources = WORK / "corresponding-sources"
    sources.mkdir()
    manifest = {"architecture": "x86_64", "mirror": MIRROR,
                "index_sha256": digest(WORK / "setup.xz"), "components": []}
    source_paths = set()
    for name in sorted(used):
        fields = downloaded[name]
        if "source" not in fields:
            raise RuntimeError(f"缺少匹配的源码包：{name}")
        source = fetch_archive(fields["source"])
        relative = PurePosixPath(fields["source"].split()[0])
        if str(relative) not in source_paths:
            target = sources / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            source_paths.add(str(relative))
        for path, content in docs.get(name, []):
            target = portable / "licenses-and-docs" / name / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        manifest["components"].append({
            "package": name, "version": fields["version"],
            "binary": fields["install"], "source": fields["source"],
            "files": sorted(file for file in included if owners[file] == name),
        })
    manifest["files_sha256"] = {name: digest(portable / name) for name in sorted(included)}
    for folder in (portable, sources):
        (folder / "components.lock.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        shutil.copyfile(ROOT / "README.md", folder / "README.md")
        shutil.copyfile(ROOT / "LICENSE", folder / "TEMPLATE-LICENSE.txt")
    # 与产物匹配的索引、签名和构建模板一并归档，不包含 runner 环境信息。
    for name in ("setup.xz", "setup.xz.sig", "cygwin-pubring.asc"):
        shutil.copyfile(WORK / name, sources / name)
    for relative in ("scripts/package.py", ".github/workflows/package.yml", ".github/workflows/verify.yml"):
        target = sources / "template" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, target)
    zip_directory(portable, OUT / "rsync-portable-windows-x64.zip")
    zip_directory(sources, OUT / "corresponding-sources.zip")
    (OUT / "SHA256SUMS").write_text("".join(
        f"{digest(path)}  {path.name}\n" for path in sorted(OUT.glob("*.zip"))
    ), encoding="utf-8")
    print("打包完成：便携 ZIP、对应源码 ZIP、SHA256SUMS", flush=True)


if __name__ == "__main__":
    main()
