"""docs_public/의 PDF가 텍스트 추출 가능한지, 스캔 이미지인지 점검한다.

    python scripts/check_pdfs.py            # 표로 출력
    python scripts/check_pdfs.py --tables   # 표 개수까지 (느림)
    python scripts/check_pdfs.py --json out.json
"""

import argparse
import json
import re
import sys
from pathlib import Path

import pdfplumber

ROOT = Path(__file__).resolve().parent.parent
PDF_DIR = ROOT / "docs_public"

LOW_TEXT_CHARS = 50  # 이보다 적으면 표지·뒤표지 등 저텍스트 페이지로 본다
IMAGE_PAGE_RATIO = 0.5  # 이미지가 페이지의 이 비율을 넘으면 이미지 페이지로 본다
# 09번 등의 표 괘선은 0.5pt 조각 수천 개로 그려져 있어 기본 길이 필터에서 사라진다.
TABLE_SETTINGS = {"edge_min_length_prefilter": 0, "edge_min_length": 1}
HANGUL = re.compile(r"[가-힣]")
ARTICLE_LINE = re.compile(r"^\s*제\s*\d+\s*조")


def text_metrics(page_texts: list[str]) -> dict:
    """페이지별 텍스트로 문자 수, 한글 비율, 비정상 문자, 조문 줄 수를 계산한다."""
    joined = "".join(page_texts)
    chars = sum(len("".join(t.split())) for t in page_texts)
    hangul = len(HANGUL.findall(joined))
    return {
        "chars": chars,
        "hangul_pct": round(100 * hangul / chars) if chars else 0,
        "replacement_chars": sum(c == "�" or 0xE000 <= ord(c) <= 0xF8FF for c in joined),
        "control_chars": sum(ord(c) < 32 and c not in "\n\t\r" for c in joined),
        "empty_pages": [i for i, t in enumerate(page_texts, 1) if not t.strip()],
        "low_text_pages": [
            i for i, t in enumerate(page_texts, 1)
            if 0 < len("".join(t.split())) < LOW_TEXT_CHARS
        ],
        "article_lines": sum(
            bool(ARTICLE_LINE.match(line)) for t in page_texts for line in t.splitlines()
        ),
    }


def is_scanned(metrics: dict, pages: int) -> bool:
    """전체 페이지의 절반 이상이 비어 있거나 이미지면 스캔 문서로 판정한다."""
    blank = set(metrics["empty_pages"]) | set(metrics["image_pages"])
    return len(blank) * 2 >= pages


def image_ratio(page) -> float:
    area = float(page.width * page.height)
    covered = sum(
        max(0, float(im["x1"] - im["x0"])) * max(0, float(im["bottom"] - im["top"]))
        for im in page.images
    )
    return covered / area if area else 0.0


def inspect_pdf(path: Path, count_tables: bool = False) -> dict:
    with pdfplumber.open(path) as pdf:
        pages = pdf.pages
        result = text_metrics([p.extract_text() or "" for p in pages])
        result["image_pages"] = [
            i for i, p in enumerate(pages, 1) if image_ratio(p) > IMAGE_PAGE_RATIO
        ]
        if count_tables:
            result["tables"] = sum(len(p.find_tables(TABLE_SETTINGS)) for p in pages)
        result["pages"] = len(pages)
        result["producer"] = pdf.metadata.get("Producer", "")
    result["no"] = path.name[:2]
    result["scanned"] = is_scanned(result, result["pages"])
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dir", type=Path, default=PDF_DIR)
    parser.add_argument("--tables", action="store_true", help="표 개수도 센다")
    parser.add_argument("--json", type=Path, help="결과를 JSON으로 저장한다")
    args = parser.parse_args()

    reports = [inspect_pdf(p, args.tables) for p in sorted(args.dir.glob("*.pdf"))]
    for r in reports:
        print(
            f"{r['no']} {'스캔' if r['scanned'] else '텍스트'} {r['pages']}쪽 {r['chars']}자 "
            f"한글{r['hangul_pct']}% 깨짐{r['replacement_chars']} 제어{r['control_chars']} "
            f"조문줄{r['article_lines']} 표{r.get('tables', '-')} 이미지쪽{r['image_pages']}"
        )
    scanned = [r["no"] for r in reports if r["scanned"]]
    print(f"총 {len(reports)}개, 스캔 {len(scanned)}개 {scanned}")
    if args.json:
        args.json.write_text(json.dumps(reports, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
