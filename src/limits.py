"""공개 데모의 서버 전체 사용량 제한. 세션과 상관없이 모든 질문을 함께 센다.

서버 프로세스 메모리에만 두므로 서버가 재시작하면 처음부터 다시 센다.
하루의 경계는 한국 시간 자정이다.
"""

import threading
import time
from collections import deque
from datetime import datetime, timedelta, timezone

PER_MINUTE = 10
PER_DAY = 200
KST = timezone(timedelta(hours=9))

MSG_MINUTE = f"지금 질문이 많습니다. 잠시 후 다시 질문해 주세요. (데모 전체 분당 {PER_MINUTE}회 제한)"
MSG_DAY = f"오늘 데모 사용량이 다 찼습니다. 내일 다시 이용해 주세요. (데모 전체 하루 {PER_DAY}회 제한)"


class UsageLimiter:
    def __init__(self, per_minute: int = PER_MINUTE, per_day: int = PER_DAY, clock=time.time):
        self.per_minute = per_minute
        self.per_day = per_day
        self.clock = clock
        self.lock = threading.Lock()
        self.recent: deque[float] = deque()
        self.day = ""
        self.day_count = 0

    def acquire(self) -> str:
        """질문 1회를 쓸 수 있으면 세고 빈 문자열을, 제한에 걸리면 안내 문구를 돌려준다."""
        with self.lock:
            now = self.clock()
            today = datetime.fromtimestamp(now, KST).date().isoformat()
            if today != self.day:
                self.day, self.day_count = today, 0
            if self.day_count >= self.per_day:
                return MSG_DAY
            while self.recent and now - self.recent[0] >= 60:
                self.recent.popleft()
            if len(self.recent) >= self.per_minute:
                return MSG_MINUTE
            self.recent.append(now)
            self.day_count += 1
            return ""
