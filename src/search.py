"""규정 청크 검색. 키워드(BM25)와 임베딩을 순위 융합(RRF)으로 합친다.

규정명과 조번호를 직접 물으면(`선수 규정 제2조`) 해당 조문을 맨 앞에 둔다.
임베딩 모델은 encoder로 주입하므로 모델 없이 키워드 검색만 쓸 수도 있다.
"""

import hashlib
import json
import math
import re
from collections import Counter
from pathlib import Path
from typing import Protocol

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
CHUNK_DIR = ROOT / "data" / "chunks"
INDEX_DIR = ROOT / "data" / "index"

BM25_K1 = 1.5
BM25_B = 0.75
RRF_K = 60  # 순위 융합의 표준 상수
EMBED_BATCH = 16
DEFAULT_MODEL = "intfloat/multilingual-e5-small"  # eval/search_results.md의 비교 결과로 선택

WORD = re.compile(r"[가-힣]+|[A-Za-z]+|\d+(?:[.,]\d+)*")
ARTICLE_REF = re.compile(
    r"(?P<prefix>부칙|별첨\s*\d+)?\s*(?:의\s*)?제?\s*(?P<number>\d+)\s*조(?:의\s*(?P<branch>\d+))?"
)
SCHEDULE_REF = re.compile(r"별표\s*(?P<table>\d+)(?:\s*(?:의\s*)?(?P<item>\d+))?")
ANNEX_REF = re.compile(r"별첨\s*(?P<number>\d+)")
FORM_REF = re.compile(r"별지\s*서식\s*(?P<number>\d+)")
DOTTED_REF = re.compile(r"(?<![\d.])(\d+\.\d+(?:\.\d+)*)(?![\d.]*\d)")


class Encoder(Protocol):
    name: str

    def encode_passages(self, texts: list[str]) -> np.ndarray: ...

    def encode_query(self, text: str) -> np.ndarray: ...


def tokenize(text: str) -> list[str]:
    """낱말과 한글 두 글자 조각을 함께 쓴다. 조사가 붙어도 어근이 겹치게 하기 위해서다."""
    tokens = []
    for word in WORD.findall(text.lower()):
        tokens.append(word)
        if word[0] >= "가" and len(word) > 2:
            tokens += [word[i:i + 2] for i in range(len(word) - 1)]
    return tokens


class BM25:
    def __init__(self, texts: list[str]):
        docs = [Counter(tokenize(text)) for text in texts]
        self.lengths = np.array([sum(d.values()) for d in docs], dtype=float)
        self.average = float(self.lengths.mean()) if len(docs) else 0.0
        self.postings: dict[str, list[tuple[int, int]]] = {}
        for index, counts in enumerate(docs):
            for token, count in counts.items():
                self.postings.setdefault(token, []).append((index, count))
        self.size = len(docs)

    def scores(self, query: str) -> np.ndarray:
        scores = np.zeros(self.size)
        for token in tokenize(query):
            posting = self.postings.get(token)
            if not posting:
                continue
            idf = math.log(1 + (self.size - len(posting) + 0.5) / (len(posting) + 0.5))
            for index, count in posting:
                norm = 1 - BM25_B + BM25_B * self.lengths[index] / self.average
                scores[index] += idf * count * (BM25_K1 + 1) / (count + BM25_K1 * norm)
        return scores


def rank_of(scores: np.ndarray) -> list[int]:
    return [int(i) for i in np.argsort(-scores, kind="stable") if scores[i] > 0]


def rrf(rankings: list[list[int]]) -> list[int]:
    """여러 순위를 합친다. 점수 척도가 달라도 순위만으로 합칠 수 있다."""
    scores: dict[int, float] = {}
    for ranking in rankings:
        for rank, index in enumerate(ranking):
            scores[index] = scores.get(index, 0.0) + 1 / (RRF_K + rank + 1)
    return sorted(scores, key=lambda index: (-scores[index], index))


def _squeeze(text: str) -> str:
    return re.sub(r"\s+", "", text)


def find_regulation(query: str, titles: list[str]) -> str | None:
    """질문에 나온 규정명을 찾는다. `K리그`를 뺀 이름도 인정하고 가장 긴 이름을 고른다."""
    squeezed = _squeeze(query)
    matches = []
    for title in titles:
        name = _squeeze(title)
        for candidate in (name, name.removeprefix("K리그")):
            if candidate in squeezed:
                matches.append((len(candidate), title))
                break
    return max(matches)[1] if matches else None


def article_keys(query: str) -> list[str]:
    """질문에서 조번호를 chunk의 article_no 형식으로 뽑는다."""
    keys = []
    for match in ARTICLE_REF.finditer(query):
        prefix = _squeeze(match["prefix"] or "")
        lead = "부칙 " if prefix == "부칙" else f"{prefix} " if prefix else ""
        branch = f"의{match['branch']}" if match["branch"] else ""
        keys.append(f"{lead}제{match['number']}조{branch}")
    for match in SCHEDULE_REF.finditer(query):
        keys.append(f"별표{match['table']} {match['item']}" if match["item"] else f"별표{match['table']}")
    for match in FORM_REF.finditer(query):
        keys.append(f"별지 서식 {match['number']}")
    if not keys:
        keys += [f"별첨{match['number']}" for match in ANNEX_REF.finditer(query)]
    return keys


class Searcher:
    def __init__(self, chunks: list[dict], encoder: Encoder | None = None,
                 vectors: np.ndarray | None = None):
        self.chunks = chunks
        self.bm25 = BM25([chunk["text"] for chunk in chunks])
        self.encoder = encoder
        self.vectors = vectors
        if encoder is not None and vectors is None:
            self.vectors = encoder.encode_passages([chunk["text"] for chunk in chunks])
        self.titles = sorted({chunk["regulation"] for chunk in chunks})

    def keyword_ranking(self, query: str) -> list[int]:
        return rank_of(self.bm25.scores(query))

    def embedding_ranking(self, query: str) -> list[int]:
        scores = self.vectors @ self.encoder.encode_query(query)
        return [int(i) for i in np.argsort(-scores, kind="stable")]

    def direct_hits(self, query: str) -> set[int]:
        """규정명과 조번호가 함께 나오면 그 조문의 청크를 돌려준다."""
        title = find_regulation(query, self.titles)
        if title is None:
            return set()
        scope = [(i, c) for i, c in enumerate(self.chunks) if c["regulation"] == title]
        keys = set(article_keys(query))
        hits = {i for i, c in scope if c.get("article_no") in keys}
        for match in DOTTED_REF.finditer(query):
            line = re.compile(rf"(?m)^{re.escape(match[1])}\.?\s")
            hits |= {i for i, c in scope if line.search(c["body"])}
        return hits

    def search(self, query: str, k: int = 5, mode: str = "hybrid") -> list[dict]:
        """mode는 keyword, embedding, hybrid 중 하나다. 임베딩이 없으면 keyword만 쓴다."""
        if mode not in ("keyword", "embedding", "hybrid"):
            raise ValueError(f"알 수 없는 검색 방식: {mode}")
        if self.encoder is None and mode != "keyword":
            raise ValueError("임베딩 모델 없이 쓸 수 있는 방식은 keyword뿐이다")
        rankings = []
        if mode in ("keyword", "hybrid"):
            rankings.append(self.keyword_ranking(query))
        if mode in ("embedding", "hybrid"):
            rankings.append(self.embedding_ranking(query))
        ranking = rrf(rankings)
        direct = self.direct_hits(query)
        if direct:
            ranked = set(ranking)
            ordered = [i for i in ranking if i in direct] + sorted(direct - ranked)
            ranking = ordered + [i for i in ranking if i not in direct]
        return [self.chunks[i] for i in ranking[:k]]


class OnnxEncoder:
    """ONNX Runtime으로 임베딩 모델을 돌린다. torch 없이 CPU에서 동작한다.

    모델 파일은 Hugging Face에서 받아 캐시한다. 쓸 때만 불러온다.
    """

    def __init__(self, model_name: str):
        import onnxruntime
        from huggingface_hub import hf_hub_download
        from tokenizers import Tokenizer

        self.name = model_name
        self.uses_prefix = "e5" in model_name  # e5 계열은 query:, passage: 접두어가 필요하다
        config = hf_hub_download(model_name, "sentence_bert_config.json")
        max_length = json.loads(Path(config).read_text(encoding="utf-8"))["max_seq_length"]
        self.tokenizer = Tokenizer.from_file(hf_hub_download(model_name, "tokenizer.json"))
        self.tokenizer.enable_truncation(max_length=max_length)
        self.tokenizer.enable_padding()
        self.session = onnxruntime.InferenceSession(
            hf_hub_download(model_name, "onnx/model.onnx"),
            providers=["CPUExecutionProvider"],
        )
        self.inputs = {item.name for item in self.session.get_inputs()}

    def _encode(self, texts: list[str]) -> np.ndarray:
        batches = []
        for start in range(0, len(texts), EMBED_BATCH):
            encodings = self.tokenizer.encode_batch(texts[start:start + EMBED_BATCH])
            mask = np.array([e.attention_mask for e in encodings], dtype=np.int64)
            feed = {
                "input_ids": np.array([e.ids for e in encodings], dtype=np.int64),
                "attention_mask": mask,
                "token_type_ids": np.zeros_like(mask),
            }
            output = self.session.run(None, {k: v for k, v in feed.items() if k in self.inputs})[0]
            weights = mask[..., None]
            pooled = (output * weights).sum(axis=1) / weights.sum(axis=1).clip(min=1)
            batches.append(pooled / np.linalg.norm(pooled, axis=1, keepdims=True).clip(min=1e-12))
        return np.vstack(batches)

    def encode_passages(self, texts: list[str]) -> np.ndarray:
        return self._encode([f"passage: {t}" if self.uses_prefix else t for t in texts])

    def encode_query(self, text: str) -> np.ndarray:
        return self._encode([f"query: {text}" if self.uses_prefix else text])[0]


def load_chunks(name: str = "article", folder: Path = CHUNK_DIR) -> list[dict]:
    path = folder / f"{name}.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def cached_vectors(encoder: Encoder, chunks: list[dict], name: str,
                   folder: Path = INDEX_DIR) -> np.ndarray:
    """임베딩을 파일로 저장해 다시 쓴다. 청크 내용이 바뀌면 파일 이름이 달라져 새로 만든다."""
    folder.mkdir(parents=True, exist_ok=True)
    texts = [chunk["text"] for chunk in chunks]
    digest = hashlib.sha256("\n".join(texts).encode("utf-8")).hexdigest()[:12]
    path = folder / f"{name}_{encoder.name.replace('/', '_')}_{digest}.npy"
    if path.exists():
        return np.load(path)
    vectors = encoder.encode_passages(texts)
    np.save(path, vectors)
    return vectors
