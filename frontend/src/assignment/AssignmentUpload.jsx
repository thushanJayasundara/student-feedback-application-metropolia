import { useState } from 'react'
import { API_BASE, TOKEN_KEY } from '../api'

const REVIEW_STEPS = [['01', 'Read', 'We read the text on your page.'], ['02', 'Check', 'We check your answer.'], ['03', 'Learn', 'Feedback helps you try again.']]

export default function AssignmentUpload({ onComplete, onError }) {
  const [file, setFile] = useState(null)
  const [status, setStatus] = useState('')
  const isUploading = status === 'Checking your work...'

  const upload = async () => {
    if (!file) return
    setStatus('Checking your work...')
    const body = new FormData()
    body.append('file', file)
    try {
      const response = await fetch(`${API_BASE}/api/upload`, { method: 'POST', headers: { Authorization: `Bearer ${localStorage.getItem(TOKEN_KEY)}` }, body })
      const result = await response.json()
      if (!response.ok) throw new Error(result.detail || 'Upload did not work')
      setStatus('Feedback ready')
      setFile(null)
      onComplete(result.grading)
    } catch (err) {
      setStatus(err.message)
      onError(err.message)
    }
  }

  return <section className="upload-grid"><div className="upload-panel"><div className="section-kicker">01 <span>SEND YOUR WORK</span></div><h2>Learn from one page at a time.</h2><p className="muted">Upload a clear photo of a question and your answer. We will read it, check it, and explain what to try next.</p><label className={`dropzone ${file ? 'has-file' : ''}`}><input type="file" accept="image/*" onChange={(event) => setFile(event.target.files[0] || null)} /><span className="upload-icon">↥</span><strong>{file ? file.name : 'Drop an image here'}</strong><small>{file ? 'Ready to check' : 'or click to choose · PNG, JPG up to 10MB'}</small></label><button className="primary-button upload-button" disabled={!file || isUploading} onClick={upload}>{isUploading ? 'Checking your work...' : 'Get feedback'} <span>→</span></button>{status && !['Feedback ready', 'Checking your work...'].includes(status) && <p className="error">{status}</p>}</div><div className="how-panel"><div className="section-kicker">02 <span>WHAT YOU GET</span></div>{REVIEW_STEPS.map(([number, title, text]) => <div className="benefit" key={number}><b>{number}</b><div><strong>{title}</strong><p>{text}</p></div></div>)}</div></section>
}
