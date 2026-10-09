/**
 * 图片草稿的准备（阶段 59）：客户端只在**超限时**压缩，服务端只校验不重编码。
 *
 * 为什么压缩放在浏览器：内核的运行期依赖只有 httpx，服务端重编码要引 Pillow；
 * 而浏览器本来就拿得到原图，canvas 缩放是它分内的事。超限的图按最长边缩到
 * DOWNSCALE_SIDE（各 provider 内部普遍会做的那个尺度），PNG 保持 PNG 不失真
 * （截图里全是文字，重编码成 JPEG 会把笔画糊掉）；chip 上照实标「已压缩」。
 *
 * 上限数字与 `avid/attachments.py` 同口径：服务端那道闸才是权威，这里只是别把
 * 明知会被拒的东西发出去。
 */

export const MAX_IMAGE_BYTES = 8 * 1024 * 1024
export const MAX_IMAGES = 8
/** 超限图的缩放目标（长边像素）：与各家 provider 内部的下采样档位同量级。 */
export const DOWNSCALE_SIDE = 1568

const ALLOWED_TYPES = /^image\/(png|jpeg|webp|gif)$/

export type DraftImage = {
  id: string
  name: string
  bytes: number
  /** 线格式要的原始 base64（不带 data: 前缀）。 */
  data: string
  /** 本地预览地址：落库前用它渲染气泡里的缩略图。 */
  url: string
  /** 超限压缩过——chip 上照实说。 */
  compressed: boolean
}

export function isImageFile(type: string): boolean {
  return ALLOWED_TYPES.test(type)
}

/** 等比缩放系数：长边超过 maxSide 时缩到 maxSide，否则 1（不动）。 */
export function scaleFor(width: number, height: number, maxSide: number): number {
  const longest = Math.max(width, height)
  return longest <= maxSide || longest === 0 ? 1 : maxSide / longest
}

export function humanBytes(size: number): string {
  if (size < 1024) return `${size}B`
  if (size < 1024 * 1024) return `${Math.floor(size / 1024)}KB`
  return `${(size / (1024 * 1024)).toFixed(1)}MB`
}

/** 字节 → base64；分块拼装，避免一次性展开大数组把调用栈压爆。 */
export function base64Of(bytes: Uint8Array): string {
  let binary = ''
  const chunk = 0x8000
  for (let offset = 0; offset < bytes.length; offset += chunk) {
    binary += String.fromCharCode(...bytes.subarray(offset, offset + chunk))
  }
  return btoa(binary)
}

export function draftFromBytes(
  bytes: Uint8Array<ArrayBuffer>,
  name: string,
  type: string,
  compressed = false,
): DraftImage {
  return {
    id: `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`,
    name,
    bytes: bytes.byteLength,
    data: base64Of(bytes),
    url: URL.createObjectURL(new Blob([bytes], { type })),
    compressed,
  }
}

/** 一个文件 → 一条草稿；超限时先缩再收，缩不动就抛（调用方把话说给用户）。 */
export async function prepareImage(file: File): Promise<DraftImage> {
  const raw = new Uint8Array(await file.arrayBuffer())
  const name = file.name || '粘贴的图片'
  if (raw.byteLength <= MAX_IMAGE_BYTES) {
    return draftFromBytes(raw, name, file.type)
  }
  const shrunk = await downscale(raw, file.type)
  if (shrunk === null) {
    throw new Error(`${name} 超过 ${humanBytes(MAX_IMAGE_BYTES)}，这张图缩不动`)
  }
  return draftFromBytes(shrunk.bytes, name, shrunk.type, true)
}

function outputType(type: string): string {
  if (type === 'image/jpeg' || type === 'image/webp') return type
  return 'image/png' // 含 gif：动画在 canvas 里留不住，按静态 PNG 收
}

async function downscale(
  bytes: Uint8Array<ArrayBuffer>,
  type: string,
): Promise<{ bytes: Uint8Array<ArrayBuffer>; type: string } | null> {
  const url = URL.createObjectURL(new Blob([bytes], { type }))
  try {
    const image = await loadImage(url)
    const scale = scaleFor(image.naturalWidth, image.naturalHeight, DOWNSCALE_SIDE)
    const width = Math.max(1, Math.round(image.naturalWidth * scale))
    const height = Math.max(1, Math.round(image.naturalHeight * scale))
    const canvas = document.createElement('canvas')
    canvas.width = width
    canvas.height = height
    const context = canvas.getContext('2d')
    if (context === null) return null
    context.drawImage(image, 0, 0, width, height)
    const blob = await toBlob(canvas, outputType(type))
    if (blob === null) return null
    return { bytes: new Uint8Array(await blob.arrayBuffer()), type: outputType(type) }
  } catch {
    return null
  } finally {
    URL.revokeObjectURL(url)
  }
}

function loadImage(url: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const image = new Image()
    image.onload = () => resolve(image)
    image.onerror = () => reject(new Error('图片解不开'))
    image.src = url
  })
}

function toBlob(canvas: HTMLCanvasElement, type: string): Promise<Blob | null> {
  return new Promise((resolve) => canvas.toBlob((blob) => resolve(blob), type, 0.9))
}
