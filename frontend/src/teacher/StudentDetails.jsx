import { useState } from 'react'
import { api } from '../api'
import AssignmentForm from './AssignmentForm'
import ConfirmModal from '../components/ConfirmModal'

const formatDate = (value) => value ? new Date(value).toLocaleString() : 'Recently'

export default function StudentDetails({ detail, onBack, onRefresh }) {
  const [editor, setEditor] = useState(null)
  const [form, setForm] = useState({ filename: '', text: '', feedback: '', is_correct: '' })
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [pendingDelete, setPendingDelete] = useState(null)
  const updateField = (field) => (event) => setForm((current) => ({ ...current, [field]: event.target.value }))
  const startEdit = (item) => { setEditor(item.id); setForm({ filename: item.filename, text: item.text || '', feedback: item.feedback || '', is_correct: item.is_correct === true ? 'true' : item.is_correct === false ? 'false' : '' }); setError('') }
  const save = async (event) => {
    event.preventDefault()
    setSaving(true)
    setError('')
    const payload = { ...form, is_correct: form.is_correct === '' ? null : form.is_correct === 'true' }
    try {
      await api(`/teacher/uploads/${editor}`, { method: 'PATCH', body: JSON.stringify(payload) })
      setEditor(null)
      await onRefresh()
    } catch (err) {
      setError(err.message)
    } finally {
      setSaving(false)
    }
  }
  const remove = async (item) => {
    setError('')
    try { await api(`/teacher/uploads/${item.id}`, { method: 'DELETE' }); await onRefresh() } catch (err) { setError(err.message) }
  }
  return <section className="student-detail"><div className="detail-header"><button className="back-button" onClick={onBack}>← All students</button><p className="eyebrow">STUDENT DETAILS</p><h2>{detail.student.name}</h2><p className="muted">{detail.student.email} · {detail.uploads.length} uploads</p></div>{error && <p className="error">{error}</p>}{detail.uploads.length ? <div className="submission-list section-scroll">{detail.uploads.map((item, index) => <article className="submission-card" key={item.id}>{editor === item.id ? <AssignmentForm form={form} updateField={updateField} saving={saving} onSave={save} onCancel={() => setEditor(null)} /> : <><div className="submission-heading"><div><span className="submission-number">{String(index + 1).padStart(2, '0')}</span><div><p className="eyebrow">UPLOADED {formatDate(item.created_at)}</p><h3>{item.filename}</h3></div></div><div className={`mini-score ${item.score === 100 ? 'correct' : ''}`}>{item.is_correct === null ? '—' : `${item.score}%`}</div></div><div className="submission-content"><div><p className="eyebrow">EXTRACTED WORK</p><p className="submission-text">{item.text || 'No text was extracted from this image.'}</p></div><div><p className="eyebrow">AI FEEDBACK</p><p className="submission-feedback">{item.feedback}</p></div></div><div className="submission-actions"><button className="edit-button" type="button" onClick={() => startEdit(item)}>Edit</button><button className="danger-button" type="button" onClick={() => setPendingDelete(item)}>Delete</button></div></>}</article>)}</div> : <div className="empty-state">This student has not uploaded any work yet.</div>}<ConfirmModal open={Boolean(pendingDelete)} title={`Delete ${pendingDelete?.filename}?`} message="This will permanently remove the uploaded work and its feedback." onCancel={() => setPendingDelete(null)} onConfirm={async () => { await remove(pendingDelete); setPendingDelete(null) }} /></section>
}
