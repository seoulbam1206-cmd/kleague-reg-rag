"""PDF 점검 검증. 실제 PDF 테스트는 파일이 없으면(저장소 복제 직후 등) 건너뛴다."""

from pathlib import Path

import pdfplumber
import pytest

from check_pdfs import TABLE_SETTINGS, inspect_pdf, is_scanned, text_metrics

PDF_DIR = Path(__file__).resolve().parent.parent / "docs_public"


def pdf_path(prefix: str) -> Path:
    found = sorted(PDF_DIR.glob(f"{prefix}_*.pdf"))
    if not found:
        pytest.skip("규정 PDF 없음: scripts/download_regulations.py로 받을 것")
    return found[0]


def test_text_metrics_counts_characters_without_whitespace():
    metrics = text_metrics(["제1조 목적\n본문", ""])
    assert metrics["chars"] == 7
    assert metrics["empty_pages"] == [2]
    assert metrics["low_text_pages"] == [1]


def test_text_metrics_detects_abnormal_characters():
    metrics = text_metrics(["가\x01나�"])
    assert metrics["control_chars"] == 1
    assert metrics["replacement_chars"] == 2


def test_article_lines_ignore_inline_references():
    metrics = text_metrics(["제1조. 명칭\n 제 2조 목적\n이는 제3조에 따른다"])
    assert metrics["article_lines"] == 2


def test_is_scanned():
    assert is_scanned({"empty_pages": [1], "image_pages": [2]}, 3) is True
    assert is_scanned({"empty_pages": [], "image_pages": [30]}, 86) is False


@pytest.mark.slow
def test_real_pdf_is_text_based():
    report = inspect_pdf(pdf_path("05"))
    assert report["pages"] == 20
    assert report["scanned"] is False
    assert report["replacement_chars"] == 0


@pytest.mark.slow
def test_flowchart_page_is_detected_as_image():
    assert inspect_pdf(pdf_path("15"))["image_pages"] == [30]


@pytest.mark.slow
def test_dashed_rule_tables_are_found():
    """09번 29쪽 표는 0.5pt 조각 괘선이라 기본 설정으로는 찾지 못한다."""
    with pdfplumber.open(pdf_path("09")) as pdf:
        page = pdf.pages[28]
        assert page.find_tables() == []
        tables = page.find_tables(TABLE_SETTINGS)
        assert len(tables) == 1
        assert "라운드별 팀 벌점 합산" in tables[0].extract()[0][0]
