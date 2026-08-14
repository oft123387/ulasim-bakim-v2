"""Bakım Bilgi Bankası (Faz 27).

Bakım ağacındaki bir madde artık "nasıl yapılır" bilgi kartı olarak da
okunabiliyor: açıklama metni (TreeNode.note) + video / PDF / görsel ekleri.
İçeriği admin/mühendis/amir girer, usta yalnızca görüntüler ve arar.
"""

import pytest

from .conftest import auth


@pytest.fixture(scope="module")
def kb_engineer_token(client, admin_token):
    client.post(
        "/api/users", headers=auth(admin_token),
        json={"username": "kb_muh", "full_name": "Bilgi Bankası Mühendisi", "role": "engineer", "password": "kbmuhpass1"},
    )
    r = client.post("/api/auth/login", data={"username": "kb_muh", "password": "kbmuhpass1"})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


@pytest.fixture(scope="module")
def kb_node(client, admin_token):
    """Bilgi bankası testleri için tek bir plan + tek bir yaprak madde."""
    plan = client.post(
        "/api/plans", headers=auth(admin_token),
        json={"name": "KB Test Planı", "plan_type": "km", "interval_km": 40_000},
    ).json()
    root = client.post(
        "/api/tree", headers=auth(admin_token),
        json={"name": "Fren Grubu", "plan_id": plan["id"]},
    ).json()
    node = client.post(
        "/api/tree", headers=auth(admin_token),
        json={"name": "Fren Balatası Değişimi", "parent_id": root["id"]},
    ).json()
    return {"plan": plan, "root": root, "node": node}


def test_engineer_can_write_note_but_technician_cannot(client, usta_token, kb_engineer_token, kb_node):
    nid = kb_node["node"]["id"]
    r = client.patch(
        f"/api/tree/{nid}/kb-note", headers=auth(kb_engineer_token),
        json={"note": "Balatayı sök, kalınlığı ölç, 3 mm altındaysa değiştir."},
    )
    assert r.status_code == 200, r.text
    assert r.json()["note"].startswith("Balatayı sök")

    # Usta bilgi bankası içeriğini DEĞİŞTİREMEZ (yalnızca okur).
    assert client.patch(
        f"/api/tree/{nid}/kb-note", headers=auth(usta_token), json={"note": "olmaz"},
    ).status_code == 403

    # Ağaçtan okunduğunda metin görünür durumda.
    tree = client.get(f"/api/tree?plan_id={kb_node['plan']['id']}", headers=auth(usta_token)).json()
    leaf = tree[0]["children"][0]
    assert leaf["note"].startswith("Balatayı sök")


def test_youtube_link_is_marked_as_video(client, kb_engineer_token, kb_node):
    """Tanınan video adresleri kind='video' olur ki arayüz gömülü oynatıcıda
    açsın; tanınmayanlar eskisi gibi 'link' kalır (davranış değişmez)."""
    nid = kb_node["node"]["id"]
    vid = client.post(
        f"/api/tree/{nid}/attachment-link", headers=auth(kb_engineer_token),
        json={"label": "Eğitim videosu", "url": "https://www.youtube.com/watch?v=abc123"},
    )
    assert vid.status_code == 200, vid.text
    assert vid.json()["kind"] == "video"

    doc = client.post(
        f"/api/tree/{nid}/attachment-link", headers=auth(kb_engineer_token),
        json={"label": "Talimat", "url": "https://ornek.com/talimat"},
    )
    assert doc.json()["kind"] == "link"

    # Usta ekleri görebilir ama silemez; mühendis silebilir.
    assert client.get(f"/api/tree/{nid}/attachments", headers=auth(kb_engineer_token)).status_code == 200
    assert client.delete(f"/api/attachments/{doc.json()['id']}", headers=auth(kb_engineer_token)).status_code == 200


def test_technician_can_search_knowledge(client, usta_token, kb_node):
    hits = client.get("/api/knowledge/search?q=Balata", headers=auth(usta_token))
    assert hits.status_code == 200, hits.text
    mine = [h for h in hits.json() if h["id"] == kb_node["node"]["id"]]
    assert mine, hits.json()
    hit = mine[0]
    assert hit["has_video"] is True
    # Ağaçtaki yol kökten maddeye doğru sıralı gelir (ekranda "Fren Grubu ›
    # Fren Balatası Değişimi" olarak gösterilir).
    assert hit["path"] == ["Fren Grubu", "Fren Balatası Değişimi"]
    assert hit["plan_id"] == kb_node["plan"]["id"]
    assert hit["plan_name"] == "KB Test Planı"


def test_search_needs_at_least_two_characters(client, usta_token):
    """İstemcideki eşikle aynı — tek harfle tüm ağacı taramaya gerek yok."""
    assert client.get("/api/knowledge/search?q=B", headers=auth(usta_token)).json() == []
    assert client.get("/api/knowledge/search?q=", headers=auth(usta_token)).json() == []


def test_search_matches_note_text(client, usta_token, kb_node):
    """Madde adı tutmasa bile açıklama metni içinde aranabilir."""
    hits = client.get("/api/knowledge/search?q=kalınlığı", headers=auth(usta_token)).json()
    assert any(h["id"] == kb_node["node"]["id"] for h in hits)


def test_maintenance_items_expose_source_node_id(client, admin_token, kb_node):
    """Bakım detayındaki maddeler, "Bilgi" düğmesinin Bakım Bilgi Bankası'nda
    açacağı şablon düğümünün id'sini taşır."""
    vehicle = client.post(
        "/api/vehicles", headers=auth(admin_token),
        json={"code": "KB-01", "name": "Bilgi Bankası Test Aracı", "current_km": 10_000},
    ).json()
    created = client.post(
        "/api/maintenances/manual", headers=auth(admin_token),
        json={"vehicle_id": vehicle["id"], "plan_id": kb_node["plan"]["id"], "target_km": 40_000},
    )
    assert created.status_code == 200, created.text
    detail = client.get(f"/api/maintenances/{created.json()['id']}", headers=auth(admin_token)).json()

    def walk(items):
        for i in items:
            yield i
            yield from walk(i.get("children", []))

    all_items = list(walk(detail["items"]))
    assert all_items
    assert all("source_node_id" in i for i in all_items)
    assert any(i["source_node_id"] is not None for i in all_items)


def test_technician_sees_completed_maintenances_with_completion_date(client, admin_token, usta_token, kb_node):
    """Usta takvimi (Faz 27) tamamlananları da yeşil olarak gösterir: usta,
    tamamlanma tarihine göre süzülmüş listeyi görebilmeli ve completed_at
    dolu gelmeli — takvim bakımı bu güne yerleştirir."""
    vehicle = client.post(
        "/api/vehicles", headers=auth(admin_token),
        json={"code": "KB-02", "name": "Takvim Test Aracı", "current_km": 10_000},
    ).json()
    m = client.post(
        "/api/maintenances/manual", headers=auth(admin_token),
        json={"vehicle_id": vehicle["id"], "plan_id": kb_node["plan"]["id"], "target_km": 40_000},
    ).json()

    done = client.post(f"/api/maintenances/{m['id']}/force-complete", headers=auth(admin_token))
    assert done.status_code == 200, done.text

    # Usta hem genel listede hem de "tamamlanma tarihine göre" süzülmüş
    # listede bu iş emrini görür (atanmamış olduğu için erişimi vardır).
    rows = client.get(
        "/api/maintenances?status=completed&date_field=completed&date_from=2000-01-01",
        headers=auth(usta_token),
    )
    assert rows.status_code == 200, rows.text
    mine = [r for r in rows.json() if r["id"] == m["id"]]
    assert mine, "Tamamlanan iş emri ustanın listesinde görünmedi"
    assert mine[0]["status"] == "completed"
    assert mine[0]["completed_at"], "Takvimin gün ataması completed_at'e dayanıyor"
