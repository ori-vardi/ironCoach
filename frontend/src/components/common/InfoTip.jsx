import { useState, useRef, useEffect, useCallback } from 'react'
import { createPortal } from 'react-dom'

export default function InfoTip({ text }) {
  const [pos, setPos] = useState(null)
  const [pinned, setPinned] = useState(false)
  const ref = useRef(null)
  const popupRef = useRef(null)
  const closeTimer = useRef(null)

  const clearCloseTimer = useCallback(() => {
    if (closeTimer.current) { clearTimeout(closeTimer.current); closeTimer.current = null }
  }, [])

  function calcPos() {
    if (!ref.current) return null
    const rect = ref.current.getBoundingClientRect()
    return {
      top: rect.bottom + 8,
      left: Math.max(8, Math.min(rect.left - 200, window.innerWidth - 460)),
    }
  }

  function handleEnter() {
    if (pinned) return
    clearCloseTimer()
    setPos(calcPos())
  }

  function handleLeave() {
    if (pinned) return
    closeTimer.current = setTimeout(() => setPos(null), 150)
  }

  function handlePopupEnter() {
    if (pinned) return
    clearCloseTimer()
  }

  function handlePopupLeave() {
    if (pinned) return
    closeTimer.current = setTimeout(() => setPos(null), 150)
  }

  function handleClick(e) {
    e.stopPropagation()
    if (pinned) {
      setPinned(false)
      setPos(null)
    } else {
      setPinned(true)
      setPos(calcPos())
    }
  }

  // Close pinned on outside click
  useEffect(() => {
    if (!pinned) return
    const handler = (e) => {
      if (ref.current && ref.current.contains(e.target)) return
      if (popupRef.current && popupRef.current.contains(e.target)) return
      setPinned(false)
      setPos(null)
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [pinned])

  if (!text || typeof text !== 'string') return null

  const popup = pos && createPortal(
    <div
      className="info-tip-popup"
      ref={popupRef}
      onMouseEnter={handlePopupEnter}
      onMouseLeave={handlePopupLeave}
      dir="auto"
      style={{ display: 'block', top: pos.top, left: pos.left }}
    >
      {parseInfoLines(text)}
    </div>,
    document.body
  )

  return (
    <span className="info-tip" ref={ref} onMouseEnter={handleEnter} onMouseLeave={handleLeave} onClick={handleClick}>
      <span className={`info-tip-icon${pinned ? ' info-tip-pinned' : ''}`}>i</span>
      {popup}
    </span>
  )
}

export function parseInfoLines(text) {
  if (!text || typeof text !== 'string') return null
  return text.split('\n').map((line, i) => {
    if (!line.trim()) return <br key={i} />
    const isBold = line.startsWith('**') && line.endsWith('**')
    if (isBold) return <strong key={i} className="info-tip-heading">{line.slice(2, -2)}</strong>
    const tokens = line.split(/(\*\*.+?\*\*|\[dot:#[a-fA-F0-9]{3,6}\])/)
    const rendered = tokens.map((tok, j) => {
      if (tok.startsWith('**') && tok.endsWith('**')) return <strong key={j}>{tok.slice(2, -2)}</strong>
      const dotMatch = tok.match(/^\[dot:(#[a-fA-F0-9]{3,6})\]$/)
      if (dotMatch) return <span key={j} className="info-tip-dot" style={{ background: dotMatch[1] }} />
      return tok
    })
    return <span key={i} className="info-tip-line">{rendered}</span>
  })
}

