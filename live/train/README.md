# Retraining the robot detector

Everything here ran once on atomosserver (RTX 3060 / 3060 Ti); inputs are kept in
`data/training/` of the stack so a rebuild never re-downloads from Roboflow.

1. `dl.py` -- downloads 6 public FRC robot datasets from Roboflow ONCE (needs `RF=<key>`;
   ~10 credits/month free tier, so don't re-run if `data/training/rf/` exists).
2. `pool.py` -- normalises them into one-class YOLO format (`pool/`).
3. Round 1: `yolo detect train data=pool/data.yaml model=yolo11s.pt imgsz=960 epochs=40`.
4. `../make_labels.py` -- auto-labels event footage: round-1 detections + bumper-colour
   proposals (`../bglabel.py`), held-out matches as `--val`. -> `dom/`
5. `r2.sh` -- fine-tunes round 1 on pool + event labels at 1280 (both GPUs).
6. Export for the worker (rectangular, fp16, ~3x faster than square fp32):
   `yolo export model=r2.pt format=onnx imgsz=544,1280 half=True device=0 simplify=True opset=17`
   and copy to `data/models/robot.onnx`.

Python env: `uv venv -p 3.12 trainenv && uv pip install ultralytics==8.4.138 roboflow==1.4.2 onnx onnxslim`.
