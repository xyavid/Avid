/**
 * Image draft preparation: over-limit images are downscaled in the browser (PNG stays PNG,
 * screenshots are text), the server only validates. Size limits mirror `avid/attachments.py`,
 * which is the authoritative gate.
 */

export const MAX_IMAGE_BYTES = 8 * 1024 * 1024
export const MAX_IMAGES = 8
/** Longest-side target for downscaled images; same order as provider-side downsampling. */
export const DOWNSCALE_SIDE = 1568

const ALLOWED_TYPES = /^image\/(png|jpeg|webp|gif)$/

export type DraftImage = {
  id: string
  name: string
  bytes: number
  /** Raw base64 for the wire format (no data: prefix). */
  data: string
  /** Local preview URL; valid only before the message is persisted. */
  url: string
  /** Downscaled because it exceeded the size limit. */
  compressed: boolean
}

export function isImageFile(type: string): boolean {
  return ALLOWED_TYPES.test(type)
}

/** Scale factor: maxSide / longest side, or 1 when already within maxSide. */
export function scaleFor(width: number, height: number, maxSide: number): number {
  const longest = Math.max(width, height)
  return longest <= maxSide || longest === 0 ? 1 : maxSide / longest
}

export function humanBytes(size: number): string {
  if (size < 1024) return `${size}B`
  if (size < 1024 * 1024) return `${Math.floor(size / 1024)}KB`
  return `${(size / (1024 * 1024)).toFixed(1)}MB`
}

/** bytes → base64, chunked so a large array can't blow the call stack. */
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

/** File → draft; over-limit files are downscaled first, throws when that fails. */
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
  return 'image/png' // includes gif: animation can't survive canvas, so it lands as static PNG
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
