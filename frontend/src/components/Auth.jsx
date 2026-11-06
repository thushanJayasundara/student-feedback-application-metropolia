import { useEffect, useState } from 'react'
import { api, TOKEN_KEY } from '../api'

export default function Auth({ onLogin }) {
  const [role, setRole] = useState('student')
  const [mode, setMode] = useState('login')
  const [form, setForm] = useState({ name: '', email: '', password: '', grade: '', class_section: '', class_id: '', enrollment_key: '', subject: '' })
  const [showPassword, setShowPassword] = useState(false)
  const [error, setError] = useState('')
  const [success, setSuccess] = useState('')
  const [loading, setLoading] = useState(false)
  const [classes, setClasses] = useState([])
  const isLogin = mode === 'login'

  useEffect(() => {
    if (role !== 'student' || isLogin) return
    api('/classes').then((data) => setClasses(data.classes || [])).catch((err) => setError(err.message))
  }, [role, isLogin])

  const submit = async (event) => {
    event.preventDefault()
    setError('')
    setSuccess('')
    setLoading(true)
    try {
      const data = await api(`/auth/${mode}/${role}`, { method: 'POST', body: JSON.stringify(form) })
      if (!isLogin) {
        setMode('login')
        setForm((current) => ({ ...current, name: '', password: '', class_id: '', enrollment_key: '' }))
        setSuccess('Account created successfully. Please log in with your new account.')
        return
      }
      localStorage.setItem(TOKEN_KEY, data.token)
      onLogin(data.user)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  const updateField = (field) => (event) => setForm((current) => ({ ...current, [field]: event.target.value }))
  const switchRole = (accountRole) => {
    setRole(accountRole)
    setForm({ name: '', email: '', password: '', grade: '', class_section: '', class_id: '', enrollment_key: '', subject: '' })
    setShowPassword(false)
    setError('')
    setSuccess('')
  }
  const toggleMode = () => { setMode(isLogin ? 'signup' : 'login'); setError(''); setSuccess('') }
  const selectedGradeClasses = classes.filter((item) => item.grade === form.grade)

  return (
    <main className="auth-page">
      <section className="auth-intro">
        <div className="brand-mark">sf</div>
        <p className="eyebrow">STUDENT FEEDBACK SYSTEM</p>
        <h1>Learn, Improve and Grow</h1>
        <p className="intro-copy">Upload your work, get feedback, and see your progress.</p>
        <div className="signal"><span /> Quick feedback <span /> teacher help</div>
      </section>
      <section className="auth-card">
        <div className="role-switch" aria-label="Choose account type">
          {['student', 'teacher'].map((accountRole) => <button key={accountRole} className={role === accountRole ? 'active' : ''} onClick={() => switchRole(accountRole)} type="button">{accountRole[0].toUpperCase() + accountRole.slice(1)}</button>)}
        </div>
        <div className="form-heading"><p className="eyebrow">{isLogin ? 'WELCOME BACK' : 'GET STARTED'}</p><h2>{isLogin ? `Log in as a ${role}` : `Make your ${role} account`}</h2><p>{isLogin ? 'Keep going from where you stopped.' : 'A simple place to learn.'}</p></div>
        <form onSubmit={submit}>
          {!isLogin && <label>Name<input required value={form.name} onChange={updateField('name')} placeholder="Alex Morgan" /></label>}
          {!isLogin && role === 'student' && <>{classes.length ? <><label>Grade<select required value={form.grade} onChange={(event) => setForm((current) => ({ ...current, grade: event.target.value, class_section: '', class_id: '' }))}><option value="">Choose a grade</option>{[...new Set(classes.map((item) => item.grade))].map((grade) => <option key={grade} value={grade}>{grade}</option>)}</select></label><label>Class<select required disabled={!form.grade} value={form.class_id} onChange={(event) => { const selected = selectedGradeClasses.find((item) => item.id === event.target.value); setForm((current) => ({ ...current, class_id: event.target.value, class_section: selected?.section || '' })) }}><option value="">Choose a section and teacher</option>{selectedGradeClasses.map((item) => <option key={item.id} value={item.id}>Section {item.section} · {item.teacher_name}</option>)}</select></label><label>Enrollment key<input required autoCapitalize="characters" value={form.enrollment_key} onChange={(event) => setForm((current) => ({ ...current, enrollment_key: event.target.value.toUpperCase() }))} placeholder="Enter the key from your teacher" /></label></> : <p className="error">No classes are available. Please contact your teacher.</p>}</>}
          {!isLogin && role === 'teacher' && <label>Subject<select value={form.subject} onChange={updateField('subject')} required><option value="">Choose a subject</option><option>Mathematics</option><option>English</option><option>Science</option></select></label>}
          {success && <p className="success-message" role="status">{success}</p>}
          <label>Email<input required type="email" value={form.email} onChange={updateField('email')} placeholder="you@school.edu" /></label>
          <label>Password<div className="password-field"><input required minLength="8" type={showPassword ? 'text' : 'password'} value={form.password} onChange={updateField('password')} placeholder="8+ characters" /><button className="password-toggle" type="button" onClick={() => setShowPassword((current) => !current)} aria-label={showPassword ? 'Hide password' : 'Show password'}>{showPassword ? 'Hide' : 'Show'}</button></div></label>
          {error && <p className="error">{error}</p>}
          <button className="primary-button" disabled={loading} type="submit">{loading ? 'Getting things ready...' : isLogin ? 'Log in' : 'Sign up'} <span>→</span></button>
        </form>
        <p className="auth-toggle">{isLogin ? 'New here?' : 'Already have an account?'}{' '}<button type="button" onClick={toggleMode}>{isLogin ? 'Sign up' : 'Log in'}</button></p>
      </section>
    </main>
  )
}
