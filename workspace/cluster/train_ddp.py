"""Multi-GPU training template (DistributedDataParallel).

Single PC, all GPUs:
    torchrun --standalone --nproc_per_node=gpu train_ddp.py

Replace `ToyModel` with your real model / voice-AI pipeline. Each process
gets one GPU automatically (LOCAL_RANK from torchrun / accelerate).
"""

import os

import torch
import torch.distributed as dist
import torch.nn as nn
from torch.nn.parallel import DistributedDataParallel as DDP


class ToyModel(nn.Module):
    def __init__(self):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(512, 1024), nn.GELU(), nn.Linear(1024, 512)
        )

    def forward(self, x):
        return self.net(x)


def main():
    dist.init_process_group(backend="nccl" if torch.cuda.is_available() else "gloo")
    local_rank = int(os.environ.get("LOCAL_RANK", 0))

    if torch.cuda.is_available():
        torch.cuda.set_device(local_rank)
        device = torch.device("cuda", local_rank)
        print(f"[rank {dist.get_rank()}] GPU {local_rank}: {torch.cuda.get_device_name(local_rank)}")
    else:
        device = torch.device("cpu")
        print(f"[rank {dist.get_rank()}] CPU mode")

    model = DDP(ToyModel().to(device))
    optimizer = torch.optim.AdamW(model.parameters(), lr=3e-4)

    for step in range(20):
        x = torch.randn(256, 512, device=device)
        target = torch.randn(256, 512, device=device)
        loss = nn.functional.mse_loss(model(x), target)

        optimizer.zero_grad(set_to_none=True)
        loss.backward()
        optimizer.step()

        if step % 5 == 0 and dist.get_rank() == 0:
            print(f"step {step:3d}  loss {loss.item():.4f}")

    if torch.cuda.is_available():
        alloc = torch.cuda.memory_allocated(local_rank) / 1024**2
        print(f"[rank {dist.get_rank()}] VRAM used: {alloc:.1f} MB")

    dist.destroy_process_group()
    print(f"[rank {dist.get_rank()}] done - DDP across {dist.get_world_size()} process(es)")


if __name__ == "__main__":
    main()
