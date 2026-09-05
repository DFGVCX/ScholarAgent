from __future__ import annotations

import re


ACADEMIC_CONCEPTS: tuple[tuple[str, ...], ...] = (
    ("联邦学习", "federated learning", "FL"),
    ("检索增强生成", "retrieval-augmented generation", "retrieval augmented generation", "RAG"),
    ("机器学习", "machine learning", "ML"),
    ("深度学习", "deep learning", "DL"),
    ("大语言模型", "large language model", "LLM"),
    ("区块链", "blockchain"),
    ("全同态加密", "fully homomorphic encryption", "FHE"),
    ("同态加密", "homomorphic encryption", "HE"),
    ("差分隐私", "differential privacy", "DP"),
    ("隐私保护", "privacy-preserving", "privacy preserving"),
    ("投毒攻击", "poisoning attack", "model poisoning"),
    ("后门攻击", "backdoor attack"),
    ("拜占庭鲁棒", "byzantine-robust", "byzantine robust"),
    ("恶意客户端", "malicious client"),
    ("聚合规则", "aggregation rule"),
    ("安全聚合", "secure aggregation"),
    ("余弦相似度", "cosine similarity"),
    ("非独立同分布", "non-IID", "non IID"),
    ("根数据集", "root dataset"),
    (
        "安全多方计算",
        "secure multi-party computation",
        "secure multiparty computation",
        "SMPC",
        "MPC",
    ),
    ("模型更新", "model update"),
    ("知识图谱", "knowledge graph", "KG"),
    ("图神经网络", "graph neural network", "GNN"),
)


def academic_query_aliases(query: str) -> tuple[str, ...]:
    """Return bidirectional Chinese/English/abbreviation aliases.

    ASCII terms use token boundaries so short abbreviations such as ``FL`` do
    not fire inside unrelated words such as ``workflow``.
    """
    lowered = query.lower()
    compact = re.sub(r"\s+", "", lowered)
    aliases: list[str] = []
    for concept in ACADEMIC_CONCEPTS:
        matched: set[str] = set()
        for term in concept:
            normalized = term.lower()
            if re.search(r"[\u3400-\u9fff]", term):
                if re.sub(r"\s+", "", normalized) in compact:
                    matched.add(term)
            elif re.search(
                rf"(?<![a-z0-9]){re.escape(normalized)}(?![a-z0-9])",
                lowered,
            ):
                matched.add(term)
        if matched:
            aliases.extend(term for term in concept if term not in matched)
    return tuple(dict.fromkeys(aliases))
