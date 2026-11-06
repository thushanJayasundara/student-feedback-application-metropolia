import { useEffect, useRef, useState } from 'react'

export default function ConfirmModal({ open, title = 'Confirm deletion', message, onCancel, onConfirm }) {
  const [confirming, setConfirming] = useState(false)
  const cancelButtonRef = useRef(null)

  useEffect(() => {
    if (!open) return undefined
    cancelButtonRef.current?.focus()
    const handleKeyDown = (event) => {
      if (event.key === 'Escape' && !confirming) onCancel()
    }
    document.addEventListener('keydown', handleKeyDown)
    return () => document.removeEventListener('keydown', handleKeyDown)
  }, [open, confirming, onCancel])

  if (!open) return null

  const confirm = async () => {
    setConfirming(true)
    try {
      await onConfirm()
    } finally {
      setConfirming(false)
    }
  }

  return <div className="modal-backdrop" role="presentation">
    <div className="confirm-modal" role="dialog" aria-modal="true" aria-labelledby="confirm-modal-title" aria-describedby="confirm-modal-message">
      <p className="eyebrow">ARE YOU SURE?</p>
      <h2 id="confirm-modal-title">{title}</h2>
      <p id="confirm-modal-message">{message}</p>
      <div className="confirm-modal-actions">
        <button className="secondary-button" type="button" onClick={onCancel} disabled={confirming} ref={cancelButtonRef}>Cancel</button>
        <button className="modal-delete-button" type="button" onClick={confirm} disabled={confirming}>{confirming ? 'Deleting...' : 'Delete'}</button>
      </div>
    </div>
  </div>
}
