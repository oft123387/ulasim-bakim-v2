"""Basit, tek-process içi "canlı güncelleme" yayıncısı (Sunucu Gönderimli
Olaylar / SSE — bkz. main.py `/api/events/stream`). Redis vb. bir mesaj
kuyruğu gerektirmez çünkü docker-compose.yml tek servis/tek konteyner
çalıştırır (bkz. docker-compose.yml) — tüm bağlı istemciler aynı process
içinde tutulur.

Neden asyncio.Queue DEĞİL: mutasyon yapan endpoint'ler (main.py) senkron
`def` fonksiyonlar olduğu için Starlette bunları ayrı bir thread pool'da
çalıştırır (bkz. starlette.concurrency.run_in_threadpool). asyncio.Queue
thread-safe DEĞİLDİR — başka bir thread'den `put_nowait` çağırmak veri
bozulmasına yol açabilir. Bunun yerine sade bir `threading.Lock` + `deque`
kullanılır; SSE tarafı (async) bunu kısa aralıklarla (polling) okur — bu,
"gerçek" push'a kıyasla en fazla ~0.5 saniye gecikme ekler ama thread
güvenliği açısından çok daha sağlamdır.
"""

import threading
import time
from collections import deque

_lock = threading.Lock()
_subscribers: dict[int, "deque[dict]"] = {}
_next_id = 0


def subscribe() -> int:
    """Yeni bir SSE bağlantısı için abone kimliği oluşturur."""
    global _next_id
    with _lock:
        sid = _next_id
        _next_id += 1
        _subscribers[sid] = deque(maxlen=50)
    return sid


def unsubscribe(sid: int) -> None:
    with _lock:
        _subscribers.pop(sid, None)


def broadcast(entity_type: str, action: str) -> None:
    """Bir mutasyon olduğunda çağrılır (bkz. services.log_action) — bağlı
    TÜM istemcilere iletilir. İstemci taraflı süzme (hangi sayfanın hangi
    entity_type'larla ilgilendiği) main.py'de değil, frontend'de yapılır —
    burası kasıtlı olarak "kaba" (coarse-grained) tutulur, basitliği korumak
    için."""
    event = {"entity_type": entity_type, "action": action, "ts": time.time()}
    with _lock:
        for q in _subscribers.values():
            q.append(event)


def poll(sid: int) -> list[dict]:
    """Bu abonenin son `poll` çağrısından beri biriken olayları döner ve
    kuyruğu boşaltır."""
    with _lock:
        q = _subscribers.get(sid)
        if q is None:
            return []
        items = list(q)
        q.clear()
        return items


def subscriber_count() -> int:
    """Sistem Durumu sayfasında gösterilebilir — o an bağlı canlı istemci sayısı."""
    with _lock:
        return len(_subscribers)
