from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
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
    manufacturer: Mapped[str] = mapped_column(String(100), default="")
    model: Mapped[str] = mapped_column(String(100), default="")
    chassis_no: Mapped[str] = mapped_column(String(100), default="")
    notes: Mapped[str] = mapped_column(String(500), default="")
    last_km_update_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Araç Profil Sayfası: üretim yılı ve profil kapak fotoğrafı (sunucudaki
    # dosya adı; /uploads/vehicles/<filename> altında servis edilir).
    production_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    photo_filename: Mapped[str] = mapped_column(String(300), default="")
    # Süre bazlı (aylık/yıllık) bakım planları için başlangıç referans tarihi
    # ("hizmete başlama tarihi"). Bu tarih olmadan süre bazlı bakımların
    # "kaç ay geçti" hesabı yapılamaz — bkz. services.check_and_open_time_maintenances.
    # Girilmezse araç oluşturulduğu gün otomatik atanır (create_vehicle).
    commissioning_date: Mapped[date | None] = mapped_column(Date, nullable=True)

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

    Açılma (tolerans) penceresi iki farklı şekilde tanımlanabilir
    (threshold_type):
      - "percent" (varsayılan, geriye dönük uyumlu):
        Alt limit = hedef_km - interval_km * lower_pct/100
        Üst limit = hedef_km + interval_km * upper_pct/100
        Örn. interval=25.000, lower=50, upper=10 → 1.025.000 km bakımı
        için aralık [1.012.500 , 1.027.500].
      - "km" (sabit kalan km):
        Alt limit = hedef_km - lower_km
        Üst limit = hedef_km + upper_km
        Örn. hedef=25.000, lower_km=2000, upper_km=500 → aralık
        [23.000 , 25.500]. Periyot büyüklüğünden bağımsız, sabit bir
        kilometre payı isteyen kullanıcılar için (ör. "hedeften 2000 km
        önce açılsın" gibi periyottan bağımsız net bir kural).

    plan_type ("km" | "time"): "time" ise interval_km yerine interval_months
    kullanılır — periyot kilometre değil takvim ayıdır (ör. 1/3/6 ay,
    1/2/6/10 yıl → 12/24/72/120 ay). Açılma mantığı KM motoruyla birebir
    aynıdır (bkz. services.check_and_open_time_maintenances) — sadece "km"
    yerine "aracın hizmete başlama tarihinden geçen ay sayısı" kullanılır.
    Bu durumda lower_pct/upper_pct de aynı şekilde ama AY biriminde
    yorumlanır (threshold_type/lower_km/upper_km süre planlarında kullanılmaz).
    """

    __tablename__ = "maintenance_plans"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    interval_km: Mapped[int] = mapped_column(Integer)
    # "percent" veya "km" — hangi alan çiftinin (lower_pct/upper_pct ya da
    # lower_km/upper_km) kullanılacağını belirler.
    threshold_type: Mapped[str] = mapped_column(String(10), default="percent")
    lower_pct: Mapped[float] = mapped_column(Float, default=5.0)
    upper_pct: Mapped[float] = mapped_column(Float, default=10.0)
    lower_km: Mapped[float] = mapped_column(Float, default=0.0)
    upper_km: Mapped[float] = mapped_column(Float, default=0.0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # Boş ("") = "genel" plan, filodaki TÜM araçlara (modelinden bağımsız)
    # uygulanır — mevcut/eski planların hepsi bu şekildedir, geriye dönük
    # uyumluluk için varsayılan budur. Doluysa yalnızca Vehicle.model'i
    # birebir eşleşen araçlara uygulanır (farklı araç modelleri için ayrı
    # bakım ağaçları/periyotları tanımlamayı sağlar).
    vehicle_model: Mapped[str] = mapped_column(String(100), default="")
    # "km" (varsayılan, geriye dönük uyumlu) veya "time" (süre bazlı).
    plan_type: Mapped[str] = mapped_column(String(10), default="km")
    # Yalnızca plan_type="time" için: periyot, ay cinsinden (1 ay, 3 ay,
    # 12 ay = 1 yıl, 72 ay = 6 yıl, vb.).
    interval_months: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Toplu katalog içe aktarımında, bir periyot (ör. 800.000 km) TEMEL bir
    # periyodun (ör. 200.000 km) tam katıysa: kendi başına AYRI bir bakım
    # sayılmaz (200.000 km planı zaten 800.000'de de otomatik tetiklenir —
    # km motoru periyodun her katında tekrar eder), bu yüzden bu plan
    # is_catalog_only=True olarak işaretlenir:
    #   - Bakım Planları sayfasında (GET /api/plans varsayılanı) GÖSTERİLMEZ,
    #     kendi başına bakım/iş emri AÇMAZ (services.check_and_open_* bu
    #     planları eler).
    #   - Bakım Ağacı sayfasında ("Bakım Planı" seçicisinde) yine de
    #     GÖRÜNÜR ve düzenlenebilir — kaynak katalogdaki içeriği kaybolmasın,
    #     kullanıcı o periyoda özel maddeleri görebilsin diye
    #     (GET /api/plans?include_catalog_only=true).
    is_catalog_only: Mapped[bool] = mapped_column(Boolean, default=False)


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
    # Toplu bakım kataloğu içe aktarmasından gelen "KART KODU" (üretici bakım
    # kartı/talimat referans numarası, ör. "SKA-MRM-13-A03", "B0734"). Madde
    # adının yanında küçük bir rozet olarak gösterilir — bkz. MaintenancesPage
    # .card-code-pill (pdf-pill ile aynı desen).
    card_code: Mapped[str] = mapped_column(String(50), default="")
    # Bir bakım açıldığında bu maddenin GENEL varsayılan durumu: True (varsayılan)
    # ise her araçta normal şekilde ustanın tiklemesi gerekir. Admin/mühendis/amir
    # bunu "zorunlu değil" yaparsa (bkz. PATCH /api/tree/{id}/mandatory), BUNDAN
    # SONRA açılan iş emirlerinde bu madde otomatik tiklenmiş gelir — usta ekstra
    # işlem yapmaz. Belirli tek tek araçlar için bu genel değeri geçersiz kılmak
    # üzere TreeNodeVehicleException kullanılır (bkz. aşağıda).
    is_mandatory: Mapped[bool] = mapped_column(Boolean, default=True)

    children = relationship(
        "TreeNode", cascade="all, delete-orphan", order_by="TreeNode.order_index"
    )
    attachments = relationship(
        "NodeAttachment",
        back_populates="node",
        cascade="all, delete-orphan",
        order_by="NodeAttachment.id",
    )
    vehicle_exceptions = relationship(
        "TreeNodeVehicleException",
        cascade="all, delete-orphan",
        order_by="TreeNodeVehicleException.id",
    )


class TreeNodeVehicleException(Base):
    """TreeNode.is_mandatory'nin GENEL (tüm araçlar için geçerli) varsayılanını
    tek bir araç için geçersiz kılar — ör. bir madde genelde zorunluyken belirli
    bir araçta (o parça zaten değiştirildiği/o araca özel bir durum olduğu için)
    opsiyonel yapılabilir, ya da tam tersi (genelde opsiyonel ama bu araçta
    zorunlu). Yalnızca satırı olan (node_id, vehicle_id) çiftleri için istisna
    uygulanır; başka hiçbir araç bundan etkilenmez — bkz. services.py
    _effective_node_mandatory."""

    __tablename__ = "tree_node_vehicle_exceptions"
    __table_args__ = (
        UniqueConstraint("node_id", "vehicle_id", name="uq_tree_node_vehicle_exception"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    node_id: Mapped[int] = mapped_column(
        ForeignKey("tree_nodes.id", ondelete="CASCADE"), index=True
    )
    vehicle_id: Mapped[int] = mapped_column(
        ForeignKey("vehicles.id", ondelete="CASCADE"), index=True
    )
    is_mandatory: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)

    vehicle = relationship("Vehicle")


class TreeNodePartUsage(Base):
    """Bir bakım ağacı maddesinin (genellikle en alt seviye/yaprak madde)
    tamamlanması sırasında kullanılan parça(lar) ve adetleri.

    Usta bu maddeyi işaretleyip tamamladığında (bkz. services.complete_item
    / force_complete_maintenance), buradaki her satır için
    PartCatalogItem.stock_qty otomatik olarak `qty` kadar düşülür (bkz.
    services._adjust_stock_for_node); madde kilidi açılıp sıfırlanırsa
    (services.unlock_item) aynı miktar geri eklenir. Aynı (tree_node_id,
    part_id) çifti için tek satır olur — tekrar eklenirse qty güncellenir
    (upsert, bkz. main.py add_node_part_usage)."""

    __tablename__ = "tree_node_part_usages"
    __table_args__ = (
        UniqueConstraint("tree_node_id", "part_id", name="uq_node_part"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tree_node_id: Mapped[int] = mapped_column(
        ForeignKey("tree_nodes.id", ondelete="CASCADE"), index=True
    )
    part_id: Mapped[int] = mapped_column(
        ForeignKey("part_catalog_items.id", ondelete="CASCADE"), index=True
    )
    # Float (Integer değil): "1 kg'lık üründen bu maddede 0.2 kg kullanılır"
    # gibi kesirli miktarları desteklemek için — bkz. PartCatalogItem.unit.
    qty: Mapped[float] = mapped_column(Float, default=1.0)

    part = relationship("PartCatalogItem")


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
    # KM planlarında gerçek hedef km. SÜRE planlarında (plan.plan_type=="time")
    # bu sütun hâlâ NOT NULL olduğu için ve mevcut uq_once benzersizlik kısıtını
    # (vehicle_id, plan_id, target_km) şema değişikliği yapmadan yeniden
    # kullanabilmek için, süre bakımlarında target_km = -(hedef ay sayısı)
    # (negatif "sentinel" değer) tutulur — asla gerçek bir km hedefiyle
    # çakışmaz (km hedefleri her zaman pozitiftir) ve aynı planın art arda
    # gelen her ay/yıl tekrarı için ayrı, benzersiz bir değer üretir. Gerçek
    # takvim tarihi için her zaman target_date alanına bakılmalı; target_km
    # süre bakımlarında YALNIZCA dahili benzersizlik amaçlıdır, kullanıcıya
    # asla gösterilmez (bkz. services.check_and_open_time_maintenances).
    target_km: Mapped[int] = mapped_column(Integer)
    # Yalnızca süre bazlı bakımlarda dolu — bu iş emrinin hedeflediği takvim
    # tarihi (aracın hizmete başlama tarihinden + N ay). KM bakımlarında None.
    target_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    km_at_open: Mapped[int] = mapped_column(Integer)
    # pending (Bekliyor) | in_progress (Devam Ediyor) | completed (Tamamlandı)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    assigned_to_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    # DİKKAT: started_at, teknisyenin checklist'te İLK maddeyi tamamladığı an
    # (status pending -> in_progress geçişi, bkz. services.complete_item) set
    # edilir — aşağıdaki is_activated/activated_at ile KARIŞTIRILMAMALI.
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Faz 26 — Bakım başlatma onayı: otomatik-açma (check_and_open_maintenances)
    # bir iş emrini artık DOĞRUDAN görünür/çalışılabilir yapmaz; yalnızca bir
    # "aday" olarak oluşturur (is_activated=False). Admin/Mühendis (yetkisi
    # olan roller) Bakımlar sayfasındaki "Başlatılmayı Bekleyenler" bölümünden
    # bu adayı gözden geçirip "Başlat" demeden iş emri; Bakımlar listesinde,
    # Takvim'de, Dashboard'da ve Alarm Merkezi'nde HİÇBİR role görünmez ve
    # teknisyen ekranında çalışılamaz. Eski (bu özellikten önce açılmış)
    # kayıtlar geriye dönük uyumluluk için migrate.py'de is_activated=1 ile
    # doldurulur — bu özellik yalnızca BUNDAN SONRA açılan yeni adaylara
    # uygulanır.
    is_activated: Mapped[bool] = mapped_column(Boolean, default=False)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    activated_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    # "Başlatılmayı Bekleyenler" listesinde admin/mühendis "Başlat" derken
    # bugün yerine ileri bir tarih seçebilir — bu durumda hemen değil, o
    # tarih geldiğinde arka plan zamanlayıcısı tarafından otomatik başlatılır
    # (bkz. services.schedule_maintenance_start / run_scheduled_maintenance_starts).
    # is_activated hâlâ False kalır ta ki gerçekten başlayana kadar; bu alan
    # yalnızca "ne zaman otomatik başlasın" bilgisini tutar.
    scheduled_start_at: Mapped[date | None] = mapped_column(Date, nullable=True)

    vehicle = relationship("Vehicle", back_populates="maintenances")
    plan = relationship("MaintenancePlan")
    assigned_to = relationship("User", foreign_keys=[assigned_to_id])
    activated_by = relationship("User", foreign_keys=[activated_by_id])
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


class ItemExecAttachment(Base):
    """Faz 21 — Usta/mühendis/amir bakım SIRASINDA (bir checklist maddesini
    tikleyip incelerken) tablet kamerasıyla veya galeriden fotoğraf
    ekleyebilir. NodeAttachment'tan (admin'in bakım ağacı ŞABLONUNA eklediği
    referans/talimat görseli) FARKLIDIR — bu, sahada o anki durumu (arıza,
    aşınma, tamamlanmış iş kanıtı vb.) belgelemek için doğrudan bu iş
    emrinin (Maintenance) bu maddesine (MaintenanceItem) bağlıdır, şablona
    YANSIMAZ. Bilerek MaintenanceItem tarafında aynı adlı bir ORM
    relationship TANIMLANMAMIŞTIR — aksi halde ItemOut.model_validate()
    her maddede otomatik (N+1) bir sorgu tetiklerdi; bunun yerine
    main.py._build_item_tree tek seferde toplu sorgu ile doldurur (bkz.
    NodeAttachment için 'attach' sözlüğüyle aynı desen). item_id üzerindeki
    ondelete=CASCADE, iş emri/madde silinince bu ekleri de veritabanı
    seviyesinde temizler (dosyalar elle silinen tekil ek durumunda
    silinir; toplu silmede dosya artıkları kalabilir — mevcut ek
    sistemlerindeki kabul edilmiş davranışla aynı)."""

    __tablename__ = "item_exec_attachments"

    id: Mapped[int] = mapped_column(primary_key=True)
    item_id: Mapped[int] = mapped_column(
        ForeignKey("maintenance_items.id", ondelete="CASCADE"), index=True
    )
    filename: Mapped[str] = mapped_column(String(300), default="")
    original_name: Mapped[str] = mapped_column(String(300), default="")
    uploaded_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)

    item = relationship("MaintenanceItem")
    uploaded_by = relationship("User")


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

    # Parça stok takibi: depo/stok adedi ve düşük stok eşiği. stock_qty, bu
    # parça bir bakım ağacı maddesine (TreeNodePartUsage) bağlıysa ilgili
    # madde tamamlanınca otomatik düşer (services._adjust_stock_for_node),
    # madde kilidi açılıp sıfırlanırsa geri eklenir. min_stock=0 (varsayılan)
    # "bu parça için stok takibi/eşiği tanımlanmadı" anlamına gelir —
    # Bildirim Merkezi'nde yalnızca min_stock > 0 olan parçalar için düşük
    # stok uyarısı üretilir (aksi halde henüz stok girilmemiş yüzlerce parça
    # gürültü oluştururdu). Float (Integer değil): "1 kg'lık bir üründen bir
    # bakımda 0.2 kg kullanılır" gibi kesirli miktarları (gram/litre/metre
    # cinsinden ölçülen sarf malzemeleri) desteklemek için.
    stock_qty: Mapped[float] = mapped_column(Float, default=0.0)
    min_stock: Mapped[float] = mapped_column(Float, default=0.0)
    unit: Mapped[str] = mapped_column(String(20), default="adet")


class PartRepairMaterial(Base):
    """Bir parçanın (ör. "Körük") tamirinde/bakımında kullanılan malzeme
    satırı. İki türlü olabilir:
      * material_part_id doluysa: Parça Bilgi Bankası'ndan seçilmiş, stok
        takipli bir malzeme (ör. "Vida M6") — "Tamir Edildi" (bkz.
        main.py repair_part) denince bu malzemenin stoğu `qty` kadar
        otomatik düşer.
      * free_text doluysa (material_part_id boş): kataloğa ayrı bir parça
        olarak eklenmemiş, serbest metinle tanımlanmış malzeme (ör. "4 adet
        M6 vida") — yalnızca bilgi amaçlıdır, stok bağlantısı olmadığı için
        "Tamir Edildi" denince düşülmez."""

    __tablename__ = "part_repair_materials"

    id: Mapped[int] = mapped_column(primary_key=True)
    part_id: Mapped[int] = mapped_column(
        ForeignKey("part_catalog_items.id", ondelete="CASCADE"), index=True
    )
    material_part_id: Mapped[int | None] = mapped_column(
        ForeignKey("part_catalog_items.id", ondelete="CASCADE"), nullable=True, index=True
    )
    free_text: Mapped[str] = mapped_column(String(200), default="")
    qty: Mapped[float] = mapped_column(Float, default=1.0)

    part = relationship("PartCatalogItem", foreign_keys=[part_id])
    material = relationship("PartCatalogItem", foreign_keys=[material_part_id])


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
    """Faz 30 — Usta/mühendis/amir/admin'in sahada fark ettiği bir
    eksikliği/iyileştirmeyi bildirdiği öneri. Artık BELİRLİ bir araca değil
    bir ARAÇ MODELİNE (vehicle_model — ör. "Bozankaya Tip-1", "Sirio")
    bağlanır, çünkü öneri genelde o modelin TÜMÜNÜ ilgilendirir (bkz.
    /api/vehicle-models). vehicle_id, bu değişiklikten ÖNCE belirli bir
    araca bağlı oluşturulmuş eski kayıtlar için geriye dönük uyumluluk
    amacıyla hâlâ var ama artık NULL olabilir ve yeni önerilerde
    kullanılmaz.

    İKİ AŞAMALI HİYERARŞİK ONAY: bir öneri önce AMİR'e düşer (amir_status);
    amir onaylarsa (amir_status='approved') MÜHENDİS'e düşer (engineer_
    status) ve mühendisin kararı (approved/rejected/added) NİHAİDİR; amir
    reddederse (amir_status='rejected') öneri MÜHENDİSE HİÇ DÜŞMEZ, iş
    orada biter. `status`, bu iki aşamanın DERLENMİŞ (üst düzey, filtreleme/
    rozet için kullanılan) görünümüdür:
      pending          -> yeni, amir'in kararını bekliyor
      pending_engineer -> amir onayladı, mühendisin kararını bekliyor
      rejected         -> amir VEYA mühendis reddetti (hangisi olduğu
                          amir_status/engineer_status'tan ayırt edilir)
      approved         -> mühendis onayladı (nihai)
      added            -> mühendis bakım ağacına eklendi olarak işaretledi (nihai)
    Bu alan, ilgili aşama alanıyla AYNI ANDA (bkz. services.apply_
    suggestion_review) güncellenir — asla tek başına değiştirilmez."""

    __tablename__ = "suggestions"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Geriye dönük uyumluluk (bu özellikten ÖNCE belirli bir araca bağlı
    # oluşturulmuş öneriler) — artık NULL olabilir, yeni önerilerde
    # kullanılmaz (bkz. vehicle_model).
    vehicle_id: Mapped[int | None] = mapped_column(
        ForeignKey("vehicles.id", ondelete="CASCADE"), nullable=True, index=True
    )
    # Yeni önerilerde ZORUNLU — önerinin hangi araç modelini ilgilendirdiği.
    vehicle_model: Mapped[str] = mapped_column(String(100), default="")
    # Öneri belirli bir bakım periyoduna yönelikse plan; genel bir öneriyse
    # (herhangi bir periyoda özgü değilse) None olabilir.
    plan_id: Mapped[int | None] = mapped_column(
        ForeignKey("maintenance_plans.id", ondelete="SET NULL"), nullable=True
    )
    text: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    # ESKİ tek-aşamalı alan — artık yalnızca "en son hangi aşama tamamlandı,
    # kim/ne zaman" özetini taşır (geriye dönük uyumluluk + basit "son
    # işlem" gösterimi için); asıl kaynak amir_*/engineer_* alanlarıdır.
    admin_note: Mapped[str] = mapped_column(Text, default="")
    created_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, index=True)
    reviewed_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # 1. aşama — AMİR: pending | approved | rejected
    amir_status: Mapped[str] = mapped_column(String(20), default="pending")
    amir_reviewed_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    amir_reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    amir_note: Mapped[str] = mapped_column(Text, default="")

    # 2. aşama — MÜHENDİS: yalnızca amir_status='approved' olunca sırası
    # gelir. pending | approved | rejected | added
    engineer_status: Mapped[str] = mapped_column(String(20), default="pending")
    engineer_reviewed_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    engineer_reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    engineer_note: Mapped[str] = mapped_column(Text, default="")

    vehicle = relationship("Vehicle")
    plan = relationship("MaintenancePlan")
    created_by = relationship("User", foreign_keys=[created_by_id])
    reviewed_by = relationship("User", foreign_keys=[reviewed_by_id])
    amir_reviewed_by = relationship("User", foreign_keys=[amir_reviewed_by_id])
    engineer_reviewed_by = relationship("User", foreign_keys=[engineer_reviewed_by_id])


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
    # Bu alarm için admin/mühendis/amir'e (doğrulanmış e-postası olanlara)
    # kritik-alarm e-postası ne zaman gönderildi — None ise hiç gönderilmedi.
    # Aynı alarma (aynı vehicle_id+target_km) tekrar tekrar e-posta
    # atılmasını önlemek için kullanılır (bkz. main.py _send_critical_alarm_
    # emails). Alarm onaylanır/çözülür/kapatılırsa da tekrar e-posta atılmaz.
    emailed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
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
