export default function Header({ user, onLogout }) {
  const roleLabel = user.role === 'teacher' ? 'Teacher workspace' : 'Student portal'

  return (
    <header className="app-header">
      <div className="brand">
        <div className="brand-mark small">SF</div>
        <div className="brand-copy">
          <span>{roleLabel}</span>
          <strong>Student Feedback</strong>
        </div>
      </div>

      <div className="user-menu">
        <span className="avatar">{user.name.slice(0, 1).toUpperCase()}</span>
        <div className="user-details">
          <strong>{user.name}</strong>
          <span>{user.role === 'teacher' ? 'Teacher' : 'Student'}</span>
        </div>
        <button type="button" onClick={onLogout}>Log out</button>
      </div>
    </header>
  )
}
