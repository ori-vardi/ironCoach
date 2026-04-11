import { useEffect, useRef, useCallback } from 'react'
import { useChat } from '../../context/ChatContext'

export default function Modal({ open = true, onClose, title, children, small = false, wide = false, onBack }) {
  const { chatOpen } = useChat()
  const modalRef = useRef(null)

  // Auto-focus the modal container so it can receive keyboard events
  useEffect(() => {
    if (open !== false && modalRef.current) {
      modalRef.current.focus()
    }
  }, [open])

  const handleKeyDown = useCallback((e) => {
    if (e.key === 'Escape') {
      e.preventDefault()
      e.stopPropagation()
      onClose?.()
    }
  }, [onClose])

  if (open === false) return null

  return (
    <div
      ref={modalRef}
      className={`modal${chatOpen ? ' modal-with-chat' : ''}`}
      onKeyDown={handleKeyDown}
      tabIndex={-1}
      style={{ outline: 'none' }}
    >
      <div className="modal-backdrop" onClick={chatOpen ? undefined : onClose} />
      <div className={`modal-content${small ? ' modal-sm' : ''}${wide ? ' modal-wide' : ''}`} role="dialog" aria-modal="true" aria-labelledby="modal-title">
        <div className="modal-header">
          <h3 id="modal-title">{title}</h3>
          <div className="modal-header-actions">
            {onBack && <button className="btn btn-sm modal-back" onClick={onBack}>&larr;</button>}
            <button className="btn btn-sm modal-close" onClick={onClose}>&times;</button>
          </div>
        </div>
        <div className="modal-body">{children}</div>
      </div>
    </div>
  )
}
