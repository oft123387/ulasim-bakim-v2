"""Bellek-içi hız sınırlama (Faz 23 — Güvenlik sertleştirme / API Rate
Limiting).

Bu projenin docker-compose.yml'i tek bir 'app' konteyneri çalıştırdığı için
(yatay ölçeklenmiş çoklu kopya yok), harici bir bağımlılık (Redis vb.)
gerektirmeyen, kayan pencere (sliding window) sayaçlarıyla çalışan hafif bir
uygulama yeterlidir. İleride birden fazla backend kopyası arkasında
çalışılacaksa (yük dengeleyici + N konteyner) paylaşımlı bir depoya
taşınması gerekir."""

from __future__ import annotations

import time
from collections import defaultdict, deque
from threading import Lock


class SlidingWindowLimiter:
    def __init__(self, max_events: int, window_seconds: float):
        self.max_events = max_events
        self.window = window_seconds
        self._hits: dict[str, deque] = defaultdict(deque)
        self._lock = Lock()

    def allow(self, key: str) -> bool:
        """key için bir olay daha izinliyse True döner ve sayar; değilse
        sayaç artırılmadan False döner."""
        now = time.monotonic()
        with self._lock:
            dq = self._hits[key]
            while dq and now - dq[0] > self.window:
                dq.popleft()
            if len(dq) >= self.max_events:
                return False
            dq.append(now)
            return True

    def retry_after(self, key: str) -> int:
        """Sınıra takıldıysa, saniye cinsinden ne kadar sonra tekrar
        denenebileceğini döner (Retry-After başlığı için)."""
        now = time.monotonic()
        with self._lock:
            dq = self._hits.get(key)
            if not dq:
                return 0
            return max(0, int(self.window - (now - dq[0])) + 1)


# Genel API — kaba kuvvet/otomatik kazıma (scraping) girişimlerine karşı
# ikinci savunma katmanı (asıl önlem her zaman ters proxy/güvenlik duvarı
# seviyesinde olmalıdır). Yalnızca /api/* uç noktaları sayılır.
api_limiter = SlidingWindowLimiter(max_events=300, window_seconds=60)

# Girişte kaba kuvvet (brute-force) koruması — IP başına 5 dakikada en fazla
# 10 giriş denemesi (başarılı/başarısız fark etmez).
login_limiter = SlidingWindowLimiter(max_events=10, window_seconds=300)
