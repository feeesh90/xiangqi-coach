# Third-party notices

## Pikafish

Official project:
https://github.com/official-pikafish/Pikafish

This desktop project is designed for the official Pikafish 2026-09-06 Windows x86-64 universal release.

The engine executable is GPLv3. If you redistribute a build containing it, comply with the
GPL corresponding-source and notice requirements.

The NNUE/network file may have separate upstream terms. Review the official network/license
terms yourself for the intended use, especially before commercial distribution.

For this reason the source ZIP does not pre-bundle the engine/network. The provided setup
script downloads the official matched release only after the user explicitly accepts the
network terms. The separate Windows portable release package does bundle the official
Pikafish 2026-09-06 executable and NNUE; correspond with the official project above for
GPLv3 corresponding source and preserve all upstream notices.

The application does not use a home-made fallback AI. If Pikafish is absent or fails, AI
features intentionally stop.


## Chinese Chess Practical Dataset (CCPD)

Project:
https://github.com/Yvonne761/Chinese-Chess-Practical-Dataset

License: Creative Commons Attribution 4.0 International (CC BY 4.0), as stated by the upstream project.

The source repository does not bundle the full dataset. `scripts/setup_master_book.ps1` downloads
the public repository and converts supported master PGNs into a local SQLite lookup database.
The Windows portable release package bundles that derived SQLite database; it is distributed
under CC BY 4.0 with attribution to the upstream project above.


## Architecture reference
The engine-GUI separation, fixed time/depth/nodes search modes, stop behavior and MultiPV workflow were reviewed against sojourners/public-Xiangqi (TCHESS). No TCHESS source code is copied into this project.


## Local screenshot recognition (added 2026-10-10)

The piece detector and classifier are from
[dffge552/xiangqi-pwa-offline](https://github.com/dffge552/xiangqi-pwa-offline/tree/3ff21f4502a03f30bb0df55db6f3814ceeb989f5),
fixed commit `3ff21f4502a03f30bb0df55db6f3814ceeb989f5`.

The upstream repository's adapter code is MIT licensed; its copyright/license notice
is preserved in `models/board_recognition/LICENSE.txt`. The two ONNX files embed
Ultralytics 8.1.0 metadata declaring **AGPL-3.0**. The repository's MIT license
does not replace this embedded model license. The AGPL text is preserved in
`models/board_recognition/AGPL-3.0.txt`; model filenames, sizes and hashes are
recorded in `models/board_recognition/manifest.json`.

`ch_PP-OCRv4_rec_infer.onnx` is the PaddleOCR PP-OCRv4 Chinese recognition model,
obtained from the official PyPI `rapidocr_onnxruntime` 1.4.4 wheel. The model and
character table are used with direct ONNX Runtime inference; the RapidOCR Python
package is not installed. Apache-2.0 is preserved in
`models/board_recognition/Apache-2.0.txt`.

Source projects: [PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR),
[RapidOCR](https://github.com/RapidAI/RapidOCR).

The application adds its own grid finding, colour checks, draft review and process
management. Normal screenshot recognition makes no network requests. Only the
explicit installation script downloads resources.

An independent-style verification screenshot was extracted from page 12 of
[T. S. Hsu's 2025 course slides](https://homepage.iis.sinica.edu.tw/~tshsu/tcg/2025/slides/slide2.pdf).
It depicts Chinese Chess Endgame and is kept under `data/endgame_verification/external/`
as a cited test example, not as an application asset or model-training dataset.


## Optional screen-follow model (2026-10-28)

- `models/board_recognition/tchess_yolov11.onnx`: from https://github.com/sojourners/public-Xiangqi, git blob `dde7a0a10fdc221adbb252da80c37343c534c82f`.
- SHA-256: `099c4ef0cbfbd07f680037bb1aabf59024f5c0243964b36aeec7c7a57f7213e1`.
- Model metadata: Ultralytics 8.3.17, YOLO11n, **AGPL-3.0**. Upstream Java adapter repository: GPL-3.0; its license is preserved in `models/board_recognition/TChess-LICENSE.txt`. Model licensing is not replaced by the adapter license.
- This is an explicitly selected experimental screenshot recognizer. It passed the supplied JJ image but regressed on existing generated screenshots, so the compatible recognizer remains the default.
- The separate layout-model comparison used TheOne1006/chinese-chess-recognition and author-hosted `nano_v3-0319.onnx` (SHA-256 `da66ba9809f15127f8ae729b1755e42ee61c100c4f9979ce0ef13602ac471298`). It was an evaluation artifact only, is not a production option, has no embedded license metadata, and is not included in the cleaned project or the portable release package.


## Windows portable release runtime

The portable release bundles a private Python runtime and the following runtime packages so
that users do not need to install Python:

- Python 3.14 (Python Software Foundation License), https://www.python.org/
- Pillow (HPND), https://python-pillow.org/
- NumPy (BSD-3-Clause), https://numpy.org/
- OpenCV (Apache-2.0), https://opencv.org/
- ONNX Runtime (MIT), https://github.com/microsoft/onnxruntime
- protobuf / flatbuffers / packaging (BSD / Apache-2.0 / Apache-2.0)

Their license files remain in the runtime's site-packages / distribution metadata and the
Python `LICENSE.txt` is kept at the root of the bundled runtime. The source repository itself
does not contain these binaries; they are built only for the Windows release package.
