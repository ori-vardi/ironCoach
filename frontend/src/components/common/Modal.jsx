import { useEffect } from 'react'
import { useChat } from '../../context/ChatContext'

export default function Modal({ open = true, onClose, title, children, small = false, wide = false, onBack }) {
  const { chatOpen } = useChat()

  useEffect(() => {
    const onKey = (e) => {
      if (e.key === 'Escape') {
        e.stopPropagation()
        onClose?.()
      }
    }
    document.addEventListener('keydown', onKey, true) // capture phase to beat other ESC handlers
    return () => document.removeEventListener('keydown', onKey, true)
  }, [onClose])

  if (open === false) return null

  return (
    <div className={`modal${chatOpen ? ' modal-with-chat' : ''}`}>
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
