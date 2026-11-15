"""Explicit one-time resource setup; normal recognition never downloads."""
from pathlib import Path
import hashlib
import json
import subprocess
import sys
import urllib.request
import zipfile
import io
import importlib.util
import struct

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from board_recognizer import MODEL_HASHES

COMMIT="3ff21f4502a03f30bb0df55db6f3814ceeb989f5"


def download(url):
    request=urllib.request.Request(url,headers={"User-Agent":"Xiangqi-local-recognition-setup"})
    with urllib.request.urlopen(request,timeout=60) as response:
        return response.read(40*1024*1024)


def main():
    if sys.version_info < (3,12) or struct.calcsize("P") != 8:
        raise RuntimeError("图片识别需要 64 位 Python 3.12 或更高版本；本机已验证 Python 3.14.7。")
    # The main GUI needs only Pillow for its in-memory preview. Leave an
    # existing installation untouched; inference dependencies stay isolated.
    if importlib.util.find_spec("PIL") is None:
        subprocess.run([sys.executable,"-m","pip","install","Pillow==12.0.0"],check=True)
    folder=ROOT/"models/board_recognition"
    folder.mkdir(parents=True,exist_ok=True)
    missing=[name for name,sha in MODEL_HASHES.items() if not (folder/name).is_file()
             or hashlib.sha256((folder/name).read_bytes()).hexdigest()!=sha]
    for name in missing:
        if name.startswith("online_"):
            data=download(f"https://raw.githubusercontent.com/dffge552/xiangqi-pwa-offline/{COMMIT}/{name}")
        else:
            meta=json.loads(download("https://pypi.org/pypi/rapidocr_onnxruntime/1.4.4/json"))
            wheel=next(entry for entry in meta["urls"] if entry["filename"].endswith(".whl"))
            raw=download(wheel["url"])
            if hashlib.sha256(raw).hexdigest()!=wheel["digests"]["sha256"]:
                raise RuntimeError("OCR 下载包校验失败。")
            with zipfile.ZipFile(io.BytesIO(raw)) as z:
                data=z.read("rapidocr_onnxruntime/models/ch_PP-OCRv4_rec_infer.onnx")
        if hashlib.sha256(data).hexdigest()!=MODEL_HASHES[name]:
            raise RuntimeError("识别模型校验失败："+name)
        temp=folder/(name+".download")
        temp.write_bytes(data)
        temp.replace(folder/name)
    env=ROOT/".recognition-venv"
    python=env/("Scripts/python.exe" if sys.platform=="win32" else "bin/python")
    if not python.is_file():
        subprocess.run([sys.executable,"-m","venv",str(env)],check=True)
    subprocess.run([str(python),"-m","pip","install","--disable-pip-version-check",
                    "-r",str(ROOT/"requirements-recognition.txt")],check=True)
    subprocess.run([str(python),"-X","utf8","-c",
                    "from board_recognizer import BoardRecognizer; BoardRecognizer(); print('本地图片识别资源验证成功。')"],
                   cwd=ROOT,check=True)
    print("安装完成。重新启动象棋软件，在棋盘上方“残局”菜单选择“图片导入残局”。")


if __name__=="__main__":
    try:main()
    except Exception as exc:
        print("安装失败："+str(exc),file=sys.stderr)
        raise SystemExit(1)
