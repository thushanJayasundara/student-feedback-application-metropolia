import { useEffect, useState } from 'react'
import './teacher-dashboard.css'
import './student-uploads.css'
import { api, TOKEN_KEY } from './api'
import Auth from './components/Auth'
import Header from './components/Header'
import Student from './student/Student'
import Teacher from './teacher/Teacher'

export default function App() {
  const [user, setUser] = useState(null)
  const [checking, setChecking] = useState(() => Boolean(localStorage.getItem(TOKEN_KEY)))

  useEffect(() => {
    if (!localStorage.getItem(TOKEN_KEY)) {
      setChecking(false)
      return
    }
    api('/me').then(setUser).catch(() => localStorage.removeItem(TOKEN_KEY)).finally(() => setChecking(false))
  }, [])

  const logout = () => { localStorage.removeItem(TOKEN_KEY); setUser(null) }
  if (checking) return <div className="loading">Getting things ready...</div>
  if (!user) return <Auth onLogin={setUser} />
  return <><Header user={user} onLogout={logout} />{user.role === 'teacher' ? <Teacher /> : <Student user={user} />}</>
}
