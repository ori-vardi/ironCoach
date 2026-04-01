import Modal from './Modal'
import { useI18n } from '../../i18n/I18nContext'

export default function ConfirmDialog({ open, title, message, onConfirm, onCancel, confirmLabel, danger = true }) {
  const { t } = useI18n()
  if (!open) return null
  return (
    <Modal open onClose={onCancel} title={title || t('confirm')} small>
      <p style={{ margin: '12px 0 20px' }}>{message}</p>
      <div className="form-actions">
        <button className={`btn ${danger ? 'btn-red' : 'btn-accent'}`} onClick={onConfirm}>{confirmLabel || t('delete')}</button>
        <button className="btn" onClick={onCancel}>{t('cancel')}</button>
      </div>
    </Modal>
  )
}
