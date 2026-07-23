import { useEffect, useRef, useState } from 'react'
import * as THREE from 'three'
import { GLTFLoader, OrbitControls } from 'three/addons'

export interface Tram3DHandle {
  focusMesh: (meshName: string) => void
}

/**
 * Tıklanabilir, döndürülebilir 3D tramvay görüntüleyici.
 *
 * - src: .glb dosya yolu
 * - meshTints: {mesh_adı: '#renkkodu'} — eşleşen parçalar bu renkte vurgulanır
 * - onMeshClick: bir parçaya tıklanınca (mesh adı) çağrılır
 * - height: canvas yüksekliği (px)
 */
export default function Tram3D({
  src,
  meshTints,
  onMeshClick,
  height = 420,
}: {
  src: string
  meshTints?: Record<string, string>
  onMeshClick?: (meshName: string) => void
  height?: number
}) {
  const containerRef = useRef<HTMLDivElement>(null)
  const sceneRef = useRef<THREE.Scene | null>(null)
  const meshesRef = useRef<THREE.Mesh[]>([])
  const cameraRef = useRef<THREE.PerspectiveCamera | null>(null)
  const controlsRef = useRef<OrbitControls | null>(null)
  const onMeshClickRef = useRef(onMeshClick)
  onMeshClickRef.current = onMeshClick
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [loadPct, setLoadPct] = useState(0)

  // ------------------------------------------------------------ Sahne kurulumu
  useEffect(() => {
    const container = containerRef.current
    if (!container) return
    let disposed = false

    const scene = new THREE.Scene()
    scene.background = new THREE.Color(0xf2f4f7)
    sceneRef.current = scene

    const width = container.clientWidth || 600
    const camera = new THREE.PerspectiveCamera(45, width / height, 0.01, 5000)
    camera.position.set(4, 3, 6)
    cameraRef.current = camera

    // Bazı bilgisayarlarda/uzak masaüstü oturumlarında donanım hızlandırma
    // (WebGL) kapalı/kullanılamaz olabilir. Bu durumda three.js hata fırlatır;
    // yakalamazsak tüm sayfa (React ağacı) çöker. Burada nazikçe bir hata
    // mesajı gösterip geri kalan sayfanın çalışmaya devam etmesini sağlıyoruz.
    let renderer: THREE.WebGLRenderer
    try {
      renderer = new THREE.WebGLRenderer({ antialias: true })
    } catch (err) {
      console.error('WebGL context oluşturulamadı:', err)
      setError(
        '3D model bu tarayıcıda görüntülenemiyor (donanım hızlandırma/WebGL kapalı ' +
          'olabilir). Diğer sayfa özellikleri normal şekilde çalışmaya devam eder.',
      )
      setLoading(false)
      return
    }
    renderer.setSize(width, height)
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2))
    container.innerHTML = ''
    container.appendChild(renderer.domElement)

    scene.add(new THREE.AmbientLight(0xffffff, 1.1))
    const dir1 = new THREE.DirectionalLight(0xffffff, 1.3)
    dir1.position.set(5, 10, 7)
    scene.add(dir1)
    const dir2 = new THREE.DirectionalLight(0xffffff, 0.6)
    dir2.position.set(-6, -3, -5)
    scene.add(dir2)

    const controls = new OrbitControls(camera, renderer.domElement)
    controls.enableDamping = true
    controls.dampingFactor = 0.08
    controlsRef.current = controls

    const loader = new GLTFLoader()
    loader.load(
      src,
      (gltf) => {
        if (disposed) return
        const root = gltf.scene
        scene.add(root)

        const meshes: THREE.Mesh[] = []
        root.traverse((obj) => {
          if ((obj as THREE.Mesh).isMesh) {
            const mesh = obj as THREE.Mesh
            mesh.userData.origMaterial = mesh.material
            meshes.push(mesh)
          }
        })
        meshesRef.current = meshes

        const box = new THREE.Box3().setFromObject(root)
        const size = box.getSize(new THREE.Vector3())
        const center = box.getCenter(new THREE.Vector3())
        const maxDim = Math.max(size.x, size.y, size.z) || 1
        root.position.sub(center)
        const dist = maxDim * 1.6
        camera.position.set(dist * 0.6, dist * 0.45, dist * 0.9)
        camera.near = maxDim / 100
        camera.far = maxDim * 20
        camera.updateProjectionMatrix()
        controls.target.set(0, 0, 0)
        controls.update()

        setLoading(false)
      },
      (evt) => {
        if (evt.total) setLoadPct(Math.round((evt.loaded / evt.total) * 100))
      },
      (err) => {
        console.error(err)
        if (!disposed) {
          setError('3D model yüklenemedi.')
          setLoading(false)
        }
      },
    )

    const raycaster = new THREE.Raycaster()
    const pointer = new THREE.Vector2()
    let downX = 0
    let downY = 0

    function onPointerDown(e: PointerEvent) {
      downX = e.clientX
      downY = e.clientY
    }

    function onPointerUp(e: PointerEvent) {
      // Sürükleme (kamera döndürme) ile tıklamayı ayırt et
      if (Math.abs(e.clientX - downX) > 4 || Math.abs(e.clientY - downY) > 4) return
      if (!onMeshClickRef.current) return
      const rect = renderer.domElement.getBoundingClientRect()
      pointer.x = ((e.clientX - rect.left) / rect.width) * 2 - 1
      pointer.y = -((e.clientY - rect.top) / rect.height) * 2 + 1
      raycaster.setFromCamera(pointer, camera)
      const hits = raycaster.intersectObjects(meshesRef.current, false)
      if (hits.length > 0) {
        let obj: THREE.Object3D | null = hits[0].object
        while (obj && !obj.name) obj = obj.parent
        if (obj && obj.name) onMeshClickRef.current(obj.name)
      }
    }

    renderer.domElement.addEventListener('pointerdown', onPointerDown)
    renderer.domElement.addEventListener('pointerup', onPointerUp)

    let raf = 0
    function animate() {
      raf = requestAnimationFrame(animate)
      controls.update()
      renderer.render(scene, camera)
    }
    animate()

    const resizeObserver = new ResizeObserver(() => {
      const w = container.clientWidth || 600
      camera.aspect = w / height
      camera.updateProjectionMatrix()
      renderer.setSize(w, height)
    })
    resizeObserver.observe(container)

    return () => {
      disposed = true
      cancelAnimationFrame(raf)
      resizeObserver.disconnect()
      renderer.domElement.removeEventListener('pointerdown', onPointerDown)
      renderer.domElement.removeEventListener('pointerup', onPointerUp)
      controls.dispose()
      renderer.dispose()
      meshesRef.current.forEach((m) => {
        m.geometry?.dispose()
      })
      container.innerHTML = ''
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [src, height])

  // ------------------------------------------------------------ Renk vurgulama
  useEffect(() => {
    for (const mesh of meshesRef.current) {
      const tint = meshTints?.[mesh.name]
      if (tint) {
        const base = mesh.userData.origMaterial as THREE.Material
        let cloned = mesh.userData.tintedMaterial as THREE.MeshStandardMaterial | undefined
        if (!cloned || mesh.userData.tintedColor !== tint) {
          cloned = (base as THREE.MeshStandardMaterial).clone
            ? (base.clone() as THREE.MeshStandardMaterial)
            : new THREE.MeshStandardMaterial()
          const c = new THREE.Color(tint)
          if ('color' in cloned) cloned.color = c
          if ('emissive' in cloned) {
            cloned.emissive = c
            cloned.emissiveIntensity = 0.55
          }
          mesh.userData.tintedMaterial = cloned
          mesh.userData.tintedColor = tint
        }
        mesh.material = cloned
      } else if (mesh.userData.origMaterial) {
        mesh.material = mesh.userData.origMaterial
      }
    }
  }, [meshTints, loading])

  return (
    <div style={{ position: 'relative' }}>
      <div ref={containerRef} style={{ width: '100%', height, borderRadius: 'var(--radius)', overflow: 'hidden', border: '1px solid var(--border)' }} />
      {loading && !error && (
        <div style={{ position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'rgba(247,248,250,0.88)', borderRadius: 'var(--radius)' }}>
          <span className="muted">3D model yükleniyor{loadPct ? ` (%${loadPct})` : ''}…</span>
        </div>
      )}
      {error && (
        <div style={{ position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'rgba(247,248,250,0.92)', borderRadius: 'var(--radius)' }}>
          <span className="error" style={{ margin: 0 }}>{error}</span>
        </div>
      )}
    </div>
  )
}
