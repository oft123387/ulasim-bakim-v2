import { useEffect, useState } from 'react'
import { api } from '../api'
import type { SystemStatus } from '../types'
import { PageHeader, Badge } from '../components/ui'
import { RefreshCw, Database, Server, HardDrive, Clock } from 'lucide-react'

const fmtDt = (s: string | null) => (s ? new Date(s).toLocaleString('tr-TR') : '—')

function fmtUptime(seconds: number): string {
  const d = Math.floor(seconds / 86400)
  const h = Math.floor((seconds % 86400) / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  const parts: string[] = []
  if (d > 0) parts.push(`${d} gün`)
  if (h > 0 || d > 0) parts.push(`${h} sa`)
  parts.push(`${m} dk`)
  return parts.join(' ')
}

function StatRow({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="row" style={{ justifyContent: 'space-between', padding: '7px 0', borderBottom: '1px solid var(--border-soft)' }}>
      <span className="muted">{label}</span>
      <span style={{ fontWeight: 600 }}>{value}</span>
    </div>
  )
}

/** Admin için tanı/teşhis sayfası — özellikle "kodu/veriyi düzenledim ama
 * uygulamada değişiklik görünmüyor" durumlarında, uygulamanın GERÇEKTE
 * hangi veritabanı dosyasına/sunucusuna bağlı olduğunu tek bakışta gösterir
 * (bkz. backend/app/main.py system_status() docstring'i — bu projede
 * gerçekten yaşanan bir Docker named-volume karışıklığından esinlenildi). */
export default function SystemStatusPage() {
  const [status, setStatus] = useState<SystemStatus | null>(null)
  const [error, setError] = useState('')
  const [loadingAt, setLoadingAt] = useState<Date | null>(null)

  const load = () => {
    api<SystemStatus>('/system/status')
      .then((s) => {
        setStatus(s)
        setLoadingAt(new Date())
        setError('')
      })
      .catch((e) => setError((e as Error).message))
  }
  useEffect(load, [])

  return (
    <>
      <PageHeader
        title="Sistem Durumu"
        subtitle="Uygulamanın gerçekte hangi veritabanına bağlı olduğunu ve temel sayaçları gösterir."
        actions={
          <button className="small" onClick={load}>
            <RefreshCw size={13} style={{ marginRight: 5, verticalAlign: -2 }} />
            Yenile
          </button>
        }
      />

      {error && <div className="error">{error}</div>}

      {status && (
        <>
          <div className="card slide-up" style={{ marginBottom: 16 }}>
            <div className="card-head">
              <div className="card-title">
                <Database size={15} style={{ marginRight: 6, verticalAlign: -2 }} />
                Veritabanı
              </div>
              <Badge tone={status.foreign_keys_enforced === false ? 'pending' : 'completed'}>
                {status.db_backend.toUpperCase()}
              </Badge>
            </div>
            <StatRow
              label="Konum"
              value={<code style={{ fontSize: 12.5, wordBreak: 'break-all' }}>{status.db_location}</code>}
            />
            {status.foreign_keys_enforced !== null && (
              <StatRow
                label="Yabancı anahtar (FK) zorlaması"
                value={status.foreign_keys_enforced ? 'Açık' : 'Kapalı'}
              />
            )}
            <StatRow label="Son yedek" value={fmtDt(status.last_backup_at)} />
            <p className="muted" style={{ marginTop: 10, fontSize: 12.5 }}>
              Bu ekranda gördüğün sayılarla Bakım Planları/Araçlar sayfalarındaki sayılar
              tutmuyorsa, uygulamanın farklı bir veritabanına bağlı olduğu anlamına gelebilir
              (ör. Docker ile çalışırken kalıcı bir volume kullanılıyorsa).
            </p>
          </div>

          <div className="card slide-up" style={{ marginBottom: 16 }}>
            <div className="card-head">
              <div className="card-title">
                <Server size={15} style={{ marginRight: 6, verticalAlign: -2 }} />
                Sayaçlar
              </div>
            </div>
            <StatRow label="Araçlar" value={`${status.active_vehicle_count} aktif / ${status.vehicle_count} toplam`} />
            <StatRow label="Kullanıcılar" value={status.user_count} />
            <StatRow
              label="Bakım planları"
              value={
                status.catalog_only_plan_count > 0
                  ? `${status.plan_count} (${status.catalog_only_plan_count} katalog-amaçlı)`
                  : status.plan_count
              }
            />
            <StatRow label="Ağaç düğümleri" value={status.tree_node_count} />
            <StatRow
              label="İş emirleri"
              value={`${status.maintenance_pending} bekliyor / ${status.maintenance_in_progress} devam ediyor / ${status.maintenance_completed} tamamlandı`}
            />
            <StatRow
              label="Periyodu belirsiz (?) madde"
              value={
                <Badge tone={status.unresolved_period_count > 0 ? 'pending' : 'completed'}>
                  {status.unresolved_period_count}
                </Badge>
              }
            />
          </div>

          <div className="card slide-up">
            <div className="card-head">
              <div className="card-title">
                <HardDrive size={15} style={{ marginRight: 6, verticalAlign: -2 }} />
                Klasörler ve çalışma süresi
              </div>
            </div>
            <StatRow label="Yükleme klasörü" value={<code style={{ fontSize: 12.5 }}>{status.upload_dir}</code>} />
            <StatRow label="Yedek klasörü" value={<code style={{ fontSize: 12.5 }}>{status.backup_dir}</code>} />
            <StatRow label="Log klasörü" value={<code style={{ fontSize: 12.5 }}>{status.log_dir}</code>} />
            <StatRow
              label="Başlangıç"
              value={
                <span>
                  <Clock size={12} style={{ marginRight: 4, verticalAlign: -1 }} />
                  {fmtDt(status.started_at)} ({fmtUptime(status.uptime_seconds)} önce)
                </span>
              }
            />
          </div>

          {loadingAt && (
            <p className="muted" style={{ marginTop: 10, fontSize: 12 }}>
              Son yenileme: {loadingAt.toLocaleTimeString('tr-TR')}
            </p>
          )}
        </>
      )}
    </>
  )
}
