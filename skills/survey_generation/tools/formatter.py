from __future__ import annotations

import json
from pathlib import Path
from typing import Any

class CitationFormatter:
    """CiteAdapt citation standardization with an explicit rule fallback."""
    
    def __init__(self, adapter_path: str = "models/lora_adapters/cite_adapt"):
        self.adapter_path = adapter_path
        self.is_loaded = False
        self.mode = "rule_fallback"
        self.last_warning = "CiteAdapt LoRA adapter has not been loaded"
        self.model: Any = None
        self.tokenizer: Any = None

    def load(self) -> bool:
        adapter = Path(self.adapter_path)
        base_model = Path("models/base_models/Qwen-1.5B")
        if not adapter.exists() or not base_model.exists():
            self.mode = "rule_fallback"
            self.last_warning = "CiteAdapt weights are not present under models/"
            return False
        try:
            from peft import PeftModel  # type: ignore
            from transformers import AutoModelForCausalLM, AutoTokenizer  # type: ignore
        except ImportError as exc:
            self.mode = "rule_fallback"
            self.last_warning = f"CiteAdapt dependencies are not installed: {exc}"
            return False
        self.tokenizer = AutoTokenizer.from_pretrained(str(base_model), local_files_only=True)
        base = AutoModelForCausalLM.from_pretrained(str(base_model), local_files_only=True)
        self.model = PeftModel.from_pretrained(base, str(adapter))
        self.is_loaded = True
        self.mode = "citeadapt_lora"
        self.last_warning = ""
        return True

    def status(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "adapter_path": self.adapter_path,
            "loaded": self.is_loaded,
            "warning": self.last_warning,
        }

    def format_citation(self, paper_metadata: dict, style: str = "IEEE") -> str:
        """
        Formats a single citation using the LoRA-tuned model.
        Args:
            paper_metadata: Dictionary containing title, authors, year, journal, etc.
            style: One of ["IEEE", "APA", "GB/T 7714"]
        """
        if self.mode == "rule_fallback" and self.last_warning == "CiteAdapt LoRA adapter has not been loaded":
            self.load()
        prompt = f"Format the following paper as {style} citation:\n{json.dumps(paper_metadata, ensure_ascii=False)}"
        if self.is_loaded and self.model is not None and self.tokenizer is not None:
            inputs = self.tokenizer(prompt, return_tensors="pt")
            outputs = self.model.generate(**inputs, max_new_tokens=128)
            return self.tokenizer.decode(outputs[0], skip_special_tokens=True)
        
        authors = paper_metadata.get("authors") or ["Unknown Author"]
        title = paper_metadata.get("title", "Untitled")
        year = str(paper_metadata.get("published_at") or "n.d.")[:4]
        source = paper_metadata.get("source", "local")
        if style == "APA":
            return f"{authors[0]} ({year}). {title}. {source}."
        if style == "GB/T 7714":
            return f"{authors[0]}. {title}[J]. {source}, {year}."
        return f"{authors[0]}, \"{title},\" {source}, {year}."

    def batch_process(self, papers: list, style: str) -> list:
        """Processes a list of citations efficiently."""
        if not papers:
            return []
        
        results = []
        for paper in papers:
            formatted = self.format_citation(paper, style)
            results.append(formatted)
            
        return results

    def integrate_to_text(self, text: str, citation_map: dict, style: str) -> str:
        """
        将文本中的占位符引用替换为 CiteAdapt 生成的标准化格式。
        """
        for placeholder, metadata in citation_map.items():
            formatted = self.format_citation(metadata, style)
            text = text.replace(f"[{placeholder}]", f"[{placeholder}]")
        return text
