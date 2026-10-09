/**
 * Right-aligned user bubble; text renders through the same markdown as assistant messages.
 * Images go below the text, each thumbnail linking to the full image.
 */

import type { HTMLAttributes } from 'react'

import { Markdown } from '../../markdown'
import { cx } from '../../ui/cx'

export type UserBubbleImage = {
  src: string
  name: string | null
}

export type UserBubbleProps = HTMLAttributes<HTMLDivElement> & {
  images?: UserBubbleImage[]
}

export function UserBubble({ className, children, images = [], ...rest }: UserBubbleProps) {
  const hasText = typeof children === 'string' ? children.length > 0 : children != null
  return (
    <div className="flex justify-end">
      <div
        className={cx(
          'max-w-[75%] rounded-md bg-accent-light px-[14px] py-[9px] font-ui text-chat leading-[1.6] text-ink',
          '[&_pre]:my-a8 [&_.markdown>:first-child]:mt-0 [&_.markdown>:last-child]:mb-0',
          className,
        )}
        {...rest}
      >
        {typeof children === 'string' ? <Markdown>{children}</Markdown> : children}
        {images.length > 0 && (
          <div
            className={cx('flex flex-wrap gap-a8', hasText ? 'mt-a8' : '')}
            data-testid="user-images"
          >
            {images.map((image) => (
              <a
                key={image.src}
                href={image.src}
                target="_blank"
                rel="noreferrer"
                title={image.name ?? '查看原图'}
                className="block"
              >
                <img
                  src={image.src}
                  alt={image.name ?? '图片'}
                  loading="lazy"
                  className="max-h-[220px] max-w-[260px] rounded-sm border-hairline border-hair object-contain"
                />
              </a>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
