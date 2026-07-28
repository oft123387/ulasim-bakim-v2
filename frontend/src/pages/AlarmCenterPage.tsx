import { useEffect, useMemo, useState } from 'react'
import { api } from '../api'
import type { AlarmCenter, AlarmItem, AlarmPriority, User } from '../types'
import { ALARM_LEVEL_TR, ALARM_PRIORITY_TR } from '../types'
import { PageHeader, EmptyState, SkeletonTableRows, Badge } from '../components/ui'
import { useLiveEvent } from '../liveEvents'
import { Siren, AlertTriangle, Clock, ShieldCheck, RefreshCw, Truck, Check, CheckCircle2, XCircle, EyeOff } from 'lucide-react'

const fmtKm = (n: number) => n.toLocaleString('tr-TR')

// Hedef zaten geçildiyse "X km geçti", değilse "X km kaldı".
const kmDisplay = (remaining: number) =>
  remaining <= 0 ? `${fmtKm(Math.abs(remaining))} km geçti` : `${fmtKm(remaining)} km kaldı`

// Süre bazlı (aylık/yıllık) alarmlarda target_date doludur ve remaining_km
// alanı gerçekte gün sayısıdır (km'ye çevirmek anlamsız/yanlış olur).
const remainingDisplay = (a: AlarmItem) =>
  a.target_date
    ? a.remaining_km <= 0
      ? `${Math.abs(a.remaining_km)} gün geçti`
      : `${a.remaining_km} gün kaldı`
    : kmDisplay(a.remaining_km)
const hedefDisplay = (a: AlarmItem) =>
  a.target_date ? new Date(a.target_date).toLocaleDateString('tr-TR') : `${fmtKm(a.target_km)} km`

const LEVELS: AlarmItem['level'][] = ['critical', 'warning', 'normal']

const LEVEL_ICON: Record<AlarmItem['level'], React.ReactNode> = {
  critical: <AlertTriangle size={20} />,
  warning: <Clock size={20} />,
  normal: <ShieldCheck size={20} />,
}

function LevelPill({ level }: { level: AlarmItem['level'] }) {
  const cls =
    level === 'critical' ? 'critical-pill' : level === 'warning' ? 'urgent-pill' : 'alert-pill'
  return (
    <span className={cls}>
      <span className={`alarm-row-dot lv-${level}`} /> {ALARM_LEVEL_TR[level]}
    </span>
  )
}

const PRIORITY_TONE: Record<AlarmPriority, 'neutral' | 'pending' | 'in_progress' | 'danger'> = {
  low: 'neutral',
  normal: 'pending',
  high: 'in_progress',
  critical: 'danger',
}

export default function AlarmCenterPage({
  onOpenMaintenance,
  onOpenVehicle,
}: {
  onOpenMaintenance: (id: number) => void
  onOpenVehicle: (id: number) => void
}) {
  const [data, setData] = useState<AlarmCenter | null>(null)
  const [filter, setFilter] = useState<AlarmItem['level'] | 'all'>('all')
  const [refreshing, setRefreshing] = useState(false)
  const [includeClosed, setIncludeClosed] = useState(false)
  const [users, setUsers] = useState<User[]>([])
  const [busyId, setBusyId] = useState<number | null>(null)

  const load = () => {
    setRefreshing(true)
    api<AlarmCenter>(`/alarms${includeClosed ? '?include_closed=true' : ''}`)
      .then(setData)
      .catch(() => setData({ critical: 0, warning: 0, normal: 0, items: [] }))
      .finally(() => setRefreshing(false))
  }
  useEffect(() => {
    load()
    // Canlı kalması için 60 sn'de bir tazele (SSE koparsa yedek).
    const id = setInterval(load, 60000)
    return () => clearInterval(id)
  }, [includeClosed])

  // Canlı Güncelleme (SSE) — biri bir alarmı onaylayınca/atayınca ya da yeni
  // bir bakım/km değişikliği alarm durumunu etkileyebileceği için anında yenile.
  useLiveEvent(['alarm', 'maintenance', 'vehicle'], load)

  useEffect(() => {
    api<User[]>('/users').then(setUsers).catch(() => setUsers([]))
  }, [])

  async function patchAlarm(
    stateId: number,
    body: Partial<{ priority: AlarmPriority; assigned_to_id: number | null; acknowledged: boolean; resolved: boolean; closed: boolean }>,
  ) {
    setBusyId(stateId)
    try {
      await api(`/alarms/${stateId}`, { method: 'PATCH', body })
      load()
    } finally {
      setBusyId(null)
    }
  }

  const counts = useMemo(
    () => ({
      critical: data?.critical ?? 0,
      warning: data?.warning ?? 0,
      normal: data?.normal ?? 0,
    }),
    [data],
  )

  const items = useMemo(() => {
    const all = data?.items ?? []
    return filter === 'all' ? all : all.filter((a) => a.level === filter)
  }, [data, filter])

  const LEVEL_LABEL: Record<AlarmItem['level'], string> = {
    critical: 'Kritik — geciken / çok yakın',
    warning: 'Uyarı — ~14 günden az',
    normal: 'Normal — yaklaşan',
  }

  return (
    <>
      <PageHeader
        title="Canlı Alarm Merkezi"
        subtitle="Önceliklendirin, sorumlu atayın ve müdahale durumunu buradan takip edin."
        actions={
          <div className="alarm-header-actions">
            <span className="alarm-live-status"><span className="live-dot" /> Canlı</span>
            <button className="ghost" onClick={load} disabled={refreshing}>
              <RefreshCw size={14} style={{ marginRight: 6, verticalAlign: -2 }} />
              {refreshing ? 'Yenileniyor…' : 'Yenile'}
            </button>
          </div>
        }
      />

      <div className="alarm-summary">
        {LEVELS.map((lv) => (
          <button
            key={lv}
            className={`alarm-sum-card lv-${lv}${filter === lv ? ' active' : ''}`}
            onClick={() => setFilter((f) => (f === lv ? 'all' : lv))}
            title={`${LEVEL_LABEL[lv]} — tıklayınca filtrele`}
          >
            <span className="alarm-sum-ico">{LEVEL_ICON[lv]}</span>
            <span>
              <span className="alarm-sum-val">{counts[lv]}</span>
              <span className="alarm-sum-lbl">{ALARM_LEVEL_TR[lv]}</span>
            </span>
          </button>
        ))}
      </div>

      <div className="table-card fade-in alarm-list-card">
        <div className="table-toolbar alarm-toolbar">
          <div className="card-title alarm-list-title">
            <Siren size={15} style={{ verticalAlign: -2, marginRight: 6 }} />
            Alarmlar
            {data !== null && <span className="muted">{items.length} kayıt</span>}
          </div>
          <div className="row alarm-filters" style={{ gap: 6, marginLeft: 'auto' }}>
            <button className={`small${filter === 'all' ? ' primary' : ''}`} onClick={() => setFilter('all')}>
              Tümü
            </button>
            {LEVELS.map((lv) => (
              <button
                key={lv}
                className={`small${filter === lv ? ' primary' : ''}`}
                onClick={() => setFilter(lv)}
              >
                {ALARM_LEVEL_TR[lv]}
              </button>
            ))}
            <button
              className={`small${includeClosed ? ' primary' : ''}`}
              onClick={() => setIncludeClosed((v) => !v)}
              title="Kapatılmış alarmları da göster"
            >
              <EyeOff size={12} style={{ marginRight: 5, verticalAlign: -2 }} />
              Kapatılanlar
            </button>
          </div>
        </div>
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>Seviye</th>
                <th>Araç</th>
                <th>Bakım</th>
                <th>Durum</th>
                <th>Tahmini</th>
                <th>Öncelik</th>
                <th>Atanan</th>
                <th>İş Akışı</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {data === null && <SkeletonTableRows columns={9} />}
              {data !== null &&
                items.map((a, i) => {
                  const st = a.state
                  const busy = st != null && busyId === st.id
                  return (
                    <tr key={`${a.vehicle.id}-${a.target_km}-${i}`} className={`alarm-tr-${a.level}`}>
                      <td>
                        <LevelPill level={a.level} />
                      </td>
                      <td className="clickable-row" onClick={() => onOpenVehicle(a.vehicle.id)}>
                        <span className="vehicle-code">{a.vehicle.code}</span>
                        {a.vehicle.name && <span className="muted"> — {a.vehicle.name}</span>}
                      </td>
                      <td>
                        {a.plan_name}
                        <div className="muted" style={{ fontSize: 12 }}>
                          Hedef {hedefDisplay(a)}
                        </div>
                      </td>
                      <td style={{ fontWeight: 600, color: a.overdue ? 'var(--red)' : undefined }}>
                        {remainingDisplay(a)}
                      </td>
                      <td>{a.estimated_days != null ? (a.overdue ? 'Gecikti' : `~${a.estimated_days} gün`) : '—'}</td>
                      <td>
                        {st && (
                          <select
                            value={st.priority}
                            disabled={busy}
                            onChange={(e) => patchAlarm(st.id, { priority: e.target.value as AlarmPriority })}
                            style={{ fontSize: 12.5 }}
                          >
                            {(Object.keys(ALARM_PRIORITY_TR) as AlarmPriority[]).map((p) => (
                              <option key={p} value={p}>{ALARM_PRIORITY_TR[p]}</option>
                            ))}
                          </select>
                        )}
                      </td>
                      <td>
                        {st && (
                          <select
                            value={st.assigned_to?.id ?? ''}
                            disabled={busy}
                            onChange={(e) =>
                              patchAlarm(st.id, { assigned_to_id: e.target.value ? Number(e.target.value) : null })
                            }
                            style={{ fontSize: 12.5 }}
                          >
                            <option value="">Atanmamış</option>
                            {users.map((u) => (
                              <option key={u.id} value={u.id}>{u.full_name}</option>
                            ))}
                          </select>
                        )}
                      </td>
                      <td>
                        {st && (
                          <div className="row" style={{ gap: 4, flexWrap: 'wrap' }}>
                            <button
                              className={`small${st.acknowledged ? ' primary' : ''}`}
                              disabled={busy}
                              title={st.acknowledged ? 'Onay kaldır' : 'Onayla (Acknowledged)'}
                              onClick={() => patchAlarm(st.id, { acknowledged: !st.acknowledged })}
                            >
                              <Check size={12} />
                            </button>
                            <button
                              className={`small${st.resolved ? ' primary' : ''}`}
                              disabled={busy}
                              title={st.resolved ? 'Çözümü kaldır' : 'Çözüldü (Resolved)'}
                              onClick={() => patchAlarm(st.id, { resolved: !st.resolved })}
                            >
                              <CheckCircle2 size={12} />
                            </button>
                            <button
                              className={`small${st.closed ? ' primary' : ''}`}
                              disabled={busy}
                              title={st.closed ? 'Yeniden aç' : 'Kapat (Closed)'}
                              onClick={() => patchAlarm(st.id, { closed: !st.closed })}
                            >
                              <XCircle size={12} />
                            </button>
                            {(st.acknowledged || st.resolved || st.closed) && (
                              <Badge tone={PRIORITY_TONE[st.priority]}>
                                {st.closed ? 'Kapatıldı' : st.resolved ? 'Çözüldü' : 'Onaylandı'}
                              </Badge>
                            )}
                          </div>
                        )}
                      </td>
                      <td>
                        {a.maintenance_id != null ? (
                          <button className="small primary" onClick={() => onOpenMaintenance(a.maintenance_id!)}>
                            İş Emrini Aç
                          </button>
                        ) : (
                          <button className="small" onClick={() => onOpenVehicle(a.vehicle.id)}>
                            Aracı Aç
                          </button>
                        )}
                      </td>
                    </tr>
                  )
                })}
            </tbody>
          </table>
          {data !== null && items.length === 0 && (
            <EmptyState
              icon={<Truck size={22} />}
              title="Alarm yok"
              description={
                filter === 'all'
                  ? 'Şu an geciken veya yaklaşan bir bakım bulunmuyor.'
                  : 'Bu seviyede alarm bulunmuyor.'
              }
            />
          )}
        </div>
      </div>
    </>
  )
}
