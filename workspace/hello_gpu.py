# Connect - Remote GPU Development Platform
# Specifically configured for PyTorch, CUDA, and Voice Agent workflows

import os
import torch

print("========================================")
print(" Connect GPU Sandbox Ready")
print("========================================")
print(f"PyTorch Version: {torch.__version__}")
print(f"CUDA Available:  {torch.cuda.is_available()}")

if torch.cuda.is_available():
    print(f"Device Name:     {torch.cuda.get_device_name(0)}")
    print(f"Device Count:    {torch.cuda.device_count()}")
    x = torch.randn(1000, 1000, device="cuda")
    print("CUDA Matrix Allocation: SUCCESS")

print(f"Hugging Face Cache: {os.environ.get('HF_HOME')}")
print("========================================")
