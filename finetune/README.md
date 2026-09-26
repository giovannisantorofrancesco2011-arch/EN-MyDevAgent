# QLoRA fine-tuning of MyDevAgent on your projects

| File | What it does |
|---|---|
| `build_dataset.py` | from your git repos → `data/train.jsonl`, `data/val.jsonl`, `data/fim.jsonl` |
| `config_qlora.yaml` | base model, LoRA (r=16), hyperparameters, export |
| `train_qlora.py` | 4-bit QLoRA training with Unsloth + TRL |
| `export_gguf.sh` | merge + GGUF quantization + `ollama create mydevagent-custom` |

## Requirements
- NVIDIA GPU with 8 GB+ (1.5B/3B), ~10 GB (7B), ~16 GB (14B). Linux or WSL2.
- No GPU: upload the folder to Google Colab/Kaggle (free 16 GB T4) and run the same commands.
- `pip install -e ".[finetune]"` (installs unsloth, trl, datasets, peft, transformers).

## Steps
```bash
# 1. Dataset (small, well-described commits work best)
python finetune/build_dataset.py ~/code/app ~/code/lib --max-commits 3000 --fim-per-repo 300

# (optional) your own question/answer pairs, one per line:
#   {"prompt": "How do we handle errors in services?", "response": "We use Result<T, AppError> ..."}
python finetune/build_dataset.py ~/code/app --extra finetune/my_qa.jsonl

# 2. Training
python finetune/train_qlora.py --config finetune/config_qlora.yaml

# 3. Export to Ollama
bash finetune/export_gguf.sh

# 4. Use it in the 15-agent team
MYDEVAGENT_MODEL_MAIN=mydevagent-custom mydevagent chat
```

## Tips
- **Quality > quantity**: remove noisy commits (formatting, bumps, merges). The builder already filters the
  obvious ones and generated files.
- **Epochs**: 1–2. More epochs = overfitting on your code and loss of general abilities.
- **Evaluate** by comparing `mydevagent-custom` and the base model on the same 10–20 real tasks.
- **FIM**: fill-in-the-middle examples improve autocomplete; if you only train for chat
  set `fim_ratio: 0`. For a personalized autocomplete, train a *base* model
  (`unsloth/Qwen2.5-Coder-1.5B`) on the FIM file only and use it in Continue as the `autocomplete` model.
- **Privacy**: everything stays on your PC. Still, check `data/*.jsonl` before using Colab.
