"""추출한 규정을 검색용 청크로 나눈다.

- 조문 청킹: 조문 하나를 청크 하나로 만들고, 길면 항 단위로 나눈다. 조 제목은 유지한다.
- 고정 길이 청킹: 정답률 비교용. 조문 경계를 무시하고 글자 수로 자른다.

data/extracted/*.json을 읽어 data/chunks/article.jsonl, fixed.jsonl로 저장한다.
"""

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXTRACTED_DIR = ROOT / "data" / "extracted"
CHUNK_DIR = ROOT / "data" / "chunks"

MAX_CHARS = 1000  # 조문 청크 본문의 최대 글자 수
MAX_LINE_CHARS = 800  # 한 줄(표의 행)의 최대 글자 수. 머리글을 붙여도 MAX_CHARS 안에 들어간다
MIN_PREAMBLE_CHARS = 40  # 이보다 짧은 서문은 표제일 뿐이므로 청크로 만들지 않는다
FIXED_SIZE = 800
FIXED_OVERLAP = 100
TITLE_MAX_CHARS = 40

# 조사가 붙은 참조(제9조의 대상자, 제28조에 따른)는 제목이 아니다. 제2조의1은 허용한다.
ARTICLE = re.compile(r"^제\s*(\d+)\s*조(?:의\s*(\d+))?(?:\.|\s+|$|(?=[\[(]))\s*(.*)$")
ARTICLE_15 = re.compile(r"^(\d+)조 \(Article \d+\):\s*(.+)$")
ADDENDUM = re.compile(r"^(?:제\d+장\s*)?부\s*칙(?:\s*[(<].*)?$")
SCHEDULE = re.compile(r"^\[별표\s*(\d+)\]$")
FORM = re.compile(r"^별지 서식\s*(\d+)$")
ANNEX = re.compile(r"^별첨\s*(\d+)(?:\.\s*|\s*\(Annex \d+\):\s*|\))\s*(.*)$")
SECTION = re.compile(r"^(?:제\s*\d+\s*[장절]\s+\S.*|\d+장\.\s*\S.*|[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+\.\s*\S.*)$")
SCHEDULE_ITEM = re.compile(r"^(\d{1,2})\.\s+(\S.*)$")
ENGLISH_SUFFIX = re.compile(r"\s*\([A-Za-z][^가-힣]*\)$")
TOC_LEADER = re.compile(r"[·.…]{3,}|\s\d{1,3}$")

CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"
# 긴 조문을 나누는 경계. 앞쪽일수록 큰 단위다.
SPLIT_LEVELS = [
    re.compile(rf"^[{CIRCLED}]"),  # 항
    re.compile(r"^\| [A-Z]{1,2}\.\d{2} 기준"),  # 15번 기준표
    re.compile(r"^\d+\.\d+ "),  # 15번 4.1
    re.compile(r"^\d+\.\d+\.\d+ "),  # 15번 4.1.1
    re.compile(r"^(?:■|##\s)"),
    re.compile(r"^\(\d+\) "),
    re.compile(r"^[가-하]\. "),
    re.compile(r"^\d+[.)] "),
]
PARAGRAPH_LABEL = re.compile(rf"^(?:\| )?([{CIRCLED}]|[A-Z]{{1,2}}\.\d{{2}}(?= 기준)|\d+\.\d+(?:\.\d+)*(?= ))")
TABLE_RULE = re.compile(r"^\|( --- \|)+$")
SENTENCE_END = re.compile(r"(?<=[다음함임])\.\s+|;\s+")


def normalize_title(title: str) -> str:
    """조 제목의 괄호를 벗기고 영문 병기와 벌어진 글자(목 적)를 정리한다."""
    title = title.strip()
    if len(title) >= 2 and title[0] in "[(" and title[-1] in "])":
        title = title[1:-1].strip()
    title = ENGLISH_SUFFIX.sub("", title).strip()
    tokens = title.split()
    if len(tokens) > 1 and all(len(t) == 1 for t in tokens):
        return "".join(tokens)
    return title


def is_article_title(title: str) -> bool:
    """줄바꿈으로 줄 앞에 온 참조나 본문 문장을 제목으로 잡지 않는다."""
    if len(normalize_title(title)) > TITLE_MAX_CHARS or re.match(r"^제\s*\d", title):
        return False
    if TOC_LEADER.search(title):  # 목차의 "제1조. 프로계약 ···· 3"
        return False
    return not re.search(r"(다|한다|있다)\.$", title)


class Structure:
    """줄을 차례로 받아 규정 구조에 따라 구간으로 나눈다."""

    def __init__(self, doc: dict):
        self.doc = doc
        self.segments: list[dict] = []
        self.dropped: list[str] = []  # 메타데이터로 옮겨 본문에서 뺀 줄
        self.section = ""
        self.part = "본문"
        self.prefix = ""  # 부칙, 별첨1 등 조번호 앞에 붙는 말
        self.number = 0
        self.item_number = 0
        self.schedule = 0
        self.await_title = False
        self.open("서문", "")

    def open(self, article_no: str, title: str) -> None:
        self.current = {
            "article_no": article_no,
            "article_title": title,
            "part": self.part,
            "section": self.section,
            "lines": [],
        }
        self.segments.append(self.current)

    def enter_part(self, part: str, prefix: str, article_no: str, title: str) -> None:
        self.part, self.prefix, self.section = part, prefix, ""
        self.number = self.item_number = 0
        self.open(article_no, title)

    def try_article(self, line: str) -> bool:
        match = ARTICLE_15.match(line) if self.doc["no"] == 15 else ARTICLE.match(line)
        if not match:
            return False
        if self.doc["no"] == 15:
            number, sub, title = int(match[1]), None, match[2]
        else:
            number, sub, title = int(match[1]), match[2], match[3]
        expected = number == self.number if sub else number == self.number + 1
        if not expected or not is_article_title(title):
            return False
        self.number = number
        suffix = f"의{sub}" if sub else ""
        self.open(f"{self.prefix}제{number}조{suffix}", normalize_title(title))
        return True

    def try_schedule_item(self, line: str) -> bool:
        match = SCHEDULE_ITEM.match(line)
        if not match or int(match[1]) != self.item_number + 1:
            return False
        self.item_number += 1
        self.open(f"별표{self.schedule} {self.item_number}", match[2].strip())
        return True

    def feed(self, page: int, line: str) -> None:
        text = line.strip()
        if self.await_title:
            self.current["article_title"] = normalize_title(text)
            self.section = self.current["section"] = self.current["article_title"]
            self.await_title = False
            self.dropped.append(text)
            return
        if not text.startswith("|"):
            if self.consume_heading(text):
                self.dropped.append(text)
                return
        self.current["lines"].append((page, line))

    def consume_heading(self, text: str) -> bool:
        if ADDENDUM.match(text):
            self.enter_part("부칙", "부칙 ", "부칙", "")
            return True
        if match := SCHEDULE.match(text):
            self.schedule = int(match[1])
            self.enter_part("별표", "", f"별표{self.schedule}", "")
            self.await_title = True
            return True
        if match := FORM.match(text):
            self.enter_part("별지", "", f"별지 서식 {match[1]}", "")
            self.await_title = True
            return True
        if match := ANNEX.match(text):
            title = normalize_title(match[2])
            self.enter_part("별첨", f"별첨{match[1]} ", f"별첨{match[1]}", title)
            self.await_title = not title
            return True
        if self.part == "별표":
            return self.try_schedule_item(text)
        if self.part != "별지" and SECTION.match(text):
            self.section = text
            return True
        return self.part != "별지" and self.try_article(text)


def parse_structure(doc: dict) -> Structure:
    structure = Structure(doc)
    for page in doc["pages"]:
        if page["kind"] == "body":
            for line in page["text"].splitlines():
                structure.feed(page["page"], line)
    return structure


def _boundaries(lines: list[tuple], pattern: re.Pattern) -> list[int]:
    return [i for i, (_, text) in enumerate(lines) if pattern.match(text.strip())]


def _size(lines: list[tuple]) -> int:
    return sum(len(text) + 1 for _, text in lines)


def split_units(lines: list[tuple], level: int = 0) -> list[list[tuple]]:
    """MAX_CHARS를 넘는 묶음을 큰 단위 경계부터 차례로 나눈다."""
    if _size(lines) <= MAX_CHARS:
        return [lines]
    for depth in range(level, len(SPLIT_LEVELS)):
        starts = _boundaries(lines, SPLIT_LEVELS[depth])
        if len(starts) < 2 and not (starts and starts[0] > 0):
            continue
        cuts = sorted({0, *starts}) + [len(lines)]
        units = []
        for start, end in zip(cuts, cuts[1:]):
            units += split_units(lines[start:end], depth + 1)
        return units
    return [[line] for line in lines]


def attach_table_heads(units: list[list[tuple]]) -> list[list[tuple]]:
    """표의 머리글과 구분선이 단위 끝에 혼자 남지 않게 다음 단위로 옮긴다."""
    fixed: list[list[tuple]] = []
    pending: list[tuple] = []
    for unit, following in zip(units, [*units[1:], []]):
        unit = pending + unit
        pending = []
        if following:
            if TABLE_RULE.match(unit[-1][1]):
                cut = max(len(unit) - 2, 0)
            elif TABLE_RULE.match(following[0][1]) and unit[-1][1].startswith("|"):
                cut = len(unit) - 1
            else:
                cut = len(unit)
            unit, pending = unit[:cut], unit[cut:]
        if unit:
            fixed.append(unit)
    return fixed


def merge_units(units: list[list[tuple]]) -> list[list[tuple]]:
    """잘게 나뉜 단위를 MAX_CHARS 안에서 이어 붙인다."""
    merged: list[list[tuple]] = []
    for unit in units:
        if merged and _size(merged[-1]) + _size(unit) <= MAX_CHARS:
            merged[-1] = merged[-1] + unit
        else:
            merged.append(unit)
    return merged


def hard_split(line: tuple) -> list[tuple]:
    """한 줄이 MAX_LINE_CHARS를 넘으면 문장 경계에서 나눈다. 표의 행도 여기서 나뉜다."""
    page, text = line
    if len(text) <= MAX_LINE_CHARS:
        return [line]
    pieces, current, position = [], "", 0
    ends = [m.end() for m in SENTENCE_END.finditer(text)] + [len(text)]
    for end in ends:
        sentence = text[position:end]
        position = end
        while len(sentence) > MAX_LINE_CHARS:
            room = MAX_LINE_CHARS - len(current)
            pieces.append(current + sentence[:room])
            sentence, current = sentence[room:], ""
        if len(current) + len(sentence) > MAX_LINE_CHARS:
            pieces.append(current)
            current = ""
        current += sentence
    if current:
        pieces.append(current)
    return [(page, piece) for piece in pieces]


def table_header(lines: list[tuple], start: int) -> list[str]:
    """start 줄이 표 중간이면 그 표의 머리글 줄을 돌려준다."""
    if not lines[start][1].startswith("|"):
        return []
    if start + 1 < len(lines) and TABLE_RULE.match(lines[start + 1][1]):
        return []  # 새 표의 머리글로 시작하면 앞 표의 머리글을 붙이지 않는다
    for i in range(start - 1, -1, -1):
        if not lines[i][1].startswith("|"):
            return []
        if TABLE_RULE.match(lines[i][1]):
            return [lines[i - 1][1], lines[i][1]] if i > 0 else []
    return []


def paragraph_label(unit: list[tuple]) -> str:
    labels = [m[1] for _, text in unit if (m := PARAGRAPH_LABEL.match(text.strip()))]
    # 15번의 ①은 11.2.3 아래의 하위 목록이므로 11.x가 있으면 그쪽만 표시한다.
    labels = [label for label in labels if label not in CIRCLED] or labels
    if not labels:
        return ""
    return labels[0] if len(labels) == 1 else f"{labels[0]}~{labels[-1]}"


def segment_chunks(doc: dict, segment: dict) -> list[dict]:
    lines = [piece for line in segment["lines"] for piece in hard_split(line)]
    if not lines:
        return []
    units = merge_units(attach_table_heads(split_units(lines)))
    label = " ".join(x for x in (segment["article_no"], segment["article_title"]) if x)
    label = f"[{doc['title']}] {label}"
    chunks = []
    offset = 0
    for index, unit in enumerate(units, 1):
        body = "\n".join(text for _, text in unit)
        header = table_header(lines, offset)
        offset += len(unit)
        chunks.append({
            "id": f"{doc['no']:02d}-{segment['article_no']}-{index}",
            "doc_no": doc["no"],
            "regulation": doc["title"],
            "article_no": segment["article_no"],
            "article_title": segment["article_title"],
            "part": segment["part"],
            "section": segment["section"],
            "paragraph": paragraph_label(unit) if len(units) > 1 else "",
            "effective_date": doc["effective_date"],
            "effective_kind": doc["effective_kind"],
            "enforcement_date": doc["enforcement_date"],
            "visibility": "public",  # 외부 API 전송 허용 표시. 비공개 문서에는 쓰지 않는다
            "pages": sorted({page for page, _ in unit}),
            "body": body,
            "text": "\n".join([label, *header, body]),
        })
    return chunks


def page_segment(article_no: str, title: str, part: str, page: dict) -> dict:
    lines = [(page["page"], line) for line in page["text"].splitlines() if line.strip()]
    return {
        "article_no": article_no, "article_title": title, "part": part,
        "section": "", "lines": lines,
    }


def clean_preamble(segment: dict, title: str, dropped: list[str]) -> dict:
    """서문에서 규정 표제 줄을 빼고, 표제뿐인 서문은 비운다."""
    key = title.replace(" ", "")
    kept = [x for x in segment["lines"] if x[1].replace(" ", "") != key]
    if sum(len(text) for _, text in kept) < MIN_PREAMBLE_CHARS:
        kept = []
    dropped += [text for page, text in segment["lines"] if (page, text) not in kept]
    return {**segment, "lines": kept}


def build_segments(doc: dict) -> tuple[list[dict], list[str]]:
    """청크로 만들 구간과, 메타데이터로 옮겨 본문에서 뺀 줄을 돌려준다."""
    structure = parse_structure(doc)
    segments = [
        clean_preamble(s, doc["title"], structure.dropped) if s["article_no"] == "서문" else s
        for s in structure.segments
    ]
    for page in doc["pages"]:
        if page["kind"] == "manual":
            segments.append(page_segment(
                f"{page['page']}쪽 요약", "라이선싱 절차 흐름도 요약", "수동요약", page,
            ))
        elif page["kind"] == "history":
            segments.append(page_segment("개정내역", "", "개정내역", page))
    return segments, structure.dropped


def article_chunks(doc: dict) -> list[dict]:
    """문서 하나를 조문 단위 청크로 나눈다."""
    segments, _ = build_segments(doc)
    chunks = [chunk for segment in segments for chunk in segment_chunks(doc, segment)]
    ids = [chunk["id"] for chunk in chunks]
    if len(ids) != len(set(ids)):
        raise ValueError(f"청크 ID 중복: {doc['title']}")
    return chunks


def fixed_chunks(doc: dict, size: int = FIXED_SIZE, overlap: int = FIXED_OVERLAP) -> list[dict]:
    """조문 구조를 무시하고 글자 수로 자른다. 정답률 비교 기준선으로 쓴다."""
    if overlap >= size:
        raise ValueError("overlap은 size보다 작아야 한다")
    text = ""
    spans = []
    for page in doc["pages"]:
        if page["kind"] in ("body", "manual", "history") and page["text"]:
            spans.append((len(text), len(text) + len(page["text"]), page["page"]))
            text += page["text"] + "\n"
    chunks = []
    for index, start in enumerate(range(0, max(len(text) - overlap, 1), size - overlap), 1):
        end = start + size
        body = text[start:end].strip()
        if not body:
            continue
        chunks.append({
            "id": f"{doc['no']:02d}-fixed-{index}",
            "doc_no": doc["no"],
            "regulation": doc["title"],
            "effective_date": doc["effective_date"],
            "effective_kind": doc["effective_kind"],
            "enforcement_date": doc["enforcement_date"],
            "visibility": "public",  # 외부 API 전송 허용 표시. 비공개 문서에는 쓰지 않는다
            "pages": [p for s, e, p in spans if s < end and e > start],
            "body": body,
            "text": f"[{doc['title']}]\n{body}",
        })
    return chunks


def load_documents(folder: Path = EXTRACTED_DIR) -> list[dict]:
    return [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(folder.glob("*.json"))
    ]


def write_jsonl(path: Path, chunks: list[dict]) -> None:
    lines = [json.dumps(chunk, ensure_ascii=False) for chunk in chunks]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    docs = load_documents()
    if not docs:
        print("data/extracted가 비어 있음: src/extract.py를 먼저 실행할 것")
        return 1
    CHUNK_DIR.mkdir(parents=True, exist_ok=True)
    articles = [chunk for doc in docs for chunk in article_chunks(doc)]
    fixed = [chunk for doc in docs for chunk in fixed_chunks(doc)]
    write_jsonl(CHUNK_DIR / "article.jsonl", articles)
    write_jsonl(CHUNK_DIR / "fixed.jsonl", fixed)
    print(f"조문 청크 {len(articles)}개, 고정 길이 청크 {len(fixed)}개")
    return 0


if __name__ == "__main__":
    sys.exit(main())
