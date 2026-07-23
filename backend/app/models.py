from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(100))
    password_hash: Mapped[str] = mapped_column(String(200))
    # admin | engineer | technician | amir
    role: Mapped[str] = mapped_column(String(20), default="technician")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # Gmail/e-posta adresi — doğrulama kodu ile teyit edilene kadar
    # email_verified False kalır. Şifremi unuttum akışı yalnızca doğrulanmış
    # bir e-postası olan hesaplar için çalışır.
    email: Mapped[str] = mapped_column(String(200), default="")
    email_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    # Vardiyalı çalışma modu açıksa, bakım maddesi/not tamamlama seçici
    # penceresinde yalnızca aynı vardiyadaki kişiler listelenir. Kapalıysa
    # bu alan kullanılmaz — o an oturum açmış kişi otomatik atanır.
    shift_id: Mapped[int | None] = mapped_column(
        ForeignKey("shifts.id", ondelete="SET NULL"), nullable=True
    )

    shift = relationship("Shift")


class Shift(Base):
    """Vardiya (ör. 'Vardiya 1/2/3'). Paylaşılan tablet senaryosunda, aynı
    anda birden çok usta çalışıyorsa bakım maddesi tamamlama/not ekleme
    seçicisi yalnızca o an seçili vardiyadaki kişileri listeler."""

    __tablename__ = "shifts"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(50))
    order_index: Mapped[int] = mapped_column(Integer, default=0)


class VerificationCode(Base):
    """Kayıt/profil e-posta doğrulama ve şifre sıfırlama için tek kullanımlık,
    süreli doğrulama kodu. Kod düz metin saklanmaz (code_hash), brute-force'a
    karşı deneme sayısı sınırlıdır (bkz. services.CODE_MAX_ATTEMPTS)."""

    __tablename__ = "verification_codes"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    # verify_email | reset_password
    purpose: Mapped[str] = mapped_column(String(30))
    # Kodun gönderildiği adres (verify_email akışında henüz onaylanmamış
    # YENİ adres olabilir; kullanıcının o anki users.email'inden farklı
    # olabileceği için ayrıca saklanır).
    email: Mapped[str] = mapped_column(String(200))
    code_hash: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)

    user = relationship("User")


class Vehicle(Base):
    __tablename__ = "vehicles"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(20), unique=True, index=True)  # ör. 38.01
    name: Mapped[str] = mapped_column(String(100), default="")
    current_km: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    manufacturer: Mapped[str] = mapped_column(String(100), default="")
    model: Mapped[str] = mapped_column(String(100), default="")
    chassis_no: Mapped[str] = mapped_column(String(100), default="")
    notes: Mapped[str] = mapped_column(String(500), default="")
    last_km_update_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Araç Profil Sayfası: üretim yılı ve profil kapak fotoğrafı (sunucudaki
    # dosya adı; /uploads/vehicles/<filename> altında servis edilir).
    production_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    photo_filename: Mapped[str] = mapped_column(String(300), default="")

    maintenances = relationship("Maintenance", back_populates="vehicle")
    attachments = relationship(
        "VehicleAttachment",
        back_populates="vehicle",
        cascade="all, delete-orphan",
        order_by="VehicleAttachment.id",
    )


class VehicleAttachment(Base):
    """Bir araca bağlı belge/doküman: PDF, servis manueli, fotoğraf, CAD/STEP
    mühendislik dosyası, elektrik şeması veya harici bağlantı — Parça Bilgi
    Bankası'ndaki ek sistemiyle aynı desen (kind/label/filename/url)."""

    __tablename__ = "vehicle_attachments"

    id: Mapped[int] = mapped_column(primary_key=True)
    vehicle_id: Mapped[int] = mapped_column(
        ForeignKey("vehicles.id", ondelete="CASCADE"), index=True
    )
    # image | pdf | video | cad | file | link
    kind: Mapped[str] = mapped_column(String(20), default="file")
    label: Mapped[str] = mapped_column(String(200), default="")
    filename: Mapped[str] = mapped_column(String(300), default="")
    original_name: Mapped[str] = mapped_column(String(300), default="")
    url: Mapped[str] = mapped_column(String(600), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)

    vehicle = relationship("Vehicle", back_populates="attachments")


class MaintenancePlan(Base):
    """Periyodik bakım planı: her interval_km'de bir bakım.

    Alt limit = hedef_km - interval_km * lower_pct/100
    Üst limit = hedef_km + interval_km * upper_pct/100
    Örn. interval=25.000, lower=50, upper=10 → 1.025.000 km bakımı
    için aralık [1.012.500 , 1.027.500].
    """

    __tablename__ = "maintenance_plans"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    interval_km: Mapped[int] = mapped_column(Integer)
    lower_pct: Mapped[float] = mapped_column(Float, default=50.0)
    upper_pct: Mapped[float] = mapped_column(Float, default=10.0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # Boş ("") = "genel" plan, filodaki TÜM araçlara (modelinden bağımsız)
    # uygulanır — mevcut/eski planların hepsi bu şekildedir, geriye dönük
    # uyumluluk için varsayılan budur. Doluysa yalnızca Vehicle.model'i
    # birebir eşleşen araçlara uygulanır (farklı araç modelleri için ayrı
    # bakım ağaçları/periyotları tanımlamayı sağlar).
    vehicle_model: Mapped[str] = mapped_column(String(100), default="")


class TreeNode(Base):
    """Bakım ağacı şablonu (admin düzenler, sınırsız seviye).

    Kök düğümler (parent_id=None) bir bakım planına aittir (plan_id).
    plan_id=None olan kök düğümler "genel/ortak" şablondur: o plana özel
    ağaç tanımlanmamışsa yedek (fallback) olarak kullanılır.
    """

    __tablename__ = "tree_nodes"

    id: Mapped[int] = mapped_column(primary_key=True)
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("tree_nodes.id", ondelete="CASCADE"), nullable=True
    )
    plan_id: Mapped[int | None] = mapped_column(
        ForeignKey("maintenance_plans.id", ondelete="CASCADE"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(200))
    order_index: Mapped[int] = mapped_column(Integer, default=0)
    # Faz 10: admin'in maddeye eklediği açıklama/talimat metni (usta görür).
    note: Mapped[str] = mapped_column(Text, default="")

    children = relationship(
        "TreeNode", cascade="all, delete-orphan", order_by="TreeNode.order_index"
    )
    attachments = relationship(
        "NodeAttachment",
        back_populates="node",
        cascade="all, delete-orphan",
        order_by="NodeAttachment.id",
    )


class Maintenance(Base):
    """Bakım + iş emri. Aynı (araç, plan, hedef km) için yalnızca bir kez açılır.

    plan_id, bu iş emrini "yöneten" (adını veren) en yüksek periyotlu plandır.
    Hedef km birden fazla planın periyoduna bölünüyorsa (ör. 150.000 km hem
    25.000 hem 50.000'e bölünür), checklist tüm uygulanabilir planların
    birleşimidir (bkz. services.check_and_open_maintenances).
    """

    __tablename__ = "maintenances"
    __table_args__ = (
        UniqueConstraint("vehicle_id", "plan_id", "target_km", name="uq_once"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    vehicle_id: Mapped[int] = mapped_column(ForeignKey("vehicles.id"))
    plan_id: Mapped[int] = mapped_column(ForeignKey("maintenance_plans.id"))
    target_km: Mapped[int] = mapped_column(Integer)
    km_at_open: Mapped[int] = mapped_column(Integer)
    # pending (Bekliyor) | in_progress (Devam Ediyor) | completed (Tamamlandı)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    assigned_to_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    vehicle = relationship("Vehicle", back_populates="maintenances")
    plan = relationship("MaintenancePlan")
    assigned_to = relationship("User")
    items = relationship(
        "MaintenanceItem",
        back_populates="maintenance",
        cascade="all, delete-orphan",
        order_by="MaintenanceItem.order_index",
    )
    notes = relationship(
        "MaintenanceNote",
        back_populates="maintenance",
        cascade="all, delete-orphan",
        order_by="MaintenanceNote.created_at",
    )


class MaintenanceItem(Base):
    """Bakım açıldığında şablondan kopyalanan ağaç (snapshot).

    source_node_id: bu maddenin kopyalandığı TreeNode şablon düğümünün id'si
    (varsa; kademeli bakımlardaki sarmalayıcı grup düğümlerinde None olur).
    3D model üzerindeki parçaları (PartHotspot) doğru maddeyle eşleştirmek
    için kullanılır.
    """

    __tablename__ = "maintenance_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    maintenance_id: Mapped[int] = mapped_column(
        ForeignKey("maintenances.id", ondelete="CASCADE"), index=True
    )
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("maintenance_items.id", ondelete="CASCADE"), nullable=True
    )
    source_node_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    order_index: Mapped[int] = mapped_column(Integer, default=0)
    # Kademeli bakımda bu maddenin ait olduğu periyot(lar), km olarak virgülle
    # ayrılmış (ör. "25000,50000,100000"). Tek periyotlu bakımda boştur.
    tiers: Mapped[str] = mapped_column(String(100), default="")
    is_completed: Mapped[bool] = mapped_column(Boolean, default=False)
    is_locked: Mapped[bool] = mapped_column(Boolean, default=False)
    completed_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Usta/mühendisin bakım SIRASINDA bu maddeye özel eklediği not (şablon
    # notundan farklı — TreeNode.note admin'in talimatıdır, bu ise sahada
    # tikleme/inceleme sırasında düşülen bulgu/açıklamadır). Ağaçta bir
    # ünlem rozetiyle vurgulanır.
    exec_note: Mapped[str] = mapped_column(Text, default="")
    exec_note_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    exec_note_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    maintenance = relationship("Maintenance", back_populates="items")
    completed_by = relationship("User", foreign_keys=[completed_by_id])
    exec_note_by = relationship("User", foreign_keys=[exec_note_by_id])


class MaintenanceNote(Base):
    """Usta tarafından bir iş emrine eklenen serbest metin not (checklist
    dışında bulunan ek arıza/bakım ihtiyacı bildirmek için). Paylaşılan
    tablet senaryosunda (tek oturum, çoklu kullanıcı) notu giren kişi,
    o an oturum açmış kullanıcıdan bağımsız olarak seçilebilir; bu yüzden
    author_id nullable'dır ve seçilemezse oturum sahibine düşer."""

    __tablename__ = "maintenance_notes"

    id: Mapped[int] = mapped_column(primary_key=True)
    maintenance_id: Mapped[int] = mapped_column(
        ForeignKey("maintenances.id", ondelete="CASCADE"), index=True
    )
    author_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)

    maintenance = relationship("Maintenance", back_populates="notes")
    author = relationship("User")


class Model3D(Base):
    """Admin tarafından yüklenen 3D tramvay modeli (.glb). Plan'dan bağımsızdır
    (aynı fiziksel tramvay tüm periyotlar için ortaktır); üzerindeki parça
    eşleşmeleri (PartHotspot) plana özeldir."""

    __tablename__ = "models_3d"

    id: Mapped[int] = mapped_column(primary_key=True)
    label: Mapped[str] = mapped_column(String(100), default="")
    filename: Mapped[str] = mapped_column(String(300))
    order_index: Mapped[int] = mapped_column(Integer, default=0)

    hotspots = relationship(
        "PartHotspot",
        back_populates="model",
        cascade="all, delete-orphan",
        order_by="PartHotspot.id",
    )


class PartHotspot(Base):
    """3D model üzerinde bir mesh/parça adını, belirli bir bakım planının
    ağaç maddesiyle eşleştirir (ör. mesh_name='Coupler Scharfenberg' ->
    25.000 km planındaki 'Otomatik Kuplör' maddesi). Aynı fiziksel parça
    farklı planlarda farklı maddelere karşılık gelebileceği için plan_id
    taşır."""

    __tablename__ = "part_hotspots"

    id: Mapped[int] = mapped_column(primary_key=True)
    model_id: Mapped[int] = mapped_column(
        ForeignKey("models_3d.id", ondelete="CASCADE"), index=True
    )
    plan_id: Mapped[int] = mapped_column(
        ForeignKey("maintenance_plans.id", ondelete="CASCADE"), index=True
    )
    tree_node_id: Mapped[int] = mapped_column(
        ForeignKey("tree_nodes.id", ondelete="CASCADE"), index=True
    )
    mesh_name: Mapped[str] = mapped_column(String(300))
    label: Mapped[str] = mapped_column(String(200), default="")

    model = relationship("Model3D", back_populates="hotspots")
    tree_node = relationship("TreeNode")


class NodeAttachment(Base):
    """Faz 10 — Admin bilgilendirme eki. Bir bakım ağacı maddesine (TreeNode)
    bağlı fotoğraf / PDF / teknik talimat dosyası veya (video vb.) bağlantı.

    kind:
      * 'image' — resim dosyası (usta ekranında önizlenir)
      * 'pdf'   — PDF dosyası
      * 'file'  — diğer dosya (talimat, teknik çizim vb.)
      * 'link'  — harici bağlantı (url alanında), dosya yok

    Usta bir bakımı açtığında, ilgili maddenin source_node_id'si üzerinden
    bu ekler o maddede görüntülenir.
    """

    __tablename__ = "node_attachments"

    id: Mapped[int] = mapped_column(primary_key=True)
    node_id: Mapped[int] = mapped_column(
        ForeignKey("tree_nodes.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(20), default="file")
    label: Mapped[str] = mapped_column(String(200), default="")
    filename: Mapped[str] = mapped_column(String(300), default="")  # sunucudaki dosya adı
    original_name: Mapped[str] = mapped_column(String(300), default="")  # yüklenen özgün ad
    url: Mapped[str] = mapped_column(String(600), default="")  # kind='link' için
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)

    node = relationship("TreeNode", back_populates="attachments")


class PartCatalogItem(Base):
    """Parça Bilgi Bankası — arıza/bakım anında referans için, sık kullanılan
    fiziksel parçaların (buji, kuplör, vb.) görselli kataloğu. Her parçanın
    dokümanı, kablo/devre şeması, eğitim videosu gibi ekleri
    (PartCatalogAttachment) olabilir. Bakım ağacından bağımsızdır — genel
    bir referans kütüphanesidir."""

    __tablename__ = "part_catalog_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(String(500), default="")
    image_filename: Mapped[str] = mapped_column(String(300), default="")  # kapak görseli
    order_index: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    # Bir parçanın (ör. "Motorlu Boji") alt parçaları olabilir (ör. "Motor",
    # "Fren Sistemi") — üst parçanın detay sayfasında listelenir, sürükleyip
    # bırakarak sıralanabilir. Üst parça silinirse alt parçalar da silinir.
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("part_catalog_items.id", ondelete="CASCADE"), nullable=True, index=True
    )

    attachments = relationship(
        "PartCatalogAttachment",
        back_populates="part",
        cascade="all, delete-orphan",
        order_by="PartCatalogAttachment.id",
    )
    children = relationship(
        "PartCatalogItem",
        cascade="all, delete-orphan",
        order_by="PartCatalogItem.order_index",
    )


class PartCatalogAttachment(Base):
    """Bir parça kataloğu maddesine bağlı doküman / kablo şeması / devre
    şeması / eğitim videosu / diğer ek.

    kind:
      * 'image' — resim dosyası
      * 'pdf'   — PDF dosyası
      * 'video' — video dosyası (yüklenmiş, doğrudan oynatılır)
      * 'file'  — diğer dosya
      * 'link'  — harici bağlantı (url alanında; ör. YouTube eğitim videosu)
    label: kullanıcının serbestçe girdiği kategori/açıklama (ör. "Kablo
    Şeması", "Devre Şeması", "Eğitim Videosu", "Teknik Doküman")."""

    __tablename__ = "part_catalog_attachments"

    id: Mapped[int] = mapped_column(primary_key=True)
    part_id: Mapped[int] = mapped_column(
        ForeignKey("part_catalog_items.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(20), default="file")
    label: Mapped[str] = mapped_column(String(200), default="")
    filename: Mapped[str] = mapped_column(String(300), default="")
    original_name: Mapped[str] = mapped_column(String(300), default="")
    url: Mapped[str] = mapped_column(String(600), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)

    part = relationship("PartCatalogItem", back_populates="attachments")


class AuditLog(Base):
    """Faz 17 — Değiştirilemez işlem kaydı. Yalnızca eklenir (append-only);
    hiçbir uç nokta güncellemez/siler. Yalnızca Admin görüntüleyebilir.

    Aktörün adı (actor_name) kayıt anında sabitlenir; kullanıcı sonradan
    silinse/yeniden adlandırılsa bile geçmiş bozulmaz.
    """

    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, index=True)
    actor_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    actor_name: Mapped[str] = mapped_column(String(120), default="")
    # create | update | delete | complete | unlock | login | import | export ...
    action: Mapped[str] = mapped_column(String(30), index=True)
    # vehicle | plan | user | tree_node | maintenance | item | attachment | model3d ...
    entity_type: Mapped[str] = mapped_column(String(40), index=True)
    entity_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    summary: Mapped[str] = mapped_column(String(300), default="")
    detail: Mapped[str] = mapped_column(Text, default="")  # eski→yeni vb.
    # Yapılandırılmış eski/yeni değer (yalnızca anlamlı olduğunda doldurulur
    # — ör. rol/izin/km değişikliği); IP ve tarayıcı bilgisi istekten
    # otomatik yakalanır (bkz. main.py _capture_request_context middleware).
    old_value: Mapped[str] = mapped_column(Text, default="")
    new_value: Mapped[str] = mapped_column(Text, default="")
    ip_address: Mapped[str] = mapped_column(String(64), default="")
    user_agent: Mapped[str] = mapped_column(String(300), default="")

    actor = relationship("User")


class AppSetting(Base):
    """Basit anahtar/değer ayar tablosu. Şu an günlük KM simülasyonu için
    kullanılıyor (km_sim_enabled, km_sim_min, km_sim_max, km_sim_last_date)."""

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(50), primary_key=True)
    value: Mapped[str] = mapped_column(String(200), default="")


class RolePermission(Base):
    """Rol bazlı ayrıntılı yetki matrisi (Yetki Yönetimi).

    Admin her zaman tüm izinlere sahiptir (kod içinde sabit, bu tabloda
    satırı olmaz/olsa da yok sayılır) — kilitlenmeyi önlemek için admin
    yetkileri buradan kısıtlanamaz. Mühendis ve usta rolleri için her
    izin (permission) ayrı ayrı açık/kapalı tutulur.

    permission değerleri: view_maintenance, edit_maintenance,
    delete_maintenance, approve_maintenance, export_excel, manage_users
    """

    __tablename__ = "role_permissions"
    __table_args__ = (
        UniqueConstraint("role", "permission", name="uq_role_permission"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    role: Mapped[str] = mapped_column(String(20), index=True)
    permission: Mapped[str] = mapped_column(String(40), index=True)
    allowed: Mapped[bool] = mapped_column(Boolean, default=True)


class Suggestion(Base):
    """Mühendis/usta/amirin bir araç ve (varsa) bakım periyodu için sahada
    fark ettiği bir eksikliği/iyileştirmeyi bildirdiği öneri. Admin bunları
    tek bir yerden görür, değerlendirir (Onayla/Reddet), bir açıklama notu
    düşer ve uygun görürse Bakım Ağacı'na madde olarak ekleyip 'Ağaca
    Eklendi' olarak işaretler. Bildirim Merkezi'nde yeni öneriler audit_log
    üzerinden (action='create', entity_type='suggestion') otomatik görünür,
    ayrıca bu tablodan da 'pending' sayısı doğrudan sorgulanabilir."""

    __tablename__ = "suggestions"

    id: Mapped[int] = mapped_column(primary_key=True)
    vehicle_id: Mapped[int] = mapped_column(
        ForeignKey("vehicles.id", ondelete="CASCADE"), index=True
    )
    # Öneri belirli bir bakım periyoduna yönelikse plan; genel bir öneriyse
    # (herhangi bir periyoda özgü değilse) None olabilir.
    plan_id: Mapped[int | None] = mapped_column(
        ForeignKey("maintenance_plans.id", ondelete="SET NULL"), nullable=True
    )
    text: Mapped[str] = mapped_column(Text)
    # pending | approved | rejected | added
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    # Admin'in değerlendirme notu (onay/red gerekçesi veya nasıl ele alındığı).
    admin_note: Mapped[str] = mapped_column(Text, default="")
    created_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, index=True)
    reviewed_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    vehicle = relationship("Vehicle")
    plan = relationship("MaintenancePlan")
    created_by = relationship("User", foreign_keys=[created_by_id])
    reviewed_by = relationship("User", foreign_keys=[reviewed_by_id])


class AlarmState(Base):
    """Canlı Alarm Merkezi'ndeki bir alarmın iş akışı durumu (Faz 22).

    Alarmların kendisi her istekte anlık hesaplanır (bkz. /api/alarms),
    kalıcı bir tabloda tutulmaz; ama bir alarmın "kimliği" (vehicle_id,
    target_km) zaman içinde sabit kaldığından (aynı araç aynı hedef km'ye
    ulaşana kadar), bu ikili üzerinden durum bilgisi (kritiklik/öncelik,
    onaylandı/atandı/çözüldü/kapatıldı) ayrı bir tabloda saklanır ve alarm
    her hesaplandığında bu tablodan eşlenir. Bir satır yoksa alarm ilk kez
    görüldüğünde otomatik oluşturulur (öncelik o anki seviyeden gelir)."""

    __tablename__ = "alarm_states"
    __table_args__ = (
        UniqueConstraint("vehicle_id", "target_km", name="uq_alarm_state"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    vehicle_id: Mapped[int] = mapped_column(
        ForeignKey("vehicles.id", ondelete="CASCADE"), index=True
    )
    target_km: Mapped[int] = mapped_column(Integer)
    plan_name: Mapped[str] = mapped_column(String(100), default="")
    # low | normal | high | critical — ilk oluşturulduğunda hesaplanan
    # seviyeden (critical/warning/normal) gelir, sonra elle değiştirilebilir.
    priority: Mapped[str] = mapped_column(String(20), default="normal")
    acknowledged: Mapped[bool] = mapped_column(Boolean, default=False)
    acknowledged_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    assigned_to_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    resolved: Mapped[bool] = mapped_column(Boolean, default=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    closed: Mapped[bool] = mapped_column(Boolean, default=False)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.now, onupdate=datetime.now
    )

    vehicle = relationship("Vehicle")
    acknowledged_by = relationship("User", foreign_keys=[acknowledged_by_id])
    assigned_to = relationship("User", foreign_keys=[assigned_to_id])


class RefreshToken(Base):
    """JWT erişim jetonu + yenileme jetonu (refresh token) deseni (Faz 23 —
    Güvenlik sertleştirme). Erişim jetonu (auth.py'daki JWT) kısa ömürlüdür
    (2 saat); oturumun uzun süre açık kalabilmesi için bu tablodaki uzun
    ömürlü (30 gün) yenileme jetonları kullanılır. Ham jeton DEĞİL, SHA-256
    özeti saklanır (veritabanı sızıntısında doğrudan kullanılabilir jeton
    ele geçmesin diye). Her yenilemede eski jeton iptal edilip yenisi
    üretilir (rotation) — böylece çalınan bir jeton yalnızca bir kez
    kullanılabilir; sonraki kullanım denemesi (jeton zaten iptal edildiği
    için) reddedilir ve bu durum tespit edilebilir."""

    __tablename__ = "refresh_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    ip_address: Mapped[str] = mapped_column(String(64), default="")
    user_agent: Mapped[str] = mapped_column(String(300), default="")

    user = relationship("User")
