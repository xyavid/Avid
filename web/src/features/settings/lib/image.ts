/**
 * 背景插画的图片处理：把用户选的一张图压成能塞进 localStorage 的 data URL。
 *
 * 为什么要压：`uiStore` 走 zustand 的 `persist`，落点是 localStorage，配额通常 5 MB
 * （按 UTF-16 计字符，实际能放的字节更少）。手机截图或壁纸动辄 3–8 MB，**直接存原始
 * data URL 会撞配额**，而 `persist` 在写入时抛错不是用户能理解的失败方式。所以这里
 * 先把图缩到 ≤1920px、再编成 JPEG（质量 0.72），一张壁纸通常落到 200–500 KB。
 *
 * 拆成两半：`fitWithin` 是纯函数（有单测），`toBackdropDataUrl` 才碰 canvas 与 DOM。
 */

/** 缩放后的最大边长（像素）。 */
export const BACKDROP_MAX_WIDTH = 1920

/** JPEG 编码质量。0.72 是「看得出来是照片、又不会大到撞配额」的折中。 */
export const BACKDROP_QUALITY = 0.72

/**
 * data URL 的上限（字符数）。留出余量：localStorage 配额通常 5 MB，而 UI 偏好不止这一项。
 * 超过就**在写之前**拒绝并如实告诉用户，而不是让 `persist` 在某个不确定的时刻抛错。
 */
export const BACKDROP_MAX_CHARS = 1_200_000

/**
 * 等比缩到最大边长以内。**不放大**：把一张 200px 的图放大到 1920px 只会变糊、还更占地方。
 * 宽高任一非正或非有限时返回 0×0，调用方据此拒绝，而不是渲染一个畸形 canvas。
 */
export function fitWithin(
  width: number,
  height: number,
  max: number,
): { width: number; height: number } {
  if (!Number.isFinite(width) || !Number.isFinite(height) || width <= 0 || height <= 0) {
    return { width: 0, height: 0 }
  }
  const scale = Math.min(1, max / Math.max(width, height))
  return { width: Math.round(width * scale), height: Math.round(height * scale) }
}

/** 解码出来的图：既给 canvas 用，也提供原始尺寸。 */
interface Decoded {
  source: CanvasImageSource
  width: number
  height: number
  release: () => void
}

/**
 * `createImageBitmap` 优先（不占 DOM、不阻塞解码）；它解不了的格式退化到 `<img>`。
 *
 * **退化路径必须用 data URL，不能用 object URL**：应用的 CSP 把 `img-src` 限在
 * `'self' data:`，`blob:` 会被直接拦掉——那样这条路径永远走不通，而它正是为"某个格式
 * createImageBitmap 解不开"准备的（实测踩到：controller 台报 CSP 违规，报错却显示成
 * "这张图读不出来"，把真因藏了）。
 *
 * 两条路径都试过才报 `decode-failed`，所以这里的 catch 不是吞错，而是"换一条路再试"，
 * 因此打一条 warn 留痕（否则线上遇到某个格式解不开时完全没有线索）。
 */
async function decode(file: File): Promise<Decoded> {
  if (typeof createImageBitmap === 'function') {
    try {
      const bitmap = await createImageBitmap(file)
      return {
        source: bitmap,
        width: bitmap.width,
        height: bitmap.height,
        release: () => bitmap.close(),
      }
    } catch (error) {
      console.warn('[backdrop] createImageBitmap 解不开，退化到 Image 再试', error)
    }
  }
  const dataUrl = await readAsDataUrl(file)
  const image = await new Promise<HTMLImageElement>((resolve, reject) => {
    const element = new Image()
    element.onload = () => resolve(element)
    element.onerror = () => reject(new Error('decode-failed'))
    element.src = dataUrl
  })
  return {
    source: image,
    width: image.naturalWidth,
    height: image.naturalHeight,
    release: () => {
      /* data URL 没有要回收的外部资源。 */
    },
  }
}

/** File → data URL（CSP 允许 `data:`，`blob:` 不允许）。 */
function readAsDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result))
    reader.onerror = () => reject(new Error('decode-failed'))
    reader.readAsDataURL(file)
  })
}

/**
 * 编码结果是否超过本机存储能给这个功能的空间。
 *
 * 抽成纯函数是为了**能被用例覆盖**：整条链路里的 canvas 与 localStorage 在 jsdom 里都
 * 造不出来，所以把"可判定的那一半"单独拿出来测（`fitWithin` 同理）。
 */
export function exceedsBackdropLimit(dataUrl: string): boolean {
  return dataUrl.length > BACKDROP_MAX_CHARS
}

export interface BackdropImageResult {
  dataUrl: string
  width: number
  height: number
}

/**
 * File → data URL（已缩放编码）。
 *
 * 失败分三类各抛一个可区分的 `Error`（文案由调用点按 i18n 给，这里不引 i18n 以便单测）：
 * `not-an-image`（不是图片）、`decode-failed`（读不出来）、`too-large`（压完仍超上限）。
 * 这个项目的既有取向是"不要把所有失败混成一句出错了"。
 */
export async function toBackdropDataUrl(
  file: File,
  options: { maxWidth?: number; quality?: number } = {},
): Promise<BackdropImageResult> {
  const maxWidth = options.maxWidth ?? BACKDROP_MAX_WIDTH
  const quality = options.quality ?? BACKDROP_QUALITY

  if (!file.type.startsWith('image/')) throw new Error('not-an-image')

  const decoded = await decode(file)
  try {
    const target = fitWithin(decoded.width, decoded.height, maxWidth)
    if (target.width === 0) throw new Error('not-an-image')

    const canvas = document.createElement('canvas')
    canvas.width = target.width
    canvas.height = target.height
    const context = canvas.getContext('2d')
    if (!context) throw new Error('decode-failed')
    context.drawImage(decoded.source, 0, 0, target.width, target.height)

    // JPEG：不透明底、比 PNG 小一个量级（背景没有 alpha 需求）。
    const dataUrl = canvas.toDataURL('image/jpeg', quality)
    if (exceedsBackdropLimit(dataUrl)) throw new Error('too-large')
    return { dataUrl, width: target.width, height: target.height }
  } finally {
    decoded.release()
  }
}
