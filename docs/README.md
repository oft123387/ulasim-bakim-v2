# Dokümantasyon

Bu klasör projenin tüm yazılı dokümanlarını içerir. Kurulum, çalıştırma ve
mimari özeti için önce kök dizindeki [`../README.md`](../README.md) okunmalıdır.

## Dosyalar

| Dosya | İçerik | Ne zaman okunmalı |
| --- | --- | --- |
| [`sistem-aciklamasi.md`](sistem-aciklamasi.md) | Sistemin iş kuralları, roller, bakım açma mantığı — teknik olmayan anlatım | Projeye yeni başlarken, ikinci adım |
| [`gelistirme-onerileri.md`](gelistirme-onerileri.md) | Bilinen eksikler, iyileştirme fikirleri, yapılacaklar listesi | Yeni bir işe başlamadan önce |
| [`sunucu-kurulumu.md`](sunucu-kurulumu.md) | Şirket sunucusuna (192.168.1.192) kurulum ve güncelleme adımları | Dağıtım (deploy) yaparken |
| [`bakim-periyot-duzeltmesi-notlari.md`](bakim-periyot-duzeltmesi-notlari.md) | Kademeli/kümülatif bakım periyodu düzeltmesinin arka planı ve kararları | Bakım periyodu mantığına dokunmadan önce |
| [`sunumlar/`](sunumlar/) | Yönetime yapılan sunumlar (.pptx) | Projeyi dışarıya anlatırken |

## Yazım kuralları

* Dosya adları küçük harf ve tire ile: `ornek-dokuman-adi.md`.
* Yeni doküman eklendiğinde yukarıdaki tabloya bir satır eklenir.
* Kuruluma/çalıştırmaya dair bilgi kök `README.md`'de tutulur; burada
  tekrarlanmaz, gerekirse oraya link verilir.

## Eski dosya adları

Dosyalar 2026-08-17'de kök dizinden buraya taşındı. Eski adlar:

| Eski (kök dizin) | Yeni |
| --- | --- |
| `Ulasim_Bakim_Sistem_Aciklamasi.md` | `docs/sistem-aciklamasi.md` |
| `Gelistirme_Onerileri.md` | `docs/gelistirme-onerileri.md` |
| `Sunucu_Kurulum_192.168.1.192.md` | `docs/sunucu-kurulumu.md` |
| `Bakim_Periyot_Duzeltmesi_Notlari.md` | `docs/bakim-periyot-duzeltmesi-notlari.md` |
| `Ulasim_Bakim_Proje_Sunumu.pptx` | `docs/sunumlar/proje-sunumu.pptx` |
| `Ulasim_Bakim_Sunum.pptx` | `docs/sunumlar/genel-sunum.pptx` |
| `Ulasim_Bakim_Yonetim_Sunumu.pptx` | `docs/sunumlar/yonetim-sunumu.pptx` |

Geçmişe `git log --follow <yeni-yol>` ile ulaşılabilir.
