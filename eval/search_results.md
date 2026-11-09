# 검색 평가 결과

답이 있는 30문항 기준. 근거 문구가 상위 5개 청크에 있으면 정답으로 본다.

| 청킹 | 검색 | 임베딩 | 상위5 정답률 | 1위 정답률 | MRR | 유형별 | 틀린 문항 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| article | keyword | - | 90% | 70% | 0.79 | 조번호 6/6, 내용 6/7, 표 7/7, 시행일 1/1, 희귀어 5/5, 비교 1/3, 상황 적용 1/1 | Q10 Q30 Q31 |
| fixed | keyword | - | 93% | 67% | 0.76 | 조번호 5/6, 내용 6/7, 표 7/7, 시행일 1/1, 희귀어 5/5, 비교 3/3, 상황 적용 1/1 | Q03 Q10 |
| article | embedding | multilingual-e5-small | 83% | 67% | 0.74 | 조번호 6/6, 내용 6/7, 표 7/7, 시행일 1/1, 희귀어 3/5, 비교 1/3, 상황 적용 1/1 | Q10 Q22 Q26 Q30 Q31 |
| article | hybrid | multilingual-e5-small | 93% | 73% | 0.82 | 조번호 6/6, 내용 6/7, 표 7/7, 시행일 1/1, 희귀어 5/5, 비교 2/3, 상황 적용 1/1 | Q10 Q31 |
| fixed | embedding | multilingual-e5-small | 77% | 53% | 0.62 | 조번호 6/6, 내용 6/7, 표 6/7, 시행일 0/1, 희귀어 4/5, 비교 1/3, 상황 적용 0/1 | Q10 Q21 Q22 Q28 Q30 Q31 Q32 |
| fixed | hybrid | multilingual-e5-small | 93% | 60% | 0.74 | 조번호 6/6, 내용 6/7, 표 7/7, 시행일 1/1, 희귀어 5/5, 비교 2/3, 상황 적용 1/1 | Q10 Q31 |

## 문항 묶음별 상위5 정답률

| 청킹 | 검색 | 임베딩 | 기존 문항 | 추가 문항 |
| --- | --- | --- | --- | --- |
| article | keyword | - | 18/19 | 9/11 |
| fixed | keyword | - | 17/19 | 11/11 |
| article | embedding | multilingual-e5-small | 18/19 | 7/11 |
| article | hybrid | multilingual-e5-small | 18/19 | 10/11 |
| fixed | embedding | multilingual-e5-small | 17/19 | 6/11 |
| fixed | hybrid | multilingual-e5-small | 18/19 | 10/11 |
