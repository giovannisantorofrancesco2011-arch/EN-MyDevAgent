#!/usr/bin/env python3
"""QLoRA fine-tuning with Unsloth (2x faster, ~60% less VRAM) on the dataset from your projects.

  pip install -e ".[finetune]"          # requires an NVIDIA GPU (Linux/WSL2) — or use Google Colab
  python finetune/build_dataset.py ~/code/my-project
  python finetune/train_qlora.py --config finetune/config_qlora.yaml
  bash finetune/export_gguf.sh          # → Ollama model "mydevagent-custom"
"""

from __future__ import annotations

import argparse
import random
from pathlib import Path

import yaml


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="finetune/config_qlora.yaml")
    ap.add_argument("--export-gguf", action="store_true", help="also export the quantized GGUF right away")
    args = ap.parse_args()
    cfg = yaml.safe_load(Path(args.config).read_text())

    from unsloth import FastLanguageModel  # import before transformers/trl (Unsloth patches)
    from datasets import Dataset, load_dataset
    from trl import SFTConfig, SFTTrainer

    model, tokenizer = FastLanguageModel.from_pretrained(
        model_name=cfg["base_model"], max_seq_length=cfg["max_seq_length"], load_in_4bit=cfg["load_in_4bit"])
    lora = cfg["lora"]
    model = FastLanguageModel.get_peft_model(
        model, r=lora["r"], lora_alpha=lora["alpha"], lora_dropout=lora["dropout"],
        target_modules=lora["target_modules"], bias="none", use_gradient_checkpointing="unsloth",
        random_state=cfg["train"]["seed"])

    def to_text(example):
        if example.get("messages"):
            return {"text": tokenizer.apply_chat_template(example["messages"], tokenize=False)}
        return {"text": example["text"]}

    data = cfg["data"]
    train_rows = load_dataset("json", data_files=data["train"], split="train").to_list()
    if data.get("fim") and Path(data["fim"]).is_file():
        fim_rows = load_dataset("json", data_files=data["fim"], split="train").to_list()
        random.Random(cfg["train"]["seed"]).shuffle(fim_rows)
        train_rows += fim_rows[: int(len(train_rows) * data.get("fim_ratio", 0.3))]
    train_ds = Dataset.from_list([to_text(r) for r in train_rows]).shuffle(seed=cfg["train"]["seed"])
    val_ds = None
    if Path(data["val"]).is_file() and Path(data["val"]).stat().st_size:
        val_ds = Dataset.from_list([to_text(r) for r in load_dataset("json", data_files=data["val"],
                                                                      split="train").to_list()])

    t = cfg["train"]
    sft = SFTConfig(
        output_dir=t["output_dir"], num_train_epochs=t["epochs"], per_device_train_batch_size=t["batch_size"],
        gradient_accumulation_steps=t["grad_accum"], learning_rate=t["learning_rate"],
        warmup_ratio=t["warmup_ratio"], lr_scheduler_type=t["lr_scheduler"], weight_decay=t["weight_decay"],
        logging_steps=t["logging_steps"], save_steps=t["save_steps"],
        eval_strategy="steps" if val_ds is not None else "no", eval_steps=t["eval_steps"],
        optim="adamw_8bit", seed=t["seed"], dataset_text_field="text", max_seq_length=cfg["max_seq_length"],
        packing=False, report_to="none")
    kwargs = dict(model=model, train_dataset=train_ds, eval_dataset=val_ds, args=sft)
    try:
        trainer = SFTTrainer(processing_class=tokenizer, **kwargs)   # recent TRL
    except TypeError:
        trainer = SFTTrainer(tokenizer=tokenizer, **kwargs)          # TRL < 0.12
    trainer.train()

    out = Path(t["output_dir"])
    model.save_pretrained(out / "adapter")
    tokenizer.save_pretrained(out / "adapter")
    print(f"LoRA saved to {out / 'adapter'}")

    if args.export_gguf:
        exp = cfg["export"]
        model.save_pretrained_gguf(exp["gguf_dir"], tokenizer, quantization_method=exp["quantization"])
        print(f"GGUF saved to {exp['gguf_dir']} — now run: bash finetune/export_gguf.sh")


if __name__ == "__main__":
    main()
