#!/bin/bash -x

srun -N1 -p booster --account=hai_cons_ee --gres gpu:4 --time=23:59:00 --pty singularity exec --bind "${PWD}:/mnt" --nv ../hyperview_latest.sif python main.py -m 0 -c 5 -l 0.001000 -b 32 -w 224 -p --num-epochs 105 --train-dir 'train_data/train_data/' --label-dir 'train_data/train_gt.csv' --eval-dir 'test_data/' --out-dir 'modeldir/' &
srun -N1 -p booster --account=hai_cons_ee --gres gpu:4 --time=23:59:00 --pty singularity exec --bind "${PWD}:/mnt" --nv ../hyperview_latest.sif python main.py -m 0 -c 5 -l 0.000500 -b 32 -w 224 -p --num-epochs 105 --train-dir 'train_data/train_data/' --label-dir 'train_data/train_gt.csv' --eval-dir 'test_data/' --out-dir 'modeldir/' &
srun -N1 -p booster --account=hai_cons_ee --gres gpu:4 --time=23:59:00 --pty singularity exec --bind "${PWD}:/mnt" --nv ../hyperview_latest.sif python main.py -m 0 -c 5 -l 0.000250 -b 32 -w 224 -p --num-epochs 105 --train-dir 'train_data/train_data/' --label-dir 'train_data/train_gt.csv' --eval-dir 'test_data/' --out-dir 'modeldir/' &
srun -N1 -p booster --account=hai_cons_ee --gres gpu:4 --time=23:59:00 --pty singularity exec --bind "${PWD}:/mnt" --nv ../hyperview_latest.sif python main.py -m 0 -c 5 -l 0.000125 -b 32 -w 224 -p --num-epochs 105 --train-dir 'train_data/train_data/' --label-dir 'train_data/train_gt.csv' --eval-dir 'test_data/' --out-dir 'modeldir/' &
srun -N1 -p booster --account=hai_cons_ee --gres gpu:4 --time=23:59:00 --pty singularity exec --bind "${PWD}:/mnt" --nv ../hyperview_latest.sif python main.py -m 0 -c 5 -l 0.000050 -b 32 -w 224 -p --num-epochs 105 --train-dir 'train_data/train_data/' --label-dir 'train_data/train_gt.csv' --eval-dir 'test_data/' --out-dir 'modeldir/' &
srun -N1 -p booster --account=hai_cons_ee --gres gpu:4 --time=23:59:00 --pty singularity exec --bind "${PWD}:/mnt" --nv ../hyperview_latest.sif python main.py -m 0 -c 5 -l 0.000025 -b 32 -w 224 -p --num-epochs 105 --train-dir 'train_data/train_data/' --label-dir 'train_data/train_gt.csv' --eval-dir 'test_data/' --out-dir 'modeldir/' &
srun -N1 -p booster --account=hai_cons_ee --gres gpu:4 --time=23:59:00 --pty singularity exec --bind "${PWD}:/mnt" --nv ../hyperview_latest.sif python main.py -m 0 -c 5 -l 0.000012 -b 32 -w 224 -p --num-epochs 105 --train-dir 'train_data/train_data/' --label-dir 'train_data/train_gt.csv' --eval-dir 'test_data/' --out-dir 'modeldir/' &
srun -N1 -p booster --account=hai_cons_ee --gres gpu:4 --time=23:59:00 --pty singularity exec --bind "${PWD}:/mnt" --nv ../hyperview_latest.sif python main.py -m 0 -c 5 -l 0.000005 -b 32 -w 224 -p --num-epochs 105 --train-dir 'train_data/train_data/' --label-dir 'train_data/train_gt.csv' --eval-dir 'test_data/' --out-dir 'modeldir/' &
srun -N1 -p booster --account=hai_cons_ee --gres gpu:4 --time=23:59:00 --pty singularity exec --bind "${PWD}:/mnt" --nv ../hyperview_latest.sif python main.py -m 0 -c 5 -l 0.000002 -b 32 -w 224 -p --num-epochs 105 --train-dir 'train_data/train_data/' --label-dir 'train_data/train_gt.csv' --eval-dir 'test_data/' --out-dir 'modeldir/' &
srun -N1 -p booster --account=hai_cons_ee --gres gpu:4 --time=23:59:00 --pty singularity exec --bind "${PWD}:/mnt" --nv ../hyperview_latest.sif python main.py -m 0 -c 5 -l 0.000001 -b 32 -w 224 -p --num-epochs 105 --train-dir 'train_data/train_data/' --label-dir 'train_data/train_gt.csv' --eval-dir 'test_data/' --out-dir 'modeldir/' &
