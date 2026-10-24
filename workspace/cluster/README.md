# GConnect GPU Cluster — real distributed compute

Connecting GPUs does **not** transparently merge VRAM into one CUDA device —
no framework can do that across machines. What GConnect gives you:

1. **One control room** (the Cluster panel): every PC's real GPUs, pooled VRAM,
   per-GPU load/temp — and a terminal + workspace on each of them.
2. **All local GPUs in your sandbox already**: the container is started with
   `DeviceRequest(count=-1)`, so every GPU on the host is visible to PyTorch.
3. **Real pooling via Ray / torchrun**, bootstrapped from here:

## Single PC, multiple GPUs (no extra setup)

`torchrun` fans your script out across all GPUs (DistributedDataParallel):

```bash
cd /workspace/cluster
torchrun --standalone --nproc_per_node=gpu train_ddp.py
```

Model sharding across GPUs for big models (no code changes):

```python
from transformers import AutoModelForCausalLM
model = AutoModelForCausalLM.from_pretrained(name, device_map="auto", torch_dtype="auto")
```

## Multiple PCs (the cluster)

Each PC runs its own GConnect daemon; each sandbox joins ONE Ray cluster.

**On PC 1 (head):**
```bash
# GConnect terminal on PC 1
python /workspace/cluster/ray_node.py --head
# prints: ray start --head ... or in-process address ray://<pc1-ip>:10001
```

**On PC 2..N (workers)** — GConnect terminal on each PC:
```bash
python /workspace/cluster/ray_node.py --join <pc1-ip>:6379
```

**Then, from ANY node's terminal:**

```python
import ray
ray.init("ray://<pc1-ip>:10001")
print(ray.cluster_resources())
# {'node:...:GPU': 2.0, 'node:...:GPU': 1.0, 'memory': ...}  <- all PCs' GPUs
```

```bash
# Hugging Face across the whole cluster:
pip install "accelerate[torch]"
accelerate launch --multi_gpu --num_processes <total_gpus> train_ddp.py
```

Ray distributes actors/tasks across every GPU in the cluster; use
`@ray.remote(num_gpus=1)` per worker, or DeepSpeed/FSDP for sharded
training. Requirements: Ray needs the same version on every node, and
the PCs must reach each other (Tailscale works fine).

## Install Ray (per sandbox)

```bash
pip install ray[train]
```
