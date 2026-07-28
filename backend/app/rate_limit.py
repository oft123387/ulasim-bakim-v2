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


class AccountLockout:
    """Hesap bazlı geçici kilitleme (Faz 24 — Güvenlik sertleştirme, 2. tur).

    Yukarıdaki login_limiter IP bazlıdır; bu ise KULLANICI ADI bazlıdır —
    farklı IP'lerden (ör. botnet/dağıtık kaba kuvvet) aynı hesaba yapılan
    deneme dizisini de yakalar. Yalnızca BAŞARISIZ denemeler sayılır; bir
    başarılı girişte sayaç sıfırlanır."""

    def __init__(self, max_attempts: int, lockout_seconds: float):
        self.max_attempts = max_attempts
        self.lockout_seconds = lockout_seconds
        self._failures: dict[str, int] = defaultdict(int)
        self._locked_until: dict[str, float] = {}
        self._lock = Lock()

    def check(self, key: str) -> int:
        """Hesap şu an kilitliyse kalan saniyeyi (>0) döner; değilse 0."""
        now = time.monotonic()
        with self._lock:
            until = self._locked_until.get(key)
            if until is None:
                return 0
            remaining = until - now
            if remaining <= 0:
                self._locked_until.pop(key, None)
                self._failures.pop(key, None)
                return 0
            return int(remaining) + 1

    def record_failure(self, key: str) -> int:
        """Başarısız denemeyi sayar. Eşiğe YENİ ulaşıldıysa (bu denemeyle
        kilitlendiyse) kilit süresini (saniye) döner; aksi halde 0."""
        with self._lock:
            self._failures[key] += 1
            if self._failures[key] >= self.max_attempts:
                self._locked_until[key] = time.monotonic() + self.lockout_seconds
                return int(self.lockout_seconds)
            return 0

    def record_success(self, key: str) -> None:
        with self._lock:
            self._failures.pop(key, None)
            self._locked_until.pop(key, None)


# 5 ardışık başarısız denemeden sonra hesap 15 dakika kilitlenir.
account_lockout = AccountLockout(max_attempts=5, lockout_seconds=15 * 60)
