# 검색 평가 결과

답이 있는 29문항 기준. 근거 문구가 상위 5개 청크에 있으면 정답으로 본다.

| 청킹 | 검색 | 임베딩 | 상위5 정답률 | 1위 정답률 | MRR | 유형별 | 틀린 문항 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| article | keyword | - | 90% | 69% | 0.78 | 조번호 6/6, 내용 6/7, 표 7/7, 시행일 1/1, 희귀어 5/5, 비교 1/3 | Q10 Q30 Q31 |
| fixed | keyword | - | 93% | 69% | 0.77 | 조번호 5/6, 내용 6/7, 표 7/7, 시행일 1/1, 희귀어 5/5, 비교 3/3 | Q03 Q10 |
| article | embedding | multilingual-e5-small | 83% | 66% | 0.73 | 조번호 6/6, 내용 6/7, 표 7/7, 시행일 1/1, 희귀어 3/5, 비교 1/3 | Q10 Q22 Q26 Q30 Q31 |
| article | hybrid | multilingual-e5-small | 90% | 72% | 0.80 | 조번호 6/6, 내용 6/7, 표 7/7, 시행일 1/1, 희귀어 4/5, 비교 2/3 | Q10 Q22 Q31 |
| fixed | embedding | multilingual-e5-small | 76% | 55% | 0.64 | 조번호 6/6, 내용 6/7, 표 6/7, 시행일 0/1, 희귀어 3/5, 비교 1/3 | Q10 Q21 Q22 Q26 Q28 Q30 Q31 |
| fixed | hybrid | multilingual-e5-small | 93% | 62% | 0.76 | 조번호 6/6, 내용 6/7, 표 7/7, 시행일 1/1, 희귀어 5/5, 비교 2/3 | Q10 Q31 |

## 문항 묶음별 상위5 정답률

| 청킹 | 검색 | 임베딩 | 기존 문항 | 추가 문항 |
| --- | --- | --- | --- | --- |
| article | keyword | - | 18/19 | 8/10 |
| fixed | keyword | - | 17/19 | 10/10 |
| article | embedding | multilingual-e5-small | 18/19 | 6/10 |
| article | hybrid | multilingual-e5-small | 18/19 | 8/10 |
| fixed | embedding | multilingual-e5-small | 17/19 | 5/10 |
| fixed | hybrid | multilingual-e5-small | 18/19 | 9/10 |
