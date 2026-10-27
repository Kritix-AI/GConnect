# GConnect - Universal Remote GPU Development Platform
# Production-grade environment for PyTorch, CUDA, LLMs, Computer Vision & Audio AI

import os

print("========================================")
print(" GConnect Universal GPU Sandbox Ready")
print("========================================")

try:
    import torch
    print(f"PyTorch Version: {torch.__version__}")
    print(f"CUDA Available:  {torch.cuda.is_available()}")

    if torch.cuda.is_available():
        print(f"Device Name:     {torch.cuda.get_device_name(0)}")
        print(f"Device Count:    {torch.cuda.device_count()}")
        x = torch.randn(1000, 1000, device="cuda")
        print("CUDA Matrix Allocation: SUCCESS")
    else:
        print("CUDA:            Unavailable (Running in CPU mode)")
        x = torch.randn(1000, 1000)
        print("Tensor Math:     SUCCESS (CPU)")
except ImportError:
    print("PyTorch:         Not installed in local Python environment.")
    print("Note:            Run inside the GConnect Docker container (pytorch/pytorch) or 'pip install torch'.")

print(f"Hugging Face Cache: {os.environ.get('HF_HOME', 'Default (~/.cache/huggingface)')}")
print("========================================")
