# 검색 평가 결과

답이 있는 19문항 기준. 근거 문구가 상위 5개 청크에 있으면 정답으로 본다.

| 청킹 | 검색 | 임베딩 | 상위5 정답률 | 1위 정답률 | MRR | 유형별 | 틀린 문항 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| article | keyword | - | 95% | 84% | 0.88 | 조번호 6/6, 내용 6/7, 표 5/5, 시행일 1/1 | Q10 |
| fixed | keyword | - | 89% | 74% | 0.81 | 조번호 5/6, 내용 6/7, 표 5/5, 시행일 1/1 | Q03 Q10 |
| article | embedding | multilingual-e5-small | 95% | 89% | 0.92 | 조번호 6/6, 내용 6/7, 표 5/5, 시행일 1/1 | Q10 |
| article | hybrid | multilingual-e5-small | 95% | 89% | 0.91 | 조번호 6/6, 내용 6/7, 표 5/5, 시행일 1/1 | Q10 |
| fixed | embedding | multilingual-e5-small | 89% | 74% | 0.79 | 조번호 6/6, 내용 6/7, 표 5/5, 시행일 0/1 | Q10 Q21 |
| fixed | hybrid | multilingual-e5-small | 95% | 79% | 0.86 | 조번호 6/6, 내용 6/7, 표 5/5, 시행일 1/1 | Q10 |
| article | embedding | ko-sroberta-multitask | 74% | 68% | 0.71 | 조번호 6/6, 내용 4/7, 표 4/5, 시행일 0/1 | Q08 Q10 Q13 Q18 Q21 |
| article | hybrid | ko-sroberta-multitask | 84% | 74% | 0.76 | 조번호 6/6, 내용 5/7, 표 5/5, 시행일 0/1 | Q08 Q10 Q21 |
| fixed | embedding | ko-sroberta-multitask | 68% | 42% | 0.53 | 조번호 5/6, 내용 4/7, 표 3/5, 시행일 1/1 | Q01 Q08 Q09 Q10 Q17 Q18 |
| fixed | hybrid | ko-sroberta-multitask | 84% | 58% | 0.69 | 조번호 6/6, 내용 5/7, 표 4/5, 시행일 1/1 | Q08 Q10 Q17 |
