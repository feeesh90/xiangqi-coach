"""Real CPU model tests, separate from protocol/GUI doubles."""
from pathlib import Path
import json
import pytest
import subprocess
from recognition_process import RecognitionJob, ROOT
from position_import import normalize_board
from rules import Position

SAMPLES=ROOT/"data/endgame_verification/samples"
EXTERNAL=SAMPLES.parent/"external"
VISION_PYTHON=ROOT/".recognition-venv/Scripts/python.exe"


@pytest.mark.parametrize("manual",[False,True])
def test_independent_software_style_and_grid_correction(manual):
    if not VISION_PYTHON.is_file() or not (EXTERNAL/"verification.json").is_file():
        pytest.skip("External verification capture and isolated runtime required")
    label=json.loads((EXTERNAL/"verification.json").read_text(encoding="utf-8"))
    result=RecognitionJob().run(EXTERNAL/"page_11_image_00_Im6.png",label["corners"] if manual else None)
    assert result["board"]==Position(label["fen"]).b


def test_independent_style_smaller_jpeg(tmp_path):
    if not VISION_PYTHON.is_file() or not (EXTERNAL/"verification.json").is_file():
        pytest.skip("External verification capture and isolated runtime required")
    from PIL import Image
    label=json.loads((EXTERNAL/"verification.json").read_text(encoding="utf-8"))
    with Image.open(EXTERNAL/"page_11_image_00_Im6.png") as image:
        image.resize((763,486)).save(tmp_path/"缩小截图 (1).jpg",quality=95)
    result=RecognitionJob().run(tmp_path/"缩小截图 (1).jpg")
    assert result["board"]==Position(label["fen"]).b


@pytest.mark.parametrize("corrupt",[False,True])
def test_actual_runtime_missing_or_corrupt_model(tmp_path,corrupt):
    if not VISION_PYTHON.is_file():pytest.skip("Isolated vision runtime required")
    if corrupt:(tmp_path/"online_xiangqi_piece_detector.onnx").write_bytes(b"broken")
    code="from board_recognizer import BoardRecognizer; BoardRecognizer("+repr(str(tmp_path))+")"
    response=subprocess.run([str(VISION_PYTHON),"-X","utf8","-c",code],cwd=ROOT,capture_output=True,timeout=10)
    assert response.returncode!=0
    assert ("校验失败" if corrupt else "模型缺失") in response.stderr.decode("utf-8")


@pytest.mark.parametrize("i",list(range(2,10)))
def test_real_endgame_screenshot_matches_independent_fen(i):
    if not (SAMPLES/"labels.json").is_file() or not (ROOT/".recognition-venv/Scripts/python.exe").is_file():
        pytest.skip("Run setup and capture_endgame_samples.py for real model tests")
    sample=json.loads((SAMPLES/"labels.json").read_text(encoding="utf-8"))[i]
    job=RecognitionJob()
    result=job.run(SAMPLES/sample["file"])
    assert normalize_board(result["board"],sample["red_bottom"])==Position(sample["fen"]).b
    assert job.process is None and job.finished.is_set()
    assert result["memory"]["peak_mb"]>0


def test_real_blank_image_rejected_and_file_unchanged(tmp_path):
    from PIL import Image
    p=tmp_path/"空图片.png";Image.new("RGB",(600,600),"white").save(p)
    before=p.read_bytes()
    with pytest.raises(RuntimeError,match="棋盘"):
        RecognitionJob().run(p)
    assert p.read_bytes()==before
    assert [f.name for f in tmp_path.iterdir()]==[p.name]
