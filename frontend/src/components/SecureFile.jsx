import { useEffect, useState } from 'react'
import { apiFile } from '../api'

export default function SecureFile({ file }) {
  const [result, setResult] = useState({ url: '', source: '', error: '' })

  useEffect(() => {
    let active = true
    let objectUrl = ''
    if (!file?.url) return undefined
    apiFile(file.url).then((url) => {
      objectUrl = url
      if (active) setResult({ url: file.url, source: url, error: '' })
      else URL.revokeObjectURL(url)
    }).catch((err) => {
      if (active) setResult({ url: file.url, source: '', error: err.message })
    })
    return () => {
      active = false
      if (objectUrl) URL.revokeObjectURL(objectUrl)
    }
  }, [file?.url])

  if (!file?.url) return <p className="error">The file is no longer available.</p>
  if (result.url !== file.url) return <p className="muted">Loading {file?.filename || 'file'}...</p>
  if (result.error) return <p className="error">{result.error}</p>
  if (!result.source) return <p className="muted">Loading {file?.filename || 'file'}...</p>
  const isPdf = file?.content_type === 'application/pdf' || /\.pdf$/i.test(file?.filename || '')
  return (
    <div className="secure-file">
      <strong>{file?.filename || 'Submitted work'}</strong>
      {isPdf
        ? <iframe title={file?.filename || 'Submitted PDF'} src={result.source} />
        : <a href={result.source} target="_blank" rel="noreferrer"><img src={result.source} alt={file?.filename || 'Submitted work'} /></a>}
      <a className="secure-file-open" href={result.source} target="_blank" rel="noreferrer">Open full size</a>
    </div>
  )
}
