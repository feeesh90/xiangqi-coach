"""Install official, pinned GPU runtime DLLs beside a copy of the bundled Px0.

No driver installation, global PATH change, Python package installation or
replacement of the working CPU engine. Downloads are SHA-256 checked.
"""
import concurrent.futures
import hashlib
import json
from pathlib import Path
import shutil
import time
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "engine/px0-gpu"
DOWNLOADS = ROOT / "data/downloads/px0-gpu"
PACKAGES = {
    "nvidia-cuda-runtime": "13.0.96",
    "nvidia-cublas": "13.0.2.14",
    "nvidia-cudnn-cu13": "9.27.0.42",
}


def metadata(url):
    request = urllib.request.Request(url, headers={"User-Agent": "Xiangqi-Coach-GPU-Setup"})
    with urllib.request.urlopen(request, timeout=60) as stream:
        return json.load(stream)


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def download(item):
    path = DOWNLOADS / item["filename"]
    expected = item.get("sha256")
    if not path.exists() or (expected and sha(path) != expected):
        print("Downloading " + item["filename"], flush=True)
        # urllib respects the Windows proxy settings, unlike curl's defaults.
        size=item["bytes"]
        chunk=4*1024*1024
        with path.open("wb") as dest:
            dest.truncate(size)
        def part(start):
            end=min(start+chunk,size)-1
            for attempt in range(4):
                try:
                    request=urllib.request.Request(item["url"], headers={
                        "User-Agent":"Xiangqi-Coach-GPU-Setup",
                        "Range":f"bytes={start}-{end}"})
                    with urllib.request.urlopen(request,timeout=45) as source:
                        if source.status!=206:
                            raise RuntimeError("Server did not honor the download range")
                        data=source.read()
                    if len(data)!=end-start+1:
                        raise RuntimeError("Incomplete download range")
                    with path.open("r+b") as dest:
                        dest.seek(start)
                        dest.write(data)
                    return
                except Exception:
                    if attempt==3:
                        raise
                    time.sleep(1+attempt)
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as workers:
            list(workers.map(part,range(0,size,chunk)))
    actual = sha(path)
    if expected and expected != actual:
        raise RuntimeError("SHA-256 mismatch: " + item["filename"])
    item["sha256"] = actual
    item["bytes"] = path.stat().st_size
    print("Verified " + item["filename"], flush=True)
    return path, item


def main():
    receipt=TARGET/"provenance.json"
    if receipt.is_file():
        previous=json.loads(receipt.read_text(encoding="utf-8"))
        files=previous.get("dlls",[])
        if (files and (TARGET/"px0.exe").is_file()
                and sha(TARGET/"px0.exe")==previous.get("executable_sha256")
                and all(Path(item["name"]).name==item["name"]
                        and (TARGET/item["name"]).is_file()
                        and sha(TARGET/item["name"])==item["sha256"] for item in files)):
            print("Existing GPU runtime hashes verified. See provenance.json for real-search verification.")
            return
    DOWNLOADS.mkdir(parents=True, exist_ok=True)
    TARGET.mkdir(parents=True, exist_ok=True)
    release = metadata("https://api.github.com/repos/microsoft/onnxruntime/releases/tags/v1.30.0")
    asset = next(x for x in release["assets"]
                 if x["name"] == "onnxruntime-win-x64-gpu_cuda13-1.30.0.zip")
    digest = asset.get("digest", "")
    items = [{"filename": asset["name"], "url": asset["browser_download_url"],
              "sha256": digest.removeprefix("sha256:") or None,"bytes":asset["size"]}]
    for name, version in PACKAGES.items():
        data = metadata(f"https://pypi.org/pypi/{name}/{version}/json")
        wheel = next(x for x in data["urls"] if x["filename"].endswith("win_amd64.whl"))
        items.append({"filename": wheel["filename"], "url": wheel["url"],
                      "sha256": wheel["digests"]["sha256"],"bytes":wheel["size"]})
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as workers:
        results = list(workers.map(download, items))
    licenses = TARGET / "licenses"
    licenses.mkdir(exist_ok=True)
    for path, item in results:
        with zipfile.ZipFile(path) as archive:
            for member in archive.infolist():
                name = Path(member.filename).name
                is_dll = name.lower().endswith(".dll")
                is_license = any(key in name.lower() for key in ("license", "notice", "eula"))
                if is_dll or is_license:
                    destination = TARGET/name if is_dll else licenses/(path.stem+"_"+name)
                    with archive.open(member) as source, destination.open("wb") as dest:
                        shutil.copyfileobj(source, dest)
    for name in ("px0.exe", "libc++.dll", "libunwind.dll"):
        shutil.copy2(ROOT/"engine/px0"/name, TARGET/name)
    manifest = {
        "source_executable": "engine/px0/px0.exe",
        "executable_sha256": sha(TARGET/"px0.exe"),
        "backend": "onnx-cuda", "networkPath": "engine/px0/network.pb.gz",
        "downloads": [item for _, item in results],
        "dlls": [{"name": path.name, "sha256": sha(path), "bytes": path.stat().st_size}
                 for path in sorted(TARGET.glob("*.dll"))],
        "verified_search": False,
    }
    (TARGET/"provenance.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print("GPU runtime staged; a real Px0 search is still required.", flush=True)


if __name__ == "__main__":
    main()
