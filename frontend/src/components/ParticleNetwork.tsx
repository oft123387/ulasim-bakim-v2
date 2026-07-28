import { useEffect, useRef } from 'react'

/**
 * Fare ile etkileşimli, birbirine ince çizgilerle bağlanan hareketli nokta
 * ağı (kullanıcının paylaştığı ekran kaydındaki üniversite portalı
 * başlığındaki efekle aynı fikir) — saf Canvas 2D ile, ek bir kütüphane
 * gerektirmeden. Bu bileşen; konumlandırılmış (position: relative) herhangi
 * bir kapsayıcının İÇİNE mutlak konumlu bir <canvas> olarak yerleşir ve o
 * kapsayıcının boyutunu/fare hareketini izler.
 *
 * Kullanım: kapsayıcıya `position: relative; overflow: hidden` verin,
 * içindeki asıl içeriğe `position: relative; z-index: 1` verin ki metin/
 * butonlar ağın üstünde kalsın (bkz. Login.tsx ve App.tsx .brand kullanımı).
 */
export default function ParticleNetwork({
  className,
  particleDensity = 0.00009,
  minParticles = 18,
  maxParticles = 90,
  linkDistance = 130,
  mouseLinkDistance = 170,
  color = '148, 163, 184', // "r, g, b" — hem açık hem koyu temada okunaklı nötr gri-mavi
  zIndex = 0,
}: {
  className?: string
  /** px² başına parçacık sayısı — alan büyüdükçe otomatik ölçeklenir. */
  particleDensity?: number
  minParticles?: number
  maxParticles?: number
  linkDistance?: number
  mouseLinkDistance?: number
  color?: string
  /** Varsayılan 0: kapsayıcıdaki diğer içeriğe position:relative + z-index:1
   * vermeniz gerekir (bkz. Login.tsx). İçeriği elle konumlandırmak
   * istemiyorsanız -1 verin — negatif z-index normal akıştaki (position
   * verilmemiş) içerikten her zaman geride kalır (bkz. DashboardPage.tsx). */
  zIndex?: number
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null)

  useEffect(() => {
    const canvas = canvasRef.current
    const parent = canvas?.parentElement
    if (!canvas || !parent) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return

    const prefersReducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    const dpr = Math.min(window.devicePixelRatio || 1, 2)

    let width = 0
    let height = 0
    let particles: { x: number; y: number; vx: number; vy: number }[] = []
    const mouse = { x: -9999, y: -9999, active: false }
    let raf = 0

    function resize() {
      const rect = parent!.getBoundingClientRect()
      width = rect.width
      height = rect.height
      canvas!.width = Math.max(1, Math.round(width * dpr))
      canvas!.height = Math.max(1, Math.round(height * dpr))
      canvas!.style.width = `${width}px`
      canvas!.style.height = `${height}px`
      ctx!.setTransform(dpr, 0, 0, dpr, 0, 0)
      const count = Math.min(maxParticles, Math.max(minParticles, Math.round(width * height * particleDensity)))
      particles = Array.from({ length: count }, () => ({
        x: Math.random() * width,
        y: Math.random() * height,
        vx: (Math.random() - 0.5) * 0.3,
        vy: (Math.random() - 0.5) * 0.3,
      }))
    }

    function onMouseMove(e: MouseEvent) {
      const rect = parent!.getBoundingClientRect()
      mouse.x = e.clientX - rect.left
      mouse.y = e.clientY - rect.top
      mouse.active = true
    }
    function onMouseLeave() {
      mouse.active = false
    }

    function draw() {
      ctx!.clearRect(0, 0, width, height)
      for (const p of particles) {
        p.x += p.vx
        p.y += p.vy
        if (p.x < 0 || p.x > width) p.vx *= -1
        if (p.y < 0 || p.y > height) p.vy *= -1
      }
      ctx!.lineWidth = 1
      for (let i = 0; i < particles.length; i++) {
        const a = particles[i]
        for (let j = i + 1; j < particles.length; j++) {
          const b = particles[j]
          const dx = a.x - b.x
          const dy = a.y - b.y
          const dist = Math.sqrt(dx * dx + dy * dy)
          if (dist < linkDistance) {
            ctx!.strokeStyle = `rgba(${color}, ${(1 - dist / linkDistance) * 0.5})`
            ctx!.beginPath()
            ctx!.moveTo(a.x, a.y)
            ctx!.lineTo(b.x, b.y)
            ctx!.stroke()
          }
        }
        if (mouse.active) {
          const dx = a.x - mouse.x
          const dy = a.y - mouse.y
          const dist = Math.sqrt(dx * dx + dy * dy)
          if (dist < mouseLinkDistance) {
            ctx!.strokeStyle = `rgba(${color}, ${(1 - dist / mouseLinkDistance) * 0.75})`
            ctx!.beginPath()
            ctx!.moveTo(a.x, a.y)
            ctx!.lineTo(mouse.x, mouse.y)
            ctx!.stroke()
          }
        }
      }
      ctx!.fillStyle = `rgba(${color}, 0.75)`
      for (const p of particles) {
        ctx!.beginPath()
        ctx!.arc(p.x, p.y, 1.6, 0, Math.PI * 2)
        ctx!.fill()
      }
    }

    function tick() {
      draw()
      raf = requestAnimationFrame(tick)
    }

    resize()
    window.addEventListener('resize', resize)
    parent.addEventListener('mousemove', onMouseMove)
    parent.addEventListener('mouseleave', onMouseLeave)

    // Hareket azaltma tercihi açıksa tek bir statik kare çizilir, sürekli
    // animasyon başlatılmaz (erişilebilirlik).
    if (prefersReducedMotion) {
      draw()
    } else {
      raf = requestAnimationFrame(tick)
    }

    // Sekme arka plandayken (görünür değilken) animasyonu durdurup CPU/GPU
    // harcamamak için — sekmeye dönüldüğünde otomatik devam eder.
    function onVisibility() {
      if (document.hidden) {
        cancelAnimationFrame(raf)
      } else if (!prefersReducedMotion) {
        raf = requestAnimationFrame(tick)
      }
    }
    document.addEventListener('visibilitychange', onVisibility)

    return () => {
      cancelAnimationFrame(raf)
      window.removeEventListener('resize', resize)
      parent.removeEventListener('mousemove', onMouseMove)
      parent.removeEventListener('mouseleave', onMouseLeave)
      document.removeEventListener('visibilitychange', onVisibility)
    }
  }, [particleDensity, minParticles, maxParticles, linkDistance, mouseLinkDistance, color])

  return (
    <canvas
      ref={canvasRef}
      className={className}
      aria-hidden="true"
      style={{ position: 'absolute', inset: 0, pointerEvents: 'none', zIndex }}
    />
  )
}
