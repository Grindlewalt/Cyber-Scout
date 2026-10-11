#!/bin/sh
# round 2: fine-tune r1 on public pool + Sammamish auto-labels (x2), validate on held-out matches
cd "${WORK:-/mnt/nas/autoscout-scratch}"
rm -rf dom2 && mkdir -p dom2/images/train dom2/labels/train
for f in dom/images/train/*.jpg; do b=$(basename $f .jpg); ln $f dom2/images/train/${b}_b.jpg; ln dom/labels/train/$b.txt dom2/labels/train/${b}_b.txt; done
cat > r2.yaml <<EOT
path: ${WORK:-/mnt/nas/autoscout-scratch}
train: [pool/images/train, dom/images/train, dom2/images/train]
val: dom/images/val
nc: 1
names: [robot]
EOT
export YOLO_CONFIG_DIR=$PWD/.ultra
trainenv/bin/yolo detect train data=r2.yaml model=r1.pt imgsz=1280 epochs=30 batch=12 device=0,1 workers=8 \
  patience=12 close_mosaic=6 lr0=0.003 warmup_epochs=1 scale=0.4 degrees=0 fliplr=0.5 \
  project=$PWD/runs name=r2 exist_ok=True plots=False
